"""GPIO traffic light helpers for vibration status.

Uses libgpiod (gpiochip4 / pinctrl-rp1) for Raspberry Pi 5.
Auto-detects gpiod 2.x vs 1.x API.
Thread-safe: uses a lock to prevent interleaved pin writes.
State-tracked: skips redundant writes when state has not changed.
Silently disables if gpiod is unavailable (e.g. development on non-Pi hardware).

Pin assignments (BCM numbering):
    GPIO 17 -> Red LED
    GPIO 27 -> Yellow LED
    GPIO 22 -> Green LED

Public API:
    set_gpio_lights(red, yellow, green)  — set all three LEDs explicitly
    set_status(status)                   — convenience: "red" | "yellow" | "green" | "off"
    all_off()                            — turn everything off immediately
    cleanup()                            — release GPIO lines on shutdown
"""

import os
import threading

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_GPIOCHIP   = "/dev/gpiochip4"
_PIN_RED    = 17
_PIN_YELLOW = 27
_PIN_GREEN  = 22

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_lock          = threading.Lock()
_current_state = {_PIN_RED: False, _PIN_YELLOW: False, _PIN_GREEN: False}
_GPIO_AVAILABLE = False
_set_all_fn     = None   # callable(states: dict[int, bool])
_release_fn     = None   # callable()

# ---------------------------------------------------------------------------
# Initialise gpiod (auto-detect 2.x vs 1.x API)
# ---------------------------------------------------------------------------
try:
    if not os.path.exists(_GPIOCHIP):
        raise RuntimeError(f"GPIO chip not found: {_GPIOCHIP}")

    import gpiod

    # ---- gpiod 2.x -------------------------------------------------------
    if hasattr(gpiod, "request_lines") or hasattr(gpiod, "LineSettings"):
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

        def _set_all_fn(states: dict):
            _request.set_values({
                pin: (_Value.ACTIVE if val else _Value.INACTIVE)
                for pin, val in states.items()
            })

        def _release_fn():
            _request.set_values({
                _PIN_RED:    _Value.INACTIVE,
                _PIN_YELLOW: _Value.INACTIVE,
                _PIN_GREEN:  _Value.INACTIVE,
            })
            _request.release()

    # ---- gpiod 1.x -------------------------------------------------------
    else:
        _chip = gpiod.Chip(_GPIOCHIP)
        _pin_map = {
            _PIN_RED:    _chip.get_line(_PIN_RED),
            _PIN_YELLOW: _chip.get_line(_PIN_YELLOW),
            _PIN_GREEN:  _chip.get_line(_PIN_GREEN),
        }
        for _line in _pin_map.values():
            _line.request(
                consumer="vibration-monitor",
                type=gpiod.LINE_REQ_DIR_OUT,
                default_val=0,
            )

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
# Internal write — always called under _lock
# ---------------------------------------------------------------------------
def _apply(red: bool, yellow: bool, green: bool) -> None:
    """Write pin states. Caller must hold _lock."""
    global _current_state

    desired = {_PIN_RED: red, _PIN_YELLOW: yellow, _PIN_GREEN: green}

    # Skip if nothing changed
    if desired == _current_state:
        return

    # Phase 1: turn OFF any pin that should now be off (prevents ghost-on)
    off_state = {
        pin: False
        for pin, val in desired.items()
        if not val
    }
    if off_state:
        _set_all_fn(off_state)

    # Phase 2: turn ON the pins that should be on
    on_state = {
        pin: True
        for pin, val in desired.items()
        if val
    }
    if on_state:
        _set_all_fn(on_state)

    _current_state = desired


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_gpio_lights(red: bool, yellow: bool, green: bool) -> None:
    """Set all three status LEDs explicitly.

    Args:
        red:    Turn the red LED on (True) or off (False).
        yellow: Turn the yellow LED on (True) or off (False).
        green:  Turn the green LED on (True) or off (False).
    """
    if not _GPIO_AVAILABLE or _set_all_fn is None:
        return

    with _lock:
        _apply(red, yellow, green)


def set_status(status: str) -> None:
    """Convenience function — light one LED exclusively and turn off the rest.

    Args:
        status: One of "red" | "yellow" | "green" | "off"

    Example:
        set_status("red")     # Red ON, Yellow OFF, Green OFF
        set_status("green")   # Red OFF, Yellow OFF, Green ON
        set_status("off")     # All OFF
    """
    mapping = {
        "red":    (True,  False, False),
        "yellow": (False, True,  False),
        "green":  (False, False, True ),
        "off":    (False, False, False),
    }
    r, y, g = mapping.get(status.lower(), (False, False, False))
    set_gpio_lights(r, y, g)


def all_off() -> None:
    """Turn all LEDs off immediately."""
    set_gpio_lights(False, False, False)


def cleanup() -> None:
    """Turn off all LEDs and release GPIO lines. Call on application shutdown."""
    if not _GPIO_AVAILABLE or _release_fn is None:
        return

    with _lock:
        try:
            _release_fn()
        except Exception:
            pass
