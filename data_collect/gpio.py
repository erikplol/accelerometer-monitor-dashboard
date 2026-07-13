"""GPIO traffic light helpers for vibration status.

Uses libgpiod (gpiochip4 / pinctrl-rp1) for Raspberry Pi 5.
Auto-detects gpiod 2.x vs 1.x API.
Thread-safe: uses a lock to prevent interleaved pin writes.
State-tracked: skips redundant writes when state has not changed.
Debounced: new state must be requested consistently for DEBOUNCE_SECONDS
           before the physical output changes, preventing rapid on/off flicker
           that can make lamps unreliable.
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
import time

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_GPIOCHIP   = "/dev/gpiochip4"
_PIN_RED    = 17
_PIN_YELLOW = 27
_PIN_GREEN  = 22

# Minimum time (seconds) a new state must be requested consistently before the
# physical output actually switches.  Prevents rapid on/off toggling near
# thresholds that can make relays/lamps unreliable.
DEBOUNCE_SECONDS = float(os.getenv("GPIO_DEBOUNCE_S", "0.5"))

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_lock           = threading.Lock()

# State currently applied to the physical GPIO pins.
_current_state  = {_PIN_RED: False, _PIN_YELLOW: False, _PIN_GREEN: False}

# Most recent state requested by the caller.
_pending_state  = {_PIN_RED: False, _PIN_YELLOW: False, _PIN_GREEN: False}

# Debounce tracking: the candidate next-state and the monotonic timestamp when
# it was first seen.  If a *different* state arrives the timer resets.
_candidate_state = {_PIN_RED: False, _PIN_YELLOW: False, _PIN_GREEN: False}
_candidate_since = 0.0  # time.monotonic() of first request matching _candidate_state

_pending_event  = threading.Event()   # signals GPIO thread that new state is waiting
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
            output_value=_Value.INACTIVE,  # default OFF = pin LOW
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
            # LED ON  = pin HIGH (ACTIVE)
            # LED OFF = pin LOW  (INACTIVE)
            _request.set_values({
                pin: (_Value.ACTIVE if val else _Value.INACTIVE)
                for pin, val in states.items()
            })

        def _release_fn():
            # Turn OFF = set LOW (INACTIVE)
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
            # LED ON = 1 (HIGH), LED OFF = 0 (LOW)
            for pin, val in states.items():
                _pin_map[pin].set_value(1 if val else 0)

        def _release_fn():
            # Turn OFF = set LOW (0)
            for _line in _pin_map.values():
                _line.set_value(0)
                _line.release()
            _chip.close()

    _GPIO_AVAILABLE = True

except Exception:
    _GPIO_AVAILABLE = False

# ---------------------------------------------------------------------------
# Background GPIO writer thread
# ---------------------------------------------------------------------------
_gpio_thread_stop = threading.Event()
_gpio_thread = None


def _gpio_writer_loop():
    """Dedicated thread: drains _pending_state to GPIO without blocking callers.

    Applies debounce logic: a new state is only written to the physical pins
    once it has been continuously requested for at least DEBOUNCE_SECONDS.
    This prevents relay/LED flicker when sensor readings oscillate near a
    threshold boundary.
    """
    global _candidate_since

    while not _gpio_thread_stop.is_set():
        # Short timeout so that debounce timers can fire even when no new
        # request arrives.
        _pending_event.wait(timeout=0.1)

        # Snapshot desired state and clear event *inside* the lock so a
        # set() that arrives between clear() and lock-acquire is never lost.
        with _lock:
            _pending_event.clear()
            desired = dict(_pending_state)

        # Already showing this state — nothing to do.
        if desired == _current_state:
            continue

        now = time.monotonic()

        with _lock:
            if desired != _candidate_state:
                # Different state than what we've been timing — reset.
                _candidate_state.update(desired)
                _candidate_since = now
                continue   # restart the debounce clock

            elapsed = now - _candidate_since
            if elapsed < DEBOUNCE_SECONDS:
                continue   # debounce timer not yet satisfied

            # Timer satisfied — take a copy to write outside the lock.
            commit = dict(desired)

        # Perform the (potentially slow) GPIO I/O outside the lock so
        # callers of set_gpio_lights() are never blocked on hardware.
        try:
            _set_all_fn(commit)
        except Exception:
            continue

        with _lock:
            _current_state.update(commit)


if _GPIO_AVAILABLE and _set_all_fn is not None:
    _gpio_thread = threading.Thread(
        target=_gpio_writer_loop,
        name='GPIOWriter',
        daemon=True,
    )
    _gpio_thread.start()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_gpio_lights(red: bool, yellow: bool, green: bool) -> None:
    """Queue new LED state. Returns immediately — GPIO write happens in background thread.

    The physical output will only change after the new state has been
    requested consistently for at least DEBOUNCE_SECONDS (default 1 s).
    This prevents rapid on/off switching that can cause lamps to malfunction.

    Args:
        red:    Turn the red LED on (True) or off (False).
        yellow: Turn the yellow LED on (True) or off (False).
        green:  Turn the green LED on (True) or off (False).
    """
    if not _GPIO_AVAILABLE or _set_all_fn is None:
        return

    with _lock:
        _pending_state[_PIN_RED]    = red
        _pending_state[_PIN_YELLOW] = yellow
        _pending_state[_PIN_GREEN]  = green
    _pending_event.set()   # wake GPIO thread — non-blocking


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
    """Turn all LEDs off immediately (bypasses debounce)."""
    if not _GPIO_AVAILABLE or _set_all_fn is None:
        return

    with _lock:
        _pending_state[_PIN_RED]    = False
        _pending_state[_PIN_YELLOW] = False
        _pending_state[_PIN_GREEN]  = False
        _candidate_state[_PIN_RED]   = False
        _candidate_state[_PIN_YELLOW] = False
        _candidate_state[_PIN_GREEN]  = False

    # Immediate write — bypass debounce for explicit "all off".
    try:
        _set_all_fn({_PIN_RED: False, _PIN_YELLOW: False, _PIN_GREEN: False})
    except Exception:
        pass

    with _lock:
        _current_state[_PIN_RED]    = False
        _current_state[_PIN_YELLOW] = False
        _current_state[_PIN_GREEN]  = False


def cleanup() -> None:
    """Turn off all LEDs and release GPIO lines. Call on application shutdown."""
    if not _GPIO_AVAILABLE or _release_fn is None:
        return

    # Stop the writer thread and wait for it to finish so we don't race
    # with _release_fn().
    _gpio_thread_stop.set()
    _pending_event.set()   # unblock the writer thread so it exits
    if _gpio_thread is not None:
        try:
            _gpio_thread.join(timeout=2.0)
        except Exception:
            pass

    try:
        _release_fn()
    except Exception:
        pass
