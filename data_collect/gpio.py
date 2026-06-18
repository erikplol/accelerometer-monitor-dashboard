"""GPIO traffic light helpers for vibration status.

Uses libgpiod (gpiochip4 / pinctrl-rp1) for Raspberry Pi 5.
Auto-detects gpiod 2.x vs 1.x API.
Silently disables if gpiod is unavailable (e.g. development on non-Pi hardware).

Pin assignments (BCM numbering):
    GPIO 17 -> Red LED
    GPIO 27 -> Yellow LED
    GPIO 22 -> Green LED
"""

import os

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_GPIOCHIP   = "/dev/gpiochip4"
_PIN_RED    = 17
_PIN_YELLOW = 27
_PIN_GREEN  = 22

# ---------------------------------------------------------------------------
# Initialise gpiod (auto-detect 1.x vs 2.x API)
# ---------------------------------------------------------------------------
_GPIO_AVAILABLE = False
_set_pin_fn = None   # callable(pin: int, state: bool)
_set_all_fn = None   # callable(states: dict[int, bool])
_release_fn = None   # callable()

try:
    if not os.path.exists(_GPIOCHIP):
        raise RuntimeError(f"GPIO chip not found: {_GPIOCHIP}")

    import gpiod

    # ---- gpiod 2.x -------------------------------------------------------
    if hasattr(gpiod, "request_lines") or hasattr(gpiod, "LineSettings"):
        from gpiod.line import Direction, Value

        _settings = gpiod.LineSettings(
            direction=Direction.OUTPUT,
            output_value=Value.INACTIVE,
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

        def _set_pin_fn(pin: int, state: bool):
            _request.set_value(pin, Value.ACTIVE if state else Value.INACTIVE)

        def _set_all_fn(states: dict):
            _request.set_values({
                pin: (Value.ACTIVE if val else Value.INACTIVE)
                for pin, val in states.items()
            })

        def _release_fn():
            _request.set_values({
                _PIN_RED:    Value.INACTIVE,
                _PIN_YELLOW: Value.INACTIVE,
                _PIN_GREEN:  Value.INACTIVE,
            })
            _request.release()

    # ---- gpiod 1.x -------------------------------------------------------
    else:
        _chip = gpiod.Chip(_GPIOCHIP)
        _line_red    = _chip.get_line(_PIN_RED)
        _line_yellow = _chip.get_line(_PIN_YELLOW)
        _line_green  = _chip.get_line(_PIN_GREEN)

        for _line in (_line_red, _line_yellow, _line_green):
            _line.request(
                consumer="vibration-monitor",
                type=gpiod.LINE_REQ_DIR_OUT,
                default_val=0,
            )

        _pin_map = {
            _PIN_RED:    _line_red,
            _PIN_YELLOW: _line_yellow,
            _PIN_GREEN:  _line_green,
        }

        def _set_pin_fn(pin: int, state: bool):
            _pin_map[pin].set_value(1 if state else 0)

        def _set_all_fn(states: dict):
            for pin, val in states.items():
                _pin_map[pin].set_value(1 if val else 0)

        def _release_fn():
            for _line in _pin_map.values():
                _line.set_value(0)
                _line.release()
            _chip.close()

    _GPIO_AVAILABLE = True

except Exception:
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
    if not _GPIO_AVAILABLE or _set_all_fn is None:
        return

    _set_all_fn({
        _PIN_RED:    red,
        _PIN_YELLOW: yellow,
        _PIN_GREEN:  green,
    })


def cleanup() -> None:
    """Release GPIO lines. Call on application shutdown."""
    if _GPIO_AVAILABLE and _release_fn is not None:
        try:
            _release_fn()
        except Exception:
            pass
