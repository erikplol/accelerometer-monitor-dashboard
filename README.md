# Engine Vibration Monitor

Real-time dual-sensor vibration monitoring dashboard built with Dash/Plotly.

- Pixhawk (MAVLink): AZ acceleration (m/s²)
- Witmotion WTVB02-485 (Modbus RTU): VZ vibration velocity (mm/s) and HZZ (Hz)

The app is designed for Raspberry Pi or Linux desktops and can be exposed on local network with nginx + mDNS.

## Features

- Real-time VZ time-domain graph (rolling display)
- Real-time AZ FFT graph
- Live RMS cards for VZ and AZ
- Severity traffic lights based on thresholds
- Connection status for both sensors
- Timed data logging with RPM/Load metadata
- Download latest CSV or all logs as ZIP

## Project Structure

```text
accelerometer-monitor-dashboard/
├── app.py
├── run.sh
├── setup_proxy.sh
├── requirements.txt
├── README.md
├── calibration/
│   ├── pixhawk_calib.txt
│   └── wtb_calib.txt
├── dashboard/
│   ├── layout.py
│   ├── callbacks.py
│   ├── fft.py
│   └── theme.py
├── data_collect/
│   ├── __init__.py
│   ├── state.py
│   ├── gpio.py
│   ├── data_collect.py
│   ├── pixhawk.py
│   ├── witmotion.py
│   └── vb01_python_sdk/
│       ├── device_model.py
│       └── test.py
└── logs/
```

### What each module does

- `app.py`: App entry point. Builds Dash app, starts reader threads, injects layout, registers callbacks.
- `dashboard/layout.py`: UI component tree factory.
- `dashboard/callbacks.py`: Runtime behavior (graph refresh, logging controls, playback pause, downloads).
- `dashboard/fft.py`: FFT computation and refresh helpers for AZ spectrum data.
- `dashboard/theme.py`: Shared colors/styles and the custom HTML index template.
- `data_collect/state.py`: Shared runtime buffers/config/constants used by both sensor threads.
- `data_collect/gpio.py`: GPIO LED control helpers for red/yellow/green status lamps.
- `data_collect/pixhawk.py`: Pixhawk MAVLink reader thread.
- `data_collect/witmotion.py`: Witmotion Modbus reader thread.
- `data_collect/data_collect.py`: Public API and CLI wrapper around readers + logging utilities.
- `data_collect/vb01_python_sdk/device_model.py`: Vendor SDK helper for low-level Modbus operations.
- `data_collect/vb01_python_sdk/test.py`: Standalone SDK usage example script.
- `calibration/pixhawk_calib.txt`: Plain-text Pixhawk gravity offset in mG (single float value).
- `calibration/wtb_calib.txt`: Plain-text Witmotion VZ scale multiplier (single float value).
- `logs/`: Saved CSV logs.

## Function Reference

This section summarizes each function/method in the repository.

### `dashboard/layout.py`

- `create_layout(thresh_green, thresh_yellow, ui_interval_ms)`: Builds and returns the complete Dash layout (cards, charts, controls, stores, downloads, interval timer).

### `dashboard/theme.py`

- `light_style(active, color, glow)`: Returns style dict for a severity lamp (active glowing state or inactive dim state).

### `dashboard/callbacks.py`

- `register_callbacks(app, reader, wit_reader, sampling_rate, ui_interval_ms, max_display_pts, fft_window_seconds, thresh_green, thresh_yellow)`: Registers all Dash callbacks and internal helpers.
- `update_dashboard(n, playback_data)`: Refreshes graphs, RMS values, HZZ readout, severity lights, and connection badge on each interval tick.
- `do_stop(log_data, auto=False)`: Internal helper to pause readers, stop logging, save CSV, then resume readers.
- `handle_logging(n_start, n_stop, n_intervals, rpm, load_w, log_data)`: Controls start/stop/auto-stop logging flow and status text.
- `toggle_playback(n_clicks, playback_data)`: Pauses/resumes view updates (does not stop acquisition).
- `download_last_log(n_clicks, log_data)`: Sends latest log file (prefer last saved path, fallback to newest CSV in logs directory).
- `download_all_logs(n_clicks)`: Creates an in-memory ZIP of all CSV logs and sends it to browser.
- `write_zip(buff)`: Nested helper inside `download_all_logs` that writes log files into ZIP buffer.

### `dashboard/fft.py`

