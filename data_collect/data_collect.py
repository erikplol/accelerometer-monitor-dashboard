"""Public API for dual-sensor data collection.

This module keeps a stable import surface while delegating implementation to:
- data_collect.pixhawk (MAVLink/Pixhawk reader)
- data_collect.witmotion (Modbus/Witmotion reader)
"""

import argparse
import csv
import os
import time

import numpy as np

from data_collect import state
from data_collect.pixhawk import MAVLinkReader
from data_collect.witmotion import WitmotionReader

# Re-export commonly used config constants for app imports.
SAMPLING_RATE = state.SAMPLING_RATE
THRESH_GREEN = state.THRESH_GREEN
THRESH_YELLOW = state.THRESH_YELLOW
TARGET_IMU_RATE_HZ = state.TARGET_IMU_RATE_HZ
PORT = state.PORT
BAUD = state.BAUD
LOG_DIR = state.LOG_DIR


def get_histories() -> dict:
    """Get all history buffers for plotting/FFT."""
    with state._lock:
        az_ms2 = list(state._az_ms2_history)
        vz_mms = list(state._wit_vz_mms_history)
        hzz_hz = list(state._wit_hzz_history)
        wit_ts = list(state._wit_ts_history)
        ts = list(state._ts_history)
    rel = [stamp - ts[0] for stamp in ts] if ts else []
    wit_rel = [stamp - wit_ts[0] for stamp in wit_ts] if wit_ts else []
    return {
        'az_ms2': az_ms2,
        'vz_mms': vz_mms,
        'hzz_hz': hzz_hz,
        'wit_ts': wit_ts,
        'wit_rel_s': wit_rel,
        'ts': ts,
        'rel_s': rel,
    }


def get_latest_sample() -> dict:
    """Get the most recent combined sample."""
    with state._lock:
        if not state._ts_history:
            return {}
        return {
            'az_ms2': state._az_ms2_history[-1],
            'vz_mms': state._wit_latest_vz_mms,
            'hzz_hz': state._wit_latest_hzz_hz,
            'unix_time': state._ts_history[-1],
            'rate_hz': state._actual_rate_hz,
        }


def get_actual_rate() -> float:
    return state._actual_rate_hz


def is_connected() -> bool:
    return state._connected


def is_witmotion_connected() -> bool:
    return state._wit_connected


def start_logging() -> None:
    """Start capturing combined AZ/VZ samples into an in-memory log buffer."""
    with state._log_lock:
        state._log_active = True
        state._log_buffer = []
        state._log_counter = 0


def stop_logging() -> list:
    """Stop logging and return a snapshot of buffered rows."""
    with state._log_lock:
        state._log_active = False
        data = list(state._log_buffer)
    return data


def save_log(rpm: int, load_w: int, data: list) -> str:
    """Save combined Pixhawk/Witmotion rows to a CSV file."""
    os.makedirs(state.LOG_DIR, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename = f'vibration_RPM{rpm}_LOAD{load_w}W_{timestamp}.csv'
    filepath = os.path.join(state.LOG_DIR, filename)

    az_vals = [row['az_ms2'] for row in data] if data else []
    vz_vals = [row['vz_mms'] for row in data] if data else []
    timestamps = [row['unix_time'] for row in data] if data else []
    az_rms = float(np.sqrt(np.mean(np.square(az_vals)))) if az_vals else 0.0
    vz_rms = float(np.sqrt(np.mean(np.square(vz_vals)))) if vz_vals else 0.0

    effective_rate_hz = state.SAMPLING_RATE
    if len(timestamps) >= 2:
        intervals = np.diff(np.array(timestamps, dtype=float))
        median_dt = float(np.median(intervals)) if len(intervals) else 0.0
        if median_dt > 0:
            effective_rate_hz = 1.0 / median_dt

    with open(filepath, 'w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['# Engine Vibration Log (Pixhawk AZ + Witmotion VZ)'])
        writer.writerow(['# RPM', rpm])
        writer.writerow(['# Load (W)', load_w])
        writer.writerow(['# Timestamp', timestamp])
        writer.writerow(['# Samples', len(data)])
        writer.writerow(['# Target Rate (Hz)', state.TARGET_IMU_RATE_HZ])
        writer.writerow(['# Effective Rate (Hz)', f'{effective_rate_hz:.6f}'])
        writer.writerow(['# AZ RMS (m/s²)', f'{az_rms:.6f}'])
        writer.writerow(['# VZ RMS (mm/s)', f'{vz_rms:.6f}'])
        writer.writerow([])
        writer.writerow([
            'counter',
            'unix_time',
            'iso_time',
            'az_ms2',
            'vz_mms',
            'hzz_hz',
        ])
        for row in data:
            writer.writerow([
                row['counter'],
                f"{row['unix_time']:.6f}",
                row['iso_time'],
                f"{row['az_ms2']:.6f}",
                f"{row['vz_mms']:.6f}",
                f"{row.get('hzz_hz', 0.0):.6f}",
            ])

    print(
        f'[Logger] Saved -> {filepath} '
        f'({len(data)} samples, AZ RMS={az_rms:.4f} m/s², VZ RMS={vz_rms:.4f} mm/s, '
        f'rate={effective_rate_hz:.1f} Hz)'
    )
    return filepath


def main():
    parser = argparse.ArgumentParser(
        description='Read IMU data via MAVLink for vibration FFT analysis.'
    )
    parser.add_argument('--port', default=state.PORT, help='Serial port')
    parser.add_argument('--baud', type=int, default=state.BAUD, help='Baudrate')
    parser.add_argument('--rate', type=int, default=state.TARGET_IMU_RATE_HZ, help='Target IMU rate Hz')
    parser.add_argument('--duration', type=float, default=0.0, help='Run duration (0=forever)')
    parser.add_argument('--rpm', type=int, default=0, help='RPM metadata')
    parser.add_argument('--load', type=int, default=0, help='Load in watts')
    parser.add_argument('--log', action='store_true', help='Save CSV log')
    args = parser.parse_args()

    mav_reader = MAVLinkReader(
        port=args.port,
        baud=args.baud,
        target_rate_hz=args.rate,
    )
    wit_reader = WitmotionReader()

    if args.log:
        start_logging()

    mav_reader.start()
    wit_reader.start()
    started_at = time.time()
    print('Collecting data (Pixhawk AZ + Witmotion VZ). Press Ctrl+C to stop.')

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
        mav_reader.stop()
        wit_reader.stop()
        mav_reader.join(timeout=2.0)
        wit_reader.join(timeout=2.0)

    if args.log:
        data = stop_logging()
        save_log(args.rpm, args.load, data)


if __name__ == '__main__':
    main()
