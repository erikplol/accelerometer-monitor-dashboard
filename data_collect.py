import argparse
import csv
import os
import sys
import threading
import time
from collections import deque

import numpy as np
import serial
from serial import SerialException

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vb01_python_sdk'))

from vb01_python_sdk.device_model import DeviceModel


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_PORT = 'COM5' if os.name == 'nt' else '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
PORT = os.getenv('WTVB_PORT', DEFAULT_PORT)

# 115200 is strongly preferred for FFT logging. Fall back to 9600 for first-time setup.
BAUD_CANDIDATES = [
	int(part.strip())
	for part in os.getenv('WTVB_BAUD_CANDIDATES', '115200,38400,9600').split(',')
	if part.strip()
]
BAUD = BAUD_CANDIDATES[0]
MODBUS_ADDR = int(os.getenv('WTVB_MODBUS_ADDR', '0x50'), 0)

# Sensor register map
REG_AZ = 0x36
REG_VZ = 0x3C
REG_TEMP = 0x40
REG_HZZ = 0x46
REG_RATE = 0x65
REG_UNLOCK = 0x69

# Read AZ..VZ in one request so FFT can use Z acceleration while time graph uses VZ.
FAST_START_REG = REG_AZ
FAST_REG_COUNT = 7

SENSOR_RATE_HZ = int(float(os.getenv('WTVB_SENSOR_RATE_HZ', '150')))
SAMPLING_RATE = float(SENSOR_RATE_HZ)
MAX_TIME_PTS = int(max(600, 60 * SAMPLING_RATE))
SERIAL_TIMEOUT = float(os.getenv('WTVB_SERIAL_TIMEOUT', '0.15'))
AUTO_CONFIGURE_SENSOR_RATE = os.getenv('WTVB_SET_SENSOR_RATE', '0').lower() in {'1', 'true', 'yes', 'on'}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')


# ---------------------------------------------------------------------------
# Shared data buffers
# ---------------------------------------------------------------------------
_vz_history = deque(maxlen=MAX_TIME_PTS)
_raw_vz_history = deque(maxlen=MAX_TIME_PTS)
_raw_az_history = deque(maxlen=MAX_TIME_PTS)
_hzz_history = deque(maxlen=MAX_TIME_PTS)
_ts_history = deque(maxlen=MAX_TIME_PTS)

_lock = threading.Lock()
_connected = False


# ---------------------------------------------------------------------------
# Logging state
# ---------------------------------------------------------------------------
_log_active = False
_log_buffer = []
_log_counter = 0
_log_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Modbus helpers
# ---------------------------------------------------------------------------
_crc_helper = DeviceModel.__new__(DeviceModel)


def _build_read_request(addr: int, reg: int, count: int) -> bytes:
	frame = [addr, 0x03, reg >> 8, reg & 0xFF, count >> 8, count & 0xFF, 0x00, 0x00]
	crc = _crc_helper.get_crc(frame, 6)
	frame[6] = (crc >> 8) & 0xFF
	frame[7] = crc & 0xFF
	return bytes(frame)


def _build_write_request(addr: int, reg: int, value: int) -> bytes:
	frame = [addr, 0x06, reg >> 8, reg & 0xFF, (value >> 8) & 0xFF, value & 0xFF, 0x00, 0x00]
	crc = _crc_helper.get_crc(frame, 6)
	frame[6] = (crc >> 8) & 0xFF
	frame[7] = crc & 0xFF
	return bytes(frame)


def decode_vz_mm_s(raw_value: int) -> float:
	return float(raw_value) / 100.0


def decode_frequency_hz(raw_value: int) -> float:
	return raw_value / 10.0


def _extract_valid_frame(buffer: bytes, addr: int, function_code: int, byte_count: int) -> bytes:
	expected_len = byte_count + 5
	for index in range(0, max(0, len(buffer) - expected_len + 1)):
		if buffer[index] != addr:
			continue
		if buffer[index + 1] != function_code:
			continue
		if buffer[index + 2] != byte_count:
			continue
		candidate = buffer[index:index + expected_len]
		crc_calc = _crc_helper.get_crc(list(candidate), len(candidate) - 2)
		crc_recv = (candidate[-2] << 8) | candidate[-1]
		if crc_calc == crc_recv:
			return candidate
	return b''


def _parse_read_response(buffer: bytes, start_reg: int, register_count: int) -> dict:
	frame = _extract_valid_frame(buffer, MODBUS_ADDR, 0x03, 2 * register_count)
	if not frame:
		return {}

	registers = {}
	for offset in range(register_count):
		raw_value = (frame[3 + 2 * offset] << 8) | frame[4 + 2 * offset]
		registers[start_reg + offset] = raw_value
	return registers


