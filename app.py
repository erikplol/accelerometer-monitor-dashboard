import os
import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.graph_objs as go
import numpy as np

from data_collect import (
    SerialReader,
    get_histories,
    is_connected,
    start_logging,
    stop_logging,
    save_log,
    SAMPLING_RATE as DC_SAMPLING_RATE,
    MAX_TIME_PTS,
)

# ── App setup ─────────────────────────────────────────────────────────────────
app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.index_string = '''
<!DOCTYPE html>
<html>
<head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #17181a; height: 100%; overflow: hidden; }

  /* ── Responsive breakpoints ────────────────────────── */

  /* Large tablet: RMS/Freq cards can shrink, others flex */
  @media (max-width: 1200px) {
    .top-strip { flex-wrap: wrap !important; }
    /* RMS + Freq: narrow fixed → allow shrink */
    .top-strip > div.card-narrow { flex: 1 1 140px !important; min-width: 120px !important; max-width: 200px !important; }
    /* Severity + Logging: allow shrink */
    .top-strip > div.card-wide   { flex: 1 1 220px !important; min-width: 200px !important; }
  }

  /* Small tablet / large phone: stack everything */
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

SAMPLING_RATE = DC_SAMPLING_RATE
INTERVAL_MS   = 1000.0 / SAMPLING_RATE

# ISO 10816 vibration severity thresholds (mm/s RMS)
THRESH_GREEN  = 2.8    # below  → green  (good)
THRESH_YELLOW = 7.1    # below  → yellow (acceptable), above → red (alarm)

_reader = SerialReader()
_reader.start()

# ── Style helpers ─────────────────────────────────────────────────────────────
_CARD = {
    'background':    '#202124',
    'borderRadius':  8,
    'border':        '1px solid #3c4043',
    'padding':       '14px 18px',
    'boxSizing':     'border-box',
}

_LABEL = {
    'color':          '#9aa0a6',
    'fontSize':       '0.70rem',
    'fontWeight':     600,
    'textTransform':  'uppercase',
    'letterSpacing':  '0.8px',
    'marginBottom':   6,
    'flexShrink':     0,
}

_INPUT = {
    'background':   '#2a2b2f',
    'color':        '#e8eaed',
    'border':       '1px solid #3c4043',
    'borderRadius': 4,
    'padding':      '6px 10px',
    'fontSize':     '0.9rem',
    'width':        130,
    'outline':      'none',
    'boxSizing':    'border-box',
}


def _light_style(active: bool, color: str, glow: str) -> dict:
    if active:
        return {
            'width': 46, 'height': 46, 'borderRadius': '50%',
            'backgroundColor': color,
            'boxShadow': f'0 0 20px 5px {glow}',
            'transition': 'background-color 0.3s, box-shadow 0.3s',
        }
    return {
        'width': 46, 'height': 46, 'borderRadius': '50%',
        'backgroundColor': '#2d2d2d',
        'boxShadow': 'none',
        'transition': 'background-color 0.3s, box-shadow 0.3s',
    }


# ── Layout ────────────────────────────────────────────────────────────────────
app.layout = html.Div([

    # ── Header ──────────────────────────────────────────────────────────
    html.Div([
        html.H1("Engine Vibration Monitor", style={
            'margin': 0, 'color': '#e8eaed',
            'fontSize': '1.35rem', 'fontWeight': 400, 'letterSpacing': '-0.4px',
        }),
        html.Span(id='conn-status', style={'fontSize': '0.78rem', 'marginLeft': 18}),
    ], style={
        'padding': '11px 22px', 'background': '#202124',
        'borderBottom': '1px solid #3c4043',
        'display': 'flex', 'alignItems': 'center', 'flexShrink': 0,
    }),

    # ── Top strip: RMS | Freq | Severity | Logging ─────────────────────
    html.Div([

        # RMS card
        html.Div([
            html.Div("RMS Vibration", style={**_LABEL, 'textAlign': 'center'}),
            html.Div([
                html.Span(id='rms-value', children='—', style={
                    'color': '#4285f4', 'fontSize': '2.6rem',
                    'fontWeight': 300, 'lineHeight': 1,
                }),
                html.Span(" mm/s", style={
                    'color': '#9aa0a6', 'fontSize': '0.85rem',
                    'marginLeft': 6, 'alignSelf': 'flex-end', 'paddingBottom': 3,
                }),
            ], style={'display': 'flex', 'alignItems': 'baseline', 'justifyContent': 'center'}),
        ], style={**_CARD, 'flex': '0 0 160px', 'alignSelf': 'stretch',
                  'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center'},
           className='card-narrow'),

        # Frequency card
        html.Div([
            html.Div("Frequency", style={**_LABEL, 'textAlign': 'center'}),
            html.Div([
                html.Span(id='recv-hz', children='—', style={
                    'color': '#34a853', 'fontSize': '2.6rem',
                    'fontWeight': 300, 'lineHeight': 1,
                }),
                html.Span(" Hz", style={
                    'color': '#9aa0a6', 'fontSize': '0.85rem',
                    'marginLeft': 6, 'alignSelf': 'flex-end', 'paddingBottom': 3,
                }),
            ], style={'display': 'flex', 'alignItems': 'baseline', 'justifyContent': 'center'}),
        ], style={**_CARD, 'flex': '0 0 160px', 'alignSelf': 'stretch',
                  'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center'},
           className='card-narrow'),

        # Traffic-light card
        html.Div([
            html.Div("Severity Level", style={**_LABEL, 'textAlign': 'center', 'marginBottom': 8}),
            html.Div([
                # Green
                html.Div([
                    html.Div(id='light-green',
                             style=_light_style(True, '#34a853', 'rgba(52,168,83,0.5)')),
                    html.Div(f"< {THRESH_GREEN} mm/s", style={
                        'color': '#9aa0a6', 'fontSize': '0.65rem',
                        'textAlign': 'center', 'marginTop': 4,
                    }),
                ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

                # Yellow
                html.Div([
                    html.Div(id='light-yellow',
                             style=_light_style(False, '#fbbc04', 'rgba(251,188,4,0.5)')),
                    html.Div(f"{THRESH_GREEN}–{THRESH_YELLOW}", style={
                        'color': '#9aa0a6', 'fontSize': '0.65rem',
                        'textAlign': 'center', 'marginTop': 4,
                    }),
                    html.Div("mm/s", style={'color': '#9aa0a6', 'fontSize': '0.62rem', 'textAlign': 'center'}),
                ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

                # Red
                html.Div([
                    html.Div(id='light-red',
                             style=_light_style(False, '#ea4335', 'rgba(234,67,53,0.5)')),
                    html.Div(f"≥ {THRESH_YELLOW} mm/s", style={
                        'color': '#9aa0a6', 'fontSize': '0.65rem',
                        'textAlign': 'center', 'marginTop': 4,
                    }),
                ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

            ], style={
                'display': 'flex', 'flexDirection': 'row',
                'gap': 36, 'alignItems': 'flex-start', 'justifyContent': 'center',
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
                    html.Label("RPM", style={**_LABEL, 'marginBottom': 3, 'display': 'block'}),
                    dcc.Input(
                        id='rpm-input', type='number',
                        placeholder='e.g. 1600',
                        min=0, step=100, debounce=False,
                        style={**_INPUT, 'width': 110},
                    ),
                ], style={'marginRight': 8}),

                # LOAD input
                html.Div([
                    html.Label("Load (Watt)", style={**_LABEL, 'marginBottom': 3, 'display': 'block'}),
                    dcc.Input(
                        id='load-input', type='number',
                        placeholder='e.g. 3000',
                        min=0, step=500, debounce=False,
                        style={**_INPUT, 'width': 110},
                    ),
                ], style={'marginRight': 12}),

                # Buttons (vertically aligned with inputs)
                html.Div([
                    html.Label('\u00a0', style={'display': 'block', 'marginBottom': 3, 'fontSize': '0.70rem'}),
                    html.Div([
                        html.Button("▶  Start", id='btn-start-log', n_clicks=0, style={
                            'background': '#34a853', 'color': '#fff',
                            'border': 'none', 'borderRadius': 5,
                            'padding': '6px 14px', 'fontSize': '0.82rem',
                            'cursor': 'pointer', 'fontWeight': 500, 'marginRight': 7,
                        }),
                        html.Button("■  Stop", id='btn-stop-log', n_clicks=0, style={
                            'background': '#ea4335', 'color': '#fff',
                            'border': 'none', 'borderRadius': 5,
                            'padding': '6px 14px', 'fontSize': '0.82rem',
                            'cursor': 'pointer', 'fontWeight': 500,
                        }),
                    ], style={'display': 'flex', 'alignItems': 'center'}),
                ]),

                html.Div(id='log-status', children='', style={
                    'marginLeft': 10, 'color': '#9aa0a6',
                    'fontSize': '0.75rem', 'alignSelf': 'flex-end', 'paddingBottom': 2,
                }),
            ], style={
                'display': 'flex', 'alignItems': 'flex-end',
                'justifyContent': 'center', 'flexWrap': 'nowrap', 'marginTop': 6,
            }),
        ], style={**_CARD, 'flex': 2, 'alignSelf': 'stretch',
                  'display': 'flex', 'flexDirection': 'column',
                  'justifyContent': 'center', 'alignItems': 'center'},
           className='card-wide'),

    ], className='top-strip', style={
        'display': 'flex', 'padding': '9px 14px 5px',
        'gap': 10, 'flexShrink': 0, 'alignItems': 'stretch', 'flexWrap': 'wrap',
    }),

    # ── Graphs row ──────────────────────────────────────────────────────
    html.Div([

        # Graph 1 – VZ vs time
        html.Div([
            html.Div("Vibration Velocity — Real Time", style=_LABEL),
            dcc.Graph(
                id='vz-time-graph',
                style={'flex': 1, 'minHeight': 0},
                config={'displayModeBar': False, 'responsive': True},
            ),
        ], style={
            **_CARD, 'flex': 1, 'marginRight': 6,
            'display': 'flex', 'flexDirection': 'column', 'minHeight': 0,
        }),

        # Graph 2 – FFT
        html.Div([
            html.Div("Frequency Spectrum — FFT", style=_LABEL),
            dcc.Graph(
                id='fft-graph',
                style={'flex': 1, 'minHeight': 0},
                config={'displayModeBar': False, 'responsive': True},
            ),
        ], style={
            **_CARD, 'flex': 1, 'marginLeft': 6,
            'display': 'flex', 'flexDirection': 'column', 'minHeight': 0,
        }),

    ], className='graphs-row', style={
        'display': 'flex', 'padding': '5px 14px 9px',
        'gap': 10, 'flex': 1, 'minHeight': 0, 'overflow': 'hidden',
    }),

    # ── Stores & interval ────────────────────────────────────────────────
    dcc.Store(id='log-store', data={'active': False, 'rpm': 0, 'load': 0}),
    dcc.Interval(id='interval-component', interval=INTERVAL_MS, n_intervals=0),

], style={
    'fontFamily': (
        '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
        '"Helvetica Neue", Arial, sans-serif'
    ),
    'backgroundColor': '#17181a',
    'height':          '100vh',
    'margin':          0,
    'display':         'flex',
    'flexDirection':   'column',
    'overflow':        'hidden',
})

# ── Shared plot config ─────────────────────────────────────────────────────────
_YAXIS_VEL = dict(
    gridcolor='#3c4043', color='#9aa0a6', zeroline=True,
    zerolinecolor='#5f6368', zerolinewidth=1,
    rangemode='tozero',
    title=dict(text='mm/s', font=dict(size=10, color='#9aa0a6')),
    tickfont=dict(size=10),
)


_DARK_BG   = '#202124'
_GRID_CLR  = '#3c4043'
_TICK_CLR  = '#9aa0a6'
_ZERO_CLR  = '#5f6368'


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

    VZ_COLOR    = '#4285f4'
    EMPTY_STYLE = {'color': '#ea4335', 'fontSize': '0.78rem', 'marginLeft': 18}
    OK_STYLE    = {'color': '#34a853', 'fontSize': '0.78rem', 'marginLeft': 18}

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
                                '#ea4335', 'rgba(234,67,53,0.55)')
    style_yellow = _light_style(THRESH_GREEN <= rms < THRESH_YELLOW,
                                '#fbbc04', 'rgba(251,188,4,0.55)')
    style_green  = _light_style(rms < THRESH_GREEN,
                                '#34a853', 'rgba(52,168,83,0.55)')

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
            plot_bgcolor=_DARK_BG, paper_bgcolor=_DARK_BG,
            margin=dict(l=52, r=12, t=10, b=32),
            hovermode='x unified',
            font=dict(color=_TICK_CLR, size=10),
            xaxis=_xaxis_s,
            yaxis=_yaxis_v,
            showlegend=False,
        ),
    )

    # ── Graph 2: FFT ───────────────────────────────────────────────────
    fft_traces = []
    if len(vz) >= 8:
        arr   = np.array(vz) - np.mean(vz)          # remove DC
        N     = len(arr)
        freqs = np.fft.rfftfreq(N, d=1.0 / SAMPLING_RATE)
        mag   = np.abs(np.fft.rfft(arr)) * 2.0 / N  # single-sided peak amplitude
        freqs, mag = freqs[1:], mag[1:]              # drop DC bin
        fft_traces.append(go.Bar(
            x=freqs, y=mag,
            marker_color=VZ_COLOR, opacity=0.85,
            hovertemplate='%{x:.2f} Hz  %{y:.4f} mm/s<extra></extra>',
        ))
    fft_fig = go.Figure(
        data=fft_traces,
        layout=go.Layout(
            plot_bgcolor=_DARK_BG, paper_bgcolor=_DARK_BG,
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
            bargap=0.1,
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
     Output('log-store',  'data')],
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
            return "⚠ Already recording — stop first.", log_data
        rpm_val  = int(rpm)    if rpm    is not None else 0
        load_val = int(load_w) if load_w is not None else 0
        start_logging()
        return (
            f"● Recording…  RPM = {rpm_val}  |  Load = {load_val} W",
            {'active': True, 'rpm': rpm_val, 'load': load_val},
        )

    if triggered == 'btn-stop-log':
        if not active:
            return "⚠ No active recording.", log_data
        data     = stop_logging()
        rpm_val  = log_data.get('rpm',  0)
        load_val = log_data.get('load', 0)
        path     = save_log(rpm_val, load_val, data)
        fname    = os.path.basename(path)
        return (
            f"✔ Saved {len(data)} samples → {fname}",
            {'active': False, 'rpm': rpm_val, 'load': load_val},
        )

    return "", log_data


if __name__ == '__main__':
    app.run(debug=True, dev_tools_ui=False, host='0.0.0.0', port=7777)
