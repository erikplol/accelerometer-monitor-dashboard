"""
set_baud.py — Configure WTVB02-485 baud rate to 115200.

Usage:
  python set_baud.py            # change baud to 115200 (device must be at 9600)
  python set_baud.py --scan     # only scan and report current baud, no changes
  python set_baud.py --port /dev/ttyUSB1   # override port

Baud register table (reg 0x04) — confirmed by reading reg while device at 9600:
  value 0 → 2400     value 3 → 19200    value 6 → 115200
  value 1 → 4800     value 4 → 38400    value 7 → 230400
  value 2 → 9600     value 5 → 57600
  (reg reads 2 when device at 9600 → table starts at 2400)
"""

import argparse
import sys
import time

import serial

PORT        = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
BAUD_SRC    = 9600     # device default / current baud
BAUD_TARGET = 115200   # desired baud

# All Modbus frames — CRCs verified via vb01_python_sdk
#   UNLOCK:   write reg 0x69 = 0xB588 (enter config mode for ~10 seconds)
#   SET_BAUD: write reg 0x04 = 0x0006 (6 = 115200 per WTVB02 baud table)
#   SAVE:     write reg 0x00 = 0x0000 (save registers to flash)
#   REBOOT:   write reg 0x00 = 0x00FF (reboot device)
UNLOCK   = bytes([0x50, 0x06, 0x00, 0x69, 0xB5, 0x88, 0x22, 0xA1])
SET_BAUD = bytes([0x50, 0x06, 0x00, 0x04, 0x00, 0x06, 0x45, 0x88])
SAVE     = bytes([0x50, 0x06, 0x00, 0x00, 0x00, 0x00, 0x84, 0x4B])
REBOOT   = bytes([0x50, 0x06, 0x00, 0x00, 0x00, 0xFF, 0xC4, 0x0B])
# Read VZ (reg 0x3C, 1 reg) — used as comms health check
READ_VZ  = bytes([0x50, 0x03, 0x00, 0x3C, 0x00, 0x01, 0x49, 0x87])


def _open(port, baud, timeout=1.0):
    return serial.Serial(port, baud, timeout=timeout,
                         bytesize=8, parity='N', stopbits=1)