def _send_and_read_exact(ser: serial.Serial, request: bytes, expected_len: int) -> bytes:
	ser.reset_input_buffer()
	ser.write(request)
	ser.flush()
	return ser.read(expected_len)


def write_register(ser: serial.Serial, reg_addr: int, value: int) -> bool:
	request = _build_write_request(MODBUS_ADDR, reg_addr, value)
	response = _send_and_read_exact(ser, request, 8)
	return response == request


def set_sensor_sampling_rate(ser: serial.Serial, sample_rate_hz: int) -> bool:
	if not write_register(ser, REG_UNLOCK, 0xB588):
		return False
	time.sleep(0.05)
	if not write_register(ser, REG_RATE, int(sample_rate_hz)):
		return False
	time.sleep(0.05)
	return write_register(ser, 0x00, 0x0000)


# ---------------------------------------------------------------------------
# Background serial reader
# ---------------------------------------------------------------------------
class SerialReader(threading.Thread):
	def __init__(
		self,
		port: str = PORT,
		baud_candidates=None,
		sample_rate_hz: int = SENSOR_RATE_HZ,
		auto_configure_sensor_rate: bool = AUTO_CONFIGURE_SENSOR_RATE,
	):
		super().__init__(daemon=True, name='SerialReader')
		self.port = port
		self.baud_candidates = list(baud_candidates or BAUD_CANDIDATES)
		self.sample_rate_hz = int(sample_rate_hz)
		self.auto_configure_sensor_rate = auto_configure_sensor_rate
		self._stop_event = threading.Event()
		self._pause_event = threading.Event()
		self._pause_event.set()
		self._last_hzz_hz = 0.0
		self._current_baud = None

	def _probe_device(self, ser: serial.Serial) -> bool:
		request = _build_read_request(MODBUS_ADDR, FAST_START_REG, FAST_REG_COUNT)
		response = _send_and_read_exact(ser, request, 5 + 2 * FAST_REG_COUNT)
		registers = _parse_read_response(response, FAST_START_REG, FAST_REG_COUNT)
		return REG_VZ in registers

	def _open_serial(self) -> serial.Serial:
		last_error = None
		for baud in self.baud_candidates:
			try:
				ser = serial.Serial(
					self.port,
					baudrate=baud,
					timeout=SERIAL_TIMEOUT,
					bytesize=8,
					parity='N',
					stopbits=1,
				)
				if self._probe_device(ser):
					self._current_baud = baud
					return ser
				ser.close()
			except SerialException as exc:
				last_error = exc
		raise last_error or SerialException(
			f'Unable to communicate with sensor on {self.port} using bauds {self.baud_candidates}'
		)

	def run(self):
		global _connected, _log_active, _log_buffer, _log_counter

		fast_request = _build_read_request(MODBUS_ADDR, FAST_START_REG, FAST_REG_COUNT)
		fast_resp_len = 5 + 2 * FAST_REG_COUNT

		hzz_request = _build_read_request(MODBUS_ADDR, REG_HZZ, 1)
		hzz_resp_len = 7

		try:
			with self._open_serial() as ser:
				if self.auto_configure_sensor_rate:
					ok = set_sensor_sampling_rate(ser, self.sample_rate_hz)
					status = 'OK' if ok else 'FAILED'
					print(f'[SerialReader] Set sensor rate to {self.sample_rate_hz} Hz: {status}')

				_connected = True
				print(f'[SerialReader] Connected -> {self.port} @ {self._current_baud} baud')

				cycle = 0
				while not self._stop_event.is_set():
					self._pause_event.wait()
					loop_started_at = time.perf_counter()

					fast_response = _send_and_read_exact(ser, fast_request, fast_resp_len)
					registers = _parse_read_response(fast_response, FAST_START_REG, FAST_REG_COUNT)
					if not registers:
						continue

					if cycle % 10 == 0:
						hzz_response = _send_and_read_exact(ser, hzz_request, hzz_resp_len)
						hzz_registers = _parse_read_response(hzz_response, REG_HZZ, 1)
						if hzz_registers:
							self._last_hzz_hz = decode_frequency_hz(hzz_registers[REG_HZZ])

					ts = time.time()
					raw_az = registers[REG_AZ]
					raw_vz = registers[REG_VZ]
					vz_mm_s = decode_vz_mm_s(raw_vz)
					hzz_hz = self._last_hzz_hz

					with _lock:
						_raw_az_history.append(raw_az)
						_raw_vz_history.append(raw_vz)
						_vz_history.append(vz_mm_s)
						_hzz_history.append(hzz_hz)
						_ts_history.append(ts)

					with _log_lock:
						if _log_active:
							_log_counter += 1
							_log_buffer.append({
								'counter': _log_counter,
								'unix_time': ts,
								'iso_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)),
								'raw_az_u16': raw_az,
								'raw_vz_u16': raw_vz,
								'vz_mm_s': vz_mm_s,
								'hzz_hz': hzz_hz,
							})

					print(
						f'vz={vz_mm_s:8.3f} mm/s  hzz={hzz_hz:6.1f} Hz',
						end='\r',
					)

					cycle += 1
					elapsed = time.perf_counter() - loop_started_at
					sleep_time = (1.0 / self.sample_rate_hz) - elapsed
					if sleep_time > 0:
						time.sleep(sleep_time)

		except SerialException as exc:
			print(f'\n[SerialReader] Serial error: {exc}')
		finally:
			_connected = False
			print('\n[SerialReader] Disconnected.')

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
		raw_az = list(_raw_az_history)
		raw_vz = list(_raw_vz_history)
		vz = list(_vz_history)
		hzz = list(_hzz_history)
		ts = list(_ts_history)
	rel = [stamp - ts[0] for stamp in ts] if ts else []
	return {'raw_az_u16': raw_az, 'raw_vz_u16': raw_vz, 'vz': vz, 'hzz': hzz, 'ts': ts, 'rel_s': rel}


