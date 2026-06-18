"""Dash callback registration for dashboard interactions and graph updates."""

import os
import threading
import time
import zipfile

import dash
from dash import Input, Output, State, ctx, dcc
import numpy as np
import plotly.graph_objs as go

from data_collect.data_collect import (
    get_actual_rate,
    get_histories,
    get_histories_display,
    is_connected,
    is_witmotion_connected,
    save_log,
    start_logging,
    stop_logging,
    LOG_DIR,
)
from dashboard.fft import compute_az_fft
from dashboard.theme import CARD_BG, GRID_CLR, TICK_CLR, YAXIS_VEL, ZERO_CLR, light_style


def register_callbacks(
    app,
    reader,
    wit_reader,
    sampling_rate,
    ui_interval_ms,
    max_display_pts,
    fft_window_seconds,
    thresh_green,
    thresh_yellow,
):
    # Refresh FFT every ~15 seconds in a background thread (non-blocking).
    fft_update_every_n_intervals = max(1, int(15_000 / ui_interval_ms))
    fft_cache = {'x': [], 'y': [], 'sig': None}
    fft_lock = threading.Lock()
    fft_busy = threading.Event()   # set while background FFT is running
    log_duration_s = 90

    def _run_fft_background(az_snapshot, effective_rate):
        """Compute FFT off the callback thread and update fft_cache."""
        freqs, fft_vals, n_samples = compute_az_fft(
            az_snapshot, effective_rate, fft_window_seconds
        )
        if freqs:
            with fft_lock:
                fft_cache['x'] = freqs
                fft_cache['y'] = fft_vals
                fft_cache['sig'] = (len(az_snapshot), az_snapshot[-1], n_samples)
        fft_busy.clear()

    @app.callback(
        [Output('vz-time-graph', 'figure'),
         Output('fft-graph', 'figure'),
         Output('rms-value', 'children'),
         Output('az-rms-value', 'children'),
         Output('recv-hz', 'children'),
         Output('dominant-fft-hz', 'children'),
         Output('light-red', 'style'),
         Output('light-yellow', 'style'),
         Output('light-green', 'style'),
         Output('conn-status', 'children'),
         Output('conn-status', 'style')],
        Input('interval-component', 'n_intervals'),
        State('playback-store', 'data'),
    )
    def update_dashboard(n, playback_data):
        if (playback_data or {}).get('paused', False):
            return tuple([dash.no_update] * 11)

        # Cheap display fetch — only the last max_display_pts points
        h = get_histories_display(max_pts=max_display_pts)
        az_all = h.get('az_ms2', [])
        vz_all = h.get('vz_mms', [])
        hzz_all = h.get('hzz_hz', [])
        rel_all = h.get('rel_s', [])

        # Data is already trimmed to max_display_pts — no further downsampling needed.
        az_display = az_all
        rel = rel_all

        az_color = '#58a6ff'
        empty_style = {'color': '#f85149', 'fontSize': '0.82rem', 'marginLeft': 18}
        ok_style = {'color': '#3fb950', 'fontSize': '0.82rem', 'marginLeft': 18}

        pix_connected = is_connected()
        wit_connected = is_witmotion_connected()
        actual_rate = get_actual_rate()
        if pix_connected and wit_connected:
            conn_label = f'● Pixhawk {actual_rate:.1f} Hz | Witmotion connected'
        elif pix_connected:
            conn_label = f'● Pixhawk {actual_rate:.1f} Hz | Witmotion disconnected'
        elif wit_connected:
            conn_label = '● Pixhawk disconnected | Witmotion connected'
        else:
            conn_label = '● Pixhawk disconnected | Witmotion disconnected'
        conn_style = ok_style if (pix_connected or wit_connected) else empty_style

        n_1s = max(1, int(sampling_rate))
        vz_window = vz_all[-n_1s:] if vz_all else []
        az_window = az_all[-n_1s:] if az_all else []

        if vz_window:
            vz_rms = float(np.sqrt(np.mean(np.array(vz_window) ** 2)))
            rms_label = f'{vz_rms:.2f}'
        else:
            vz_rms = 0.0
            rms_label = '-'

        if az_window:
            az_rms = float(np.sqrt(np.mean(np.array(az_window) ** 2)))
            az_rms_label = f'{az_rms:.2f}'
        else:
            az_rms_label = '-'

        hzz_label = f'{hzz_all[-1]:.1f}' if hzz_all else '-'
        dominant_fft_hz_label = f'{actual_rate:.1f}' if actual_rate > 0 else '-'

        is_red = vz_rms >= thresh_yellow
        is_yellow = thresh_green <= vz_rms < thresh_yellow
        is_green = vz_rms < thresh_green

        style_red = light_style(is_red, '#f85149', 'rgba(248,81,73,0.55)')
        style_yellow = light_style(is_yellow, '#d29922', 'rgba(210,153,34,0.55)')
        style_green = light_style(is_green, '#3fb950', 'rgba(63,185,80,0.55)')

        xaxis_s = go.layout.XAxis(
            gridcolor=GRID_CLR,
            color=TICK_CLR,
            zeroline=False,
            title=go.layout.xaxis.Title(text='s', font=dict(size=10, color=TICK_CLR)),
            tickfont=dict(size=10),
        )
        yaxis_v = go.layout.YAxis(
            gridcolor=GRID_CLR,
            color=TICK_CLR,
            zeroline=True,
            zerolinecolor=ZERO_CLR,
            zerolinewidth=1,
            title=dict(text='m/s²', font=dict(size=10, color=TICK_CLR)),
            tickfont=dict(size=10),
            showgrid=True,
        )

        time_fig = go.Figure(
            data=[go.Scattergl(
                x=rel,
                y=az_display,
                mode='lines',
                line=dict(color=az_color, width=1.5),
                name='AZ',
            )],
            layout=go.Layout(
                plot_bgcolor=CARD_BG,
                paper_bgcolor=CARD_BG,
                margin=dict(l=52, r=12, t=10, b=32),
                hovermode='x unified',
                font=dict(color=TICK_CLR, size=10),
                xaxis=xaxis_s,
                yaxis=yaxis_v,
                showlegend=False,
                uirevision='az-time',
            ),
        )

        fft_traces = []

        effective_rate = actual_rate if actual_rate > 0 else sampling_rate
        should_update_fft = (n % fft_update_every_n_intervals == 0)
        if should_update_fft and not fft_busy.is_set():
            # Take a snapshot of the full history for FFT (non-blocking grab)
            try:
                full_h = get_histories()
                az_snapshot = full_h.get('az_ms2', [])
            except Exception:
                az_snapshot = []
            if len(az_snapshot) >= 64:
                fft_busy.set()
                t = threading.Thread(
                    target=_run_fft_background,
                    args=(az_snapshot, effective_rate),
                    daemon=True,
                )
                t.start()

        with fft_lock:
            cached_x = list(fft_cache['x'])
            cached_y = list(fft_cache['y'])
            cached_sig = fft_cache['sig']

        if cached_sig is not None and cached_x:
            fft_traces.append(go.Scattergl(
                x=cached_x,
                y=cached_y,
                mode='lines',
                fill='tozeroy',
                line=dict(color=az_color, width=1.5),
                fillcolor='rgba(88,166,255,0.15)',
                name='FFT',
            ))

        x_max = max(effective_rate / 2.0, 5.0)
        fft_fig = go.Figure(
            data=fft_traces,
            layout=go.Layout(
                plot_bgcolor=CARD_BG,
                paper_bgcolor=CARD_BG,
                margin=dict(l=52, r=12, t=10, b=32),
                hovermode='x unified',
                font=dict(color=TICK_CLR, size=10),
                xaxis=go.layout.XAxis(
                    gridcolor=GRID_CLR,
                    color=TICK_CLR,
                    zeroline=False,
                    range=[0, x_max],
                    title=go.layout.xaxis.Title(text='Hz', font=dict(size=10, color=TICK_CLR)),
                    tickfont=dict(size=10),
                ),
                yaxis=go.layout.YAxis(
                    **{**YAXIS_VEL, 'title': dict(text='m/s²', font=dict(size=10, color=TICK_CLR)), 'rangemode': 'nonnegative'}
                ),
                showlegend=False,
                uirevision='fft',
            ),
        )

        return (
            time_fig,
            fft_fig,
            rms_label,
            az_rms_label,
            hzz_label,
            dominant_fft_hz_label,
            style_red,
            style_yellow,
            style_green,
            conn_label,
            conn_style,
        )

    def do_stop(log_data, auto=False):
        reader.pause()
        wit_reader.pause()
        try:
            data, stopped_now = stop_logging()
            rpm_val = log_data.get('rpm', 0)
            load_val = log_data.get('load', 0)
            if stopped_now:
                path = save_log(rpm_val, load_val, data)
                fname = os.path.basename(path)
            else:
                path = log_data.get('last_path', '')
                fname = os.path.basename(path) if path else 'already-saved'
        finally:
            reader.resume()
            wit_reader.resume()

        if stopped_now:
            prefix = '✔ Auto-saved' if auto else '✔ Saved'
            status = f'{prefix} {len(data)} samples - {fname}'
        else:
            status = f'✔ Save already finished - {fname}'

        return (
            status,
            {'active': False, 'rpm': rpm_val, 'load': load_val, 'start_time': 0, 'last_path': path},
        )

    @app.callback(
        [Output('log-status', 'children'), Output('log-store', 'data')],
        [Input('btn-start-log', 'n_clicks'),
         Input('btn-stop-log', 'n_clicks'),
         Input('interval-component', 'n_intervals')],
        [State('rpm-input', 'value'), State('load-input', 'value'), State('log-store', 'data')],
        prevent_initial_call=True,
    )
    def handle_logging(n_start, n_stop, n_intervals, rpm, load_w, log_data):
        log_data = log_data or {}
        active = log_data.get('active', False)
        triggered_props = set((ctx.triggered_prop_ids or {}).keys())
        start_clicked = 'btn-start-log.n_clicks' in triggered_props
        stop_clicked = 'btn-stop-log.n_clicks' in triggered_props
        interval_ticked = 'interval-component.n_intervals' in triggered_props

        # Button clicks take priority over timer ticks to avoid click/interval races.
        if stop_clicked:
            if not active:
                return 'No active recording.', log_data
            return do_stop(log_data)

        if start_clicked:
            if active:
                return 'Already recording - stop first.', log_data
            rpm_val = int(rpm) if rpm is not None else 0
            load_val = int(load_w) if load_w is not None else 0
            start_logging()
            return (
                f'Recording... {log_duration_s}s | RPM = {rpm_val} | Load = {load_val} W',
                {
                    'active': True,
                    'rpm': rpm_val,
                    'load': load_val,
                    'start_time': time.time(),
                    'last_path': log_data.get('last_path', ''),
                },
            )

        if interval_ticked:
            if not active:
                return dash.no_update, dash.no_update

            elapsed = time.time() - log_data.get('start_time', time.time())
            remaining = log_duration_s - elapsed
            if remaining <= 0:
                return do_stop(log_data, auto=True)

            rpm_val = log_data.get('rpm', 0)
            load_val = log_data.get('load', 0)
            return (
                f'Recording... {int(remaining)}s left | RPM = {rpm_val} | Load = {load_val} W',
                dash.no_update,
            )

        return dash.no_update, dash.no_update

    @app.callback(
        [Output('playback-store', 'data'), Output('btn-toggle-play', 'children')],
        Input('btn-toggle-play', 'n_clicks'),
        State('playback-store', 'data'),
        prevent_initial_call=True,
    )
    def toggle_playback(n_clicks, playback_data):
        paused = not (playback_data or {}).get('paused', False)
        label = '▶  Play View' if paused else '⏸  Pause View'
        return {'paused': paused}, label

    @app.callback(
        Output('download-last-file', 'data'),
        Input('btn-download-last', 'n_clicks'),
        State('log-store', 'data'),
        prevent_initial_call=True,
    )
    def download_last_log(n_clicks, log_data):
        last_path = (log_data or {}).get('last_path', '')
        if last_path and os.path.exists(last_path):
            return dcc.send_file(last_path)

        # Use LOG_DIR from data_collect which points to correct logs directory
        if not os.path.isdir(LOG_DIR):
            return dash.no_update

        candidates = [
            os.path.join(LOG_DIR, name)
            for name in os.listdir(LOG_DIR)
            if name.endswith('.csv')
        ]
        if not candidates:
            return dash.no_update

        latest = max(candidates, key=os.path.getmtime)
        return dcc.send_file(latest)

    @app.callback(
        Output('download-all-files', 'data'),
        Input('btn-download-all', 'n_clicks'),
        prevent_initial_call=True,
    )
    def download_all_logs(n_clicks):
        # Use LOG_DIR from data_collect which points to correct logs directory
        if not os.path.isdir(LOG_DIR):
            return dash.no_update

        candidates = sorted(
            [
                os.path.join(LOG_DIR, name)
                for name in os.listdir(LOG_DIR)
                if name.endswith('.csv')
            ],
            key=os.path.getmtime,
        )
        if not candidates:
            return dash.no_update

        def write_zip(buff):
            with zipfile.ZipFile(buff, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
                for path in candidates:
                    zf.write(path, arcname=os.path.basename(path))

        stamp = time.strftime('%Y%m%d_%H%M%S')
        return dcc.send_bytes(write_zip, f'vibration_logs_{stamp}.zip')
