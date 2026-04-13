#!/usr/bin/env python3
"""Export interactive FFT HTML plots from vibration log CSV data."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import sys
from pathlib import Path

import numpy as np
import plotly.graph_objects as go


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dashboard.fft import compute_az_fft


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_iso_second(iso_text: str | None) -> str | None:
    if iso_text is None:
        return None

    text = iso_text.strip()
    if not text:
        return None

    normalized = text.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(normalized)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        pass

    candidate = text.replace('T', ' ')
    if len(candidate) < 19:
        return None

    second_text = candidate[:19]
    try:
        datetime.strptime(second_text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return second_text


def discover_input_files(input_path: Path, recursive: bool) -> list[Path]:
    if not input_path.exists():
        raise FileNotFoundError(f'Input path not found: {input_path}')

    if input_path.is_file():
        if input_path.suffix.lower() != '.csv':
            raise ValueError(f'Input file must be CSV: {input_path}')
        return [input_path]

    pattern = '**/*.csv' if recursive else '*.csv'
    files = [
        path
        for path in sorted(input_path.glob(pattern))
        if path.is_file() and not path.name.endswith('_fft.csv')
    ]
    if not files:
        raise ValueError(f'No CSV files found in: {input_path}')
    return files


def read_log_csv(csv_path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    metadata: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    header: list[str] | None = None

    with csv_path.open('r', newline='') as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue

            first = row[0].strip()
            if first.startswith('#'):
                key = first[1:].strip()
                value = row[1].strip() if len(row) > 1 else ''
                metadata[key] = value
                continue

            if header is None:
                header = [column.strip() for column in row]
                continue

            if len(row) < len(header):
                row = row + [''] * (len(header) - len(row))

            normalized = [cell.strip() for cell in row[: len(header)]]
            rows.append(dict(zip(header, normalized)))

    if header is None:
        raise ValueError(f'No CSV header found in: {csv_path}')
    if not rows:
        raise ValueError(f'No data rows found in: {csv_path}')

    return metadata, rows


def extract_signal_data(
    rows: list[dict[str, str]],
    value_column: str,
) -> tuple[list[float], list[str], list[float], list[float]]:
    signal_values: list[float] = []
    iso_seconds: list[str] = []
    unix_times: list[float] = []
    row_rates: list[float] = []

    for row in rows:
        signal = _safe_float(row.get(value_column))
        if signal is None:
            continue

        signal_values.append(signal)

        iso_second = _normalize_iso_second(row.get('iso_time'))
        if iso_second is not None:
            iso_seconds.append(iso_second)

        unix_time = _safe_float(row.get('unix_time'))
        if unix_time is not None:
            unix_times.append(unix_time)

        row_rate = _safe_float(row.get('rate_hz'))
        if row_rate is not None and row_rate > 0:
            row_rates.append(row_rate)

    if len(signal_values) < 64:
        raise ValueError(
            f'Need at least 64 valid samples in column "{value_column}", '
            f'got {len(signal_values)}'
        )

    return signal_values, iso_seconds, unix_times, row_rates


def infer_sample_rate(
    iso_seconds: list[str],
    unix_times: list[float],
    row_rates: list[float],
    override_rate_hz: float,
) -> tuple[float, str]:
    if override_rate_hz > 0:
        return override_rate_hz, 'override (--rate)'

    if iso_seconds:
        counts_per_second: dict[str, int] = {}
        for second_key in iso_seconds:
            counts_per_second[second_key] = counts_per_second.get(second_key, 0) + 1

        if counts_per_second:
            counts = np.asarray(list(counts_per_second.values()), dtype=float)
            return float(np.mean(counts)), 'iso_time average rows/second'

    if len(unix_times) >= 2:
        intervals = np.diff(np.asarray(unix_times, dtype=float))
        intervals = intervals[intervals > 0]
        if intervals.size > 0:
            return float(1.0 / np.median(intervals)), 'unix_time median delta'

    if row_rates:
        return float(np.median(np.asarray(row_rates, dtype=float))), 'row rate_hz median'

    raise ValueError(
        'Could not infer sample rate. Use --rate to set it manually.'
    )


def find_top_peaks(
    freqs: list[float],
    amplitudes: list[float],
    peak_count: int,
    peak_min_separation_hz: float,
) -> list[int]:
    if not freqs or not amplitudes or peak_count <= 0:
        return []

    n_points = min(len(freqs), len(amplitudes))
    local_max_indices: list[int] = []

    for idx in range(1, n_points - 1):
        left = amplitudes[idx - 1]
        mid = amplitudes[idx]
        right = amplitudes[idx + 1]
        if mid >= left and mid >= right:
            local_max_indices.append(idx)

    # If no strict local maxima are found, fall back to all bins.
    candidate_indices = local_max_indices if local_max_indices else list(range(n_points))
    candidate_indices.sort(key=lambda idx: amplitudes[idx], reverse=True)

    chosen: list[int] = []
    for idx in candidate_indices:
        freq_hz = freqs[idx]
        too_close = False
        for chosen_idx in chosen:
            if abs(freq_hz - freqs[chosen_idx]) < peak_min_separation_hz:
                too_close = True
                break
        if too_close:
            continue

        chosen.append(idx)
        if len(chosen) >= peak_count:
            break

    chosen.sort(key=lambda idx: freqs[idx])
    return chosen


def export_fft_graph(
    source_csv: Path,
    output_html: Path,
    sample_rate_hz: float,
    fft_samples: int,
    freqs: list[float],
    amplitudes: list[float],
    default_peak_count: int,
    peak_min_separation_hz: float,
    label_decimals: int,
) -> None:
    output_html.parent.mkdir(parents=True, exist_ok=True)

    initial_peak_indices = find_top_peaks(
        freqs=freqs,
        amplitudes=amplitudes,
        peak_count=default_peak_count,
        peak_min_separation_hz=peak_min_separation_hz,
    )
    initial_points = [
        {
            'x': float(freqs[idx]),
            'y': float(amplitudes[idx]),
        }
        for idx in initial_peak_indices
    ]

    fig = go.Figure(
        data=[
            go.Scattergl(
                x=freqs,
                y=amplitudes,
                mode='lines',
                line={'width': 1.4, 'color': '#4a4f9f'},
                name='FFT',
                hovertemplate=(
                    f'X: %{{x:.{label_decimals}f}} Hz'
                    '<br>Y: %{y:.3f} m/s²<extra></extra>'
                ),
            )
        ]
    )

    fig.update_layout(
        template='plotly_white',
        title=(
            f'FFT Spectrum: {source_csv.name}<br>'
            f'Sample Rate: {sample_rate_hz:.2f} Hz | FFT Samples: {fft_samples}'
        ),
        xaxis_title='Frequency (Hz)',
        yaxis_title='Amplitude (m/s²)',
        hovermode='closest',
        clickmode='event',
        legend={
            'orientation': 'v',
            'x': 1.02,
            'xanchor': 'left',
            'y': 1.0,
            'yanchor': 'top',
        },
        margin={'l': 60, 'r': 200, 't': 80, 'b': 60},
        xaxis={
            'showgrid': True,
            'gridcolor': '#dddddd',
            'zeroline': False,
            'ticks': 'outside',
        },
        yaxis={
            'showgrid': True,
            'gridcolor': '#e9e9e9',
            'zeroline': False,
            'ticks': 'outside',
        },
    )

    post_script = f"""
    const gd = document.getElementById('{{plot_id}}');
    const baseData = JSON.parse(JSON.stringify(gd.data));
    const baseLayout = JSON.parse(JSON.stringify(gd.layout));
    const selectedPoints = [];
    const labelDecimals = {label_decimals};
    const initialPoints = {json.dumps(initial_points)};

    function pointKey(x, y) {{
        return `${{Number(x).toFixed(6)}}|${{Number(y).toFixed(6)}}`;
    }}

    function rebuildPlot() {{
        const nextData = [baseData[0]];

        if (selectedPoints.length > 0) {{
            nextData.push({{
                x: selectedPoints.map((p) => p.x),
                y: selectedPoints.map((p) => p.y),
                mode: 'markers',
                marker: {{ size: 8, color: '#2ca02c', symbol: 'circle-open' }},
                name: 'Selected Peaks',
                hovertemplate:
                    `Selected<br>X: %{{x:.${{labelDecimals}}f}} Hz<br>Y: %{{y:.3f}} m/s²<extra></extra>`,
                type: 'scatter'
            }});
        }}

        const nextLayout = JSON.parse(JSON.stringify(baseLayout));
        nextLayout.shapes = selectedPoints.map((p) => ({{
            type: 'line',
            x0: p.x,
            x1: p.x,
            xref: 'x',
            y0: 0,
            y1: 1,
            yref: 'paper',
            opacity: 0.8,
            line: {{ color: '#6f6f6f', dash: 'dot', width: 1 }}
        }}));

        nextLayout.annotations = selectedPoints.map((p) => ({{
            x: p.x,
            y: p.y,
            text: `X: ${{p.x.toFixed(labelDecimals)}} Hz<br>Y: ${{p.y.toFixed(3)}} m/s²`,
            showarrow: true,
            arrowhead: 2,
            ax: 30,
            ay: -35,
            align: 'left',
            bordercolor: '#666666',
            borderwidth: 1,
            borderpad: 4,
            bgcolor: '#fffbd5',
            font: {{ size: 11, color: '#111111' }}
        }}));

        Plotly.react(gd, nextData, nextLayout, {{ responsive: true }});
    }}

    function toggleSelectedPoint(x, y) {{
        const key = pointKey(x, y);
        const existingIndex = selectedPoints.findIndex((point) => point.key === key);

        if (existingIndex >= 0) {{
            selectedPoints.splice(existingIndex, 1);
            rebuildPlot();
            return;
        }}

        selectedPoints.push({{ x: Number(x), y: Number(y), key }});
        rebuildPlot();
    }}

    gd.on('plotly_click', (eventData) => {{
        if (!eventData || !eventData.points || eventData.points.length === 0) {{
            return;
        }}

        const point = eventData.points[0];
        toggleSelectedPoint(point.x, point.y);
    }});

    initialPoints.forEach((point) => toggleSelectedPoint(point.x, point.y));
    """

    fig.write_html(
        str(output_html),
        include_plotlyjs='cdn',
        full_html=True,
        post_script=post_script,
    )


def process_one_file(
    source_csv: Path,
    output_dir: Path,
    value_column: str,
    override_rate_hz: float,
    window_seconds: float,
    default_peak_count: int,
    peak_min_separation_hz: float,
    label_decimals: int,
) -> tuple[Path, float, str]:
    _, rows = read_log_csv(source_csv)
    signal_values, iso_seconds, unix_times, row_rates = extract_signal_data(rows, value_column)

    sample_rate_hz, rate_source = infer_sample_rate(
        iso_seconds,
        unix_times,
        row_rates,
        override_rate_hz,
    )
    if sample_rate_hz <= 0:
        raise ValueError(f'Invalid sample rate {sample_rate_hz} Hz in {source_csv}')

    fft_window_seconds = window_seconds
    if fft_window_seconds <= 0:
        fft_window_seconds = len(signal_values) / sample_rate_hz

    freqs, amplitudes, fft_samples = compute_az_fft(
        signal_values,
        sample_rate_hz,
        fft_window_seconds,
    )
    if not freqs:
        raise ValueError('FFT produced no bins; check rate/window and data length')

    output_html = output_dir / f'{source_csv.stem}_fft.html'
    export_fft_graph(
        source_csv=source_csv,
        output_html=output_html,
        sample_rate_hz=sample_rate_hz,
        fft_samples=fft_samples,
        freqs=freqs,
        amplitudes=amplitudes,
        default_peak_count=default_peak_count,
        peak_min_separation_hz=peak_min_separation_hz,
        label_decimals=label_decimals,
    )
    return output_html, sample_rate_hz, rate_source


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Read vibration logs and export FFT HTML plots.'
    )
    parser.add_argument(
        'input_path',
        nargs='?',
        default='',
        help='Input CSV file or directory (default: logs)',
    )
    parser.add_argument(
        '--all-logs',
        action='store_true',
        help='Process all CSV files in logs folder individually (auto-read parameters from each file).',
    )
    parser.add_argument(
        '--logs-dir',
        default='logs',
        help='Logs folder path used by --all-logs (default: logs).',
    )
    parser.add_argument(
        '--output-dir',
        default='fft',
        help='Directory for FFT HTML output (default: fft)',
    )
    parser.add_argument(
        '--column',
        default='az_ms2',
        help='Signal column to use for FFT (default: az_ms2)',
    )
    parser.add_argument(
        '--rate',
        type=float,
        default=0.0,
        help='Override sample rate in Hz. If 0, infer from iso_time average rows/second.',
    )
    parser.add_argument(
        '--window-seconds',
        type=float,
        default=0.0,
        help='FFT window length in seconds. If 0, use full signal length.',
    )
    parser.add_argument(
        '--label-decimals',
        type=int,
        default=3,
        help='Decimal places used in peak label frequency text (default: 3).',
    )
    parser.add_argument(
        '--freq-decimals',
        type=int,
        dest='label_decimals',
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--peak-count',
        type=int,
        default=1,
        help='How many peaks are selected by default on load (default: 1).',
    )
    parser.add_argument(
        '--peak-min-separation-hz',
        type=float,
        default=1.0,
        help='Minimum spacing between highlighted peaks in Hz (default: 1.0).',
    )
    parser.add_argument(
        '--recursive',
        action='store_true',
        help='Recursively scan subdirectories when input is a directory.',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.all_logs and args.input_path:
        print('[ERROR] Use either input_path or --all-logs, not both.')
        return 1

    selected_input = args.logs_dir if (args.all_logs or not args.input_path) else args.input_path
    input_path = Path(selected_input)

    if args.label_decimals < 0:
        print('[ERROR] --label-decimals must be >= 0')
        return 1

    if args.peak_count < 0:
        print('[ERROR] --peak-count must be >= 0')
        return 1

    if args.peak_min_separation_hz < 0:
        print('[ERROR] --peak-min-separation-hz must be >= 0')
        return 1

    try:
        source_files = discover_input_files(input_path, args.recursive)
    except Exception as exc:
        print(f'[ERROR] {exc}')
        return 1

    if args.all_logs:
        print(f'[INFO] Batch mode: processing {len(source_files)} files from {input_path}')

    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif input_path.is_dir():
        output_dir = input_path / 'fft_exports'
    else:
        output_dir = input_path.parent

    success_count = 0
    fail_count = 0

    for source_csv in source_files:
        try:
            output_html, sample_rate_hz, rate_source = process_one_file(
                source_csv=source_csv,
                output_dir=output_dir,
                value_column=args.column,
                override_rate_hz=args.rate,
                window_seconds=args.window_seconds,
                default_peak_count=args.peak_count,
                peak_min_separation_hz=args.peak_min_separation_hz,
                label_decimals=args.label_decimals,
            )
            success_count += 1
            print(
                f'[OK] {source_csv} -> {output_html} '
                f'(rate={sample_rate_hz:.3f} Hz from {rate_source})'
            )
        except Exception as exc:
            fail_count += 1
            print(f'[ERROR] {source_csv}: {exc}')

    print(f'Finished: success={success_count}, failed={fail_count}')
    if success_count == 0:
        return 1
    if fail_count > 0:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
