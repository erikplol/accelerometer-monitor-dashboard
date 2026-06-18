"""Layout factory for the vibration dashboard."""

from dash import dcc, html

from dashboard.theme import BG, BORDER, CARD, CARD_BG, INPUT, LABEL, light_style


def create_layout(thresh_green: float, thresh_yellow: float, ui_interval_ms: int):
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
                html.H1('Engine Vibration Monitor', style={
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
            ], style={**CARD, 'flex': '1 1 170px', 'alignSelf': 'stretch',
                      'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center',
                      'borderTop': '2px solid #58a6ff'}, className='card-narrow'),

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
            ], style={**CARD, 'flex': '1 1 170px', 'alignSelf': 'stretch',
                      'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center',
                      'borderTop': '2px solid #3fb950'}, className='card-narrow'),

            html.Div([
                html.Div('HZZ (Witmotion)', className='card-title', style={**LABEL, 'textAlign': 'center'}),
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
                    html.Span('Pixhawk Rate:', style={
                        'color': '#8b949e',
                        'fontSize': '0.78rem',
                        'marginRight': 6,
                    }),
                    html.Span(id='dominant-fft-hz', children='-', style={
                        'color': '#e6edf3',
                        'fontSize': '0.88rem',
                        'fontWeight': 500,
                        'fontVariantNumeric': 'tabular-nums',
                    }),
                    html.Span('Hz', style={
                        'color': '#8b949e',
                        'fontSize': '0.78rem',
                        'marginLeft': 4,
                    }),
                ], style={
                    'marginTop': 4,
                    'display': 'flex',
                    'alignItems': 'center',
                    'justifyContent': 'center',
                }),
            ], style={**CARD, 'flex': '1 1 170px', 'alignSelf': 'stretch',
                      'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center',
                      'borderTop': '2px solid #d29922'}, className='card-narrow'),

            html.Div([
                html.Div('Severity Level', className='card-title', style={**LABEL, 'textAlign': 'center', 'marginBottom': 10}),
                html.Div([
                    html.Div([
                        html.Div(id='light-green', style=light_style(True, '#3fb950', 'rgba(63,185,80,0.5)')),
                        html.Div('GOOD', style={
                            'color': '#6e7681',
                            'fontSize': '0.72rem',
                            'fontWeight': 600,
                            'letterSpacing': '0.8px',
                            'textAlign': 'center',
                            'marginTop': 5,
                        }),
                        html.Div(f'< {thresh_green}', style={
                            'color': '#484f58',
                            'fontSize': '0.66rem',
                            'textAlign': 'center',
                        }),
                    ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

                    html.Div([
                        html.Div(id='light-yellow', style=light_style(False, '#d29922', 'rgba(210,153,34,0.5)')),
                        html.Div('CAUTION', style={
                            'color': '#6e7681',
                            'fontSize': '0.72rem',
                            'fontWeight': 600,
                            'letterSpacing': '0.8px',
                            'textAlign': 'center',
                            'marginTop': 5,
                        }),
                        html.Div(f'{thresh_green}-{thresh_yellow}', style={
                            'color': '#484f58',
                            'fontSize': '0.66rem',
                            'textAlign': 'center',
                        }),
                    ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),

                    html.Div([
                        html.Div(id='light-red', style=light_style(False, '#f85149', 'rgba(248,81,73,0.5)')),
                        html.Div('ALARM', style={
                            'color': '#6e7681',
                            'fontSize': '0.72rem',
                            'fontWeight': 600,
                            'letterSpacing': '0.8px',
                            'textAlign': 'center',
                            'marginTop': 5,
                        }),
                        html.Div(f'>= {thresh_yellow}', style={
                            'color': '#484f58',
                            'fontSize': '0.66rem',
                            'textAlign': 'center',
                        }),
                    ], style={'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center'}),
                ], style={
                    'display': 'flex',
                    'flexDirection': 'row',
                    'gap': 32,
                    'alignItems': 'flex-start',
                    'justifyContent': 'center',
                }),
            ], style={**CARD, 'flex': '1 1 360px', 'alignSelf': 'stretch', 'display': 'flex',
                      'flexDirection': 'column', 'alignItems': 'center', 'justifyContent': 'center'},
               className='card-wide'),

            html.Div([
                html.Div('Data Logging', className='card-title', style={**LABEL, 'textAlign': 'center'}),
                html.Div([
                    html.Div([
                        html.Label('RPM', style={**LABEL, 'marginBottom': 4, 'display': 'block'}),
                        dcc.Input(
                            id='rpm-input', type='number', placeholder='e.g. 1600',
                            min=0, step=100, debounce=False, style={**INPUT, 'width': 120},
                        ),
                    ], className='input-group'),

                    html.Div([
                        html.Label('Load (W)', style={**LABEL, 'marginBottom': 4, 'display': 'block'}),
                        dcc.Input(
                            id='load-input', type='number', placeholder='e.g. 3000',
                            min=0, step=500, debounce=False, style={**INPUT, 'width': 120},
                        ),
                    ], className='input-group'),

                    html.Div([
                        html.Label(' ', style={'display': 'block', 'marginBottom': 4, 'fontSize': '0.78rem'}),
                        html.Div([
                            html.Button('▶  Start', id='btn-start-log', n_clicks=0, className='btn-start', style={
                                'background': 'linear-gradient(135deg, #238636, #2ea043)',
                                'color': '#fff',
                                'border': '1px solid #2ea043',
                                'borderRadius': 8,
                                'padding': '7px 16px',
                                'fontSize': '0.9rem',
                                'cursor': 'pointer',
                                'fontWeight': 500,
                                'minWidth': 92,
                                'fontFamily': 'inherit',
                            }),
                            html.Button('■  Stop', id='btn-stop-log', n_clicks=0, className='btn-stop', style={
                                'background': 'linear-gradient(135deg, #b62324, #da3633)',
                                'color': '#fff',
                                'border': '1px solid #da3633',
                                'borderRadius': 8,
                                'padding': '7px 16px',
                                'fontSize': '0.9rem',
                                'cursor': 'pointer',
                                'fontWeight': 500,
                                'minWidth': 92,
                                'fontFamily': 'inherit',
                            }),
                            html.Button('⏸  Pause View', id='btn-toggle-play', n_clicks=0, className='btn-pause', style={
                                'background': 'linear-gradient(135deg, #1f6feb, #388bfd)',
                                'color': '#fff',
                                'border': '1px solid #388bfd',
                                'borderRadius': 8,
                                'padding': '7px 16px',
                                'fontSize': '0.9rem',
                                'cursor': 'pointer',
                                'fontWeight': 500,
                                'minWidth': 120,
                                'fontFamily': 'inherit',
                            }),
                        ], className='log-actions'),

                        html.Div([
                            html.Button('Download Last Log', id='btn-download-last', n_clicks=0, className='btn-download', style={
                                'background': '#0d1117',
                                'color': '#c9d1d9',
                                'border': f'1px solid {BORDER}',
                                'borderRadius': 8,
                                'padding': '6px 10px',
                                'fontSize': '0.82rem',
                                'cursor': 'pointer',
                                'minWidth': 146,
                            }),
                            html.Button('Download All Logs', id='btn-download-all', n_clicks=0, className='btn-download', style={
                                'background': '#0d1117',
                                'color': '#c9d1d9',
                                'border': f'1px solid {BORDER}',
                                'borderRadius': 8,
                                'padding': '6px 10px',
                                'fontSize': '0.82rem',
                                'cursor': 'pointer',
                                'minWidth': 146,
                            }),
                        ], className='download-actions'),
                    ], className='input-group'),
                ], className='log-form-row'),
                html.Div(id='log-status', children='', className='log-status-row', style={'color': '#9fb3ce'}),
            ], style={**CARD, 'flex': '1 1 420px', 'alignSelf': 'stretch', 'display': 'flex',
                      'flexDirection': 'column', 'justifyContent': 'center', 'alignItems': 'center'},
               className='card-wide'),

        ], className='top-strip', style={
            'display': 'flex',
            'padding': '8px 14px 6px',
            'gap': 8,
            'flexShrink': 0,
            'alignItems': 'stretch',
            'flexWrap': 'wrap',
        }),

        # ── Calibration panel (collapsed by default) ──────────────────────
        html.Div([
            html.Div([
                html.Span('⚙ Sensor Calibration', style={
                    'color': '#9cb0cc',
                    'fontSize': '0.82rem',
                    'fontWeight': 600,
                    'letterSpacing': '0.8px',
                    'textTransform': 'uppercase',
                    'cursor': 'pointer',
                    'userSelect': 'none',
                }),
                html.Span('▸', id='calib-toggle-icon', style={'color': '#4f8ef7', 'marginLeft': 8, 'cursor': 'pointer', 'fontSize': '0.9rem'}),
            ], id='calib-toggle-bar', style={
                'display': 'flex',
                'alignItems': 'center',
                'padding': '6px 20px',
                'background': '#0f1725',
                'borderBottom': '1px solid #1f2b3f',
                'cursor': 'pointer',
            }),

            html.Div([
                # ── Pixhawk block ────────────────────────────────────────
                html.Div([
                    html.Div('Pixhawk Gravity Offset', className='card-title',
                             style={**LABEL, 'textAlign': 'center', 'marginBottom': 6}),
                    html.Div(id='pix-base-label', style={
                        'color': '#8b949e', 'fontSize': '0.75rem', 'textAlign': 'center', 'marginBottom': 4,
                    }),
                    html.Div([
                        html.Div(id='pix-current-val', style={
                            'color': '#58a6ff',
                            'fontSize': '1.55rem',
                            'fontWeight': 300,
                            'fontVariantNumeric': 'tabular-nums',
                            'minWidth': 110,
                            'textAlign': 'center',
                        }),
                        html.Span('mG', style={'color': '#6e7681', 'fontSize': '0.8rem', 'alignSelf': 'flex-end', 'paddingBottom': 3}),
                    ], style={'display': 'flex', 'alignItems': 'baseline', 'justifyContent': 'center', 'gap': 4, 'marginBottom': 8}),

                    html.Div([
                        html.Button('−5', id='pix-minus5', n_clicks=0, className='btn-calib'),
                        html.Button('−1', id='pix-minus1', n_clicks=0, className='btn-calib'),
                        html.Button('−0.1', id='pix-minus01', n_clicks=0, className='btn-calib'),
                        html.Button('+0.1', id='pix-plus01', n_clicks=0, className='btn-calib btn-calib-pos'),
                        html.Button('+1', id='pix-plus1', n_clicks=0, className='btn-calib btn-calib-pos'),
                        html.Button('+5', id='pix-plus5', n_clicks=0, className='btn-calib btn-calib-pos'),
                    ], style={'display': 'flex', 'gap': 4, 'justifyContent': 'center', 'flexWrap': 'wrap', 'marginBottom': 8}),

                    html.Div([
                        html.Button('💾 Save', id='pix-save', n_clicks=0, className='btn-calib-action btn-save'),
                        html.Button('↺ Reset to Base', id='pix-reset', n_clicks=0, className='btn-calib-action btn-reset'),
                    ], style={'display': 'flex', 'gap': 6, 'justifyContent': 'center'}),
                ], style={**CARD, 'flex': '1 1 300px', 'display': 'flex', 'flexDirection': 'column',
                          'justifyContent': 'center', 'alignItems': 'center', 'borderTop': '2px solid #4f8ef7'}),

                # ── Witmotion block ──────────────────────────────────────
                html.Div([
                    html.Div('Witmotion VZ Scale', className='card-title',
                             style={**LABEL, 'textAlign': 'center', 'marginBottom': 6}),
                    html.Div(id='wtb-base-label', style={
                        'color': '#8b949e', 'fontSize': '0.75rem', 'textAlign': 'center', 'marginBottom': 4,
                    }),
                    html.Div([
                        html.Div(id='wtb-current-val', style={
                            'color': '#3fb950',
                            'fontSize': '1.55rem',
                            'fontWeight': 300,
                            'fontVariantNumeric': 'tabular-nums',
                            'minWidth': 90,
                            'textAlign': 'center',
                        }),
                        html.Span('×', style={'color': '#6e7681', 'fontSize': '0.8rem', 'alignSelf': 'flex-end', 'paddingBottom': 3}),
                    ], style={'display': 'flex', 'alignItems': 'baseline', 'justifyContent': 'center', 'gap': 4, 'marginBottom': 8}),

                    html.Div([
                        html.Button('−0.1', id='wtb-minus01', n_clicks=0, className='btn-calib'),
                        html.Button('−0.01', id='wtb-minus001', n_clicks=0, className='btn-calib'),
                        html.Button('−0.001', id='wtb-minus0001', n_clicks=0, className='btn-calib'),
                        html.Button('+0.001', id='wtb-plus0001', n_clicks=0, className='btn-calib btn-calib-pos'),
                        html.Button('+0.01', id='wtb-plus001', n_clicks=0, className='btn-calib btn-calib-pos'),
                        html.Button('+0.1', id='wtb-plus01', n_clicks=0, className='btn-calib btn-calib-pos'),
                    ], style={'display': 'flex', 'gap': 4, 'justifyContent': 'center', 'flexWrap': 'wrap', 'marginBottom': 8}),

                    html.Div([
                        html.Button('💾 Save', id='wtb-save', n_clicks=0, className='btn-calib-action btn-save'),
                        html.Button('↺ Reset to Base', id='wtb-reset', n_clicks=0, className='btn-calib-action btn-reset'),
                    ], style={'display': 'flex', 'gap': 6, 'justifyContent': 'center'}),
                ], style={**CARD, 'flex': '1 1 300px', 'display': 'flex', 'flexDirection': 'column',
                          'justifyContent': 'center', 'alignItems': 'center', 'borderTop': '2px solid #3fb950'}),

                # ── Status ───────────────────────────────────────────────
                html.Div(id='calib-status', style={
                    'color': '#9cb0cc',
                    'fontSize': '0.8rem',
                    'textAlign': 'center',
                    'flex': '1 1 200px',
                    'alignSelf': 'center',
                    'padding': '8px 12px',
                }),

            ], id='calib-panel-body', style={
                'display': 'none',   # hidden until toggle
                'flexWrap': 'wrap',
                'gap': 8,
                'padding': '10px 14px',
                'background': '#0b111c',
                'borderBottom': f'1px solid #1f2b3f',
                'alignItems': 'stretch',
            }),
        ], id='calib-panel'),

        html.Div([
            html.Div([
                html.Div('AZ · Pixhawk Real Time', style={**LABEL, 'marginBottom': 4}),
                dcc.Graph(id='vz-time-graph', style={'flex': 1, 'minHeight': 0},
                          config={'displayModeBar': False, 'responsive': True}),
            ], style={**CARD, 'flex': 1, 'display': 'flex', 'flexDirection': 'column', 'minHeight': 0}),

            html.Div([
                html.Div('AZ FFT · Pixhawk', style={**LABEL, 'marginBottom': 4}),
                dcc.Graph(id='fft-graph', style={'flex': 1, 'minHeight': 0},
                          config={'displayModeBar': False, 'responsive': True}),
            ], style={**CARD, 'flex': 1, 'display': 'flex', 'flexDirection': 'column', 'minHeight': 0}),

        ], className='graphs-row', style={
            'display': 'flex',
            'padding': '6px 14px 10px',
            'gap': 8,
            'flex': 1,
            'minHeight': 0,
            'overflow': 'hidden',
        }),

        dcc.Store(id='log-store', data={'active': False, 'rpm': 0, 'load': 0, 'start_time': 0, 'last_path': ''}),
        dcc.Store(id='playback-store', data={'paused': False}),
        dcc.Store(id='calib-store', data={}),
        dcc.Download(id='download-last-file'),
        dcc.Download(id='download-all-files'),
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
