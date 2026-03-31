import plotly.graph_objs as go

CARD_BG   = '#161b22'
BORDER    = '#21262d'
TICK_CLR  = '#8b949e'
GRID_CLR  = '#21262d'
ZERO_CLR  = '#30363d'
VZ_COLOR  = '#58a6ff'

YAXIS_VEL = dict(
    gridcolor=GRID_CLR, color=TICK_CLR, zeroline=True,
    zerolinecolor=ZERO_CLR, zerolinewidth=1,
    title=dict(text='mm/s', font=dict(size=10, color=TICK_CLR)),
    tickfont=dict(size=10),
    showgrid=True,
)


def build_time_figure(rel, vz_display):
    return go.Figure(
        data=[
            go.Scattergl(
                x=rel, y=vz_display,
                mode='lines', line=dict(color=VZ_COLOR, width=1.5),
                name='VZ',
                hovertemplate='%{y:.3f} mm/s<extra></extra>',
            )
        ],
        layout=go.Layout(
            plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
            margin=dict(l=52, r=12, t=10, b=32),
            hovermode='x unified',
            font=dict(color=TICK_CLR, size=10),
            xaxis=go.layout.XAxis(
                gridcolor=GRID_CLR, color=TICK_CLR, zeroline=False,
                title=go.layout.xaxis.Title(text='s', font=dict(size=10, color=TICK_CLR)),
                tickfont=dict(size=10),
            ),
            yaxis=go.layout.YAxis(**YAXIS_VEL),
            showlegend=False,
            uirevision='vz-time',
        ),
    )


def build_fft_figure(freqs, fft_vals, x_max):
    traces = []
    if freqs is not None and fft_vals is not None and len(freqs) > 0:
        traces.append(go.Scattergl(
            x=freqs, y=fft_vals,
            mode='lines', fill='tozeroy',
            line=dict(color=VZ_COLOR, width=1.5),
            fillcolor='rgba(88,166,255,0.15)',
            name='FFT',
            hovertemplate='%{x:.2f} Hz  %{y:.3f} mm/s<extra></extra>',
        ))
    return go.Figure(
        data=traces,
        layout=go.Layout(
            plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
            margin=dict(l=52, r=12, t=10, b=32),
            hovermode='x unified',
            font=dict(color=TICK_CLR, size=10),
            xaxis=go.layout.XAxis(
                gridcolor=GRID_CLR, color=TICK_CLR, zeroline=False,
                range=[0, x_max],
                title=go.layout.xaxis.Title(text='Hz', font=dict(size=10, color=TICK_CLR)),
                tickfont=dict(size=10),
            ),
            yaxis=go.layout.YAxis(**{
                **YAXIS_VEL,
                'title': dict(text='mm/s', font=dict(size=10, color=TICK_CLR)),
                'rangemode': 'nonnegative',
            }),
            showlegend=False,
            uirevision='fft',
        ),
    )
