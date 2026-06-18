"""Layout factory for recorded CSV replay dashboard."""

from dash import dcc, html

from dashboard.theme import BG, BORDER, CARD, CARD_BG, LABEL, light_style


def create_layout(thresh_green: float, thresh_yellow: float, ui_interval_ms: int):
    button_style = {
        'background': '#0d1117',
        'color': '#c9d1d9',
        'border': f'1px solid {BORDER}',
        'borderRadius': 8,
        'padding': '7px 12px',
        'fontSize': '0.86rem',
        'cursor': 'pointer',
        'minWidth': 140,
        'fontFamily': 'inherit',
    }

    return html.Div([
        html.Div([
            html.Div([
                html.Div(style={
                    'width': 8,
                    'height': 8,
                    'borderRadius': '50%',
                    'backgroundColor': '#58a6ff',
                    'marginRight': 10,
                    'boxShadow': '0 0 8px rgba(88,166,255,0.7)',
                }),
                html.H1('Engine Vibration Replay', style={
                    'margin': 0,
                    'color': '#e6edf3',
                    'fontSize': '1.2rem',
                    'fontWeight': 500,
                    'letterSpacing': '-0.2px',
                }),
            ], style={'display': 'flex', 'alignItems': 'center'}),
            html.Span(id='conn-status', className='status-pill', style={'fontSize': '0.82rem'}),
        ], style={
            'padding': '10px 20px',
            'background': CARD_BG,
            'borderBottom': f'1px solid {BORDER}',
            'display': 'flex',
            'alignItems': 'center',
            'justifyContent': 'space-between',
            'flexWrap': 'wrap',
            'gap': 8,
            'flexShrink': 0,
        }, className='app-header'),

        html.Div([
            html.Div([
                html.Div('VZ RMS', className='card-title', style={**LABEL, 'textAlign': 'center'}),
                html.Div([
                    html.Span(id='rms-value', children='-', style={
                        'color': '#58a6ff',
                        'fontSize': '2.8rem',
                        'fontWeight': 300,
                        'lineHeight': 1,
                        'fontVariantNumeric': 'tabular-nums',
                    }),
                    html.Span('mm/s', className='metric-unit'),
                ], className='metric-value'),
            ], style={**CARD, 'flex': '1 1 180px', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'flexDirection': 'column'}),

            html.Div([
                html.Div('AZ RMS', className='card-title', style={**LABEL, 'textAlign': 'center'}),
                html.Div([
                    html.Span(id='az-rms-value', children='-', style={
                        'color': '#3fb950',
                        'fontSize': '2.8rem',
                        'fontWeight': 300,
                        'lineHeight': 1,
                        'fontVariantNumeric': 'tabular-nums',
                    }),
                    html.Span('m/s²', className='metric-unit'),
                ], className='metric-value'),
            ], style={**CARD, 'flex': '1 1 180px', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'flexDirection': 'column'}),

            html.Div([
                html.Div('HZZ', className='card-title', style={**LABEL, 'textAlign': 'center'}),
                html.Div([
                    html.Span(id='recv-hz', children='-', style={
                        'color': '#d29922',
                        'fontSize': '2.8rem',
                        'fontWeight': 300,
                        'lineHeight': 1,
                        'fontVariantNumeric': 'tabular-nums',
                    }),
                    html.Span('Hz', className='metric-unit'),
                ], className='metric-value'),
                html.Div([
                    html.Span('Replay Rate:', style={'color': '#8b949e', 'fontSize': '0.78rem', 'marginRight': 6}),
                    html.Span(id='dominant-fft-hz', children='-', style={
                        'color': '#e6edf3',
                        'fontSize': '0.88rem',
                        'fontWeight': 500,
                        'fontVariantNumeric': 'tabular-nums',
                    }),
                    html.Span('Hz', style={'color': '#8b949e', 'fontSize': '0.78rem', 'marginLeft': 4}),
                ], style={'marginTop': 4, 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center'}),
            ], style={**CARD, 'flex': '1 1 180px', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'flexDirection': 'column'}),

            html.Div([
                html.Div('Severity Level', className='card-title', style={**LABEL, 'textAlign': 'center', 'marginBottom': 10}),
                html.Div([
                    html.Div(id='light-green', style=light_style(True, '#3fb950', 'rgba(63,185,80,0.5)')),
                    html.Div(id='light-yellow', style=light_style(False, '#d29922', 'rgba(210,153,34,0.5)')),
                    html.Div(id='light-red', style=light_style(False, '#f85149', 'rgba(248,81,73,0.5)')),
                ], style={'display': 'flex', 'gap': 14}),
                html.Div(f'Thresholds: < {thresh_green} | {thresh_green}-{thresh_yellow} | >= {thresh_yellow}', style={
                    'color': '#6e7681',
                    'fontSize': '0.72rem',
                    'marginTop': 8,
                    'textAlign': 'center',
                }),
            ], style={**CARD, 'flex': '1 1 240px', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'flexDirection': 'column'}),

            html.Div([
                html.Div('Replay Controls', className='card-title', style={**LABEL, 'textAlign': 'center'}),
                html.Div([
                    dcc.Upload(
                        id='replay-upload',
                        children=html.Button('Load Replay CSV', style=button_style),
                        multiple=False,
                    ),
                    html.Button('Replay Last Log', id='btn-replay-last', n_clicks=0, style=button_style),
                    html.Button('⏸  Pause Replay', id='btn-toggle-play', n_clicks=0, style=button_style),
                    html.Button('Stop Replay', id='btn-stop-replay', n_clicks=0, style=button_style),
                ], style={'display': 'flex', 'gap': 8, 'flexWrap': 'wrap', 'justifyContent': 'center'}),
                html.Div(id='replay-status', children='Replay: off', style={'color': '#9fb3ce', 'marginTop': 8, 'fontSize': '0.82rem'}),
            ], style={**CARD, 'flex': '2 1 420px', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'flexDirection': 'column'}),

        ], style={'display': 'flex', 'padding': '8px 14px 6px', 'gap': 8, 'flexWrap': 'wrap'}),

        html.Div([
            html.Div([
                html.Div('AZ Replay Timeline', style={**LABEL, 'marginBottom': 4}),
                dcc.Graph(id='vz-time-graph', style={'flex': 1, 'minHeight': 0}, config={'displayModeBar': False, 'responsive': True}),
            ], style={**CARD, 'flex': 1, 'display': 'flex', 'flexDirection': 'column', 'minHeight': 0}),
            html.Div([
                html.Div('AZ Replay FFT', style={**LABEL, 'marginBottom': 4}),
                dcc.Graph(id='fft-graph', style={'flex': 1, 'minHeight': 0}, config={'displayModeBar': False, 'responsive': True}),
            ], style={**CARD, 'flex': 1, 'display': 'flex', 'flexDirection': 'column', 'minHeight': 0}),
        ], style={
            'display': 'flex',
            'padding': '6px 14px 10px',
            'gap': 8,
            'flex': 1,
            'minHeight': 0,
            'overflow': 'hidden',
        }),

        dcc.Store(id='playback-store', data={
            'paused': False,
            'mode': 'live',
            'cursor': 0,
            'total': 0,
            'rate_hz': 0.0,
            'source': '',
            'error': '',
        }),
        dcc.Interval(id='interval-component', interval=ui_interval_ms, n_intervals=0),

    ], style={
        'fontFamily': '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'backgroundColor': BG,
        'height': '100vh',
        'margin': 0,
        'display': 'flex',
        'flexDirection': 'column',
        'overflow': 'hidden',
    }, className='app-shell')
