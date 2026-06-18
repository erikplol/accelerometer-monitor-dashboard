"""GPIO traffic light helpers for vibration status.

Uses libgpiod (gpiochip4 / pinctrl-rp1) for Raspberry Pi 5 compatibility.
Falls back to gpiozero for older Pi models, then silently disables if
neither library is available (e.g. development on non-Pi hardware).

Pin assignments (BCM numbering):
    GPIO 17 -> Red LED
    GPIO 27 -> Yellow LED
    GPIO 22 -> Green LED
"""

from typing import Any, Optional

# ---------------------------------------------------------------------------
# GPIO chip to use on Raspberry Pi 5 (RP1 / pinctrl-rp1)
# ---------------------------------------------------------------------------
_GPIOCHIP = "gpiochip4"
_PIN_RED = 17
_PIN_YELLOW = 27
_PIN_GREEN = 22

# ---------------------------------------------------------------------------
# Attempt to initialise via libgpiod first (Pi 5), then gpiozero (Pi 4/3/2)
# ---------------------------------------------------------------------------
_GPIO_AVAILABLE = False
_backend = None  # "gpiod" | "gpiozero"

# --- libgpiod -----------------------------------------------------------------
try:
    import gpiod  # type: ignore[import-not-found]

    _chip = gpiod.Chip(_GPIOCHIP)

    _line_red = _chip.get_line(_PIN_RED)
    _line_yellow = _chip.get_line(_PIN_YELLOW)
    _line_green = _chip.get_line(_PIN_GREEN)

    for _line in (_line_red, _line_yellow, _line_green):
        _line.request(
            consumer="vibration-monitor",
            type=gpiod.LINE_REQ_DIR_OUT,
            default_val=0,
        )

    _GPIO_AVAILABLE = True
    _backend = "gpiod"

except Exception:
    _line_red = None
    _line_yellow = None
    _line_green = None

# --- gpiozero fallback --------------------------------------------------------
if not _GPIO_AVAILABLE:
    try:
        from gpiozero import LED  # type: ignore[import-not-found]

        led_red = LED(_PIN_RED, active_high=False)
        led_yellow = LED(_PIN_YELLOW, active_high=False)
        led_green = LED(_PIN_GREEN, active_high=False)

        _GPIO_AVAILABLE = True
        _backend = "gpiozero"

    except Exception:
        led_red: Optional[Any] = None
        led_yellow: Optional[Any] = None
        led_green: Optional[Any] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_gpio_lights(red: bool, yellow: bool, green: bool) -> None:
    """Set status LEDs for red/yellow/green states.

    Args:
        red:    Turn the red LED on (True) or off (False).
        yellow: Turn the yellow LED on (True) or off (False).
        green:  Turn the green LED on (True) or off (False).
    """
    if not _GPIO_AVAILABLE:
        return

    if _backend == "gpiod":
        _line_red.set_value(1 if red else 0)
        _line_yellow.set_value(1 if yellow else 0)
        _line_green.set_value(1 if green else 0)

    elif _backend == "gpiozero":
        led_red.on() if red else led_red.off()
        led_yellow.on() if yellow else led_yellow.off()
        led_green.on() if green else led_green.off()


def cleanup() -> None:
    """Release GPIO lines. Call on application shutdown when using gpiod."""
    if _backend == "gpiod":
        for _line in (_line_red, _line_yellow, _line_green):
            if _line is not None:
                try:
                    _line.release()
                except Exception:
                    pass
