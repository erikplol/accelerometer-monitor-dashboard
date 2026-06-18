import dash

from data_collect.data_collect import (
    SAMPLING_RATE as DC_SAMPLING_RATE,
    THRESH_GREEN,
    THRESH_YELLOW,
)
from dashboard.callbacks_replay import register_callbacks
from dashboard.layout_replay import create_layout
from dashboard.theme import APP_INDEX_STRING

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.index_string = APP_INDEX_STRING
app.title = 'Engine Vibration Monitor (Replay)'

SAMPLING_RATE = DC_SAMPLING_RATE
UI_INTERVAL_MS = 100
MAX_DISPLAY_PTS = 150
FFT_WINDOW_SECONDS = 15.0

app.layout = create_layout(THRESH_GREEN, THRESH_YELLOW, UI_INTERVAL_MS)

register_callbacks(
    app=app,
    sampling_rate=SAMPLING_RATE,
    ui_interval_ms=UI_INTERVAL_MS,
    max_display_pts=MAX_DISPLAY_PTS,
    fft_window_seconds=FFT_WINDOW_SECONDS,
    thresh_green=THRESH_GREEN,
    thresh_yellow=THRESH_YELLOW,
)


if __name__ == '__main__':
    app.run(debug=True, dev_tools_ui=False, use_reloader=False, host='0.0.0.0', port=7778)
