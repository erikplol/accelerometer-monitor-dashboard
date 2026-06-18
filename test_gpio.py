#!/usr/bin/env python3
"""
test_gpio.py — Standalone GPIO sanity-check for Raspberry Pi 5.

Uses gpiod >= 2.x API (gpiochip4 / pinctrl-rp1).

Pins (BCM):
    GPIO 17 -> Red LED
    GPIO 27 -> Yellow LED
    GPIO 22 -> Green LED

Usage:
    python3 test_gpio.py
"""

import time
import sys

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GPIOCHIP    = "/dev/gpiochip4"
PIN_RED     = 17
PIN_YELLOW  = 27
PIN_GREEN   = 22
BLINK_TIMES = 3
BLINK_DELAY = 0.4
SEQ_DELAY   = 0.6

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def hr(char="─", width=52): print(char * width)
def info(msg): print(f"  [INFO]  {msg}")
def ok(msg):   print(f"  [ OK ]  {msg}")
def warn(msg): print(f"  [WARN]  {msg}")
def err(msg):  print(f"  [ERR ]  {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# gpiod 2.x backend
# ---------------------------------------------------------------------------
def run_gpiod():
    import gpiod
    from gpiod.line import Direction, Value

    hr()
    print(f"  Backend : gpiod {gpiod.version_string()}")
    print(f"  Chip    : {GPIOCHIP}")
    hr()

    PINS = {
        "Red    (GPIO 17)": PIN_RED,
        "Yellow (GPIO 27)": PIN_YELLOW,
        "Green  (GPIO 22)": PIN_GREEN,
    }

    settings = gpiod.LineSettings(
        direction=Direction.OUTPUT,
        output_value=Value.INACTIVE,
    )

    config = {pin: settings for pin in PINS.values()}

    request = gpiod.request_lines(
        GPIOCHIP,
        consumer="gpio-test",
        config=config,
    )
    ok(f"Opened {GPIOCHIP} and claimed lines: {list(PINS.values())}")

    # ---- Phase 1: blink individually ----------------------------------------
    print()
    hr()
    print("  Phase 1 : Blink each LED individually")
    hr()
    for label, pin in PINS.items():
        print(f"\n  Blinking {label} × {BLINK_TIMES} ...")
        for i in range(BLINK_TIMES):
            request.set_value(pin, Value.ACTIVE)
            print(f"    [{i+1}/{BLINK_TIMES}] ON ", end="\r")
            time.sleep(BLINK_DELAY)
            request.set_value(pin, Value.INACTIVE)
            print(f"    [{i+1}/{BLINK_TIMES}] OFF")
            time.sleep(BLINK_DELAY)
        ok(f"{label} — done")

    # ---- Phase 2: traffic-light sequence ------------------------------------
    print()
    hr()
    print("  Phase 2 : Traffic-light sequence (R→Y→G→ALL→OFF)")
    hr()
    sequence = [
        ("Red only",    {PIN_RED: Value.ACTIVE,   PIN_YELLOW: Value.INACTIVE, PIN_GREEN: Value.INACTIVE}),
        ("Yellow only", {PIN_RED: Value.INACTIVE,  PIN_YELLOW: Value.ACTIVE,   PIN_GREEN: Value.INACTIVE}),
        ("Green only",  {PIN_RED: Value.INACTIVE,  PIN_YELLOW: Value.INACTIVE, PIN_GREEN: Value.ACTIVE  }),
        ("All ON",      {PIN_RED: Value.ACTIVE,    PIN_YELLOW: Value.ACTIVE,   PIN_GREEN: Value.ACTIVE  }),
        ("All OFF",     {PIN_RED: Value.INACTIVE,  PIN_YELLOW: Value.INACTIVE, PIN_GREEN: Value.INACTIVE}),
    ]
    pin_labels = {v: k for k, v in PINS.items()}
    for step_name, values in sequence:
        print(f"\n  → {step_name}")
        request.set_values(values)
        for pin, val in values.items():
            state = "HIGH ●" if val == Value.ACTIVE else "LOW  ○"
            print(f"    {pin_labels[pin]} : {state}")
        time.sleep(SEQ_DELAY)

    # ---- Cleanup ------------------------------------------------------------
    print()
    hr()
    print("  Cleanup : releasing GPIO lines")
    hr()
    request.set_values({pin: Value.INACTIVE for pin in PINS.values()})
    request.release()
    ok("Lines released")
    print()
    ok("All done — GPIO check passed ✓")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    print()
    hr("═")
    print("  GPIO Test — Raspberry Pi 5 (gpiochip4 / pinctrl-rp1)")
    hr("═")
    print()

    try:
        import gpiod
        run_gpiod()
        return
    except ImportError:
        err("gpiod not installed. Run: pip install gpiod")
    except Exception as e:
        err(f"gpiod failed: {e}")

    print()
    err("GPIO test failed. Make sure:")
    print("    1. gpiod is installed  : pip install gpiod")
    print(f"   2. Chip exists         : ls -l {GPIOCHIP}")
    print("    3. Permissions OK      : sudo python3 test_gpio.py  (or add user to 'gpio' group)")
    print()
    sys.exit(1)


if __name__ == "__main__":
    main()
