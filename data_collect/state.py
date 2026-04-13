"""Shared runtime state and configuration for sensor collectors."""

import os
import re
import threading
from collections import deque

import numpy as np

try:
    from serial.tools import list_ports
except Exception:  # pragma: no cover - defensive fallback when pyserial is unavailable.
    list_ports = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOG_DIR = os.path.join(BASE_DIR, 'logs')

# Serial port config
WINDOWS_DEFAULT_PORT = 'COM5'
WINDOWS_DEFAULT_WITMOTION_PORT = 'COM6'
LINUX_DEFAULT_PORT = '/dev/serial/by-id/usb-Auterion_PX4_FMU_v6X.x_0-if00'
LINUX_DEFAULT_WITMOTION_PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'

DEFAULT_PORT = WINDOWS_DEFAULT_PORT if os.name == 'nt' else LINUX_DEFAULT_PORT
DEFAULT_WITMOTION_PORT = WINDOWS_DEFAULT_WITMOTION_PORT if os.name == 'nt' else LINUX_DEFAULT_WITMOTION_PORT

_PIXHAWK_KEYWORDS = ('pixhawk', 'px4', 'auterion', 'ardupilot', 'mavlink', 'fmu')
_PIXHAWK_VIDS = {0x26AC, 0x2DAE}
_WITMOTION_KEYWORDS = ('witmotion', 'wtvb', 'ch340', 'wch', 'usb-serial', 'usb serial')
_WITMOTION_VIDS = {0x1A86}


def _normalize_port_env(value):
    if value is None:
        return None
    value = value.strip()
    return value or None


def _is_auto_port(value) -> bool:
    if value is None:
        return True
    return value.lower() in {'auto', 'detect'}


def _port_info_blob(port_info) -> str:
    fields = []
    for name in ('device', 'description', 'manufacturer', 'product', 'interface', 'hwid'):
        value = getattr(port_info, name, None)
        if value:
            fields.append(str(value).lower())
    return ' '.join(fields)


def _com_sort_key(port_info):
    device = str(getattr(port_info, 'device', '')).strip()
    match = re.match(r'(?i)^com(\d+)$', device)
    if match:
        return (0, int(match.group(1)))
    return (1, device.lower())


def _list_serial_ports() -> list:
    if list_ports is None:
        return []
    try:
        ports = list(list_ports.comports())
        ports.sort(key=_com_sort_key)
        return ports
    except Exception:
        return []


def _is_witmotion_port(port_info) -> bool:
    vid = getattr(port_info, 'vid', None)
    if vid in _WITMOTION_VIDS:
        return True
    blob = _port_info_blob(port_info)
    return any(keyword in blob for keyword in _WITMOTION_KEYWORDS)


def _is_pixhawk_port(port_info) -> bool:
    vid = getattr(port_info, 'vid', None)
    if vid in _PIXHAWK_VIDS:
        return True
    blob = _port_info_blob(port_info)
    return any(keyword in blob for keyword in _PIXHAWK_KEYWORDS)


def _pick_port(ports: list, matcher, excluded_devices=None):
    excluded = {str(device).lower() for device in (excluded_devices or []) if device}
    for port_info in ports:
        device = str(getattr(port_info, 'device', '')).strip()
        if not device:
            continue
        if device.lower() in excluded:
            continue
        if matcher(port_info):
            return device
    return None