def _crc16(data: bytes) -> int:
    """CRC-16/Modbus."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _drain(ser, silence_ms=100):
    """Read and discard bytes until no data for silence_ms milliseconds."""
    ser.timeout = silence_ms / 1000.0
    while ser.read(256):
        pass
    ser.timeout = 1.0


def _is_valid_vz_response(data: bytes) -> bool:
    """
    Return True only if data contains a CRC-validated 0x50 0x03 read response.
    Searches for the 0x50 0x03 0x02 header anywhere in data to handle any
    leading noise/echo bytes.

    Modbus RTU CRC byte order: CRC_LO is sent first, CRC_HI second.
    _crc16 returns (hi << 8 | lo), so received CRC must be read as
    (data[i+6] << 8) | data[i+5]  (lo-hi swap).
    """
    for i in range(len(data) - 6):
        if data[i] == 0x50 and data[i+1] == 0x03 and data[i+2] == 0x02:
            resp = data[i:i+7]
            if len(resp) == 7:
                crc_calc = _crc16(resp[:5])
                crc_recv = (resp[6] << 8) | resp[5]   # lo-byte first in frame
                if crc_calc == crc_recv:
                    return True
    return False


def scan(port):
    """
    Probe all standard baud rates. Returns (found_baud, has_valid_modbus).
    found_baud is the baud with valid Modbus, or the first with any data.
    """
    candidates = [2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400]
    print(f"\n  {'Baud':>8}   {'Bytes':>5}   {'Modbus OK':>10}   First bytes")
    print("  " + "-" * 62)
    found_baud  = None
    modbus_baud = None
    for baud in candidates:
        try:
            with _open(port, baud, timeout=0.4) as ser:
                ser.reset_input_buffer()
                time.sleep(0.02)
                ser.write(READ_VZ)
                data = ser.read(32)
        except serial.SerialException:
            print(f"  {baud:>8}   error")
            continue

        n     = len(data)
        valid = _is_valid_vz_response(data)
        hex_s = data[:16].hex(' ').upper() if data else '(empty)'
        print(f"  {baud:>8}   {n:>5}   {'YES ✓' if valid else ('—' if n == 0 else 'noise'):>10}   {hex_s}")
        if valid and modbus_baud is None:
            modbus_baud = baud
        if n > 0 and found_baud is None:
            found_baud = baud

    best = modbus_baud or found_baud
    if best:
        label = "valid Modbus" if modbus_baud else "noise/echo only"
        print(f"\n  → Device found at {best} baud ({label})")
    else:
        print(f"\n  → No device found")
    return best, (modbus_baud is not None)


def strict_verify(port, baud, attempts=8, delay=0.5):
    """
    Try up to `attempts` times to read a CRC-valid Modbus VZ response at `baud`.
    Bus noise / TX echo returns False — only a genuine device response passes.
    """
    for i in range(1, attempts + 1):
        try:
            with _open(port, baud, timeout=0.5) as ser:
                ser.reset_input_buffer()
                time.sleep(0.05)
                ser.write(READ_VZ)
                data = ser.read(32)
                valid = _is_valid_vz_response(data)
                hex_s = data[:14].hex(' ').upper() if data else '(empty)'
                tag   = '← Modbus OK ✓' if valid else '(noise/no response)'
                print(f"    Attempt {i}/{attempts}: {len(data):2d} bytes  {hex_s}  {tag}")
                if valid:
                    return True
        except serial.SerialException as e:
            print(f"    Attempt {i}/{attempts}: port error — {e}")
        time.sleep(delay)
    return False


def change_baud_9600_to_115200(port):
    """
    Change device from 9600 → 115200 and persist to flash.

    The UNLOCK window is ~10 seconds. Strategy:
    1. Open at 9600, UNLOCK (must echo), SET_BAUD (fire-and-forget, device
       switches to 115200 immediately).
    2. Change the pyserial baudrate in-place to 115200 WITHOUT closing the
       port — the OS keeps the FD open; only the UART divisor register changes.
       This avoids OS port-close/re-open delays.
    3. Drain streaming bytes, then send SAVE within the still-open UNLOCK window.
    4. Send REBOOT and close.
    5. Wait 4s, then verify with strict CRC at 115200.
    """
    unlock_t0 = None

    with _open(port, BAUD_SRC, timeout=1.0) as ser:
        # Step 1: UNLOCK at 9600 — must get exact echo
        print("\n[1] UNLOCK at 9600 baud...")
        ser.reset_input_buffer()
        ser.write(UNLOCK)
        resp = ser.read(len(UNLOCK))
        if resp != UNLOCK:
            got = resp.hex(' ').upper() if resp else '(empty)'
            sys.exit(f"    FAILED — expected {UNLOCK.hex(' ').upper()}, got {got}\n"
                     f"    → Check cable/port, confirm device is at 9600 baud.")
        print("    OK")
        unlock_t0 = time.time()

        # Step 2: SET_BAUD at 9600 — fire and forget
        print("[2] SET_BAUD (value=6 → 115200) at 9600 baud...")
        ser.reset_input_buffer()
        ser.write(SET_BAUD)
        ser.flush()
        # Device has just switched to 115200. Give it ~150ms to complete the switch
        # then change our UART divisor in-place (no port close/reopen).
        time.sleep(0.15)

        # Step 3: Switch our end to 115200 in-place
        print("[3] Switching host to 115200 in-place (no port close)...")
        ser.baudrate = 115200
        time.sleep(0.05)          # let CH340 buffer flush

        # Step 4: SAVE within the UNLOCK window
        elapsed   = time.time() - unlock_t0
        remaining = 10.0 - elapsed
        print(f"[4] SAVE at 115200 (UNLOCK window: {remaining:.1f}s remaining)...")
        _drain(ser, silence_ms=80)
        ser.write(SAVE)
        time.sleep(0.35)

        # Step 5: REBOOT
        _drain(ser, silence_ms=80)
        print("[5] REBOOT...")
        ser.write(REBOOT)
        time.sleep(0.15)
        # Port closes here via context manager

    print("    SAVE and REBOOT sent.")

    # Step 6: Wait for boot and verify
    print("[6] Waiting 4 s for device to boot at 115200...")
    time.sleep(4.0)
    print("[7] Verifying at 115200 (strict CRC validation)...")
    if strict_verify(port, BAUD_TARGET, attempts=8, delay=0.5):
        print(f"\n✔  SUCCESS — device is permanently at {BAUD_TARGET} baud.")
        print(f"   data_collect.py is already set to BAUD={BAUD_TARGET}, SAMPLING_RATE=150.0")
        return True
    else:
        print("\n✘  FAILED — device did not respond at 115200 after reboot.")
        print("   → Power-cycle the sensor and re-run this script.")
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', default=PORT,
                        help='Serial port (default: %(default)s)')
    parser.add_argument('--scan', action='store_true',
                        help='Scan all baud rates without making changes')
    args = parser.parse_args()

    print(f"\nPort: {args.port}")
    print("=" * 62)

    if args.scan:
        scan(args.port)
        return

    # Quick pre-check: already at 115200?
    print(f"\n[0] Pre-check: is device already at {BAUD_TARGET}?")
    if strict_verify(args.port, BAUD_TARGET, attempts=3, delay=0.3):
        print(f"\nDevice is already at {BAUD_TARGET}. Nothing to do.")
        return

    # Confirm device is at 9600
    print(f"\n[0] Confirming device is at {BAUD_SRC}...")
    if not strict_verify(args.port, BAUD_SRC, attempts=3, delay=0.3):
        print(f"\nDevice not found at {BAUD_SRC} either. Running full scan...")
        found, ok = scan(args.port)
        if not found:
            sys.exit("No device found at any baud rate. Check power and cable.")
        if ok and found == BAUD_TARGET:
            print(f"Device already at {BAUD_TARGET}. Nothing to do.")
            return
        sys.exit(f"Device found at {found} baud but this script only handles 9600→115200.\n"
                 f"Manual intervention required.")
    print(f"    Confirmed at {BAUD_SRC}.")

    success = change_baud_9600_to_115200(args.port)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
