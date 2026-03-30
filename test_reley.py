from gpiozero import LED
from time import sleep

red = LED(17, active_high=False)
green = LED(22, active_high=False)
yellow = LED(27, active_high=False)

print("Turning ON...")
red.on()
green.on()
yellow.on()
sleep(2)
print("Turning OFF...")
red.off()
green.off()
yellow.off()
sleep(2)