def _resolve_windows_sensor_ports(mavlink_env, witmotion_env):
    mavlink_port = _normalize_port_env(mavlink_env)
    witmotion_port = _normalize_port_env(witmotion_env)

    if _is_auto_port(mavlink_port):
        mavlink_port = None
    if _is_auto_port(witmotion_port):
        witmotion_port = None

    if mavlink_port and witmotion_port:
        return mavlink_port, witmotion_port

    ports = _list_serial_ports()

    if not mavlink_port:
        mavlink_port = (
            _pick_port(
                ports,
                lambda info: _is_pixhawk_port(info) and not _is_witmotion_port(info),
                excluded_devices=[witmotion_port],
            )
            or _pick_port(
                ports,
                lambda info: not _is_witmotion_port(info),
                excluded_devices=[witmotion_port],
            )
            or WINDOWS_DEFAULT_PORT
        )

    if not witmotion_port:
        witmotion_port = (
            _pick_port(ports, _is_witmotion_port, excluded_devices=[mavlink_port])
            or _pick_port(ports, lambda _info: True, excluded_devices=[mavlink_port])
            or WINDOWS_DEFAULT_WITMOTION_PORT
        )

    if mavlink_port.lower() == witmotion_port.lower():
        alternate_mavlink = (
            _pick_port(
                ports,
                lambda info: not _is_witmotion_port(info),
                excluded_devices=[witmotion_port],
            )
            or _pick_port(ports, lambda _info: True, excluded_devices=[witmotion_port])
        )
        if alternate_mavlink:
            mavlink_port = alternate_mavlink

    return mavlink_port, witmotion_port


def _resolve_non_windows_port(env_value, default_value: str) -> str:
    configured = _normalize_port_env(env_value)
    if _is_auto_port(configured):
        return default_value
    return configured or default_value


_mavlink_port_env = os.getenv('MAVLINK_PORT')
_witmotion_port_env = os.getenv('WTVB_PORT')

if os.name == 'nt':
    PORT, WITMOTION_PORT = _resolve_windows_sensor_ports(_mavlink_port_env, _witmotion_port_env)
else:
    PORT = _resolve_non_windows_port(_mavlink_port_env, DEFAULT_PORT)
    WITMOTION_PORT = _resolve_non_windows_port(_witmotion_port_env, DEFAULT_WITMOTION_PORT)

BAUD = int(os.getenv('MAVLINK_BAUD', '1000000'))
TARGET_IMU_RATE_HZ = int(os.getenv('MAVLINK_IMU_RATE_HZ', '400'))
SAMPLING_RATE = float(TARGET_IMU_RATE_HZ)

# Witmotion config
WITMOTION_BAUD_CANDIDATES = [
    int(part.strip())
    for part in os.getenv('WTVB_BAUD_CANDIDATES', '115200,38400,9600').split(',')
    if part.strip()
]
WITMOTION_MODBUS_ADDR = int(os.getenv('WTVB_MODBUS_ADDR', '0x50'), 0)
WITMOTION_SENSOR_RATE_HZ = int(float(os.getenv('WTVB_SENSOR_RATE_HZ', '100')))
WITMOTION_SERIAL_TIMEOUT = float(os.getenv('WTVB_SERIAL_TIMEOUT', '0.15'))
WITMOTION_CALIB_FILE = os.getenv('WTVB_CALIB_FILE', 'calibration/wtb_calib.txt')

# Witmotion register map
REG_AZ = 0x36
REG_VZ = 0x3C
REG_HZZ = 0x46
WITMOTION_FAST_START_REG = REG_AZ
WITMOTION_FAST_REG_COUNT = 7

MAX_TIME_PTS = int(max(600, 60 * SAMPLING_RATE))

CALIB_FILE = os.getenv('MAVLINK_CALIB_FILE', 'calibration/pixhawk_calib.txt')


def _load_witmotion_scale() -> float:
    """Load Witmotion VZ scale from a plain text file (single float value)."""
    try:
        if os.path.exists(WITMOTION_CALIB_FILE):
            with open(WITMOTION_CALIB_FILE, 'r') as handle:
                return float(handle.read().strip())
    except Exception as exc:
        print(f'[Witmotion] Warning: could not load calibration from {WITMOTION_CALIB_FILE}: {exc}')
    return 1.0


WITMOTION_VZ_SCALE = _load_witmotion_scale()

THRESH_GREEN = 2.8
THRESH_YELLOW = 7.1

# ArduPilot RAW_IMU sends mG (milli-G) for xacc/yacc/zacc
MG_TO_MS2 = 9.80665 / 1000.0

# Shared data buffers
_az_ms2_history = deque(maxlen=MAX_TIME_PTS)
_wit_vz_mms_history = deque(maxlen=MAX_TIME_PTS)
_wit_hzz_history = deque(maxlen=MAX_TIME_PTS)
_wit_ts_history = deque(maxlen=MAX_TIME_PTS)
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
