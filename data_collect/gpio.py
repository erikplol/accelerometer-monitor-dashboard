"""GPIO traffic light helpers for vibration status.

Uses libgpiod 2.x (gpiochip4 / pinctrl-rp1) for Raspberry Pi 5.
Silently disables if gpiod is unavailable (e.g. development on non-Pi hardware).

Pin assignments (BCM numbering):
    GPIO 17 -> Red LED
    GPIO 27 -> Yellow LED
    GPIO 22 -> Green LED
"""

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_GPIOCHIP  = "/dev/gpiochip4"
_PIN_RED    = 17
_PIN_YELLOW = 27
_PIN_GREEN  = 22

# ---------------------------------------------------------------------------
# Initialise gpiod 2.x
# ---------------------------------------------------------------------------
_GPIO_AVAILABLE = False
_request = None

try:
    import gpiod
    from gpiod.line import Direction, Value as _Value

    _settings = gpiod.LineSettings(
        direction=Direction.OUTPUT,
        output_value=_Value.INACTIVE,
    )

    _request = gpiod.request_lines(
        _GPIOCHIP,
        consumer="vibration-monitor",
        config={
            _PIN_RED:    _settings,
            _PIN_YELLOW: _settings,
            _PIN_GREEN:  _settings,
        },
    )
    _GPIO_AVAILABLE = True

except Exception:
    _request = None
    _GPIO_AVAILABLE = False


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
    if not _GPIO_AVAILABLE or _request is None:
        return

    _request.set_values({
        _PIN_RED:    _Value.ACTIVE if red    else _Value.INACTIVE,
        _PIN_YELLOW: _Value.ACTIVE if yellow else _Value.INACTIVE,
        _PIN_GREEN:  _Value.ACTIVE if green  else _Value.INACTIVE,
    })


def cleanup() -> None:
    """Release GPIO lines. Call on application shutdown."""
    if _request is not None:
        try:
            _request.set_values({
                _PIN_RED:    _Value.INACTIVE,
                _PIN_YELLOW: _Value.INACTIVE,
                _PIN_GREEN:  _Value.INACTIVE,
            })
            _request.release()
        except Exception:
            pass
