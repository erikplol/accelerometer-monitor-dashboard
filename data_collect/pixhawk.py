"""Pixhawk MAVLink reader implementation."""

import os
import threading
import time
from typing import Any

import numpy as np
from pymavlink import mavutil

from data_collect import state


class MAVLinkReader(threading.Thread):
    """Reads IMU acceleration from Pixhawk and updates shared state."""

    def __init__(
        self,
        port: str = state.PORT,
        baud: int = state.BAUD,
        target_rate_hz: int = state.TARGET_IMU_RATE_HZ,
    ):
        super().__init__(daemon=True, name='MAVLinkReader')
        self.port = port
        self.baud = baud
        self.target_rate_hz = target_rate_hz
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        self._gravity_offset = -980.0
        self._calibrated = True
        self._calibration_samples = []

        fixed_offset = os.getenv('MAVLINK_FIXED_GRAVITY_OFFSET')
        if fixed_offset is not None:
            try:
                self._gravity_offset = float(fixed_offset)
                self._calibrated = True
            except Exception:
                self._calibrated = False
                self._calibration_samples = []
        else:
            try:
                if os.path.exists(state.CALIB_FILE):
                    with open(state.CALIB_FILE, 'r') as fh:
                        self._gravity_offset = float(fh.read().strip())
                    self._calibrated = True
                else:
                    self._calibrated = False
                    self._calibration_samples = []
            except Exception as exc:
                print(f'[MAVLink] Warning: could not load calibration: {exc}')
                self._calibrated = False
                self._calibration_samples = []

        self._msg_count = 0
        self._rate_window_start = None
        self._rate_window_count = 0

    def _connect(self) -> Any:
        print(f'[MAVLink] Connecting to {self.port} @ {self.baud} baud...')

        connection: Any = mavutil.mavlink_connection(
            self.port,
            baud=self.baud,
            autoreconnect=True,
        )

        print('[MAVLink] Waiting for heartbeat...')
        connection.wait_heartbeat(timeout=10)
        print(
            f'[MAVLink] Heartbeat received (system {connection.target_system}, '
            f'component {connection.target_component})'
        )

        return connection

    def _configure_stream_rate(self, connection: Any):
        connection.mav.request_data_stream_send(
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS,
            self.target_rate_hz,
            1,
        )

        interval_us = int(1_000_000 / self.target_rate_hz)
        connection.mav.command_long_send(
            connection.target_system,
            connection.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_HIGHRES_IMU,
            interval_us,
            0,
            0,
            0,
            0,
            0,
        )

        print(f'[MAVLink] Requested HIGHRES_IMU at {self.target_rate_hz} Hz')

    def _calibrate_gravity(self, zacc_mg: float):
        if self._calibrated:
            return

        self._calibration_samples.append(zacc_mg)
        if len(self._calibration_samples) < 50:
            return

        self._gravity_offset = float(np.median(self._calibration_samples))
        self._calibrated = True
        print(f'[MAVLink] Gravity calibrated: offset = {self._gravity_offset:.2f} mG')
        try:
            calib_dir = os.path.dirname(state.CALIB_FILE)
            if calib_dir:
                os.makedirs(calib_dir, exist_ok=True)
            with open(state.CALIB_FILE, 'w') as fh:
                fh.write(f'{self._gravity_offset:.6f}\n')
            print(f'[MAVLink] Saved gravity calibration -> {state.CALIB_FILE}')
        except Exception as exc:
            print(f'[MAVLink] Warning: could not save calibration: {exc}')

    def _update_rate_measurement(self, ts: float):
        if self._rate_window_start is None:
            self._rate_window_start = ts
            self._rate_window_count = 0

        self._rate_window_count += 1
        elapsed = ts - self._rate_window_start

        if elapsed >= 1.0:
            state._actual_rate_hz = self._rate_window_count / elapsed
            self._rate_window_start = ts
            self._rate_window_count = 0

    def _reset_state(self):
        if not self._calibrated:
            self._calibration_samples = []
        self._rate_window_start = None
        self._rate_window_count = 0

    def run(self):
        reconnect_delay = 3

        while not self._stop_event.is_set():
            connection: Any = None
            try:
                connection = self._connect()
                self._configure_stream_rate(connection)
                state._connected = True

                print('[MAVLink] Starting data collection')
                if not self._calibrated:
                    print('[MAVLink] Calibrating gravity offset (keep device still)...')
                else:
                    print(f'[MAVLink] Using fixed gravity offset: {self._gravity_offset:+.1f} mG')

                while not self._stop_event.is_set():
                    self._pause_event.wait()

                    msg = connection.recv_match(type='HIGHRES_IMU', blocking=True, timeout=0.5)
                    if msg is None:
                        continue

                    ts = time.time()
                    self._msg_count += 1

                    msg_type = msg.get_type()

                    if msg_type == 'HIGHRES_IMU':
                        raw_zacc_ms2 = msg.zacc
                        raw_zacc_mg = raw_zacc_ms2 / state.MG_TO_MS2
                        self._calibrate_gravity(raw_zacc_mg)
                        if self._calibrated:
                            az_ms2 = raw_zacc_ms2 - (self._gravity_offset * state.MG_TO_MS2)
                        else:
                            az_ms2 = raw_zacc_ms2 - 9.80665

                    else:
                        continue

                    # Use a monotonic clock for stable 1-second rate windows.
                    self._update_rate_measurement(time.perf_counter())

                    with state._lock:
                        state._az_ms2_history.append(az_ms2)
                        state._ts_history.append(ts)
                        wit_vz_mms = state._wit_latest_vz_mms
                        wit_hzz_hz = state._wit_latest_hzz_hz

                    with state._log_lock:
                        if state._log_active:
                            state._log_counter += 1
                            state._log_buffer.append({
                                'counter': state._log_counter,
                                'unix_time': ts,
                                'iso_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)),
                                'az_ms2': az_ms2,
                                'vz_mms': wit_vz_mms,
                                'hzz_hz': wit_hzz_hz,
                            })

                    if self._msg_count % 25 == 0:
                        print(f'az={az_ms2:+8.4f} m/s²  rate={state._actual_rate_hz:.1f} Hz', end='\r')

            except Exception as exc:
                print(f'\n[MAVLink] Error: {exc}')
                import traceback

                traceback.print_exc()

            finally:
                state._connected = False
                state._actual_rate_hz = 0.0
                if connection:
                    try:
                        connection.close()
                    except Exception:
                        pass

            if not self._stop_event.is_set():
                self._reset_state()
                print(f'[MAVLink] Reconnecting in {reconnect_delay}s...')
                if self._stop_event.wait(reconnect_delay):
                    break

        print('\n[MAVLink] Reader stopped.')

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()
