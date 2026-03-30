import os
import time
import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.graph_objs as go
import numpy as np

try:
    from gpiozero import LED
    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    LED = None
    _GPIO_AVAILABLE = False

from data_collect import (
    MAVLinkReader,
    get_histories,
    get_actual_rate,
    is_connected,
    start_logging,
    stop_logging,
    save_log,
    SAMPLING_RATE as DC_SAMPLING_RATE,
)

# ── App setup ─────────────────────────────────────────────────────────────────
app = dash.Dash(__name__, suppress_callback_exceptions=True)
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

  /* ── Scrollbar ──────────────────────────────────────── */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: #0f1117; }
  ::-webkit-scrollbar-thumb { background: #2a2d35; border-radius: 3px; }

  /* ── Input focus ring ───────────────────────────────── */
  input[type=number]:focus {
    outline: none;
    border-color: #4f8ef7 !important;
    box-shadow: 0 0 0 3px rgba(79,142,247,0.15);
  }
  input[type=number]::-webkit-inner-spin-button,
  input[type=number]::-webkit-outer-spin-button { opacity: 0.4; }

  /* ── Button hover effects ───────────────────────────── */
  .btn-start:hover { filter: brightness(1.15); transform: translateY(-1px); }
  .btn-stop:hover  { filter: brightness(1.15); transform: translateY(-1px); }
  .btn-start, .btn-stop { transition: filter 0.15s, transform 0.15s; }

  /* ── Responsive ─────────────────────────────────────── */
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

SAMPLING_RATE   = DC_SAMPLING_RATE
UI_INTERVAL_MS  = 350   # UI refresh rate (ms) — decoupled from sampling rate
MAX_DISPLAY_PTS = 500   # max points plotted per graph
FFT_WINDOW_SECONDS = 5.0
FFT_UPDATE_EVERY_N_INTERVALS = 3  # recompute FFT every ~1.05s with current interval

_fft_cache_x = []
_fft_cache_y = []
_fft_cache_signature = None

# Thresholds for velocity RMS (mm/s) - ISO 10816 based
THRESH_GREEN  = 2.8    # below  → green  (good)
THRESH_YELLOW = 7.1    # below  → yellow (acceptable), above → red (alarm)

_reader = MAVLinkReader()
_reader.start()

# ── GPIO traffic lights ───────────────────────────────────────────────────────
if _GPIO_AVAILABLE:
    led_red = LED(17)
    led_yellow = LED(27)
    led_green = LED(22)
else:
    led_red = led_yellow = led_green = None


def _set_gpio_lights(red: bool, yellow: bool, green: bool) -> None:
    if not _GPIO_AVAILABLE:
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

# ── Style helpers ─────────────────────────────────────────────────────────────
_BG        = '#0f1117'
_CARD_BG   = '#161b22'
_BORDER    = '#21262d'
_TICK_CLR  = '#8b949e'
_GRID_CLR  = '#21262d'
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


# ── Layout ────────────────────────────────────────────────────────────────────
app.layout = html.Div([

    # ── Header ──────────────────────────────────────────────────────────
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
    ], style={
        'padding': '10px 20px',
        'background': _CARD_BG,
        'borderBottom': f'1px solid {_BORDER}',
        'display': 'flex', 'alignItems': 'center', 'flexShrink': 0,
    }),

    # ── Top strip: RMS | Freq | Severity | Logging ─────────────────────
    html.Div([

        # RMS card (Velocity from integrated acceleration)
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

        # Frequency card
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

        # FFT Sum card
        html.Div([
            html.Div("FFT Sum", style={**_LABEL, 'textAlign': 'center'}),
            html.Div([
                html.Span(id='fft-sum', children='—', style={
                    'color': '#d29922',
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
                  'borderTop': '2px solid #d29922'},
           className='card-narrow'),

        # Traffic-light card
        html.Div([
            html.Div("Severity Level", style={**_LABEL, 'textAlign': 'center', 'marginBottom': 10}),
            html.Div([
                # Green
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

                # Yellow
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

                # Red
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

        # Logging card
        html.Div([
            html.Div("Data Logging", style={**_LABEL, 'textAlign': 'center'}),
            html.Div([
                # RPM input
                html.Div([
                    html.Label("RPM", style={**_LABEL, 'marginBottom': 4, 'display': 'block'}),
                    dcc.Input(
                        id='rpm-input', type='number',
                        placeholder='e.g. 1600',
                        min=0, step=100, debounce=False,
                        style={**_INPUT, 'width': 110},
                    ),
                ], style={'marginRight': 8}),

                # LOAD input
                html.Div([
                    html.Label("Load (W)", style={**_LABEL, 'marginBottom': 4, 'display': 'block'}),
                    dcc.Input(
                        id='load-input', type='number',
                        placeholder='e.g. 3000',
                        min=0, step=500, debounce=False,
                        style={**_INPUT, 'width': 110},
                    ),
                ], style={'marginRight': 14}),

                # Buttons
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

    # ── Graphs row ──────────────────────────────────────────────────────
    html.Div([

        # Graph 1 – VZ vs time
        html.Div([
            html.Div("VZ · Real Time", style={**_LABEL, 'marginBottom': 4}),
            dcc.Graph(
                id='vz-time-graph',
                style={'flex': 1, 'minHeight': 0},
                config={'displayModeBar': False, 'responsive': True},
            ),
        ], style={
            **_CARD, 'flex': 1,
            'display': 'flex', 'flexDirection': 'column', 'minHeight': 0,
        }),

        # Graph 2 – FFT
        html.Div([
            html.Div("Frequency Spectrum · FFT", style={**_LABEL, 'marginBottom': 4}),
            dcc.Graph(
                id='fft-graph',
                style={'flex': 1, 'minHeight': 0},
                config={'displayModeBar': False, 'responsive': True},
            ),
        ], style={
            **_CARD, 'flex': 1,
            'display': 'flex', 'flexDirection': 'column', 'minHeight': 0,
        }),

    ], className='graphs-row', style={
        'display': 'flex', 'padding': '6px 14px 10px',
        'gap': 8, 'flex': 1, 'minHeight': 0, 'overflow': 'hidden',
    }),

    # ── Stores & interval ────────────────────────────────────────────────
    dcc.Store(id='log-store', data={'active': False, 'rpm': 0, 'load': 0, 'start_time': 0}),
    dcc.Interval(id='interval-component', interval=UI_INTERVAL_MS, n_intervals=0),

], style={
    'fontFamily': '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    'backgroundColor': _BG,
    'height':          '100vh',
    'margin':          0,
    'display':         'flex',
    'flexDirection':   'column',
    'overflow':        'hidden',
})

# ── Shared plot config ─────────────────────────────────────────────────────────
_YAXIS_VEL = dict(
    gridcolor=_GRID_CLR, color=_TICK_CLR, zeroline=True,
    zerolinecolor=_ZERO_CLR, zerolinewidth=1,
    title=dict(text='mm/s', font=dict(size=10, color=_TICK_CLR)),
    tickfont=dict(size=10),
    showgrid=True,
)




# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    [Output('vz-time-graph', 'figure'),
     Output('fft-graph',     'figure'),
     Output('rms-value',     'children'),
     Output('recv-hz',       'children'),
     Output('fft-sum',       'children'),
     Output('light-red',     'style'),
     Output('light-yellow',  'style'),
     Output('light-green',   'style'),
     Output('conn-status',   'children'),
     Output('conn-status',   'style')],
    Input('interval-component', 'n_intervals'),
)
def update_dashboard(n):
    global _fft_cache_x, _fft_cache_y, _fft_cache_signature

    h      = get_histories()
    vz_all  = h.get('vz', [])             # Z velocity in mm/s (integrated)
    rel_all = h['rel_s']

    # Downsample for display — keeps the browser responsive
    if len(vz_all) > MAX_DISPLAY_PTS:
        step = len(vz_all) // MAX_DISPLAY_PTS
        vz_display = vz_all[::step]
        rel = rel_all[::step]
    else:
        vz_display = vz_all
        rel = rel_all

    VZ_COLOR    = '#58a6ff'
    EMPTY_STYLE = {'color': '#f85149', 'fontSize': '0.82rem', 'marginLeft': 18}
    OK_STYLE    = {'color': '#3fb950', 'fontSize': '0.82rem', 'marginLeft': 18}

    # ── Connection ──────────────────────────────────────────────────────
    connected  = is_connected()
    actual_rate = get_actual_rate()
    if connected:
        conn_label = f'● Connected @ {actual_rate:.1f} Hz'
    else:
        conn_label = '● Disconnected'
    conn_style = OK_STYLE if connected else EMPTY_STYLE

    # ── Velocity RMS over last 1 second (mm/s) ─────────────────────────────
    n_1s = max(1, int(SAMPLING_RATE))
    vz_window = vz_all[-n_1s:] if vz_all else []
    if vz_window:
        rms = float(np.sqrt(np.mean(np.array(vz_window) ** 2)))
        rms_label = f"{rms:.2f}"
    else:
        rms = 0.0
        rms_label = "—"

    # ── Dominant frequency from FFT peak ────────────────────────────────
    # Calculate from cached FFT data instead of sensor register
    dominant_hz = 0.0
    fft_sum = 0.0
    if _fft_cache_x and _fft_cache_y:
        peak_idx = int(np.argmax(_fft_cache_y))
        if peak_idx < len(_fft_cache_x):
            dominant_hz = _fft_cache_x[peak_idx]
        fft_sum = sum(_fft_cache_y)
    hz_label = f"{dominant_hz:.1f}" if dominant_hz > 0 else "—"
    fft_sum_label = f"{fft_sum:.2f}" if fft_sum > 0 else "—"

    # ── Traffic light ──────────────────────────────────────────────────
    is_red    = rms >= THRESH_YELLOW
    is_yellow = THRESH_GREEN <= rms < THRESH_YELLOW
    is_green  = rms < THRESH_GREEN

    _set_gpio_lights(is_red, is_yellow, is_green)

    style_red    = _light_style(is_red,    '#f85149', 'rgba(248,81,73,0.55)')
    style_yellow = _light_style(is_yellow, '#d29922', 'rgba(210,153,34,0.55)')
    style_green  = _light_style(is_green,  '#3fb950', 'rgba(63,185,80,0.55)')

    # ── Graph 1: VZ vs time ────────────────────────────────────────────
    _xaxis_s = go.layout.XAxis(
        gridcolor=_GRID_CLR, color=_TICK_CLR, zeroline=False,
        title=go.layout.xaxis.Title(text='s', font=dict(size=10, color=_TICK_CLR)),
        tickfont=dict(size=10),
    )
    _yaxis_v = go.layout.YAxis(
        gridcolor=_GRID_CLR, color=_TICK_CLR, zeroline=True,
        zerolinecolor=_ZERO_CLR, zerolinewidth=1,
        title=dict(text='mm/s', font=dict(size=10, color=_TICK_CLR)),
        tickfont=dict(size=10),
        showgrid=True,
    )
    time_fig = go.Figure(
        data=[
            go.Scattergl(
                x=rel, y=vz_display,
                mode='lines', line=dict(color=VZ_COLOR, width=1.5),
                name='VZ',
                hovertemplate='%{y:.2f} mm/s<extra></extra>',
            )
        ],
        layout=go.Layout(
            plot_bgcolor=_CARD_BG, paper_bgcolor=_CARD_BG,
            margin=dict(l=52, r=12, t=10, b=32),
            hovermode='x unified',
            font=dict(color=_TICK_CLR, size=10),
            xaxis=_xaxis_s,
            yaxis=_yaxis_v,
            showlegend=False,
            uirevision='vz-time',
        ),
    )

    # ── Graph 2: FFT (velocity spectrum in mm/s) ─────────────────────────

    fft_traces = []
    fft_shapes = []

    # Use actual sampling rate for correct frequency calculation
    effective_rate = actual_rate if actual_rate > 0 else SAMPLING_RATE
    n_fft_window = max(64, int(effective_rate * FFT_WINDOW_SECONDS))
    should_update_fft = (n % FFT_UPDATE_EVERY_N_INTERVALS == 0)
    if len(vz_all) >= n_fft_window and should_update_fft:
        # FFT uses Z-axis velocity in mm/s
        N = n_fft_window
        segment = np.array(vz_all[-N:], dtype=float)
        segment = segment - segment.mean()

        window        = np.hanning(N)
        coherent_gain = float(window.mean()) if N > 0 else 1.0
        spectrum      = np.fft.rfft(segment * window)
        fft_vals      = np.abs(spectrum) / (N * coherent_gain)
        freqs         = np.fft.rfftfreq(N, d=1.0 / effective_rate)  # Use actual rate

        if len(fft_vals) > 2:
            fft_vals[1:-1] *= 2.0

        # Drop DC bin (index 0)
        freqs    = freqs[1:]
        fft_vals = fft_vals[1:]

        _fft_cache_x = freqs.tolist()
        _fft_cache_y = fft_vals.tolist()
        _fft_cache_signature = (len(vz_all), vz_all[-1], N)

    if _fft_cache_signature is not None and _fft_cache_x:
        fft_traces.append(go.Scattergl(
            x=_fft_cache_x, y=_fft_cache_y,
            mode='lines', fill='tozeroy',
            line=dict(color=VZ_COLOR, width=1.5),
            fillcolor='rgba(88,166,255,0.15)',
            name='FFT',
            hovertemplate='%{x:.2f} Hz  %{y:.3f} mm/s<extra></extra>',
        ))

    x_max = max(effective_rate / 2.0, (dominant_hz or 0) * 1.2, 5.0)

    fft_fig = go.Figure(
        data=fft_traces,
        layout=go.Layout(
            plot_bgcolor=_CARD_BG, paper_bgcolor=_CARD_BG,
            margin=dict(l=52, r=12, t=10, b=32),
            hovermode='x unified',
            font=dict(color=_TICK_CLR, size=10),
            shapes=fft_shapes,
            xaxis=go.layout.XAxis(
                gridcolor=_GRID_CLR, color=_TICK_CLR, zeroline=False,
                range=[0, x_max],
                title=go.layout.xaxis.Title(
                    text='Hz', font=dict(size=10, color=_TICK_CLR)
                ),
                tickfont=dict(size=10),
            ),
            yaxis=go.layout.YAxis(
                **{
                    **_YAXIS_VEL,
                    'title': dict(text='mm/s', font=dict(size=10, color=_TICK_CLR)),
                    'rangemode': 'nonnegative',
                },
            ),
            showlegend=False,
            uirevision='fft',
        ),
    )

    return (
        time_fig, fft_fig, rms_label, hz_label, fft_sum_label,
        style_red, style_yellow, style_green,
        conn_label, conn_style,
    )


LOG_DURATION_S = 30


def _do_stop(log_data, auto=False):
    """Stop recording, pause the serial reader, save, then resume."""
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


if __name__ == '__main__':
    app.run(debug=True, dev_tools_ui=False, use_reloader=False, host='0.0.0.0', port=7777)
