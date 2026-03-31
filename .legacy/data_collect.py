import argparse
import csv
import os
import threading
import time
from collections import deque

import numpy as np

try:
    from gpiozero import LED
    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    LED = None
    _GPIO_AVAILABLE = False

from pymavlink import mavutil

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_PORT = 'COM5' if os.name == 'nt' else '/dev/serial/by-id/usb-Hex_ProfiCNC_CubeOrange_2F003B000C51303231383439-if00'
PORT = os.getenv('MAVLINK_PORT', DEFAULT_PORT)
BAUD = int(os.getenv('MAVLINK_BAUD', '921600'))

TARGET_IMU_RATE_HZ = int(os.getenv('MAVLINK_IMU_RATE_HZ', '100'))
SAMPLING_RATE = float(TARGET_IMU_RATE_HZ)

MAX_TIME_PTS = int(max(600, 60 * SAMPLING_RATE))

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# Thresholds for velocity RMS (mm/s) - ISO 10816 based
THRESH_GREEN  = 2.8    # below  → green  (good)
THRESH_YELLOW = 7.1    # below  → yellow (acceptable), above → red (alarm)

# ---------------------------------------------------------------------------
# GPIO Traffic Lights
# ---------------------------------------------------------------------------
if _GPIO_AVAILABLE:
    led_red = LED(17, active_high=False)
    led_yellow = LED(27, active_high=False)
    led_green = LED(22, active_high=False)
else:
    led_red = led_yellow = led_green = None

def _set_gpio_lights(red: bool, yellow: bool, green: bool) -> None:
    if not _GPIO_AVAILABLE:
        return
    if red:
        led_red.on()
    else:
        led_red.off()
    if yellow:
        led_yellow.on()
    else:
        led_yellow.off()
    if green:
        led_green.on()
    else:
        led_green.off()

# ArduPilot RAW_IMU sends mG (milli-G) for xacc/yacc/zacc
MG_TO_MS2 = 9.80665 / 1000.0

# High-pass filter cutoff for velocity integration (removes drift)
# ~0.5 Hz cutoff is typical for machine vibration
HP_CUTOFF_HZ = 0.5

# Exponential smoothing for velocity (0..1). Higher → smoother.
VEL_SMOOTH_ALPHA = 0.85

# ---------------------------------------------------------------------------
# Shared data buffers
# ---------------------------------------------------------------------------
_az_ms2_history = deque(maxlen=MAX_TIME_PTS)  # Z acceleration in m/s^2 (for FFT)
_vz_raw_mms_history = deque(maxlen=MAX_TIME_PTS)  # Unsmooth velocity for FFT/RMS
_vz_mms_history = deque(maxlen=MAX_TIME_PTS)      # Smoothed velocity for display
_ts_history = deque(maxlen=MAX_TIME_PTS)

_lock = threading.Lock()
_connected = False
_actual_rate_hz = 0.0

# ---------------------------------------------------------------------------
# Logging state
# ---------------------------------------------------------------------------
_log_active = False
_log_buffer = []
_log_counter = 0
_log_lock = threading.Lock()


