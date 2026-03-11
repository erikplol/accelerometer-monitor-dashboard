import io
import csv as _csv
import math
import os
import random
import threading
import time
from collections import deque

import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.graph_objs as go
import numpy as np

# ── Simulation configuration ──────────────────────────────────────────────────
SAMPLING_RATE  = 50.0          # Hz  (Nyquist = 25 Hz — safely captures 5–15 Hz signals)
MAX_TIME_PTS   = 3000          # 60-second rolling window @ 50 Hz
LOG_DIR        = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# Fundamental at 8 Hz + 2nd harmonic (16 Hz) + 3rd harmonic (24 Hz), typical engine pattern
_SIM_COMPS     = [(8.0, 5.0), (16.0, 2.5), (24.0, 1.0)]
_SIM_NOISE_STD = 0.4   # mm/s white noise std-dev
# HZZ drifts: dominant sine freq ± 2 Hz modulated at 0.03 Hz, plus per-sample jitter
_SIM_HZZ_BASE  = 8.0   # Hz — centre of the reported sensor frequency

# ── Shared data buffers ────────────────────────────────────────────────────────
_vz_history  = deque(maxlen=MAX_TIME_PTS)
_hzz_history = deque(maxlen=MAX_TIME_PTS)
_ts_history  = deque(maxlen=MAX_TIME_PTS)
_lock        = threading.Lock()
_connected   = False

# ── Logging state ─────────────────────────────────────────────────────────────
_log_active  = False
_log_buffer  = []
_log_counter = 0
_log_lock    = threading.Lock()


class SimulatedReader(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name='SimulatedReader')
        self._stop_event = threading.Event()

    def run(self):
        global _connected, _log_active, _log_buffer, _log_counter
        _connected = True
        print('[SimulatedReader] Started — generating synthetic vibration data.')
        t_start = time.time()
        while not self._stop_event.is_set():
            t0    = time.time()
            t_rel = t0 - t_start
            vz    = sum(a * math.sin(2 * math.pi * f * t_rel) for f, a in _SIM_COMPS)
            vz   += random.gauss(0.0, _SIM_NOISE_STD)
            # Sensor-reported dominant frequency: slow ±2 Hz drift + ±0.3 Hz jitter
            hzz   = _SIM_HZZ_BASE + 2.0 * math.sin(2 * math.pi * 0.03 * t_rel)
            hzz  += random.gauss(0.0, 0.3)
            hzz   = max(5.0, min(15.0, hzz))
            with _lock:
                _vz_history.append(vz)
                _hzz_history.append(hzz)
                _ts_history.append(t0)
            with _log_lock:
                if _log_active:
                    _log_counter += 1
                    time_str = time.strftime('%d %b %Y  %H:%M:%S', time.localtime(t0))
                    _log_buffer.append((_log_counter, time_str, vz))
            elapsed = time.time() - t0
            sleep_t = (1.0 / SAMPLING_RATE) - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)
        _connected = False
        print('[SimulatedReader] Stopped.')

    def stop(self):
        self._stop_event.set()


def get_histories() -> dict:
    with _lock:
        vz  = list(_vz_history)
        hzz = list(_hzz_history)
        ts  = list(_ts_history)
    rel = [t - ts[0] for t in ts] if ts else []
    return {'vz': vz, 'hzz': hzz, 'ts': ts, 'rel_s': rel}


def is_connected() -> bool:
    return _connected


def start_logging() -> None:
    global _log_active, _log_buffer, _log_counter
    with _log_lock:
        _log_active  = True
        _log_buffer  = []
        _log_counter = 0


def stop_logging() -> list:
    global _log_active
    with _log_lock:
        _log_active = False
        data        = list(_log_buffer)
    return data


def save_log(rpm: int, load_w: int, data: list) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename  = f'vibration_SIM_RPM{rpm}_LOAD{load_w}W_{timestamp}.csv'
    filepath  = os.path.join(LOG_DIR, filename)
    vz_vals   = [row[2] for row in data] if data else []
    rms       = float(np.sqrt(np.mean(np.array(vz_vals) ** 2))) if vz_vals else 0.0
    with open(filepath, 'w', newline='') as f:
        writer = _csv.writer(f)
        writer.writerow(['# Engine Vibration Log (Simulated)'])
        writer.writerow(['# RPM',        rpm])
        writer.writerow(['# Load (W)',   load_w])
        writer.writerow(['# Timestamp',  timestamp])
        writer.writerow(['# Samples',    len(data)])
        writer.writerow(['# RMS (mm/s)', f'{rms:.6f}'])
        writer.writerow([])
        writer.writerow(['counter', 'time', 'vz_mm_s'])
        for entry in data:
            writer.writerow([entry[0], entry[1], f'{entry[2]:.6f}'])
    print(f'[SimLogger] Saved → {filepath}  ({len(data)} samples, RMS={rms:.4f} mm/s)')
    return filepath

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
app.title = "Engine Vibration Monitor · Simulation"

