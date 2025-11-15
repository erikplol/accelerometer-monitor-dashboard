# Accelerometer Dashboard (Dash)

Real-time accelerometer data visualization with FFT frequency analysis using Dash/Plotly.

## Features

- **Real-time time-domain graphs** for X, Y, Z axes
- **Real-time frequency spectrum (FFT)** for each axis
- Interactive Plotly charts (zoom, pan, export)
- Normalized FFT magnitude showing actual signal amplitude
- Auto-updates every 100ms (10Hz sampling rate)

## Installation

```bash
pip install -r requirements.txt
```

## Run the App

```bash
python app.py
```

Then open your browser to: **http://localhost:8080**

## How It Works

1. Simulates accelerometer data (sin/cos waves with noise)
2. Stores last 256 samples for FFT analysis
3. Computes FFT with Hamming window and normalization
4. Updates 6 graphs in real-time (3 time + 3 frequency)

## Configuration

- `WINDOW_SIZE = 256` - FFT window size
- `SAMPLING_RATE = 10.0` Hz - Data sampling rate
- `MAX_TIME_POINTS = 100` - Time domain display points
- Update interval: 100ms in `dcc.Interval`

## What Changed from Flask

✅ **No HTML/CSS/JavaScript needed** - Pure Python  
✅ **Built-in real-time updates** - No manual WebSockets  
✅ **Interactive graphs** - Zoom, pan, export built-in  
✅ **Less code** - ~50% reduction in lines  
✅ **Better visualizations** - Plotly > Chart.js for data science
