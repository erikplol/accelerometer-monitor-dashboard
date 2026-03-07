# Engine Vibration Monitor

Real-time Z-axis vibration velocity monitoring dashboard for the WTVB02-485 sensor, built with Dash/Plotly.

## Features

- **Real-time time-domain graph** — VZ (mm/s) vs elapsed seconds, 60-second rolling window
- **FFT frequency spectrum** — live single-sided amplitude spectrum (mm/s vs Hz)
- **RMS indicator** — RMS of the last 1-second window, updated every cycle
- **Traffic-light severity panel** — ISO 10816-based thresholds (green / yellow / red)
- **Data logging** — start/stop log sessions tagged with RPM and Load (W); saved as CSV to `logs/`

## Hardware

| Parameter | Value |
|-----------|-------|
| Sensor    | WTVB02-485 (Modbus RTU) |
| Interface | USB–RS485 adapter |
| Port      | `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` |
| Baud rate | 9600 |
| Register  | `0x3C` — VZ vibration velocity (mm/s, signed 16-bit) |

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
./run.sh          # activates .venv and starts the app
# or
python app.py
```

Open your browser to: **http://localhost:7777**

## Vibration Severity Thresholds

| Light  | Condition          | Meaning     |
|--------|--------------------|-------------|
| 🟢 Green  | RMS < 2.8 mm/s     | Good        |
| 🟡 Yellow | 2.8 – 7.1 mm/s     | Acceptable  |
| 🔴 Red    | RMS ≥ 7.1 mm/s     | Alarm       |

## Data Logging

1. Enter **RPM** and **Load (W)** in the logging panel.
2. Click **▶ Start** to begin recording.
3. Click **■ Stop & Save** to end the session and write the CSV.

Log files are saved in `logs/` with the naming convention:

```
vibration_RPM{rpm}_LOAD{load}W_{YYYYmmdd_HHMMSS}.csv
```

Each file contains a metadata header (RPM, Load, timestamp, sample count, RMS) followed by per-sample rows: `counter, unix_time, iso_time, vz_mm_s`.

## Configuration

Edit the top of `data_collect.py`:

| Variable        | Default | Description                        |
|-----------------|---------|------------------------------------|
| `PORT`          | (by-id path) | Serial port of the RS-485 adapter |
| `BAUD`          | `9600`  | Modbus baud rate                   |
| `MODBUS_ADDR`   | `0x50`  | Device Modbus address              |
| `SAMPLING_RATE` | `10.0`  | Polling rate (Hz)                  |
| `MAX_TIME_PTS`  | `600`   | Rolling buffer size (samples)      |
