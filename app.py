import dash
from dash import dcc, html, Input, Output
import plotly.graph_objs as go
import numpy as np
import random
import math
import time
from collections import deque

# Initialize Dash app
app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "Accelerometer Monitor"

# Configuration
WINDOW_SIZE = 256
SAMPLING_RATE = 10.0  # Hz
MAX_TIME_POINTS = 100

# Data storage
data = {
    'x': deque(maxlen=WINDOW_SIZE),
    'y': deque(maxlen=WINDOW_SIZE),
    'z': deque(maxlen=WINDOW_SIZE)
}

time_data = {
    'x': deque(maxlen=MAX_TIME_POINTS),
    'y': deque(maxlen=MAX_TIME_POINTS),
    'z': deque(maxlen=MAX_TIME_POINTS),
    'time': deque(maxlen=MAX_TIME_POINTS)
}

# App layout
app.layout = html.Div([
    # Compact Header
    html.Div([
        html.Div([
            html.H1("Accelerometer Monitor", 
                    style={'margin': '0', 'color': '#e8eaed', 'fontSize': '1.5rem', 'fontWeight': '400', 'letterSpacing': '-0.5px', 'display': 'inline-block'}),
            html.Div("Real-time FFT Analysis", 
                   style={'margin': '4px 0 0 0', 'color': '#9aa0a6', 'fontSize': '0.85rem', 'fontWeight': '400', 'display': 'inline-block', 'marginLeft': '16px'}),
            html.Div([
                html.Span(f"{SAMPLING_RATE}Hz", style={'margin': '0 20px 0 0', 'color': '#9aa0a6', 'fontSize': '0.85rem'}),
                html.Span(f"{WINDOW_SIZE} samples", style={'margin': '0 20px 0 0', 'color': '#9aa0a6', 'fontSize': '0.85rem'}),
                html.Span("100ms refresh", style={'color': '#9aa0a6', 'fontSize': '0.85rem'}),
            ], className='header-stats', style={'display': 'inline-block', 'float': 'right', 'marginTop': '4px'})
        ])
    ], style={
        'padding': '20px 24px',
        'background': '#202124',
        'borderBottom': '1px solid #3c4043'
    }),
    
    # Main Content - Grid Layout
    html.Div([
        # Time Domain Column
        html.Div([
            html.Div("Time Domain", style={
                'color': '#9aa0a6',
                'fontSize': '0.75rem',
                'fontWeight': '500',
                'textTransform': 'uppercase',
                'letterSpacing': '0.5px',
                'marginBottom': '12px',
                'paddingLeft': '4px'
            }),
            dcc.Graph(id='x-time-graph', style={'height': '27vh', 'marginBottom': '4px'}, config={'displayModeBar': False, 'displaylogo': False}),
            dcc.Graph(id='y-time-graph', style={'height': '27vh', 'marginBottom': '4px'}, config={'displayModeBar': False, 'displaylogo': False}),
            dcc.Graph(id='z-time-graph', style={'height': '27vh'}, config={'displayModeBar': False, 'displaylogo': False}),
        ], className='responsive-column', style={
            'width': '49%',
            'display': 'inline-block',
            'verticalAlign': 'top',
            'padding': '16px 8px 16px 16px',
            'boxSizing': 'border-box'
        }),
        
        # Frequency Domain Column
        html.Div([
            html.Div("Frequency Spectrum", style={
                'color': '#9aa0a6',
                'fontSize': '0.75rem',
                'fontWeight': '500',
                'textTransform': 'uppercase',
                'letterSpacing': '0.5px',
                'marginBottom': '12px',
                'paddingLeft': '4px'
            }),
            dcc.Graph(id='x-freq-graph', style={'height': '27vh', 'marginBottom': '4px'}, config={'displayModeBar': False, 'displaylogo': False}),
            dcc.Graph(id='y-freq-graph', style={'height': '27vh', 'marginBottom': '4px'}, config={'displayModeBar': False, 'displaylogo': False}),
            dcc.Graph(id='z-freq-graph', style={'height': '27vh'}, config={'displayModeBar': False, 'displaylogo': False}),
        ], className='responsive-column', style={
            'width': '49%',
            'display': 'inline-block',
            'verticalAlign': 'top',
            'padding': '16px 16px 16px 8px',
            'boxSizing': 'border-box'
        }),
    ], style={'height': 'calc(100vh - 85px)', 'overflow': 'hidden', 'whiteSpace': 'nowrap'}),
    
    # Update interval (100ms = 10Hz)
    dcc.Interval(
        id='interval-component',
        interval=100,  # milliseconds
        n_intervals=0
    )
], style={
    'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    'backgroundColor': '#17181a',
    'height': '100vh',
    'margin': '0',
    'overflow': 'hidden'
})

