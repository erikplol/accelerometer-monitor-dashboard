"""Shared runtime state and configuration for sensor collectors."""

import os
import threading
from collections import deque

import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pixhawk config
DEFAULT_PORT = 'COM5' if os.name == 'nt' else '/dev/serial/by-id/usb-Auterion_PX4_FMU_v6X.x_0-if00'
PORT = os.getenv('MAVLINK_PORT', DEFAULT_PORT)
BAUD = int(os.getenv('MAVLINK_BAUD', '1000000'))
TARGET_IMU_RATE_HZ = int(os.getenv('MAVLINK_IMU_RATE_HZ', '200'))
SAMPLING_RATE = float(TARGET_IMU_RATE_HZ)

# Witmotion config
DEFAULT_WITMOTION_PORT = 'COM6' if os.name == 'nt' else '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
WITMOTION_PORT = os.getenv('WTVB_PORT', DEFAULT_WITMOTION_PORT)
WITMOTION_BAUD_CANDIDATES = [
    int(part.strip())
    for part in os.getenv('WTVB_BAUD_CANDIDATES', '115200,38400,9600').split(',')
    if part.strip()
]
WITMOTION_MODBUS_ADDR = int(os.getenv('WTVB_MODBUS_ADDR', '0x50'), 0)
WITMOTION_SENSOR_RATE_HZ = int(float(os.getenv('WTVB_SENSOR_RATE_HZ', '100')))
WITMOTION_SERIAL_TIMEOUT = float(os.getenv('WTVB_SERIAL_TIMEOUT', '0.15'))

# Witmotion register map
REG_AZ = 0x36
REG_VZ = 0x3C
REG_HZZ = 0x46
WITMOTION_FAST_START_REG = REG_AZ
WITMOTION_FAST_REG_COUNT = 7

MAX_TIME_PTS = int(max(600, 60 * SAMPLING_RATE))

LOG_DIR = os.path.join(BASE_DIR, 'logs')
CALIB_FILE = os.getenv('MAVLINK_CALIB_FILE', os.path.join(LOG_DIR, 'gravity_calib.json'))

THRESH_GREEN = 2.8
THRESH_YELLOW = 7.1

# ArduPilot RAW_IMU sends mG (milli-G) for xacc/yacc/zacc
MG_TO_MS2 = 9.80665 / 1000.0

# Shared data buffers
_az_ms2_history = deque(maxlen=MAX_TIME_PTS)
_wit_vz_mms_history = deque(maxlen=MAX_TIME_PTS)
_wit_hzz_history = deque(maxlen=MAX_TIME_PTS)
_ts_history = deque(maxlen=MAX_TIME_PTS)

_lock = threading.Lock()
_connected = False
_actual_rate_hz = 0.0
_wit_connected = False
_wit_latest_vz_mms = 0.0
_wit_latest_hzz_hz = 0.0

# Logging state
_log_active = False
_log_buffer = []
_log_counter = 0
_log_lock = threading.Lock()


def get_vz_rms_last_1s() -> float:
    """Return VZ RMS over the latest ~1 second of Witmotion data."""
    n_1s = max(1, int(WITMOTION_SENSOR_RATE_HZ))
    if len(_wit_vz_mms_history) < n_1s:
        return 0.0
    recent_vz = list(_wit_vz_mms_history)[-n_1s:]
    return float(np.sqrt(np.mean(np.array(recent_vz) ** 2)))
