# Engine Vibration Monitor

Dash/Plotly dashboard for real-time engine vibration monitoring from two sources:
- Pixhawk/ArduPilot via MAVLink (Z acceleration → velocity)
- Witmotion WTVB Modbus sensor (velocity & dominant frequency)

Designed for Raspberry Pi or any Linux host; accessible from any device on the LAN.

## Features

- Real-time VZ time series (mm/s) with compact display window
- Live FFT spectrum (Pixhawk velocity) with configurable window length
- RMS indicator (last ~1s) with ISO 10816 traffic-light thresholds
- Data logging with RPM/Load tags; download latest or all logs as CSV/zip
- Pause/resume UI updates without stopping collectors

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
python app.py
# or
python scripts/serve.py
```

Open: **http://localhost:7777** (or the host IP on your LAN).

## Configuration

UI & app settings: [src/config/settings.py](src/config/settings.py)
- `HOST`, `PORT`, `DEBUG`, `UI_INTERVAL_MS`, `DISPLAY_SECONDS`, `FFT_WINDOW_SECONDS`, `FFT_UPDATE_EVERY_N_INTERVALS`, `MAX_DISPLAY_PTS`

Pixhawk (MAVLink): environment variables (see [src/sensors/pixhawk.py](src/sensors/pixhawk.py))
- `MAVLINK_PORT` (default: by-id CubeOrange path)
- `MAVLINK_BAUD` (default: 921600)
- `MAVLINK_IMU_RATE_HZ` (default: 100)

Witmotion (Modbus): environment variables (see [src/sensors/witmotion.py](src/sensors/witmotion.py))
- `WTVB_PORT` (default: by-id USB-Serial)
- `WTVB_BAUD_CANDIDATES` (default: 115200,38400,9600 — auto-tries in order)
- `WTVB_MODBUS_ADDR`, `WTVB_SENSOR_RATE_HZ`

Logs: saved to `logs/` with names `vibration_RPM{rpm}_LOAD{load}W_{YYYYmmdd_HHMMSS}.csv`.

## Usage (UI)

1) Connect sensors, start the app.
2) Observe real-time VZ and FFT graphs.
3) Logging: enter RPM and Load (W), click **▶ Start**; stop manually or wait for auto-stop (~30s). Use download links in the header.
4) Pause button stops UI updates while collectors keep running.

## Thresholds (ISO 10816, velocity RMS)

| Light | Condition | Meaning |
|-------|-----------|---------|
| 🟢 Green  | RMS < 2.8 mm/s | Good |
| 🟡 Yellow | 2.8–7.1 mm/s   | Acceptable |
| 🔴 Red    | RMS ≥ 7.1 mm/s | Alarm |

## Project layout (refactored)

- [app.py](app.py) — entrypoint, exposes `server` for WSGI
- [scripts/serve.py](scripts/serve.py) — CLI launcher
- [src/dashboard/app.py](src/dashboard/app.py) — Dash layout & callbacks
- [src/dashboard/figures.py](src/dashboard/figures.py) — plot builders
- [src/sensors/pixhawk.py](src/sensors/pixhawk.py) — MAVLink reader & logging
- [src/sensors/witmotion.py](src/sensors/witmotion.py) — Modbus reader
- [src/utils/fft.py](src/utils/fft.py) — FFT helper
- [src/config/settings.py](src/config/settings.py) — UI/network constants
- [legacy/](legacy/) — archived legacy scripts (pre-refactor)

## Reverse proxy (optional)

Run once if you want port 80 + `.local` hostname:

```bash
sudo ./setup_proxy.sh
```

Then access:
- This machine: `http://vibration-monitor.local` or `http://localhost`
- Any LAN device: `http://vibration-monitor.local`

> Windows lacks mDNS by default; add a hosts entry pointing `vibration-monitor.local` to the device IP if needed.