# ---------------------------------------------------------------------------
# High-pass filtered integrator for velocity
# ---------------------------------------------------------------------------
class VelocityIntegrator:
    """
    Integrates acceleration to velocity with high-pass filtering.

    Uses a simple IIR high-pass filter to remove DC drift while
    preserving oscillatory (vibration) components.
    """

    def __init__(self, sample_rate: float, cutoff_hz: float = 0.5, max_dt: float = 0.1):
        self.sample_rate = sample_rate
        self.cutoff_hz = cutoff_hz
        self.max_dt = max_dt
        # Use half the target period as a reasonable minimum dt guard
        self.min_dt = max(1.0 / (sample_rate * 2.0), 0.001)
        self.reset()

    def _alpha(self, dt: float) -> float:
        # alpha = RC / (RC + dt), where RC = 1/(2*pi*fc)
        rc = 1.0 / (2.0 * np.pi * self.cutoff_hz)
        return rc / (rc + dt)

    def update(self, accel_ms2: float, timestamp: float) -> float:
        """Update with new acceleration sample, return high-pass velocity in mm/s."""
        if self.last_time is None:
            self.last_time = timestamp
            self._prev_accel = accel_ms2
            return 0.0

        dt = timestamp - self.last_time
        if dt <= 0.0:
            dt = self.min_dt
        dt = min(dt, self.max_dt)

        # Trapezoidal integration of acceleration → raw velocity (m/s)
        delta_v = 0.5 * (accel_ms2 + self._prev_accel) * dt
        v_raw = self._v_raw + delta_v

        # First-order high-pass on velocity to bleed off drift
        alpha = self._alpha(dt)
        v_hp = alpha * (self._v_hp + v_raw - self._prev_v_raw)

        # Update state
        self._prev_accel = accel_ms2
        self._prev_v_raw = v_raw
        self._v_raw = v_raw
        self._v_hp = v_hp
        self.last_time = timestamp

        return v_hp * 1000.0

    def reset(self):
        self._v_raw = 0.0
        self._prev_v_raw = 0.0
        self._v_hp = 0.0
        self._prev_accel = 0.0
        self.last_time = None


