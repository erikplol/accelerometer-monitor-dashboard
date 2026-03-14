import time

import device_model

"""
    WTVB01-485 example
"""

# region Common register address reference table
"""

hex    dec      describe

0x00    0       Save / restart / restore
0x04    4       Serial baud rate

0x1A    26      Device address

0x34    52      Acceleration x
0x35    53      Acceleration y
0x36    54      Acceleration z

0x3A    58      Vibration velocity x
0x3B    59      Vibration velocity y
0x3C    60      Vibration velocity z

0x3D    61      Reserved
0x3E    62      Reserved
0x3F    63      Reserved

0x40    64      Temperature

0x41    65      Vibration displacement x
0x42    66      Vibration displacement y
0x43    67      Vibration displacement z

0x44    68      Vibration frequency x
0x45    69      Vibration frequency y
0x46    70      Vibration frequency z

0x63    99      Cutoff frequency
0x64    100     Cutoff frequency
0x65    101     Sampling period

"""
# endregion

# Create the device model
device = device_model.DeviceModel("Test Device", "COM6", 9600, 0x50)
# Open the device
device.openDevice()
# Start polling
device.startLoopRead()
time.sleep(0.5)

# Display data
while True:
    # Refresh acceleration first (0x34..0x36), then vibration block (0x3A..0x46)
    device.readReg(0x34, 3)
    time.sleep(0.05)
    device.readReg(0x3A, 13)
    time.sleep(0.05)

    # a: acceleration, v: vibration velocity, t: temperature, s: vibration displacement, f: vibration frequency
    print("ax:{} ay:{} az:{} vx:{} vy:{} vz:{} t:{} sx:{} sy:{} sz:{} fx:{} fy:{} fz:{}".format(device.get("52"),device.get("53"),device.get("54"),device.get("58"),device.get("59"),device.get("60"),device.get("64"),device.get("65"),device.get("66"),device.get("67"),device.get("68"),device.get("69"),device.get("70")))
    time.sleep(0.2)


# Read register: read 1 register starting from 0x3a
# device.readReg(0x3a, 1)
# Get the read result
# device.get(str(0x3a))

# Write register: write 50 to 0x65, which changes the sampling period to 50 Hz
# device.writeReg(0x65, 50)