- `compute_az_fft(az_values, effective_rate, fft_window_seconds)`: Computes single-sided AZ FFT arrays and returns frequency/amplitude lists plus sample count.
- `refresh_fft_cache(fft_cache, az_values, effective_rate, fft_window_seconds, should_update)`: Refreshes the dashboard FFT cache in place when an update is due.

### `data_collect/data_collect.py`

- `get_histories()`: Returns copies of buffered histories for AZ, VZ, HZZ, absolute timestamps, and relative seconds.
- `get_latest_sample()`: Returns latest combined sample snapshot (AZ, VZ, HZZ, timestamp, rate).
- `get_actual_rate()`: Returns current measured Pixhawk receive rate.
- `is_connected()`: Returns Pixhawk connection state.
- `is_witmotion_connected()`: Returns Witmotion connection state.
- `start_logging()`: Enables in-memory logging buffer and resets counters.
- `stop_logging()`: Disables logging and returns buffered rows.
- `save_log(rpm, load_w, data)`: Writes CSV with metadata header and sample rows; returns saved file path.
- `main()`: CLI runner for starting readers, optional timed run, and optional logging from terminal.

### `data_collect/state.py`

- `get_vz_rms_last_1s()`: Computes RMS of most recent ~1 second VZ samples.

### `data_collect/gpio.py`

- `set_gpio_lights(red, yellow, green)`: Sets GPIO traffic light LEDs when gpiozero is available.

### `data_collect/pixhawk.py` (`MAVLinkReader`)

- `__init__(port, baud, target_rate_hz)`: Initializes thread control, calibration state, and config.
- `_connect()`: Opens MAVLink connection and waits for heartbeat.
- `_configure_stream_rate(connection)`: Requests RAW_IMU stream/message interval.
- `_calibrate_gravity(zacc_mg)`: Learns and stores gravity offset from early samples.
- `_update_rate_measurement(ts)`: Maintains rolling measured receive rate.
- `_reset_state()`: Resets reconnect-related counters/state.
- `run()`: Main acquisition loop; decodes IMU messages, updates shared buffers, logging buffer, and GPIO lights.
- `pause()`: Temporarily pauses processing loop.
- `resume()`: Resumes processing loop.
- `stop()`: Signals thread to terminate.

### `data_collect/witmotion.py`

- `_build_read_request(addr, reg, count)`: Builds Modbus read request frame with CRC.
- `_extract_valid_frame(buffer, addr, function_code, byte_count)`: Finds and CRC-validates one response frame in raw bytes.
- `_parse_read_response(buffer, addr, start_reg, register_count)`: Parses register values from valid Modbus response.
- `_decode_signed_u16(raw_value)`: Converts unsigned 16-bit register to signed integer.
- `_decode_vz_mm_s(raw_value)`: Converts raw VZ register to mm/s.
- `_decode_hz(raw_value)`: Converts raw frequency register to Hz.

`WitmotionReader` methods:

- `__init__(port, baud_candidates, modbus_addr, sample_rate_hz)`: Initializes serial reader thread config.
- `_send_and_read_exact(ser, request, expected_len)`: Sends one request and reads a fixed-length response.
- `_probe_device(ser)`: Checks if sensor replies correctly at current baud.
- `_open_serial()`: Tries configured baud rates and returns working serial connection.
- `run()`: Main poll loop; reads fast register block + periodic HZZ, updates shared state.
- `pause()`: Pauses polling loop.
- `resume()`: Resumes polling loop.
- `stop()`: Signals thread to terminate.

### `data_collect/vb01_python_sdk/device_model.py`

`SerialConfig` class:

- Holds serial settings fields (`portName`, `baud`).

`DeviceModel` methods:

- `__init__(deviceName, portName, baud, ADDR)`: Initializes SDK device model.
- `get_crc(datas, dlen)`: Computes Modbus CRC.
- `set(key, value)`: Stores parsed register value in internal dictionary.
- `get(key)`: Retrieves stored value by key.
- `remove(key)`: Deletes stored value by key.
- `openDevice()`: Opens serial port and starts background receive thread.
- `readDataTh(threadName, delay)`: Serial receive loop.
- `closeDevice()`: Closes serial port and marks device closed.
- `onDataReceived(data)`: Accumulates bytes, validates packet, and triggers parsing.
- `processData(length)`: Parses register payload and maps values into device dictionary.
- `sendData(data)`: Writes raw bytes to serial port.
- `readReg(regAddr, regCount)`: Sends read-register command.
- `writeReg(regAddr, sValue)`: Unlocks, writes register, then saves config.
- `get_readBytes(devid, regAddr, regCount)`: Builds read command bytes.
- `get_writeBytes(devid, regAddr, sValue)`: Builds write command bytes.
- `startLoopRead()`: Starts periodic register polling thread.
- `loopRead()`: Poll loop body.
- `stopLoopRead()`: Stops poll loop.
- `unlock()`: Sends unlock command before writes.
- `save()`: Sends save command.