# ---------------------------------------------------------------------------
# MAVLink Reader Thread
# ---------------------------------------------------------------------------
class MAVLinkReader(threading.Thread):
    """
    Reads IMU data from ArduPilot via MAVLink.

    - RAW_IMU: Z-axis acceleration for FFT analysis
    - Integrates acceleration to velocity (mm/s) with high-pass filter
    """

    def __init__(
        self,
        port: str = PORT,
        baud: int = BAUD,
        target_rate_hz: int = TARGET_IMU_RATE_HZ,
    ):
        super().__init__(daemon=True, name='MAVLinkReader')
        self.port = port
        self.baud = baud
        self.target_rate_hz = target_rate_hz
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        # Calibration
        self._gravity_offset = 0.0
        self._calibrated = False
        self._calibration_samples = []
        self._load_calibration()

        # Rate measurement
        self._msg_count = 0
        self._rate_window_start = None
        self._rate_window_count = 0

        # Velocity integrator & smoothing state
        self._integrator = VelocityIntegrator(target_rate_hz, HP_CUTOFF_HZ)
        self._vz_smooth = 0.0

    def _load_calibration(self):
        """Load prior offset as a hint, but always re-calibrate on start."""
        calib_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration.txt')
        try:
            if os.path.exists(calib_file):
                with open(calib_file, 'r') as f:
                    self._gravity_offset = float(f.read().strip())
                # Use as initial guess but force fresh calibration each run
                self._calibrated = False
                print(f"[MAVLink] Loaded prior gravity hint: {self._gravity_offset:.2f} mG (will recalibrate)")
        except Exception as e:
            print(f"[MAVLink] Could not load calibration: {e}")

    def _connect(self) -> mavutil.mavlink_connection:
        print(f'[MAVLink] Connecting to {self.port} @ {self.baud} baud...')

        connection = mavutil.mavlink_connection(
            self.port,
            baud=self.baud,
            autoreconnect=True,
        )

        print('[MAVLink] Waiting for heartbeat...')
        connection.wait_heartbeat(timeout=10)
        print(f'[MAVLink] Heartbeat received (system {connection.target_system}, '
              f'component {connection.target_component})')

        return connection

    def _configure_stream_rate(self, connection: mavutil.mavlink_connection):
        """Configure stream rate for RAW_IMU messages."""

        # Request RAW_SENSORS stream (includes RAW_IMU)
        connection.mav.request_data_stream_send(
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS,
            self.target_rate_hz,
            1,
        )

        # Also try message interval for RAW_IMU
        interval_us = int(1_000_000 / self.target_rate_hz)
        connection.mav.command_long_send(
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_RAW_IMU,
            interval_us,
            0, 0, 0, 0, 0,
        )

        print(f'[MAVLink] Requested RAW_IMU at {self.target_rate_hz} Hz')

    def _calibrate_gravity(self, zacc_mg: float):
        if self._calibrated:
            return

        # Collect a larger window for a stable estimate and reject spikes
        self._calibration_samples.append(zacc_mg)
        if len(self._calibration_samples) < 200:
            return

        samples = np.array(self._calibration_samples[-400:])  # use the most recent chunk
        median = float(np.median(samples))
        mad = float(np.median(np.abs(samples - median)))

        # Detect motion: if dispersion is high, defer calibration
        if mad > 15.0:  # mG spread threshold for “still”
            return

        # Trim outliers using MAD-based gate
        gate = 3.5 * mad if mad > 0 else 5.0
        trimmed = samples[np.abs(samples - median) <= gate]
        if len(trimmed) < 50:
            return

        self._gravity_offset = float(np.median(trimmed))
        self._calibrated = True
        print(f'[MAVLink] Gravity calibrated: offset = {self._gravity_offset:.2f} mG')

        # Save it so it's used for every subsequent run
        calib_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration.txt')
        try:
            with open(calib_file, 'w') as f:
                f.write(str(self._gravity_offset))
            print(f"[MAVLink] Saved calibration to {calib_file}")
        except Exception:
            pass

    def _update_rate_measurement(self, ts: float):
        global _actual_rate_hz

        if self._rate_window_start is None:
            self._rate_window_start = ts
            self._rate_window_count = 0

        self._rate_window_count += 1
        elapsed = ts - self._rate_window_start

        if elapsed >= 1.0:
            _actual_rate_hz = self._rate_window_count / elapsed
            self._rate_window_start = ts
            self._rate_window_count = 0

    def _reset_state(self):
        """Reset rate state for reconnection."""
        self._rate_window_start = None
        self._rate_window_count = 0
        self._integrator.reset()
        self._vz_smooth = 0.0
        # Force calibration on next run
        self._calibrated = False
        self._calibration_samples = []

    def run(self):
        global _connected, _actual_rate_hz

        RECONNECT_DELAY = 3  # seconds between reconnection attempts

        while not self._stop_event.is_set():
            connection = None
            try:
                connection = self._connect()
                self._configure_stream_rate(connection)
                _connected = True

                # Always recalibrate at the start of each connection
                self._calibrated = False
                self._calibration_samples = []

                print(f'[MAVLink] Starting data collection')
                print('[MAVLink] Calibrating gravity offset (keep device still)...')

                while not self._stop_event.is_set():
                    self._pause_event.wait()

                    msg = connection.recv_match(
                        type='RAW_IMU',
                        blocking=True,
                        timeout=0.5,
                    )

                    if msg is None:
                        continue

                    ts = time.time()
                    self._msg_count += 1
                    self._update_rate_measurement(ts)

                    raw_zacc = msg.zacc  # in mG
                    self._calibrate_gravity(raw_zacc)

                    if self._calibrated:
                        zacc_corrected = raw_zacc - self._gravity_offset
                    else:
                        zacc_corrected = raw_zacc - 1000

                    az_ms2 = zacc_corrected * MG_TO_MS2

                    # Integrate acceleration to velocity (mm/s)
                    vz_raw = self._integrator.update(az_ms2, ts)

                    # Smooth velocity to reduce display jitter
                    self._vz_smooth = (VEL_SMOOTH_ALPHA * self._vz_smooth) + ((1.0 - VEL_SMOOTH_ALPHA) * vz_raw)
                    vz_smooth = self._vz_smooth

                    with _lock:
                        _az_ms2_history.append(az_ms2)
                        _vz_raw_mms_history.append(vz_raw)
                        _vz_mms_history.append(vz_smooth)
                        _ts_history.append(ts)
                        
                        # Evaluate GPIO traffic lights every 25 samples (~0.25s at 100Hz)
                        if self._msg_count % 25 == 0:
                            n_1s = max(1, int(self.target_rate_hz))
                            if len(_vz_raw_mms_history) >= n_1s:
                                # Use raw velocity for RMS/severity to keep spectrum accurate
                                recent_vz = list(_vz_raw_mms_history)[-n_1s:]
                                rms = float(np.sqrt(np.mean(np.array(recent_vz) ** 2)))
                                
                                is_red    = rms >= THRESH_YELLOW
                                is_yellow = THRESH_GREEN <= rms < THRESH_YELLOW
                                is_green  = rms < THRESH_GREEN
                            else:
                                is_red, is_yellow, is_green = False, False, False
                            
                            _set_gpio_lights(is_red, is_yellow, is_green)

                    with _log_lock:
                        if _log_active:
                            global _log_counter, _log_buffer
                            _log_counter += 1
                            _log_buffer.append({
                                'counter': _log_counter,
                                'unix_time': ts,
                                'iso_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)),
                                'az_ms2': az_ms2,
                                'vz_raw_mms': vz_raw,
                                'vz_mms': vz_smooth,
                            })

                    if self._msg_count % 25 == 0:
                        print(
                            f'az={az_ms2:+8.4f} m/s²  vz={vz_smooth:+8.2f} mm/s  rate={_actual_rate_hz:.1f} Hz',
                            end='\r',
                        )

            except Exception as exc:
                print(f'\n[MAVLink] Error: {exc}')
                import traceback
                traceback.print_exc()

            finally:
                _connected = False
                _actual_rate_hz = 0.0
                if connection:
                    try:
                        connection.close()
                    except Exception:
                        pass

            # Retry connection unless stopped
            if not self._stop_event.is_set():
                self._reset_state()
                print(f'[MAVLink] Reconnecting in {RECONNECT_DELAY}s...')
                # Use wait with timeout so we can respond to stop_event
                if self._stop_event.wait(RECONNECT_DELAY):
                    break  # Stop event was set

        print('\n[MAVLink] Reader stopped.')

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_histories() -> dict:
    """Get all history buffers for plotting/FFT."""
    with _lock:
        az_ms2 = list(_az_ms2_history)
        vz_raw = list(_vz_raw_mms_history)
        vz_mms = list(_vz_mms_history)
        ts = list(_ts_history)
    rel = [stamp - ts[0] for stamp in ts] if ts else []
    return {
        'az_ms2': az_ms2,     # Z acceleration in m/s^2 (for FFT)
        'vz': vz_mms,         # Smoothed Z velocity in mm/s (display)
        'vz_raw': vz_raw,     # Raw Z velocity in mm/s (FFT/RMS)
        'ts': ts,
        'rel_s': rel,
    }


