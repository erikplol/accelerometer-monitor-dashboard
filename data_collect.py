import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vb01_python_sdk'))

import csv
import time
import threading
from collections import deque

import numpy as np
import serial
from serial import SerialException

from vb01_python_sdk.device_model import DeviceModel  # CRC helper only

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT          = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
BAUD          = 9600
MODBUS_ADDR   = 0x50

MAX_TIME_PTS  = 600     # 60-second rolling window @ 10 Hz
SAMPLING_RATE = 10.0    # Hz

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# ---------------------------------------------------------------------------
# Shared data buffers  (written by SerialReader thread, read by Dash callbacks)
# ---------------------------------------------------------------------------
vz_history  = deque(maxlen=MAX_TIME_PTS)
hzz_history = deque(maxlen=MAX_TIME_PTS)
ts_history  = deque(maxlen=MAX_TIME_PTS)

_lock      = threading.Lock()
_connected = False

# ---------------------------------------------------------------------------
# Logging state
# ---------------------------------------------------------------------------
_log_active  = False
_log_buffer  = []        # list of (counter, time_str, vz_mm_s)
_log_counter = 0
_log_lock    = threading.Lock()

# ---------------------------------------------------------------------------
# Modbus RTU helpers
# ---------------------------------------------------------------------------
_crc_helper = DeviceModel.__new__(DeviceModel)


def _build_read_request(addr: int, reg: int, count: int) -> bytes:
    """Build a Modbus RTU Read-Holding-Registers (0x03) request frame."""
    frame = [addr, 0x03, reg >> 8, reg & 0xFF, count >> 8, count & 0xFF, 0x00, 0x00]
    crc = _crc_helper.get_crc(frame, 6)
    frame[6] = crc >> 8
    frame[7] = crc & 0xFF
    return bytes(frame)


def _parse_read_response(buf: bytes, start_reg: int, n_regs: int) -> dict:
    """
    Parse a Modbus RTU 0x03 response.
    Returns dict of {register_address: raw_unsigned_16bit_value}.
    Returns empty dict on CRC error or length mismatch.
    """
    expected = 5 + 2 * n_regs
    if len(buf) < expected:
        return {}
    crc_calc = _crc_helper.get_crc(list(buf), len(buf) - 2)
    crc_recv = (buf[-2] << 8) | buf[-1]
    if crc_calc != crc_recv:
        return {}
    result = {}
    for i in range(n_regs):
        value = (buf[3 + 2 * i] << 8) | buf[4 + 2 * i]
        result[start_reg + i] = value
    return result


# ---------------------------------------------------------------------------
# Background serial reader thread
# ---------------------------------------------------------------------------