### `data_collect/vb01_python_sdk/test.py`

- No functions are defined in this file. It is an executable example script that creates a `DeviceModel`, reads registers, and prints decoded values.

## Hardware

| Parameter | Value |
|-----------|-------|
| Witmotion sensor | WTVB02-485 (Modbus RTU) |
| Pixhawk link | MAVLink over serial |
| Witmotion register | `0x3C` -> VZ vibration velocity (signed 16-bit) |

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell/CMD):

```bat
run_windows.bat
```

## Run

```bash
./run.sh
# or
python app.py
```

Windows:

```bat
run_windows.bat
```

On Windows, port selection is automatic by default (`MAVLINK_PORT=AUTO`, `WTVB_PORT=AUTO`),
so the app can follow COM port changes without editing files.

To force manual ports for one run:

```bat
set MAVLINK_PORT=COM7
set WTVB_PORT=COM8
run_windows.bat
```

Dashboard URL: **http://localhost:7770**

## Network Access (Reverse Proxy)

Run once:

```bash
sudo ./setup_proxy.sh
```

By default, `setup_proxy.sh` proxies port 80 to `localhost:7777`.
If your app runs on port 7770 (default in `app.py`), update `APP_PORT` in `setup_proxy.sh` to 7770.

## Vibration Severity Thresholds

| Light | Condition | Meaning |
|-------|-----------|---------|
| Green | RMS < 2.8 mm/s | Good |
| Yellow | 2.8 to 7.1 mm/s | Acceptable |
| Red | RMS >= 7.1 mm/s | Alarm |

## Data Logging

1. Enter RPM and Load (W).
2. Click Start.
3. Click Stop, or wait for auto-stop.
4. Download latest CSV or all logs as ZIP.

File naming:

```text
vibration_RPM{rpm}_LOAD{load}W_{YYYYmmdd_HHMMSS}.csv
```

Rows contain:

- `counter`
- `unix_time`
- `iso_time`
- `az_ms2`
- `vz_mms`
- `hzz_hz`

## Offline FFT Export

Use this helper script to read saved log CSV files, compute FFT, and export:

- Graph (`*_fft.html`) for FFT visualization

```bash
# Process all CSV logs in logs/ and write results to fft/
python export_fft_csv.py logs

# Explicit batch mode for all files in logs folder (automatic per-file parameters)
python export_fft_csv.py --all-logs

# Process one file only
python export_fft_csv.py logs/vibration_RPM1400_LOAD1000W_20260410_214815.csv
```

Graph output:

- HTML plot file per log (`*_fft.html`)

Interaction in HTML graph:

- Hover any point for quick X/Y readout
- Click any point to pin a label (`X`, `Y`) and vertical reference line
- Click the same selected point again to unselect it
- You can click multiple points to keep multiple peak labels
- Default is 1 preselected peak on load

Useful options:

- `--all-logs` (process all files in logs folder individually)
- `--logs-dir logs` (choose another logs folder for `--all-logs`)
- `--column az_ms2` (default)
- `--rate 350.0` (override sample rate)
- `--window-seconds 20` (FFT window length)
- `--label-decimals 3` (peak label frequency precision)
- `--peak-count 1` (number of preselected peaks on load)
- `--peak-min-separation-hz 1.0` (minimum spacing between highlighted peaks)
- `--output-dir fft`
- `--recursive` (scan subfolders)

By default, sample rate is inferred per file from `iso_time` as average rows per second, with fallback to `unix_time` / `rate_hz` when needed.

## Configuration

Main runtime settings are in `data_collect/state.py` and can be overridden with environment variables.

Key variables:

- `MAVLINK_PORT`
- `MAVLINK_BAUD`
- `MAVLINK_IMU_RATE_HZ`
- `WTVB_PORT`
- `WTVB_BAUD_CANDIDATES`
- `WTVB_MODBUS_ADDR`
- `WTVB_SENSOR_RATE_HZ`
- `WTVB_SERIAL_TIMEOUT`
- `WTVB_CALIB_FILE` (default `calibration/wtb_calib.txt`; plain-text multiplier for decoded Witmotion VZ values)
- `MAVLINK_CALIB_FILE` (default `calibration/pixhawk_calib.txt`; plain-text gravity offset in mG)
