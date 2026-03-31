import io
import os
import time
import zipfile

import dash
import numpy as np
import plotly.graph_objs as go
from dash import Dash, Input, Output, State, ctx, dcc, html
from flask import send_file, abort

from config.settings import (
    UI_INTERVAL_MS,
    MAX_DISPLAY_PTS,
    FFT_WINDOW_SECONDS,
    FFT_UPDATE_EVERY_N_INTERVALS,
    DISPLAY_SECONDS,
    HOST,
    PORT,
    DEBUG,
    DEV_TOOLS_UI,
)
from dashboard.figures import build_fft_figure, build_time_figure, CARD_BG, TICK_CLR, GRID_CLR
from sensors.pixhawk import (
    MAVLinkReader,
    get_histories,
    get_actual_rate,
    is_connected,
    start_logging,
    stop_logging,
    save_log,
    get_latest_log_path,
    get_all_log_paths,
    SAMPLING_RATE as DC_SAMPLING_RATE,
    THRESH_GREEN,
    THRESH_YELLOW,
)
from sensors.witmotion import (
    SerialReader as WitmotionReader,
    get_histories as get_witmotion_histories,
    get_latest_sample as get_witmotion_latest,
    is_connected as is_witmotion_connected,
    SAMPLING_RATE as WIT_SAMPLING_RATE,
)
from utils.fft import compute_fft

# ── App setup ─────────────────────────────────────────────────────────────────
app: Dash = dash.Dash(__name__, suppress_callback_exceptions=True)
app.index_string = '''
<!DOCTYPE html>
<html>
<head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

  *, *::before, *::after { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: #0f1117;
    height: 100%; overflow: hidden;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }

  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: #0f1117; }
  ::-webkit-scrollbar-thumb { background: #2a2d35; border-radius: 3px; }

  input[type=number]:focus {
    outline: none;
    border-color: #4f8ef7 !important;
    box-shadow: 0 0 0 3px rgba(79,142,247,0.15);
  }
  input[type=number]::-webkit-inner-spin-button,
  input[type=number]::-webkit-outer-spin-button { opacity: 0.4; }

  .btn-start:hover { filter: brightness(1.15); transform: translateY(-1px); }
  .btn-stop:hover  { filter: brightness(1.15); transform: translateY(-1px); }
  .btn-start, .btn-stop { transition: filter 0.15s, transform 0.15s; }

  @media (max-width: 1200px) {
    .top-strip { flex-wrap: wrap !important; }
    .top-strip > div.card-narrow { flex: 1 1 140px !important; min-width: 120px !important; max-width: 200px !important; }
    .top-strip > div.card-wide   { flex: 1 1 220px !important; min-width: 200px !important; }
  }
  @media (max-width: 768px) {
    .top-strip { flex-wrap: wrap !important; }
    .top-strip > div { flex: 1 1 100% !important; max-width: 100% !important; }
    .graphs-row { flex-wrap: wrap !important; overflow-y: auto !important; }
    .graphs-row > div { flex: 1 1 100% !important; min-height: 260px; }
    html, body { overflow: auto !important; height: auto !important; }
    #root > div { height: auto !important; overflow: auto !important; }
  }
</style>
</head>
<body>{%app_entry%}{%config%}{%scripts%}{%renderer%}</body>
</html>
'''
app.title = "Engine Vibration Monitor"

# ── Style helpers ─────────────────────────────────────────────────────────────
_BG        = '#0f1117'
_CARD_BG   = CARD_BG
_BORDER    = '#21262d'
_TICK_CLR  = TICK_CLR
_GRID_CLR  = GRID_CLR
_ZERO_CLR  = '#30363d'

_CARD = {
    'background':    _CARD_BG,
    'borderRadius':  12,
    'border':        f'1px solid {_BORDER}',
    'padding':       '14px 20px',
    'boxSizing':     'border-box',
}

_LABEL = {
    'color':          '#6e7681',
    'fontSize':       '0.78rem',
    'fontWeight':     600,
    'textTransform':  'uppercase',
    'letterSpacing':  '1px',
    'marginBottom':   6,
    'flexShrink':     0,
}

_INPUT = {
    'background':    '#0d1117',
    'color':         '#e6edf3',
    'border':        f'1px solid {_BORDER}',
    'borderRadius':  8,
    'padding':       '7px 11px',
    'fontSize':      '0.95rem',
    'width':         130,
    'outline':       'none',
    'boxSizing':     'border-box',
    'transition':    'border-color 0.2s',
}


