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

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'logs')
LOG_DIR = os.path.abspath(LOG_DIR)

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
    """Integrates acceleration to velocity with high-pass filtering."""

    def __init__(self, sample_rate: float, cutoff_hz: float = 0.5, max_dt: float = 0.1):
        self.sample_rate = sample_rate
        self.cutoff_hz = cutoff_hz
        self.max_dt = max_dt
        self.min_dt = max(1.0 / (sample_rate * 2.0), 0.001)
        self.reset()

    def _alpha(self, dt: float) -> float:
        rc = 1.0 / (2.0 * np.pi * self.cutoff_hz)
        return rc / (rc + dt)

    def update(self, accel_ms2: float, timestamp: float) -> float:
        if self.last_time is None:
            self.last_time = timestamp
            self._prev_accel = accel_ms2
            return 0.0

        dt = timestamp - self.last_time
        if dt <= 0.0:
            dt = self.min_dt
        dt = min(dt, self.max_dt)

        delta_v = 0.5 * (accel_ms2 + self._prev_accel) * dt
        v_raw = self._v_raw + delta_v

        alpha = self._alpha(dt)
        v_hp = alpha * (self._v_hp + v_raw - self._prev_v_raw)

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
    """Reads IMU data from ArduPilot via MAVLink."""

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

        self._gravity_offset = 0.0
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._calibration_samples = []
        self._load_calibration()

        self._msg_count = 0
        self._rate_window_start = None
        self._rate_window_count = 0

        self._integrator = VelocityIntegrator(target_rate_hz, HP_CUTOFF_HZ)
        self._vz_smooth = 0.0

    def _load_calibration(self):
        calib_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'calibration.txt')
        calib_file = os.path.abspath(calib_file)
        try:
            if os.path.exists(calib_file):
                with open(calib_file, 'r') as f:
                    prior = float(f.read().strip())
                    self._gravity_offset = prior
                    print(f'[MAVLink] Loaded prior gravity hint: {prior:+.2f} mG (will recalibrate)')
        except Exception:
            print('[MAVLink] Could not read calibration file; will recalibrate fresh')

    def _save_calibration(self, offset_mg: float):
        calib_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'calibration.txt')
        calib_file = os.path.abspath(calib_file)
        try:
            with open(calib_file, 'w') as f:
                f.write(f"{offset_mg:.2f}")
        except Exception:
            print('[MAVLink] Failed to save calibration file')

    def _connect(self):
        print(f'[MAVLink] Connecting to {self.port} @ {self.baud} baud...')
        connection = mavutil.mavlink_connection(self.port, baud=self.baud)
        connection.wait_heartbeat(timeout=10)
        print(f"[MAVLink] Heartbeat received (system {connection.target_system}, component {connection.target_component})")
        connection.mav.request_data_stream_send(
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS,
            self.target_rate_hz,
            1,
        )
        return connection

    def _reset_state(self):
        self._msg_count = 0
        self._rate_window_start = None
        self._rate_window_count = 0
        self._integrator.reset()
        self._vz_smooth = 0.0
        with _lock:
            _az_ms2_history.clear()
            _vz_raw_mms_history.clear()
            _vz_mms_history.clear()
            _ts_history.clear()

    def _update_rate(self):
        now = time.time()
        if self._rate_window_start is None:
            self._rate_window_start = now
            self._rate_window_count = 0
            return
        self._rate_window_count += 1
        elapsed = now - self._rate_window_start
        if elapsed >= 1.0:
            rate = self._rate_window_count / elapsed
            global _actual_rate_hz
            _actual_rate_hz = rate
            self._rate_window_start = now
            self._rate_window_count = 0

    def _calibrate(self, az_mg: float) -> float:
        self._calibration_samples.append(az_mg)
        if len(self._calibration_samples) >= 200:
            self._gravity_offset = float(np.median(np.array(self._calibration_samples)))
            self._calibrated = True
            self._save_calibration(self._gravity_offset)
            print(f'[MAVLink] Calibrated gravity offset: {self._gravity_offset:+.2f} mG')
        return az_mg - self._gravity_offset

    def run(self):
        global _connected
        while not self._stop_event.is_set():
            try:
                print(f'[MAVLink] Connecting to {self.port} @ {self.baud} baud...\n')
                connection = mavutil.mavlink_connection(self.port, baud=self.baud)
                if connection is None:
                    raise RuntimeError('Failed to create MAVLink connection')

                connection.mav.request_data_stream_send(
                    connection.target_system,
                    connection.target_component,
                    mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS,
                    self.target_rate_hz,
                    1,
                )

                self._msg_count = 0
                self._integrator.reset()
                _connected = True
                self._reset_state()

                while not self._stop_event.is_set():
                    self._pause_event.wait()
                    msg = connection.recv_match(type='RAW_IMU', blocking=True, timeout=1.0)
                    if msg is None:
                        continue
                    self._msg_count += 1
                    self._update_rate()

                    az_mg = float(msg.zacc) - self._gravity_offset
                    if not self._calibrated:
                        az_mg = self._calibrate(float(msg.zacc))

                    az_ms2 = az_mg * MG_TO_MS2
                    t = time.time()
                    vz_raw = self._integrator.update(az_ms2, t)
                    self._vz_smooth = (VEL_SMOOTH_ALPHA * self._vz_smooth) + ((1 - VEL_SMOOTH_ALPHA) * vz_raw)

                    with _lock:
                        _az_ms2_history.append(az_ms2)
                        _vz_raw_mms_history.append(vz_raw)
                        _vz_mms_history.append(self._vz_smooth)
                        _ts_history.append(t)

                    with _log_lock:
                        if _log_active:
                            global _log_counter
                            _log_counter += 1
                            _log_buffer.append({
                                'counter': _log_counter,
                                'unix_time': t,
                                'iso_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t)),
                                'az_ms2': az_ms2,
                                'vz_mms': self._vz_smooth,
                            })

                    is_red    = self._vz_smooth >= THRESH_YELLOW
                    is_yellow = THRESH_GREEN <= self._vz_smooth < THRESH_YELLOW
                    is_green  = self._vz_smooth < THRESH_GREEN
                    _set_gpio_lights(is_red, is_yellow, is_green)

            except Exception as exc:
                print(f'\n[MAVLink] Error: {exc}')
            finally:
                _connected = False
                print('\n[MAVLink] Reader stopped. Reconnecting in 2s...')
                if self._stop_event.is_set():
                    break
                time.sleep(2.0)

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
    with _lock:
        az_ms2 = list(_az_ms2_history)
        vz_raw = list(_vz_raw_mms_history)
        vz_mms = list(_vz_mms_history)
        ts = list(_ts_history)
    rel = [stamp - ts[0] for stamp in ts] if ts else []
    return {
        'az_ms2': az_ms2,
        'vz': vz_mms,
        'vz_raw': vz_raw,
        'ts': ts,
        'rel_s': rel,
    }


def get_latest_sample() -> dict:
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


def get_latest_log_path():
    if not os.path.isdir(LOG_DIR):
        return None
    files = [os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.lower().endswith('.csv')]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def get_all_log_paths():
    if not os.path.isdir(LOG_DIR):
        return []
    files = [os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.lower().endswith('.csv')]
    files.sort(key=os.path.getmtime, reverse=True)
    return files