def get_latest_sample() -> dict:
    """Get the most recent sample."""
    with _lock:
        if not _ts_history:
            return {}
        return {
            'az_ms2': _az_ms2_history[-1],
            'vz_raw_mms': _vz_raw_mms_history[-1],
            'vz_mms': _vz_mms_history[-1],
            'unix_time': _ts_history[-1],
            'rate_hz': _actual_rate_hz,
        }


def get_actual_rate() -> float:
    return _actual_rate_hz


def is_connected() -> bool:
    return _connected


def start_logging() -> None:
    global _log_active, _log_buffer, _log_counter
    with _log_lock:
        _log_active = True
        _log_buffer = []
        _log_counter = 0


def stop_logging() -> list:
    global _log_active
    with _log_lock:
        _log_active = False
        data = list(_log_buffer)
    return data


def save_log(rpm: int, load_w: int, data: list) -> str:
    """Save logged data to CSV file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename = f'vibration_RPM{rpm}_LOAD{load_w}W_{timestamp}.csv'
    filepath = os.path.join(LOG_DIR, filename)

    vz_vals = [row['vz_mms'] for row in data] if data else []
    timestamps = [row['unix_time'] for row in data] if data else []
    vz_rms = float(np.sqrt(np.mean(np.square(vz_vals)))) if vz_vals else 0.0

    effective_rate_hz = SAMPLING_RATE
    if len(timestamps) >= 2:
        intervals = np.diff(np.array(timestamps, dtype=float))
        median_dt = float(np.median(intervals)) if len(intervals) else 0.0
        if median_dt > 0:
            effective_rate_hz = 1.0 / median_dt

    with open(filepath, 'w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['# Engine Vibration Log (MAVLink IMU)'])
        writer.writerow(['# RPM', rpm])
        writer.writerow(['# Load (W)', load_w])
        writer.writerow(['# Timestamp', timestamp])
        writer.writerow(['# Samples', len(data)])
        writer.writerow(['# Target Rate (Hz)', TARGET_IMU_RATE_HZ])
        writer.writerow(['# Effective Rate (Hz)', f'{effective_rate_hz:.6f}'])
        writer.writerow(['# VZ RMS (mm/s)', f'{vz_rms:.6f}'])
        writer.writerow([])
        writer.writerow([
            'counter',
            'unix_time',
            'iso_time',
            'az_ms2',
            'vz_mms',
        ])
        for row in data:
            writer.writerow([
                row['counter'],
                f"{row['unix_time']:.6f}",
                row['iso_time'],
                f"{row['az_ms2']:.6f}",
                f"{row['vz_mms']:.6f}",
            ])

    print(
        f'[Logger] Saved -> {filepath} '
        f'({len(data)} samples, VZ RMS={vz_rms:.4f} mm/s, rate={effective_rate_hz:.1f} Hz)'
    )
    return filepath


# ---------------------------------------------------------------------------
# Log discovery helpers
# ---------------------------------------------------------------------------
def get_latest_log_path():
    if not os.path.isdir(LOG_DIR):
        return None
    files = [
        os.path.join(LOG_DIR, f)
        for f in os.listdir(LOG_DIR)
        if f.lower().endswith('.csv')
    ]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def get_all_log_paths():
    if not os.path.isdir(LOG_DIR):
        return []
    files = [
        os.path.join(LOG_DIR, f)
        for f in os.listdir(LOG_DIR)
        if f.lower().endswith('.csv')
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    return files


# ---------------------------------------------------------------------------
# Stand-alone mode
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Read IMU data via MAVLink for vibration FFT analysis.'
    )
    parser.add_argument('--port', default=PORT, help='Serial port')
    parser.add_argument('--baud', type=int, default=BAUD, help='Baudrate')
    parser.add_argument('--rate', type=int, default=TARGET_IMU_RATE_HZ, help='Target IMU rate Hz')
    parser.add_argument('--duration', type=float, default=0.0, help='Run duration (0=forever)')
    parser.add_argument('--rpm', type=int, default=0, help='RPM metadata')
    parser.add_argument('--load', type=int, default=0, help='Load in watts')
    parser.add_argument('--log', action='store_true', help='Save CSV log')
    args = parser.parse_args()

    reader = MAVLinkReader(
        port=args.port,
        baud=args.baud,
        target_rate_hz=args.rate,
    )

    if args.log:
        start_logging()

    reader.start()
    started_at = time.time()
    print('Collecting IMU data. Press Ctrl+C to stop.')

    try:
        while True:
            if args.duration > 0 and (time.time() - started_at) >= args.duration:
                break
            latest = get_latest_sample()
            if latest:
                print(
                    f"latest -> az={latest['az_ms2']:+.4f} m/s², "
                    f"vz={latest['vz_mms']:+.2f} mm/s, "
                    f"rate={latest.get('rate_hz', 0):.1f} Hz"
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        reader.join(timeout=2.0)

    if args.log:
        data = stop_logging()
        save_log(args.rpm, args.load, data)


if __name__ == '__main__':
    main()
