#!/usr/bin/env python3
"""
test_gpio.py — Standalone GPIO sanity-check for Raspberry Pi 5.

Tests GPIO pins on gpiochip4 (pinctrl-rp1):
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
GPIOCHIP = "gpiochip4"
PINS = {
    "Red    (GPIO 17)": 17,
    "Yellow (GPIO 27)": 27,
    "Green  (GPIO 22)": 22,
}
BLINK_TIMES = 3
BLINK_DELAY = 0.4   # seconds per on/off cycle
SEQ_DELAY   = 0.6   # seconds between traffic-light steps

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def hr(char="─", width=52):
    print(char * width)

def info(msg):  print(f"  [INFO]  {msg}")
def ok(msg):    print(f"  [ OK ]  {msg}")
def warn(msg):  print(f"  [WARN]  {msg}")
def err(msg):   print(f"  [ERR ]  {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# gpiod backend (Pi 5)
# ---------------------------------------------------------------------------
def run_gpiod():
    import gpiod

    hr()
    print(f"  Backend : gpiod (libgpiod)")
    print(f"  Chip    : {GPIOCHIP}")
    hr()

    chip = gpiod.Chip(GPIOCHIP)
    info(f"Opened {GPIOCHIP}  ({chip.num_lines()} lines)")

    lines = {}
    for label, pin in PINS.items():
        line = chip.get_line(pin)
        line.request(
            consumer="gpio-test",
            type=gpiod.LINE_REQ_DIR_OUT,
            default_val=0,
        )
        lines[label] = line
        ok(f"Claimed {label}")

    print()
    hr()
    print("  Phase 1 : Blink each LED individually")
    hr()
    for label, line in lines.items():
        print(f"\n  Blinking {label} × {BLINK_TIMES} ...")
        for i in range(BLINK_TIMES):
            line.set_value(1)
            print(f"    [{i+1}/{BLINK_TIMES}] ON", end="\r")
            time.sleep(BLINK_DELAY)
            line.set_value(0)
            print(f"    [{i+1}/{BLINK_TIMES}] OFF")
            time.sleep(BLINK_DELAY)
        ok(f"{label} — done")

    print()
    hr()
    print("  Phase 2 : Traffic-light sequence (R→Y→G→ALL→OFF)")
    hr()
    sequence = [
        ("Red only",    {"Red    (GPIO 17)": 1, "Yellow (GPIO 27)": 0, "Green  (GPIO 22)": 0}),
        ("Yellow only", {"Red    (GPIO 17)": 0, "Yellow (GPIO 27)": 1, "Green  (GPIO 22)": 0}),
        ("Green only",  {"Red    (GPIO 17)": 0, "Yellow (GPIO 27)": 0, "Green  (GPIO 22)": 1}),
        ("All ON",      {"Red    (GPIO 17)": 1, "Yellow (GPIO 27)": 1, "Green  (GPIO 22)": 1}),
        ("All OFF",     {"Red    (GPIO 17)": 0, "Yellow (GPIO 27)": 0, "Green  (GPIO 22)": 0}),
    ]
    for step_name, values in sequence:
        print(f"\n  → {step_name}")
        for label, val in values.items():
            lines[label].set_value(val)
            print(f"    {label} : {'HIGH ●' if val else 'LOW  ○'}")
        time.sleep(SEQ_DELAY)

    print()
    hr()
    print("  Cleanup : releasing GPIO lines")
    hr()
    for label, line in lines.items():
        line.set_value(0)
        line.release()
        ok(f"Released {label}")

    chip.close()
    print()
    ok("All done — GPIO check passed ✓")
    print()


# ---------------------------------------------------------------------------
# gpiozero fallback (Pi 4 / 3 / 2)
# ---------------------------------------------------------------------------
def run_gpiozero():
    from gpiozero import LED

    hr()
    print("  Backend : gpiozero (fallback)")
    hr()

    leds = {label: LED(pin, active_high=False) for label, pin in PINS.items()}
    for label in leds:
        ok(f"Claimed {label}")

    print()
    hr()
    print("  Phase 1 : Blink each LED individually")
    hr()
    for label, led in leds.items():
        print(f"\n  Blinking {label} × {BLINK_TIMES} ...")
        for i in range(BLINK_TIMES):
            led.on()
            print(f"    [{i+1}/{BLINK_TIMES}] ON", end="\r")
            time.sleep(BLINK_DELAY)
            led.off()
            print(f"    [{i+1}/{BLINK_TIMES}] OFF")
            time.sleep(BLINK_DELAY)
        ok(f"{label} — done")

    print()
    hr()
    print("  Phase 2 : Traffic-light sequence (R→Y→G→ALL→OFF)")
    hr()
    sequence = [
        ("Red only",    (True,  False, False)),
        ("Yellow only", (False, True,  False)),
        ("Green only",  (False, False, True )),
        ("All ON",      (True,  True,  True )),
        ("All OFF",     (False, False, False)),
    ]
    labels = list(leds.keys())
    led_list = list(leds.values())
    for step_name, values in sequence:
        print(f"\n  → {step_name}")
        for led, val, label in zip(led_list, values, labels):
            led.on() if val else led.off()
            print(f"    {label} : {'HIGH ●' if val else 'LOW  ○'}")
        time.sleep(SEQ_DELAY)

    print()
    hr()
    print("  Cleanup")
    hr()
    for led in led_list:
        led.off()
        led.close()
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

    # Try gpiod first (Pi 5)
    try:
        import gpiod  # noqa: F401
        run_gpiod()
        return
    except ImportError:
        warn("gpiod not installed — trying gpiozero ...")
    except Exception as e:
        err(f"gpiod failed: {e}")
        warn("Falling back to gpiozero ...")

    # Try gpiozero (Pi 4/3/2)
    try:
        import gpiozero  # noqa: F401
        run_gpiozero()
        return
    except ImportError:
        err("gpiozero not installed either.")
    except Exception as e:
        err(f"gpiozero failed: {e}")

    print()
    err("No GPIO backend available. Install gpiod or gpiozero:")
    print("      pip install gpiod gpiozero")
    print()
    sys.exit(1)


if __name__ == "__main__":
    main()