def _light_style(active: bool, color: str, glow: str) -> dict:
    if active:
        return {
            'width': 40, 'height': 40, 'borderRadius': '50%',
            'backgroundColor': color,
            'boxShadow': f'0 0 0 4px rgba(255,255,255,0.06), 0 0 18px 4px {glow}',
            'transition': 'background-color 0.3s, box-shadow 0.3s',
        }
    return {
        'width': 40, 'height': 40, 'borderRadius': '50%',
        'backgroundColor': '#1c2128',
        'border': f'1px solid {_BORDER}',
        'boxShadow': 'none',
        'transition': 'background-color 0.3s, box-shadow 0.3s',
    }

# ── Readers ─────────────────────────────────────────────────────────────────
_reader = MAVLinkReader()
_reader.start()

_witmotion_reader = WitmotionReader()
_witmotion_reader.start()

# ── Download endpoints ───────────────────────────────────────────────────────
@app.server.route('/download/latest-log')
def download_latest_log():
    path = get_latest_log_path()
    if not path or not os.path.isfile(path):
        abort(404, description='No log available')
    return send_file(path, as_attachment=True)


@app.server.route('/download/all-logs')
def download_all_logs():
    paths = get_all_log_paths()
    if not paths:
        abort(404, description='No logs available')

    memfile = io.BytesIO()
    with zipfile.ZipFile(memfile, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, arcname=os.path.basename(p))
    memfile.seek(0)
    return send_file(memfile, mimetype='application/zip', as_attachment=True, download_name='logs.zip')