INTERVAL_MS = 1000.0 / SAMPLING_RATE

# ISO 10816 vibration severity thresholds (mm/s RMS)
THRESH_GREEN  = 2.8    # below  → green  (good)
THRESH_YELLOW = 7.1    # below  → yellow (acceptable), above → red (alarm)

_reader = SimulatedReader()
_reader.start()

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

        # RMS card
        html.Div([
            html.Div("RMS Velocity", style={**_LABEL, 'textAlign': 'center'}),
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
            html.Div("Velocity · Real Time", style={**_LABEL, 'marginBottom': 4}),
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
    dcc.Store(id='log-store', data={'active': False, 'rpm': 0, 'load': 0}),
    dcc.Interval(id='interval-component', interval=INTERVAL_MS, n_intervals=0),
    dcc.Download(id='download-csv'),

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
    rangemode='tozero',
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
     Output('light-red',     'style'),
     Output('light-yellow',  'style'),
     Output('light-green',   'style'),
     Output('conn-status',   'children'),
     Output('conn-status',   'style')],
    Input('interval-component', 'n_intervals'),
)
def update_dashboard(n):
    h   = get_histories()
    vz  = [abs(v) for v in h['vz']]
    rel = h['rel_s']
    hzz = h['hzz']

    VZ_COLOR    = '#58a6ff'
    EMPTY_STYLE = {'color': '#f85149', 'fontSize': '0.82rem', 'marginLeft': 18}
    OK_STYLE    = {'color': '#3fb950', 'fontSize': '0.82rem', 'marginLeft': 18}

    # ── Connection ──────────────────────────────────────────────────────
    connected  = is_connected()
    conn_label = '● Connected' if connected else '● Disconnected'
    conn_style = OK_STYLE if connected else EMPTY_STYLE

    # ── RMS over last 1 second ─────────────────────────────────────────
    n_1s   = max(1, int(SAMPLING_RATE))
    window = vz[-n_1s:] if vz else []
    if window:
        rms       = float(np.sqrt(np.mean(np.array(window) ** 2)))
        rms_label = f"{rms:.3f}"
    else:
        rms       = 0.0
        rms_label = "—"

    # ── Actual vibration frequency from sensor (reg 0x46) ────────────────────
    if hzz:
        hz_label = f"{hzz[-1]:.1f}"
    else:
        hz_label  = "—"

    # ── Traffic light ──────────────────────────────────────────────────
    style_red    = _light_style(rms >= THRESH_YELLOW,
                                '#f85149', 'rgba(248,81,73,0.55)')
    style_yellow = _light_style(THRESH_GREEN <= rms < THRESH_YELLOW,
                                '#d29922', 'rgba(210,153,34,0.55)')
    style_green  = _light_style(rms < THRESH_GREEN,
                                '#3fb950', 'rgba(63,185,80,0.55)')

    # ── Graph 1: VZ vs time ────────────────────────────────────────────
    _xaxis_s = go.layout.XAxis(
        gridcolor=_GRID_CLR, color=_TICK_CLR, zeroline=False,
        title=go.layout.xaxis.Title(text='s', font=dict(size=10, color=_TICK_CLR)),
        tickfont=dict(size=10),
    )
    _yaxis_v = go.layout.YAxis(
        **_YAXIS_VEL,
    )
    time_fig = go.Figure(
        data=[
            go.Scatter(
                x=rel, y=vz,
                mode='lines', line=dict(color=VZ_COLOR, width=1.5),
                name='VZ',
                hovertemplate='%{y:.4f} mm/s<extra></extra>',
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
        ),
    )

    # ── Graph 2: FFT of the latest rolling window ───────────────────────────
    vz_raw     = h['vz']
    N_WIN_SECS = 4                              # 4-second window → 0.25 Hz resolution
    N_FFT      = int(SAMPLING_RATE * N_WIN_SECS)
    fft_traces = []

    if len(vz_raw) >= N_FFT:
        segment = np.array(vz_raw[-N_FFT:])
        segment = segment - segment.mean()      # remove DC offset
        window  = np.hanning(N_FFT)
        mag     = np.abs(np.fft.rfft(segment * window)) * 2.0 / window.sum()
        freqs   = np.fft.rfftfreq(N_FFT, d=1.0 / SAMPLING_RATE)
        freqs   = freqs[1:]                     # drop DC bin
        mag     = mag[1:]
        fft_traces.append(go.Scatter(
            x=freqs.tolist(), y=mag.tolist(),
            mode='lines', fill='tozeroy',
            line=dict(color=VZ_COLOR, width=1.5),
            fillcolor='rgba(88,166,255,0.15)',
            name='Spectrum',
            hovertemplate='%{x:.2f} Hz · %{y:.4f} mm/s<extra></extra>',
        ))
    fft_fig = go.Figure(
        data=fft_traces,
        layout=go.Layout(
            plot_bgcolor=_CARD_BG, paper_bgcolor=_CARD_BG,
            margin=dict(l=52, r=12, t=10, b=32),
            hovermode='x unified',
            font=dict(color=_TICK_CLR, size=10),
            xaxis=go.layout.XAxis(
                gridcolor=_GRID_CLR, color=_TICK_CLR, zeroline=False,
                title=go.layout.xaxis.Title(
                    text='Hz', font=dict(size=10, color=_TICK_CLR)
                ),
                tickfont=dict(size=10),
            ),
            yaxis=go.layout.YAxis(**_YAXIS_VEL),
            showlegend=False,
        ),
    )

    return (
        time_fig, fft_fig, rms_label, hz_label,
        style_red, style_yellow, style_green,
        conn_label, conn_style,
    )


@app.callback(
    [Output('log-status', 'children'),
     Output('log-store',  'data'),
     Output('download-csv', 'data')],
    [Input('btn-start-log', 'n_clicks'),
     Input('btn-stop-log',  'n_clicks')],
    [State('rpm-input',  'value'),
     State('load-input', 'value'),
     State('log-store',  'data')],
    prevent_initial_call=True,
)
def handle_logging(n_start, n_stop, rpm, load_w, log_data):
    triggered = ctx.triggered_id
    active    = log_data.get('active', False)

    if triggered == 'btn-start-log':
        if active:
            return "⚠ Already recording — stop first.", log_data, dash.no_update
        rpm_val  = int(rpm)    if rpm    is not None else 0
        load_val = int(load_w) if load_w is not None else 0
        start_logging()
        return (
            f"● Recording…  RPM = {rpm_val}  |  Load = {load_val} W",
            {'active': True, 'rpm': rpm_val, 'load': load_val},
            dash.no_update,
        )

    if triggered == 'btn-stop-log':
        if not active:
            return "⚠ No active recording.", log_data, dash.no_update
        data     = stop_logging()
        rpm_val  = log_data.get('rpm',  0)
        load_val = log_data.get('load', 0)
        path     = save_log(rpm_val, load_val, data)
        fname    = os.path.basename(path)

        # Build CSV content in memory for browser download
        vz_vals   = [row[2] for row in data] if data else []
        rms       = float(np.sqrt(np.mean(np.array(vz_vals) ** 2))) if vz_vals else 0.0
        buf = io.StringIO()
        w   = _csv.writer(buf)
        w.writerow(['# Engine Vibration Log (Simulated)'])
        w.writerow(['# RPM',        rpm_val])
        w.writerow(['# Load (W)',   load_val])
        w.writerow(['# Samples',    len(data)])
        w.writerow(['# RMS (mm/s)', f'{rms:.6f}'])
        w.writerow([])
        w.writerow(['counter', 'time', 'vz_mm_s'])
        for entry in data:
            w.writerow([entry[0], entry[1], f'{entry[2]:.6f}'])

        return (
            f"✔ Saved & downloading {len(data)} samples — {fname}",
            {'active': False, 'rpm': rpm_val, 'load': load_val},
            dcc.send_string(buf.getvalue(), filename=fname),
        )

    return "", log_data, dash.no_update


if __name__ == '__main__':
    app.run(debug=True, dev_tools_ui=False, use_reloader=False, host='0.0.0.0', port=7778)
