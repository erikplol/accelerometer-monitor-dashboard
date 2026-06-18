import dash
import os

from data_collect.data_collect import (
    MAVLinkReader,
    WitmotionReader,
    SAMPLING_RATE as DC_SAMPLING_RATE,
    THRESH_GREEN,
    THRESH_YELLOW,
)
from dashboard.callbacks import register_callbacks
from dashboard.layout import create_layout
from dashboard.theme import APP_INDEX_STRING

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.index_string = APP_INDEX_STRING
app.title = "Engine Vibration Monitor"

SAMPLING_RATE = DC_SAMPLING_RATE
# Windows needs longer intervals to prevent connection issues
# Pi 5 needs a longer interval — 100 ms forces Dash to rebuild 2 Plotly figures
# 10× per second which saturates a single CPU core. 250 ms is a good balance.
UI_INTERVAL_MS = 400 if os.name == 'nt' else 250
MAX_DISPLAY_PTS = 200  # points sent to browser per callback tick
FFT_WINDOW_SECONDS = 15.0

_reader = MAVLinkReader()
try:
    _reader.start()
except Exception as e:
    print(f'[Warning] Failed to start Pixhawk reader: {e}')

_wit_reader = WitmotionReader()
try:
    _wit_reader.start()
except Exception as e:
    print(f'[Warning] Failed to start Witmotion reader: {e}')

app.layout = create_layout(THRESH_GREEN, THRESH_YELLOW, UI_INTERVAL_MS)

register_callbacks(
    app=app,
    reader=_reader,
    wit_reader=_wit_reader,
    sampling_rate=SAMPLING_RATE,
    ui_interval_ms=UI_INTERVAL_MS,
    max_display_pts=MAX_DISPLAY_PTS,
    fft_window_seconds=FFT_WINDOW_SECONDS,
    thresh_green=THRESH_GREEN,
    thresh_yellow=THRESH_YELLOW,
)


if __name__ == '__main__':
    print('[Dashboard] Starting Engine Vibration Monitor on http://localhost:7777')
    print('[Dashboard] Press Ctrl+C to stop')
    # Disable debug and hot reload on Windows to prevent connection issues
    app.run(
        debug=False,
        dev_tools_ui=False,
        use_reloader=False,
        host='0.0.0.0',
        port=7777,
        threaded=True,
    )
