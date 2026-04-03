"""Witmotion serial reader implementation."""

import os
import sys
import threading
import time

import serial
from serial import SerialException

from data_collect import state

sys.path.insert(0, os.path.join(state.BASE_DIR, 'vb01_python_sdk'))
from vb01_python_sdk.device_model import DeviceModel

_crc_helper = DeviceModel.__new__(DeviceModel)


def _build_read_request(addr: int, reg: int, count: int) -> bytes:
    frame = [addr, 0x03, reg >> 8, reg & 0xFF, count >> 8, count & 0xFF, 0x00, 0x00]
    crc = _crc_helper.get_crc(frame, 6)
    frame[6] = (crc >> 8) & 0xFF
    frame[7] = crc & 0xFF
    return bytes(frame)


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


def _parse_read_response(buffer: bytes, addr: int, start_reg: int, register_count: int) -> dict:
    frame = _extract_valid_frame(buffer, addr, 0x03, 2 * register_count)
    if not frame:
        return {}

    registers = {}
    for offset in range(register_count):
        raw_value = (frame[3 + 2 * offset] << 8) | frame[4 + 2 * offset]
        registers[start_reg + offset] = raw_value
    return registers


def _decode_signed_u16(raw_value: int) -> int:
    return raw_value - 65536 if raw_value >= 32768 else raw_value


def _decode_vz_mm_s(raw_value: int) -> float:
    return float(_decode_signed_u16(raw_value)) / 100.0


def _decode_hz(raw_value: int) -> float:
    return float(_decode_signed_u16(raw_value)) / 10.0


class WitmotionReader(threading.Thread):
    """Polls Witmotion over Modbus and updates VZ/HZZ shared state."""

    def __init__(
        self,
        port: str = state.WITMOTION_PORT,
        baud_candidates=None,
        modbus_addr: int = state.WITMOTION_MODBUS_ADDR,
        sample_rate_hz: int = state.WITMOTION_SENSOR_RATE_HZ,
    ):
        super().__init__(daemon=True, name='WitmotionReader')
        self.port = port
        self.baud_candidates = list(baud_candidates or state.WITMOTION_BAUD_CANDIDATES)
        self.modbus_addr = int(modbus_addr)
        self.sample_rate_hz = int(sample_rate_hz)
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._current_baud = None

    def _send_and_read_exact(self, ser: serial.Serial, request: bytes, expected_len: int) -> bytes:
        ser.reset_input_buffer()
        ser.write(request)
        ser.flush()
        return ser.read(expected_len)

    def _probe_device(self, ser: serial.Serial) -> bool:
        request = _build_read_request(self.modbus_addr, state.WITMOTION_FAST_START_REG, state.WITMOTION_FAST_REG_COUNT)
        response = self._send_and_read_exact(ser, request, 5 + 2 * state.WITMOTION_FAST_REG_COUNT)
        registers = _parse_read_response(response, self.modbus_addr, state.WITMOTION_FAST_START_REG, state.WITMOTION_FAST_REG_COUNT)
        return state.REG_VZ in registers

    def _open_serial(self) -> serial.Serial:
        last_error = None
        for baud in self.baud_candidates:
            try:
                ser = serial.Serial(
                    self.port,
                    baudrate=baud,
                    timeout=state.WITMOTION_SERIAL_TIMEOUT,
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
            f'Unable to communicate with Witmotion sensor on {self.port} '
            f'using bauds {self.baud_candidates}'
        )

    def run(self):
        fast_request = _build_read_request(self.modbus_addr, state.WITMOTION_FAST_START_REG, state.WITMOTION_FAST_REG_COUNT)
        fast_resp_len = 5 + 2 * state.WITMOTION_FAST_REG_COUNT
        hzz_request = _build_read_request(self.modbus_addr, state.REG_HZZ, 1)
        hzz_resp_len = 7

        while not self._stop_event.is_set():
            try:
                with self._open_serial() as ser:
                    state._wit_connected = True
                    print(f'[Witmotion] Connected -> {self.port} @ {self._current_baud} baud')
                    cycle = 0

                    while not self._stop_event.is_set():
                        self._pause_event.wait()
                        loop_started_at = time.perf_counter()

                        fast_response = self._send_and_read_exact(ser, fast_request, fast_resp_len)
                        registers = _parse_read_response(
                            fast_response,
                            self.modbus_addr,
                            state.WITMOTION_FAST_START_REG,
                            state.WITMOTION_FAST_REG_COUNT,
                        )
                        if not registers:
                            continue

                        if cycle % 10 == 0:
                            hzz_response = self._send_and_read_exact(ser, hzz_request, hzz_resp_len)
                            hzz_registers = _parse_read_response(hzz_response, self.modbus_addr, state.REG_HZZ, 1)
                            if hzz_registers:
                                state._wit_latest_hzz_hz = _decode_hz(hzz_registers[state.REG_HZZ])

                        ts = time.time()
                        vz_mms = _decode_vz_mm_s(registers[state.REG_VZ])

                        with state._lock:
                            state._wit_latest_vz_mms = vz_mms
                            state._wit_vz_mms_history.append(vz_mms)
                            state._wit_hzz_history.append(state._wit_latest_hzz_hz)

                        cycle += 1
                        elapsed = time.perf_counter() - loop_started_at
                        sleep_time = (1.0 / self.sample_rate_hz) - elapsed
                        if sleep_time > 0:
                            time.sleep(sleep_time)

            except SerialException as exc:
                state._wit_connected = False
                print(f'\n[Witmotion] Serial error: {exc}')
                if self._stop_event.wait(1.0):
                    break
            except Exception as exc:
                state._wit_connected = False
                print(f'\n[Witmotion] Error: {exc}')
                if self._stop_event.wait(1.0):
                    break

        state._wit_connected = False
        print('\n[Witmotion] Reader stopped.')

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()