class SerialReader(threading.Thread):
    """
    Polls the WTVB02-485 via synchronous Modbus RTU.
    Reads registers 0x3C–0x46 in one request:
      0x3C  VZ  — Z vibration velocity (mm/s, signed 16-bit, ÷10000)
      0x44  HZX — X vibration frequency (Hz, unsigned, ÷10)
      0x45  HZY — Y vibration frequency (Hz, unsigned, ÷10)
      0x46  HZZ — Z vibration frequency (Hz, unsigned, ÷10)
    """

    START_REG = 0x3C
    N_REGS    = 11      # 0x3C … 0x46 inclusive
    REG_VZ    = 0x3C
    REG_HZZ   = 0x46

    def __init__(self, port: str = PORT, baud: int = BAUD):
        super().__init__(daemon=True, name="SerialReader")
        self.port        = port
        self.baud        = baud
        self._stop_event = threading.Event()

    def run(self):
        global _connected, _log_active, _log_buffer, _log_counter
        request  = _build_read_request(MODBUS_ADDR, self.START_REG, self.N_REGS)
        resp_len = 5 + 2 * self.N_REGS   # 7 bytes for 1 register

        try:
            with serial.Serial(self.port, BAUD, timeout=1.0) as ser:
                _connected = True
                print(f"[SerialReader] Connected → {self.port}  @  {BAUD} baud")

                while not self._stop_event.is_set():
                    t0 = time.time()

                    ser.reset_input_buffer()
                    ser.write(request)
                    buf = ser.read(resp_len)

                    if len(buf) < resp_len:
                        print(f"[SerialReader] Short read: got {len(buf)}/{resp_len} bytes")
                        continue

                    regs = _parse_read_response(buf, self.START_REG, self.N_REGS)
                    if not regs:
                        print("[SerialReader] CRC error or bad response, retrying…")
                        continue

                    ts     = time.time()
                    raw_vz = regs.get(self.REG_VZ, 0)
                    if raw_vz > 32767:
                        raw_vz -= 65536
                    vz_mm_s = float(raw_vz) / 10000.0

                    # Vibration frequency Z-axis (unsigned, unit = 0.1 Hz → divide by 10)
                    hzz = float(regs.get(self.REG_HZZ, 0)) / 10.0

                    with _lock:
                        vz_history.append(vz_mm_s)
                        hzz_history.append(hzz)
                        ts_history.append(ts)

                    with _log_lock:
                        if _log_active:
                            _log_counter += 1
                            time_str = time.strftime('%d %b %Y  %H:%M:%S', time.localtime(ts))
                            _log_buffer.append(
                                (_log_counter, time_str, vz_mm_s)
                            )

                    print(f"vz={vz_mm_s:+8.4f} mm/s  hzz={hzz:6.1f} Hz      ", end='\r')

                    elapsed    = time.time() - t0
                    sleep_time = (1.0 / SAMPLING_RATE) - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)

        except SerialException as exc:
            print(f"\n[SerialReader] Serial error: {exc}")
        finally:
            _connected = False
            print("\n[SerialReader] Disconnected.")

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_histories() -> dict:
    """Return thread-safe snapshots of history deques.

    Returns
    -------
    dict with keys:
      'vz'   : list[float]  – VZ mm/s
      'hzz'  : list[float]  – Z-axis vibration frequency (Hz)
      'ts'   : list[float]  – Unix timestamps (seconds)
      'rel_s': list[float]  – seconds since first sample (x-axis)
    """
    with _lock:
        vz  = list(vz_history)
        hzz = list(hzz_history)
        ts  = list(ts_history)
    rel = [t - ts[0] for t in ts] if ts else []
    return {'vz': vz, 'hzz': hzz, 'ts': ts, 'rel_s': rel}


def is_connected() -> bool:
    """Return True when the serial reader is actively connected."""
    return _connected


def start_logging() -> None:
    """Begin buffering VZ samples for a log session."""
    global _log_active, _log_buffer, _log_counter
    with _log_lock:
        _log_active  = True
        _log_buffer  = []
        _log_counter = 0


def stop_logging() -> list:
    """Stop buffering and return the collected samples."""
    global _log_active
    with _log_lock:
        _log_active = False
        data        = list(_log_buffer)
    return data


def save_log(rpm: int, load_w: int, data: list) -> str:
    """
    Write *data* to a timestamped CSV in LOG_DIR.

    File name: vibration_RPM{rpm}_LOAD{load_w}W_{YYYYmmdd_HHMMSS}.csv

    File content:
      - Metadata header rows (RPM, Load, Timestamp, Samples, RMS)
      - Data rows: counter, time, vz_mm_s

    Returns the absolute path of the saved file.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename  = f'vibration_RPM{rpm}_LOAD{load_w}W_{timestamp}.csv'
    filepath  = os.path.join(LOG_DIR, filename)

    vz_vals = [row[3] for row in data] if data else []
    rms     = float(np.sqrt(np.mean(np.array(vz_vals) ** 2))) if vz_vals else 0.0

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['# Engine Vibration Log'])
        writer.writerow(['# RPM',        rpm])
        writer.writerow(['# Load (W)',   load_w])
        writer.writerow(['# Timestamp',  timestamp])
        writer.writerow(['# Samples',    len(data)])
        writer.writerow(['# RMS (mm/s)', f'{rms:.6f}'])
        writer.writerow([])
        writer.writerow(['counter', 'time', 'vz_mm_s'])
        for entry in data:
            writer.writerow([entry[0], entry[1], f'{entry[2]:.6f}'])

    print(f"[Logger] Saved → {filepath}  ({len(data)} samples, RMS={rms:.4f} mm/s)")
    return filepath


# ---------------------------------------------------------------------------
# Stand-alone mode
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    reader = SerialReader()
    reader.start()
    print("Collecting… (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    reader.stop()
    reader.join(timeout=2)