def simulate_accelerometer_data():
    """Generate simulated accelerometer data"""
    timestamp = time.time()
    return {
        'x': math.sin(timestamp) + random.uniform(-0.1, 0.1),
        'y': math.cos(timestamp * 1.2) + random.uniform(-0.1, 0.1),
        'z': math.sin(timestamp / 2.0) + random.uniform(-0.1, 0.1),
        'time': timestamp
    }

def compute_fft(axis_data):
    """Compute FFT for given axis data"""
    if len(axis_data) < WINDOW_SIZE:
        return [], []
    
    samples = np.array(axis_data)
    # Remove DC component and apply Hamming window
    windowed = (samples - np.mean(samples)) * np.hamming(WINDOW_SIZE)
    fft_vals = np.fft.rfft(windowed)
    
    # Normalize magnitude
    freq_magnitude = np.abs(fft_vals) / (WINDOW_SIZE * 0.54)
    freq_magnitude[1:] *= 2  # Account for positive frequencies only
    
    # Compute frequency axis
    freq_axis = np.fft.rfftfreq(WINDOW_SIZE, d=1.0/SAMPLING_RATE)
    
    return freq_axis, freq_magnitude

@app.callback(
    [Output('x-time-graph', 'figure'),
     Output('y-time-graph', 'figure'),
     Output('z-time-graph', 'figure'),
     Output('x-freq-graph', 'figure'),
     Output('y-freq-graph', 'figure'),
     Output('z-freq-graph', 'figure')],
    Input('interval-component', 'n_intervals')
)
def update_graphs(n):
    """Update all graphs with new data"""
    # Get new data point
    new_data = simulate_accelerometer_data()
    
    # Update data buffers
    for axis in ['x', 'y', 'z']:
        data[axis].append(new_data[axis])
        time_data[axis].append(new_data[axis])
    time_data['time'].append(new_data['time'])
    
    # Define colors and axis info - clean Google-inspired colors
    axis_info = {
        'x': {'color': '#ea4335', 'name': 'X'},
        'y': {'color': '#34a853', 'name': 'Y'},
        'z': {'color': '#4285f4', 'name': 'Z'}
    }
    
    time_figures = []
    freq_figures = []
    
    for axis in ['x', 'y', 'z']:
        info = axis_info[axis]
        
        # Create clean time domain figure
        time_fig = go.Figure()
        time_fig.add_trace(go.Scatter(
            y=list(time_data[axis]),
            mode='lines',
            line=dict(color=info['color'], width=1.5),
            hovertemplate='%{y:.3f}<extra></extra>'
        ))
        time_fig.update_layout(
            title=dict(
                text=f'{info["name"]}-axis',
                font=dict(size=13, color='#9aa0a6'),
                x=0.02,
                xanchor='left',
                y=0.98,
                yanchor='top'
            ),
            xaxis=dict(
                gridcolor='#3c4043',
                color='#9aa0a6',
                showticklabels=False,
                zeroline=False
            ),
            yaxis=dict(
                gridcolor='#3c4043',
                color='#9aa0a6',
                range=[-1.5, 1.5],
                zeroline=True,
                zerolinecolor='#5f6368',
                zerolinewidth=1
            ),
            plot_bgcolor='#202124',
            paper_bgcolor='#202124',
            margin=dict(l=45, r=10, t=30, b=25),
            showlegend=False,
            hovermode='closest'
        )
        time_figures.append(time_fig)
        
        # Create clean frequency domain figure
        freq_axis_vals, freq_magnitude = compute_fft(data[axis])
        
        freq_fig = go.Figure()
        if len(freq_axis_vals) > 0:
            freq_fig.add_trace(go.Scatter(
                x=freq_axis_vals,
                y=freq_magnitude,
                mode='lines',
                line=dict(color=info['color'], width=1.5),
                fill='tozeroy',
                fillcolor=f'rgba({int(info["color"][1:3], 16)}, {int(info["color"][3:5], 16)}, {int(info["color"][5:7], 16)}, 0.2)',
                hovertemplate='%{x:.2f} Hz<br>%{y:.3f}<extra></extra>'
            ))
        
        freq_fig.update_layout(
            title=dict(
                text=f'{info["name"]}-axis',
                font=dict(size=13, color='#9aa0a6'),
                x=0.02,
                xanchor='left',
                y=0.98,
                yanchor='top'
            ),
            xaxis=dict(
                gridcolor='#3c4043',
                color='#9aa0a6',
                tickfont=dict(size=10),
                zeroline=False
            ),
            yaxis=dict(
                gridcolor='#3c4043',
                color='#9aa0a6',
                zeroline=False
            ),
            plot_bgcolor='#202124',
            paper_bgcolor='#202124',
            margin=dict(l=45, r=10, t=30, b=30),
            showlegend=False,
            hovermode='closest'
        )
        freq_figures.append(freq_fig)
    
    return time_figures + freq_figures

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
