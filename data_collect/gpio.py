"""GPIO traffic light helpers for vibration status."""

from typing import Any, Optional

try:
    from gpiozero import LED  # type: ignore[import-not-found]
    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    LED = None
    _GPIO_AVAILABLE = False

if _GPIO_AVAILABLE and LED is not None:
    led_red = LED(17, active_high=False)
    led_yellow = LED(27, active_high=False)
    led_green = LED(22, active_high=False)
else:
    led_red: Optional[Any] = None
    led_yellow: Optional[Any] = None
    led_green: Optional[Any] = None


def set_gpio_lights(red: bool, yellow: bool, green: bool) -> None:
    """Set status LEDs for red/yellow/green states."""
    if not _GPIO_AVAILABLE or led_red is None or led_yellow is None or led_green is None:
        return

    if red:
        led_red.on()
    else:
        led_red.off()

    if yellow:
        led_yellow.on()
    else:
        led_yellow.off()

    if green:
        led_green.on()
    else:
        led_green.off()
