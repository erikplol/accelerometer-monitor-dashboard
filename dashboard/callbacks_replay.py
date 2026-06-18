"""Dash callback registration for recorded CSV replay dashboard."""

import base64
import csv
import io
import os
import traceback

import dash
from dash import Input, Output, State, ctx
import numpy as np
import plotly.graph_objs as go

from dashboard.fft import refresh_fft_cache
from dashboard.theme import CARD_BG, GRID_CLR, TICK_CLR, YAXIS_VEL, ZERO_CLR, light_style


def register_callbacks(
    app,
    sampling_rate,
    ui_interval_ms,
    max_display_pts,
    fft_window_seconds,
    thresh_green,
    thresh_yellow,
):
    fft_update_every_n_intervals = max(1, int(15_000 / ui_interval_ms))
    fft_cache = {'x': [], 'y': [], 'sig': None}
    replay_cache = {
        'samples': [],
        'az': np.asarray([], dtype=float),
        'vz': np.asarray([], dtype=float),
        'hzz': np.asarray([], dtype=float),
        'rel': np.asarray([], dtype=float),
        'rate_hz': float(sampling_rate),
        'source': '',
    }

    def _default_playback_state():
        return {
            'paused': False,
            'mode': 'live',
            'cursor': 0,
            'total': 0,
            'rate_hz': float(sampling_rate),
            'source': '',
            'error': '',
            'upload_sig': '',
            'elapsed_s': 0.0,
            'duration_s': 0.0,
        }

    def _logs_dir() -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')

    def _safe_float(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _parse_replay_rows(csv_text: str):
        rows = []
        header = None

        for row in csv.reader(io.StringIO(csv_text)):
            if not row:
                continue

            first = row[0].strip() if row else ''
            if first.startswith('#'):
                continue

            if header is None:
                header = [col.strip() for col in row]
                continue

            if len(row) < len(header):
                row = row + [''] * (len(header) - len(row))

            values = [cell.strip() for cell in row[: len(header)]]
            mapped = dict(zip(header, values))

            az_ms2 = _safe_float(mapped.get('az_ms2'))
            if az_ms2 is None:
                continue

            vz_mms = _safe_float(mapped.get('vz_mms'))
            unix_time = _safe_float(mapped.get('unix_time'))
            rate_hz = _safe_float(mapped.get('rate_hz'))
            hzz_hz = _safe_float(mapped.get('hzz_hz'))

            rows.append({
                'az_ms2': az_ms2,
                'vz_mms': float(vz_mms) if vz_mms is not None else 0.0,
                'unix_time': unix_time,
                'rate_hz': float(rate_hz) if rate_hz is not None else 0.0,
                'hzz_hz': float(hzz_hz) if hzz_hz is not None else 0.0,
            })

        if len(rows) < 2:
            raise ValueError('CSV replay needs at least 2 rows with az_ms2 data.')

        row_rates = [row['rate_hz'] for row in rows if row['rate_hz'] > 0]
        unix_times = [row['unix_time'] for row in rows if row['unix_time'] is not None]
        inferred_rate = float(sampling_rate)

        if row_rates:
            inferred_rate = float(np.median(np.asarray(row_rates, dtype=float)))
        elif len(unix_times) >= 2:
            unix_array = np.asarray(unix_times, dtype=float)
            deltas = np.diff(unix_array)
            deltas = deltas[deltas > 0]
            if deltas.size:
                inferred_rate = float(1.0 / np.median(deltas))

        inferred_rate = max(1.0, inferred_rate)

        if len(unix_times) >= 2:
            first_time = unix_times[0]
            for row in rows:
                if row['unix_time'] is None:
                    row['rel_s'] = 0.0
                else:
                    row['rel_s'] = float(row['unix_time'] - first_time)
        else:
            for idx, row in enumerate(rows):
                row['rel_s'] = float(idx) / inferred_rate

        return rows, inferred_rate

    def _load_replay_csv(csv_text: str, source_name: str):
        rows, inferred_rate = _parse_replay_rows(csv_text)
        replay_cache['samples'] = rows
        replay_cache['az'] = np.asarray([row['az_ms2'] for row in rows], dtype=float)
        replay_cache['vz'] = np.asarray([row['vz_mms'] for row in rows], dtype=float)
        replay_cache['hzz'] = np.asarray([row['hzz_hz'] for row in rows], dtype=float)
        replay_cache['rel'] = np.asarray([row['rel_s'] for row in rows], dtype=float)
        replay_cache['rate_hz'] = inferred_rate
        replay_cache['source'] = source_name
        duration_s = float(replay_cache['rel'][-1]) if replay_cache['rel'].size else 0.0
        return {
            'paused': False,
            'mode': 'replay',
            'cursor': 1,
            'total': len(rows),
            'rate_hz': inferred_rate,
            'source': source_name,
            'error': '',
            'upload_sig': '',
            'elapsed_s': 0.0,
            'duration_s': duration_s,
        }

    def _play_button_label(playback_data):
        data = playback_data or {}
        mode = data.get('mode', 'live')
        paused = bool(data.get('paused', False))
        if mode == 'replay':
            return '▶  Resume Replay' if paused else '⏸  Pause Replay'
        return '▶  Play Replay' if paused else '⏸  Pause Replay'

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
    def update_dashboard(n_intervals, playback_data):
        playback_data = {**_default_playback_state(), **(playback_data or {})}
        replay_mode = playback_data.get('mode') == 'replay' and bool(replay_cache['samples'])

        if playback_data.get('paused', False) and not replay_mode:
            return tuple([dash.no_update] * 11)

        if replay_mode:
            total = int(playback_data.get('total', len(replay_cache['samples'])))
            cursor = max(0, min(int(playback_data.get('cursor', 0)), total))
            az_all = replay_cache['az'][:cursor]
            vz_all = replay_cache['vz'][:cursor]
            hzz_all = replay_cache['hzz'][:cursor]
            rel_all = replay_cache['rel'][:cursor]
        else:
            az_all = np.asarray([], dtype=float)
            vz_all = np.asarray([], dtype=float)
            hzz_all = np.asarray([], dtype=float)
            rel_all = np.asarray([], dtype=float)

        if rel_all.size and len(rel_all) != len(az_all):
            aligned_n = min(len(rel_all), len(az_all))
            rel_all = rel_all[-aligned_n:]
            az_all = az_all[-aligned_n:]

        if len(az_all) > max_display_pts:
            step = max(1, len(az_all) // max_display_pts)
            az_display = az_all[::step]
            rel = rel_all[::step]
        else:
            az_display = az_all
            rel = rel_all

        az_color = '#58a6ff'
        empty_style = {'color': '#f85149', 'fontSize': '0.82rem', 'marginLeft': 18}
        ok_style = {'color': '#3fb950', 'fontSize': '0.82rem', 'marginLeft': 18}

        if replay_mode:
            actual_rate = float(playback_data.get('rate_hz', replay_cache['rate_hz']))
            source = playback_data.get('source', replay_cache['source'])
            cursor = max(0, min(int(playback_data.get('cursor', 0)), int(playback_data.get('total', 0))))
            total = int(playback_data.get('total', 0))
            conn_label = f'● Replay {source} ({cursor}/{total})'
            conn_style = ok_style
        else:
            actual_rate = float(sampling_rate)
            conn_label = '● Replay idle - load CSV to start'
            conn_style = empty_style

        n_1s = max(1, int(actual_rate))
        vz_window = vz_all[-n_1s:] if len(vz_all) else np.asarray([], dtype=float)
        az_window = az_all[-n_1s:] if len(az_all) else np.asarray([], dtype=float)

        if len(vz_window):
            vz_rms = float(np.sqrt(np.mean(vz_window ** 2)))
            rms_label = f'{vz_rms:.2f}'
        else:
            vz_rms = 0.0
            rms_label = '-'

        if len(az_window):
            az_rms = float(np.sqrt(np.mean(az_window ** 2)))
            az_rms_label = f'{az_rms:.2f}'
        else:
            az_rms_label = '-'

        hzz_label = f'{hzz_all[-1]:.1f}' if len(hzz_all) else '-'
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
                hovertemplate='%{y:.3f} m/s²<extra></extra>',
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
                uirevision='az-time-replay',
            ),
        )

        fft_traces = []

        should_update_fft = (n_intervals % fft_update_every_n_intervals == 0)
        refresh_fft_cache(
            fft_cache,
            az_all,
            actual_rate,
            fft_window_seconds,
            should_update_fft,
        )

        if fft_cache['sig'] is not None and fft_cache['x']:
            fft_traces.append(go.Scattergl(
                x=fft_cache['x'],
                y=fft_cache['y'],
                mode='lines',
                fill='tozeroy',
                line=dict(color=az_color, width=1.5),
                fillcolor='rgba(88,166,255,0.15)',
                name='FFT',
                hovertemplate='%{x:.2f} Hz  %{y:.3f} m/s²<extra></extra>',
            ))

        x_max = max(actual_rate / 2.0, 5.0)
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
                uirevision='fft-replay',
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

    @app.callback(
        Output('playback-store', 'data'),
        [Input('btn-toggle-play', 'n_clicks'),
         Input('replay-upload', 'contents'),
         Input('replay-upload', 'filename'),
         Input('btn-replay-last', 'n_clicks'),
         Input('btn-stop-replay', 'n_clicks')],
        State('playback-store', 'data'),
        prevent_initial_call=True,
    )
    def manage_playback_controls(n_toggle, upload_contents, upload_filename, n_replay_last, n_stop_replay, playback_data):
        del n_toggle, n_replay_last, n_stop_replay

        playback_data = {**_default_playback_state(), **(playback_data or {})}
        trigger = ctx.triggered_id
        triggered_props = list((ctx.triggered_prop_ids or {}).keys())
        triggered_set = set(triggered_props)
        trigger = ctx.triggered_id
        upload_size = len(upload_contents) if upload_contents else 0
        upload_sig = f'{upload_filename}|{upload_size}' if upload_filename and upload_size else ''
        is_new_upload = bool(upload_sig) and upload_sig != playback_data.get('upload_sig', '')
        upload_triggered = (
            'replay-upload.contents' in triggered_set
            or 'replay-upload.filename' in triggered_set
            or is_new_upload
        )

        print(
            '[ReplayDebug] manage_playback '
            f'trigger={trigger} props={triggered_props} '
            f'filename={upload_filename!r} upload_size={upload_size} '
            f'new_upload={is_new_upload} upload_triggered={upload_triggered} '
            f'mode={playback_data.get("mode")} paused={playback_data.get("paused")} '
            f'cursor={playback_data.get("cursor")}/{playback_data.get("total")}'
        )

        try:
            if upload_triggered:
                if not upload_contents:
                    failed_state = _default_playback_state()
                    failed_state['error'] = 'Replay load failed: upload had no file content.'
                    failed_state['upload_sig'] = upload_sig
                    print('[ReplayDebug] upload missing content')
                    return failed_state

                payload = upload_contents
                if ',' in payload:
                    payload = payload.split(',', 1)[1]

                decoded = base64.b64decode(payload)
                csv_text = decoded.decode('utf-8', errors='replace')
                source_name = upload_filename or 'uploaded_replay.csv'
                replay_state = _load_replay_csv(csv_text, source_name)
                replay_state['upload_sig'] = upload_sig
                print(
                    '[ReplayDebug] upload loaded '
                    f'source={source_name} decoded_bytes={len(decoded)} '
                    f'total={replay_state.get("total")} rate={replay_state.get("rate_hz"):.2f}'
                )
                return replay_state

            if trigger == 'btn-toggle-play':
                playback_data['paused'] = not bool(playback_data.get('paused', False))
                playback_data['error'] = ''
                print(f'[ReplayDebug] toggle paused={playback_data["paused"]}')
                return playback_data

            if trigger == 'btn-stop-replay':
                live_state = _default_playback_state()
                replay_cache['samples'] = []
                replay_cache['az'] = np.asarray([], dtype=float)
                replay_cache['vz'] = np.asarray([], dtype=float)
                replay_cache['hzz'] = np.asarray([], dtype=float)
                replay_cache['rel'] = np.asarray([], dtype=float)
                replay_cache['rate_hz'] = float(sampling_rate)
                replay_cache['source'] = ''
                live_state['upload_sig'] = playback_data.get('upload_sig', '')
                live_state['elapsed_s'] = 0.0
                live_state['duration_s'] = 0.0
                print('[ReplayDebug] stop replay and clear cache')
                return live_state

            if trigger == 'btn-replay-last':
                logs_dir = _logs_dir()
                print(f'[ReplayDebug] replay-last logs_dir={logs_dir}')
                if not os.path.isdir(logs_dir):
                    failed_state = _default_playback_state()
                    failed_state['error'] = f'Replay load failed: logs directory not found ({logs_dir})'
                    return failed_state

                candidates = [
                    os.path.join(logs_dir, name)
                    for name in os.listdir(logs_dir)
                    if name.endswith('.csv')
                ]
                if not candidates:
                    failed_state = _default_playback_state()
                    failed_state['error'] = 'Replay load failed: no CSV files found in logs directory.'
                    return failed_state

                latest = max(candidates, key=os.path.getmtime)
                with open(latest, 'r', encoding='utf-8', newline='') as handle:
                    csv_text = handle.read()
                replay_state = _load_replay_csv(csv_text, os.path.basename(latest))
                print(
                    '[ReplayDebug] replay-last loaded '
                    f'source={replay_state.get("source")} total={replay_state.get("total")} '
                    f'rate={replay_state.get("rate_hz"):.2f}'
                )
                return replay_state

            print('[ReplayDebug] unhandled trigger, returning no_update')
            return dash.no_update

        except Exception as exc:
            print(f'[ReplayDebug] ERROR in manage_playback: {exc}')
            print(traceback.format_exc())
            failed_state = _default_playback_state()
            failed_state['error'] = f'Replay callback failed: {exc}'
            return failed_state

    @app.callback(
        Output('playback-store', 'data', allow_duplicate=True),
        Input('interval-component', 'n_intervals'),
        State('playback-store', 'data'),
        prevent_initial_call=True,
    )
    def advance_playback_interval(n_intervals, playback_data):
        playback_data = {**_default_playback_state(), **(playback_data or {})}

        if n_intervals % 20 == 0:
            print(
                '[ReplayDebug] interval tick '
                f'n={n_intervals} mode={playback_data.get("mode")} paused={playback_data.get("paused")}'
            )

        if playback_data.get('mode') != 'replay' or playback_data.get('paused', False):
            return dash.no_update

        total = max(0, int(playback_data.get('total', 0)))
        if total <= 0:
            return dash.no_update

        dt_s = ui_interval_ms / 1000.0
        elapsed_s = max(0.0, float(playback_data.get('elapsed_s', 0.0)) + dt_s)
        duration_s = max(0.0, float(playback_data.get('duration_s', 0.0)))
        if duration_s > 0:
            elapsed_s = min(elapsed_s, duration_s)

        if replay_cache['rel'].size:
            next_cursor = int(np.searchsorted(replay_cache['rel'], elapsed_s, side='right'))
        else:
            next_cursor = int(playback_data.get('cursor', 0))
        next_cursor = max(1, min(total, next_cursor))

        playback_data['cursor'] = next_cursor
        playback_data['elapsed_s'] = elapsed_s
        playback_data['paused'] = (next_cursor >= total) or (duration_s > 0 and elapsed_s >= duration_s)
        playback_data['error'] = ''
        if n_intervals % 10 == 0:
            print(
                '[ReplayDebug] interval advance '
                f'next_cursor={next_cursor} total={total} elapsed={elapsed_s:.2f}s duration={duration_s:.2f}s'
            )
        return playback_data

    @app.callback(
        Output('btn-toggle-play', 'children'),
        Input('playback-store', 'data'),
    )
    def update_play_button_label(playback_data):
        return _play_button_label(playback_data)

    @app.callback(
        Output('replay-status', 'children'),
        Input('playback-store', 'data'),
    )
    def update_replay_status(playback_data):
        playback_data = {**_default_playback_state(), **(playback_data or {})}

        if playback_data.get('error'):
            return playback_data['error']

        if playback_data.get('mode') != 'replay':
            return 'Replay: off'

        cursor = max(0, int(playback_data.get('cursor', 0)))
        total = max(0, int(playback_data.get('total', 0)))
        rate_hz = max(0.0, float(playback_data.get('rate_hz', 0.0)))
        elapsed_s = max(0.0, float(playback_data.get('elapsed_s', 0.0)))
        duration_s = max(0.0, float(playback_data.get('duration_s', 0.0)))
        source = playback_data.get('source', 'log.csv')

        if total <= 0:
            return f'Replay loaded: {source} (0 samples)'
        if cursor >= total:
            return f'Replay finished: {source} ({total} samples, {duration_s:.1f}s)'
        if playback_data.get('paused', False):
            return f'Replay paused: {source} ({elapsed_s:.1f}/{duration_s:.1f}s, {cursor}/{total}) at {rate_hz:.1f} Hz'
        return f'Replay playing: {source} ({elapsed_s:.1f}/{duration_s:.1f}s, {cursor}/{total}) at {rate_hz:.1f} Hz'