# ── Layout ───────────────────────────────────────────────────────────────────
app.layout = html.Div([
    html.Div([
        html.Div([
            html.Div(style={
                'width': 8, 'height': 8, 'borderRadius': '50%',
                'backgroundColor': '#58a6ff', 'marginRight': 10,
                'boxShadow': '0 0 8px rgba(88,166,255,0.7)',
            }),
            html.H1("Engine Vibration Monitor", style={
                'margin': 0, 'color': '#e6edf3',
                'fontSize': '1.2rem', 'fontWeight': 500, 'letterSpacing': '-0.2px',
            }),
        ], style={'display': 'flex', 'alignItems': 'center'}),
        html.Span(id='conn-status', style={'fontSize': '0.82rem', 'marginLeft': 18}),
        html.Button(
            "⏸ Pause",
            id='btn-pause',
            n_clicks=0,
            style={
                'marginLeft': 'auto',
                'background': '#30363d', 'color': '#e6edf3',
                'border': f'1px solid {_BORDER}', 'borderRadius': 8,
                'padding': '6px 12px', 'fontSize': '0.9rem',
                'cursor': 'pointer', 'fontFamily': 'inherit'
            }
        ),
    ], style={
        'padding': '10px 20px',
        'background': _CARD_BG,
        'borderBottom': f'1px solid {_BORDER}',
        'display': 'flex', 'alignItems': 'center', 'flexShrink': 0,
    }),

    html.Div([
        html.Div([
            html.Div("RMS VZ", style={**_LABEL, 'textAlign': 'center'}),
            html.Div([
                html.Span(id='rms-value', children='—', style={
                    'color': '#58a6ff',
                    'fontSize': '2.8rem', 'fontWeight': 300, 'lineHeight': 1,
                    'fontVariantNumeric': 'tabular-nums',
                }),
                html.Span(" mm/s", style={
                    'color': '#6e7681', 'fontSize': '0.9rem',
                    'marginLeft': 5, 'alignSelf': 'flex-end', 'paddingBottom': 3,
                }),
            ], style={'display': 'flex', 'alignItems': 'baseline', 'justifyContent': 'center'}),
        ], style={**_CARD, 'flex': '0 0 160px', 'alignSelf': 'stretch',
                  'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center',
                  'borderTop': '2px solid #58a6ff'},
           className='card-narrow'),

        html.Div([
            html.Div("Vib. Freq Z", style={**_LABEL, 'textAlign': 'center'}),
            html.Div([
                html.Span(id='recv-hz', children='—', style={
                    'color': '#3fb950',
                    'fontSize': '2.8rem', 'fontWeight': 300, 'lineHeight': 1,
                    'fontVariantNumeric': 'tabular-nums',
                }),
                html.Span(" Hz", style={
                    'color': '#6e7681', 'fontSize': '0.9rem',
                    'marginLeft': 5, 'alignSelf': 'flex-end', 'paddingBottom': 3,
                }),
            ], style={'display': 'flex', 'alignItems': 'baseline', 'justifyContent': 'center'}),
        ], style={**_CARD, 'flex': '0 0 160px', 'alignSelf': 'stretch',
                  'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center',
                  'borderTop': '2px solid #3fb950'},
           className='card-narrow'),

        html.Div([
            html.Div("Severity Level", style={**_LABEL, 'textAlign': 'center', 'marginBottom': 10}),
            html.Div([
                html.Div([
                    html.Div(id='light-green',
                             style=_light_style(True, '#3fb950', 'rgba(63,185,80,0.5)')),
                    html.Div("GOOD", style={
                        'color': '#6e7681', 'fontSize': '0.72rem', 'fontWeight': 600,
                        'letterSpacing': '0.8px', 'textAlign': 'center', 'marginTop': 5,
                    }),
                    html.Div(f"< {THRESH_GREEN}", style={
                        'color': '#484f58', 'fontSize': '0.66rem',
                        'textAlign': 'center',
                    }),
                ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

                html.Div([
                    html.Div(id='light-yellow',
                             style=_light_style(False, '#d29922', 'rgba(210,153,34,0.5)')),
                    html.Div("CAUTION", style={
                        'color': '#6e7681', 'fontSize': '0.72rem', 'fontWeight': 600,
                        'letterSpacing': '0.8px', 'textAlign': 'center', 'marginTop': 5,
                    }),
                    html.Div(f"{THRESH_GREEN}–{THRESH_YELLOW}", style={
                        'color': '#484f58', 'fontSize': '0.66rem', 'textAlign': 'center',
                    }),
                ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

                html.Div([
                    html.Div(id='light-red',
                             style=_light_style(False, '#f85149', 'rgba(248,81,73,0.5)')),
                    html.Div("ALARM", style={
                        'color': '#6e7681', 'fontSize': '0.72rem', 'fontWeight': 600,
                        'letterSpacing': '0.8px', 'textAlign': 'center', 'marginTop': 5,
                    }),
                    html.Div(f"≥ {THRESH_YELLOW}", style={
                        'color': '#484f58', 'fontSize': '0.66rem', 'textAlign': 'center',
                    }),
                ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

            ], style={
                'display': 'flex', 'flexDirection': 'row',
                'gap': 32, 'alignItems': 'flex-start', 'justifyContent': 'center',
            }),
        ], style={**_CARD, 'flex': 2, 'alignSelf': 'stretch',
                  'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center',
                  'justifyContent': 'center'},
           className='card-wide'),

        html.Div([
            html.Div("Data Logging", style={**_LABEL, 'textAlign': 'center'}),
            html.Div([
                html.Div([
                    html.Label("RPM", style={**_LABEL, 'marginBottom': 4, 'display': 'block'}),
                    dcc.Input(
                        id='rpm-input', type='number',
                        placeholder='e.g. 1600',
                        min=0, step=100, debounce=False,
                        style={**_INPUT, 'width': 110},
                    ),
                ], style={'marginRight': 8}),

                html.Div([
                    html.Label("Load (W)", style={**_LABEL, 'marginBottom': 4, 'display': 'block'}),
                    dcc.Input(
                        id='load-input', type='number',
                        placeholder='e.g. 3000',
                        min=0, step=500, debounce=False,
                        style={**_INPUT, 'width': 110},
                    ),
                ], style={'marginRight': 14}),

                html.Div([
                    html.Label('\u00a0', style={'display': 'block', 'marginBottom': 4, 'fontSize': '0.78rem'}),
                    html.Div([
                        html.Button("▶  Start", id='btn-start-log', n_clicks=0,
                            className='btn-start',
                            style={
                                'background': 'linear-gradient(135deg, #238636, #2ea043)',
                                'color': '#fff', 'border': '1px solid #2ea043',
                                'borderRadius': 8, 'padding': '7px 16px',
                                'fontSize': '0.9rem', 'cursor': 'pointer',
                                'fontWeight': 500, 'marginRight': 8,
                                'fontFamily': 'inherit',
                            }),
                        html.Button("■  Stop", id='btn-stop-log', n_clicks=0,
                            className='btn-stop',
                            style={
                                'background': 'linear-gradient(135deg, #b62324, #da3633)',
                                'color': '#fff', 'border': '1px solid #da3633',
                                'borderRadius': 8, 'padding': '7px 16px',
                                'fontSize': '0.9rem', 'cursor': 'pointer',
                                'fontWeight': 500,
                                'fontFamily': 'inherit',
                            }),
                    ], style={'display': 'flex', 'alignItems': 'center'}),
                ]),

                html.Div(id='log-status', children='', style={
                    'marginLeft': 12, 'color': '#6e7681',
                    'fontSize': '0.8rem', 'alignSelf': 'flex-end', 'paddingBottom': 2,
                }),
                html.Div([
                    html.A("⬇ Download last log", href='/download/latest-log', target='_blank', style={
                        'color': '#58a6ff', 'textDecoration': 'none', 'fontSize': '0.86rem',
                        'marginRight': 10,
                    }),
                    html.A("⬇ Download all logs", href='/download/all-logs', target='_blank', style={
                        'color': '#58a6ff', 'textDecoration': 'none', 'fontSize': '0.86rem',
                    }),
                ], style={'display': 'flex', 'alignItems': 'center', 'marginLeft': 12, 'marginTop': 6}),
            ], style={
                'display': 'flex', 'alignItems': 'flex-end',
                'justifyContent': 'center', 'flexWrap': 'nowrap', 'marginTop': 8,
            }),
        ], style={**_CARD, 'flex': 2, 'alignSelf': 'stretch',
                  'display': 'flex', 'flexDirection': 'column',
                  'justifyContent': 'center', 'alignItems': 'center'},
           className='card-wide'),

    ], className='top-strip', style={
        'display': 'flex', 'padding': '8px 14px 6px',
        'gap': 8, 'flexShrink': 0, 'alignItems': 'stretch', 'flexWrap': 'wrap',
    }),

    html.Div([
        html.Div([
            html.Div("VZ · Real Time", style={**_LABEL, 'marginBottom': 4}),
            dcc.Graph(
                id='vz-time-graph',
                style={'flex': 1, 'minHeight': 0},
                config={
                    'displayModeBar': True,
                    'displaylogo': False,
                    'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'autoScale2d', 'hoverCompareCartesian', 'hoverClosestCartesian'],
                    'toImageButtonOptions': {'format': 'png', 'filename': 'vz_time', 'height': 480, 'width': 800, 'scale': 1},
                    'responsive': True,
                },
            ),
        ], style={
            **_CARD, 'flex': 1,
            'display': 'flex', 'flexDirection': 'column', 'minHeight': 0,
        }),

        html.Div([
            html.Div("Frequency Spectrum · FFT", style={**_LABEL, 'marginBottom': 4}),
            dcc.Graph(
                id='fft-graph',
                style={'flex': 1, 'minHeight': 0},
                config={
                    'displayModeBar': True,
                    'displaylogo': False,
                    'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'autoScale2d', 'hoverCompareCartesian', 'hoverClosestCartesian'],
                    'toImageButtonOptions': {'format': 'png', 'filename': 'fft_spectrum_vz', 'height': 480, 'width': 800, 'scale': 1},
                    'responsive': True,
                },
            ),
        ], style={
            **_CARD, 'flex': 1,
            'display': 'flex', 'flexDirection': 'column', 'minHeight': 0,
        }),

    ], className='graphs-row', style={
        'display': 'flex', 'padding': '6px 14px 10px',
        'gap': 8, 'flex': 1, 'minHeight': 0, 'overflow': 'hidden',
    }),

    dcc.Store(id='log-store', data={'active': False, 'rpm': 0, 'load': 0, 'start_time': 0}),
    dcc.Store(id='view-store', data={'paused': False}),
    dcc.Interval(id='interval-component', interval=UI_INTERVAL_MS, n_intervals=0, disabled=False),

], style={
    'fontFamily': '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    'backgroundColor': _BG,
    'height':          '100vh',
    'margin':          0,
    'display':         'flex',
    'flexDirection':   'column',
    'overflow':        'hidden',
})


# ── Callbacks ─────────────────────────────────────────────────────────────────
@app.callback(
    [Output('interval-component', 'disabled'),
     Output('btn-pause', 'children'),
     Output('view-store', 'data')],
    Input('btn-pause', 'n_clicks'),
    State('view-store', 'data'),
    prevent_initial_call=True,
)
def toggle_pause(n_clicks, view_data):
    view_data = view_data or {}
    paused = not view_data.get('paused', False)
    label = "▶ Resume" if paused else "⏸ Pause"
    return paused, label, {'paused': paused}


@app.callback(
    [Output('vz-time-graph', 'figure'),
     Output('fft-graph',     'figure'),
     Output('rms-value',     'children'),
     Output('recv-hz',       'children'),
     Output('light-red',     'style'),
     Output('light-yellow',  'style'),
     Output('light-green',   'style'),
     Output('conn-status',   'children'),
     Output('conn-status',   'style')],
    Input('interval-component', 'n_intervals'),
)
def update_dashboard(n):
    h_pix = get_histories()
    h_wit = get_witmotion_histories()
    wit_latest = get_witmotion_latest()

    az_all         = h_pix.get('az_ms2', [])
    vz_pix_raw     = h_pix.get('vz_raw', [])
    rel_pix        = h_pix.get('rel_s', [])

    vz_display_all = h_wit.get('vz', [])
    vz_raw_all     = h_wit.get('vz', [])
    hzz_all        = h_wit.get('hzz', [])
    rel_all        = h_wit.get('rel_s', [])

    if (not rel_all) and wit_latest:
        vz_sample = float(wit_latest.get('vz_mm_s', 0.0))
        vz_display_all = [vz_sample]
        vz_raw_all     = [vz_sample]
        rel_all        = [0.0]

    filtered = [
        (vz, vz_raw, t)
        for vz, vz_raw, t in zip(vz_display_all, vz_raw_all, rel_all)
    ]
    if filtered and DISPLAY_SECONDS > 0:
        latest_t = filtered[-1][2]
        cutoff = latest_t - DISPLAY_SECONDS
        filtered = [p for p in filtered if p[2] >= cutoff]
    if filtered:
        vz_display_all = [p[0] for p in filtered]
        vz_raw_all     = [p[1] for p in filtered]
        rel_all        = [p[2] for p in filtered]
    else:
        vz_display_all, vz_raw_all, rel_all = [], [], []

    if len(vz_display_all) > MAX_DISPLAY_PTS:
        step = max(1, len(vz_display_all) // MAX_DISPLAY_PTS)
        vz_display = vz_display_all[::step]
        rel = rel_all[::step]
    else:
        vz_display = vz_display_all
        rel = rel_all

    pix_connected = is_connected()
    wit_connected = is_witmotion_connected()
    actual_rate = get_actual_rate()
    conn_label = (
        f'● Witmotion: {"Connected" if wit_connected else "Disconnected"} | '
        f'Pixhawk: {"Connected" if pix_connected else "Disconnected"} @ {actual_rate:.1f} Hz'
    )
    EMPTY_STYLE = {'color': '#f85149', 'fontSize': '0.82rem', 'marginLeft': 18}
    OK_STYLE    = {'color': '#3fb950', 'fontSize': '0.82rem', 'marginLeft': 18}
    conn_style = OK_STYLE if (pix_connected and wit_connected) else EMPTY_STYLE

    vz_latest = float(wit_latest.get('vz_mm_s', 0.0)) if wit_latest else 0.0
    hzz_latest = float(wit_latest.get('hzz_hz', 0.0)) if wit_latest else 0.0

    n_1s = max(1, int(WIT_SAMPLING_RATE))
    vz_window = vz_raw_all[-n_1s:] if vz_raw_all else []
    if vz_window:
        rms = float(np.sqrt(np.mean(np.array(vz_window) ** 2)))
    else:
        rms = abs(vz_latest)
    rms_label = f"{rms:.2f}" if rms > 0 else "—"

    dominant_hz = hzz_latest if hzz_latest > 0 else (hzz_all[-1] if hzz_all else 0.0)
    hz_label = f"{dominant_hz:.1f}" if dominant_hz > 0 else "—"

    is_red    = rms >= THRESH_YELLOW
    is_yellow = THRESH_GREEN <= rms < THRESH_YELLOW
    is_green  = rms < THRESH_GREEN

    style_red    = _light_style(is_red,    '#f85149', 'rgba(248,81,73,0.55)')
    style_yellow = _light_style(is_yellow, '#d29922', 'rgba(210,153,34,0.55)')
    style_green  = _light_style(is_green,  '#3fb950', 'rgba(63,185,80,0.55)')

    if n % 25 == 0:
        pix_vz_latest = vz_pix_raw[-1] if vz_pix_raw else 0.0
        wit_vz_latest = vz_raw_all[-1] if vz_raw_all else 0.0
        print(f"[VZ] Pixhawk={pix_vz_latest:+.3f} mm/s | Witmotion={wit_vz_latest:+.3f} mm/s")

    time_fig = build_time_figure(rel, vz_display)

    fft_fig = go.Figure(layout=go.Layout(plot_bgcolor=_CARD_BG, paper_bgcolor=_CARD_BG))
    effective_rate = actual_rate if actual_rate > 0 else DC_SAMPLING_RATE
    n_fft_window = max(64, int(effective_rate * FFT_WINDOW_SECONDS))
    should_update_fft = (n % FFT_UPDATE_EVERY_N_INTERVALS == 0)
    if len(vz_pix_raw) >= n_fft_window and should_update_fft:
        segment = np.array(vz_pix_raw[-n_fft_window:], dtype=float)
        segment = segment - segment.mean()
        freqs, fft_vals = compute_fft(segment, effective_rate)
        x_max = max(effective_rate / 2.0, (dominant_hz or 0) * 1.2, 5.0)
        fft_fig = build_fft_figure(freqs, fft_vals, x_max)
    else:
        # keep empty plot but stable axes
        x_max = max(effective_rate / 2.0, (dominant_hz or 0) * 1.2, 5.0)
        fft_fig = build_fft_figure([], [], x_max)

    return (
        time_fig, fft_fig, rms_label, hz_label,
        style_red, style_yellow, style_green,
        conn_label, conn_style,
    )


LOG_DURATION_S = 30


def _do_stop(log_data, auto=False):
    _reader.pause()
    try:
        data     = stop_logging()
        rpm_val  = log_data.get('rpm',  0)
        load_val = log_data.get('load', 0)
        path     = save_log(rpm_val, load_val, data)
        fname    = os.path.basename(path)
    finally:
        _reader.resume()
    prefix = '✔ Auto-saved' if auto else '✔ Saved'
    return (
        f"{prefix} {len(data)} samples — {fname}",
        {'active': False, 'rpm': rpm_val, 'load': load_val, 'start_time': 0},
    )


@app.callback(
    [Output('log-status', 'children'),
     Output('log-store',  'data')],
    [Input('btn-start-log',       'n_clicks'),
     Input('btn-stop-log',        'n_clicks'),
     Input('interval-component',  'n_intervals')],
    [State('rpm-input',  'value'),
     State('load-input', 'value'),
     State('log-store',  'data')],
    prevent_initial_call=True,
)
def handle_logging(n_start, n_stop, n_intervals, rpm, load_w, log_data):
    triggered = ctx.triggered_id
    active    = log_data.get('active', False)

    if triggered == 'btn-start-log':
        if active:
            return "⚠ Already recording — stop first.", log_data
        rpm_val  = int(rpm)    if rpm    is not None else 0
        load_val = int(load_w) if load_w is not None else 0
        start_logging()
        return (
            f"● Recording…  {LOG_DURATION_S}s  |  RPM = {rpm_val}  |  Load = {load_val} W",
            {'active': True, 'rpm': rpm_val, 'load': load_val, 'start_time': time.time()},
        )

    if triggered == 'btn-stop-log':
        if not active:
            return "⚠ No active recording.", log_data
        return _do_stop(log_data)

    if triggered == 'interval-component':
        if not active:
            return dash.no_update, dash.no_update
        elapsed   = time.time() - log_data.get('start_time', time.time())
        remaining = LOG_DURATION_S - elapsed
        if remaining <= 0:
            return _do_stop(log_data, auto=True)
        rpm_val  = log_data.get('rpm',  0)
        load_val = log_data.get('load', 0)
        return (
            f"● Recording…  {int(remaining)}s left  |  RPM = {rpm_val}  |  Load = {load_val} W",
            dash.no_update,
        )

    return dash.no_update, dash.no_update


def serve():
    app.run(debug=DEBUG, dev_tools_ui=DEV_TOOLS_UI, use_reloader=False, host=HOST, port=PORT)


if __name__ == '__main__':
    serve()