def get_latest_sample() -> dict:
	with _lock:
		if not _ts_history:
			return {}
		return {
			'vz_mm_s': _vz_history[-1],
			'hzz_hz': _hzz_history[-1],
			'unix_time': _ts_history[-1],
		}


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

	vz_vals = [row['vz_mm_s'] for row in data] if data else []
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
		writer.writerow(['# Engine Vibration Log'])
		writer.writerow(['# RPM', rpm])
		writer.writerow(['# Load (W)', load_w])
		writer.writerow(['# Timestamp', timestamp])
		writer.writerow(['# Samples', len(data)])
		writer.writerow(['# Sensor Rate (Hz)', SENSOR_RATE_HZ])
		writer.writerow(['# Effective Poll Rate (Hz)', f'{effective_rate_hz:.6f}'])
		writer.writerow(['# VZ RMS (mm/s)', f'{vz_rms:.6f}'])
		writer.writerow([])
		writer.writerow([
			'counter',
			'unix_time',
			'iso_time',
			'vz_mm_s',
			'hzz_hz',
			'raw_vz_u16',
		])
		for row in data:
			writer.writerow([
				row['counter'],
				f"{row['unix_time']:.6f}",
				row['iso_time'],
				f"{row['vz_mm_s']:.6f}",
				f"{row['hzz_hz']:.6f}",
				row['raw_vz_u16'],
			])

	print(
		f'[Logger] Saved -> {filepath} '
		f'({len(data)} samples, VZ RMS={vz_rms:.4f} mm/s)'
	)
	return filepath


# ---------------------------------------------------------------------------
# Stand-alone mode
# ---------------------------------------------------------------------------
def main():
	global SENSOR_RATE_HZ, SAMPLING_RATE

	parser = argparse.ArgumentParser(
		description='Read WTVB02/WTVB01 sensor data using unsigned VZ only for FFT logging.'
	)
	parser.add_argument('--port', default=PORT, help='Serial port, for example COM6')
	parser.add_argument(
		'--baud',
		type=int,
		nargs='*',
		default=BAUD_CANDIDATES,
		help='Baud candidates to try, highest first. Example: --baud 115200 9600',
	)
	parser.add_argument('--duration', type=float, default=0.0, help='Run duration in seconds. 0 means forever.')
	parser.add_argument('--rpm', type=int, default=0, help='RPM metadata for saved logs')
	parser.add_argument('--load', type=int, default=0, help='Load metadata in watts for saved logs')
	parser.add_argument('--log', action='store_true', help='Save a CSV log when the run finishes')
	parser.add_argument(
		'--set-rate',
		type=int,
		default=None,
		help='Write the sensor sampling-rate register before streaming. Example: --set-rate 150',
	)
	args = parser.parse_args()

	if args.set_rate is not None:
		SENSOR_RATE_HZ = int(args.set_rate)
		SAMPLING_RATE = float(args.set_rate)

	reader = SerialReader(
		port=args.port,
		baud_candidates=args.baud,
		sample_rate_hz=args.set_rate or SENSOR_RATE_HZ,
		auto_configure_sensor_rate=args.set_rate is not None,
	)

	if args.log:
		start_logging()

	reader.start()
	started_at = time.time()
	print('Collecting sensor data. Press Ctrl+C to stop.')

	try:
		while True:
			if args.duration > 0 and (time.time() - started_at) >= args.duration:
				break
			latest = get_latest_sample()
			if latest:
				print(
					f"latest -> vz={latest['vz_mm_s']:+.3f} mm/s, "
					f"hzz={latest['hzz_hz']:.1f} Hz"
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
