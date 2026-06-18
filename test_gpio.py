#!/usr/bin/env python3
"""
test_gpio.py — Standalone GPIO sanity-check for Raspberry Pi 5.

Targets gpiochip4 (pinctrl-rp1) on Pi 5.

Pins (BCM):
    GPIO 17 -> Red LED
    GPIO 27 -> Yellow LED
    GPIO 22 -> Green LED

Usage:
    python3 test_gpio.py
    # or if permission denied:
    sudo .venv/bin/python3 test_gpio.py
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
# Detect gpiod API version and run accordingly
# ---------------------------------------------------------------------------
def run_gpiod():
    import gpiod

    version = getattr(gpiod, "__version__", "unknown")

    hr()
    print(f"  Backend : gpiod {version}")
    print(f"  Chip    : {GPIOCHIP}")
    hr()

    PINS = {
        "Red    (GPIO 17)": PIN_RED,
        "Yellow (GPIO 27)": PIN_YELLOW,
        "Green  (GPIO 22)": PIN_GREEN,
    }

    # ------------------------------------------------------------------ #
    # gpiod 2.x API                                                        #
    # ------------------------------------------------------------------ #
    if hasattr(gpiod, "request_lines") or hasattr(gpiod, "LineSettings"):
        from gpiod.line import Direction, Value

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

        def set_pin(pin, state: bool):
            request.set_value(pin, Value.ACTIVE if state else Value.INACTIVE)

        def set_all(states: dict):
            request.set_values({
                pin: (Value.ACTIVE if val else Value.INACTIVE)
                for pin, val in states.items()
            })

        def release():
            set_all({pin: False for pin in PINS.values()})
            request.release()

    # ------------------------------------------------------------------ #
    # gpiod 1.x API (fallback)                                             #
    # ------------------------------------------------------------------ #
    else:
        chip = gpiod.Chip(GPIOCHIP)
        lines = {label: chip.get_line(pin) for label, pin in PINS.items()}
        for label, line in lines.items():
            line.request(
                consumer="gpio-test",
                type=gpiod.LINE_REQ_DIR_OUT,
                default_val=0,
            )

        def set_pin(pin, state: bool):
            # find line by offset
            for line in lines.values():
                if line.offset() == pin:
                    line.set_value(1 if state else 0)
                    break

        def set_all(states: dict):
            for pin, val in states.items():
                set_pin(pin, val)

        def release():
            for line in lines.values():
                line.set_value(0)
                line.release()
            chip.close()

    ok(f"Claimed lines: {list(PINS.values())}")

    # ---- Phase 1: blink individually ------------------------------------
    print()
    hr()
    print("  Phase 1 : Blink each LED individually")
    hr()
    for label, pin in PINS.items():
        print(f"\n  Blinking {label} × {BLINK_TIMES} ...")
        for i in range(BLINK_TIMES):
            set_pin(pin, True)
            print(f"    [{i+1}/{BLINK_TIMES}] ON ", end="\r")
            time.sleep(BLINK_DELAY)
            set_pin(pin, False)
            print(f"    [{i+1}/{BLINK_TIMES}] OFF")
            time.sleep(BLINK_DELAY)
        ok(f"{label} — done")

    # ---- Phase 2: traffic-light sequence --------------------------------
    print()
    hr()
    print("  Phase 2 : Traffic-light sequence (R→Y→G→ALL→OFF)")
    hr()
    sequence = [
        ("Red only",    {PIN_RED: True,  PIN_YELLOW: False, PIN_GREEN: False}),
        ("Yellow only", {PIN_RED: False, PIN_YELLOW: True,  PIN_GREEN: False}),
        ("Green only",  {PIN_RED: False, PIN_YELLOW: False, PIN_GREEN: True }),
        ("All ON",      {PIN_RED: True,  PIN_YELLOW: True,  PIN_GREEN: True }),
        ("All OFF",     {PIN_RED: False, PIN_YELLOW: False, PIN_GREEN: False}),
    ]
    pin_labels = {v: k for k, v in PINS.items()}
    for step_name, states in sequence:
        print(f"\n  → {step_name}")
        set_all(states)
        for pin, val in states.items():
            print(f"    {pin_labels[pin]} : {'HIGH ●' if val else 'LOW  ○'}")
        time.sleep(SEQ_DELAY)

    # ---- Cleanup --------------------------------------------------------
    print()
    hr()
    print("  Cleanup : releasing GPIO lines")
    hr()
    release()
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

    # Check chip exists
    import os
    if not os.path.exists(GPIOCHIP):
        err(f"Chip not found: {GPIOCHIP}")
        err("Run: gpiodetect   to see available chips")
        sys.exit(1)
    info(f"Chip found: {GPIOCHIP}")

    try:
        import gpiod
        info(f"gpiod imported from: {gpiod.__file__}")
        run_gpiod()
    except ImportError:
        err("gpiod not installed.")
        err("Run: pip install gpiod")
        sys.exit(1)
    except PermissionError:
        err(f"Permission denied on {GPIOCHIP}")
        print()
        print("  Fix with one of:")
        print("    sudo usermod -aG gpio $USER  (then re-login)")
        print("    sudo .venv/bin/python3 test_gpio.py")
        sys.exit(1)
    except Exception as e:
        err(f"Failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
