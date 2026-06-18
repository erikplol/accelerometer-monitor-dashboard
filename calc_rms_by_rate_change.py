#!/usr/bin/env python3
"""Compute AZ/VZ RMS for each contiguous rate_hz segment across log CSV files.

A new segment starts whenever rate_hz changes by more than --rate-eps.
The script scans all CSV files in logs/ by default and writes a combined summary CSV.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class Sample:
    counter: int
    unix_time: float
    az_ms2: float
    vz_mms: float
    rate_hz: float


@dataclass
class SegmentSummary:
    source_file: str
    segment_index: int
    start_counter: int
    end_counter: int
    samples: int
    start_unix_time: float
    end_unix_time: float
    duration_s: float
    rate_hz_start: float
    rate_hz_end: float
    rate_hz_mean: float
    az_rms_ms2: float
    vz_rms_mms: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Compute AZ/VZ RMS for each rate_hz change segment in log CSV files.'
    )
    parser.add_argument(
        '--logs-dir',
        default='logs',
        help='Directory containing log CSV files (default: logs).',
    )
    parser.add_argument(
        '--pattern',
        default='*.csv',
        help='Glob pattern for CSV files inside logs dir (default: *.csv).',
    )
    parser.add_argument(
        '--rate-eps',
        type=float,
        default=1e-6,
        help='Rate change threshold (Hz) that starts a new segment (default: 1e-6).',
    )
    parser.add_argument(
        '--output',
        default='logs/rms_by_rate_change_summary.csv',
        help='Output summary CSV path.',
    )
    parser.add_argument(
        '--separate-output-dir',
        default='',
        help=(
            'Optional folder for separate per-source summaries. '
            'If set, one CSV is written per source file grouped by RPM/LOAD.'
        ),
    )
    parser.add_argument(
        '--exclude-merged',
        action='store_true',
        help='Exclude files that contain "_MERGED" in filename.',
    )
    return parser.parse_args()


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


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def read_samples(csv_path: Path) -> list[Sample]:
    samples: list[Sample] = []
    header: list[str] | None = None

    with csv_path.open('r', newline='') as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue

            first = row[0].strip()
            if first.startswith('#'):
                continue

            if header is None:
                header = [col.strip() for col in row]
                continue

            if len(row) < len(header):
                row = row + [''] * (len(header) - len(row))

            mapped = dict(zip(header, [cell.strip() for cell in row[: len(header)]]))
            counter = _safe_int(mapped.get('counter'))
            unix_time = _safe_float(mapped.get('unix_time'))
            az_ms2 = _safe_float(mapped.get('az_ms2'))
            vz_mms = _safe_float(mapped.get('vz_mms'))
            rate_hz = _safe_float(mapped.get('rate_hz'))

            if (
                counter is None
                or unix_time is None
                or az_ms2 is None
                or vz_mms is None
                or rate_hz is None
            ):
                continue

            samples.append(
                Sample(
                    counter=counter,
                    unix_time=unix_time,
                    az_ms2=az_ms2,
                    vz_mms=vz_mms,
                    rate_hz=rate_hz,
                )
            )

    return samples


def rms(values: Iterable[float]) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    return math.sqrt(sum(v * v for v in vals) / len(vals))


def summarize_segments(samples: list[Sample], source_file: str, rate_eps: float) -> list[SegmentSummary]:
    if not samples:
        return []

    out: list[SegmentSummary] = []
    seg_start = 0
    seg_index = 1

    def close_segment(start_idx: int, end_idx: int, idx: int) -> None:
        seg = samples[start_idx:end_idx]
        if not seg:
            return

        start = seg[0]
        end = seg[-1]
        duration = max(0.0, end.unix_time - start.unix_time)
        rate_vals = [s.rate_hz for s in seg]
        az_vals = [s.az_ms2 for s in seg]
        vz_vals = [s.vz_mms for s in seg]

        out.append(
            SegmentSummary(
                source_file=source_file,
                segment_index=idx,
                start_counter=start.counter,
                end_counter=end.counter,
                samples=len(seg),
                start_unix_time=start.unix_time,
                end_unix_time=end.unix_time,
                duration_s=duration,
                rate_hz_start=start.rate_hz,
                rate_hz_end=end.rate_hz,
                rate_hz_mean=sum(rate_vals) / len(rate_vals),
                az_rms_ms2=rms(az_vals),
                vz_rms_mms=rms(vz_vals),
            )
        )

    for i in range(1, len(samples)):
        prev = samples[i - 1]
        cur = samples[i]
        if abs(cur.rate_hz - prev.rate_hz) > rate_eps:
            close_segment(seg_start, i, seg_index)
            seg_start = i
            seg_index += 1

    close_segment(seg_start, len(samples), seg_index)
    return out


def write_summary(path: Path, summaries: list[SegmentSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow([
            'az_rms_ms2',
            'vz_rms_mms',
            'rate_hz',
        ])

        for s in summaries:
            writer.writerow([
                f'{s.az_rms_ms2:.6f}',
                f'{s.vz_rms_mms:.6f}',
                f'{s.rate_hz_mean:.6f}',
            ])


def _group_key_from_filename(filename: str) -> str:
    match = re.search(r'RPM\d+_LOAD\d+W', filename)
    if match:
        return match.group(0)
    return 'UNGROUPED'


def write_separate_summaries(output_dir: Path, summaries: list[SegmentSummary]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    by_source: dict[str, list[SegmentSummary]] = {}
    for summary in summaries:
        by_source.setdefault(summary.source_file, []).append(summary)

    for source_file, source_summaries in by_source.items():
        group_dir = output_dir / _group_key_from_filename(source_file)
        group_dir.mkdir(parents=True, exist_ok=True)
        out_name = f'{Path(source_file).stem}_rate_segments.csv'
        write_summary(group_dir / out_name, source_summaries)


def main() -> None:
    args = parse_args()

    logs_dir = Path(args.logs_dir)
    if not logs_dir.is_dir():
        raise SystemExit(f'Logs directory not found: {logs_dir}')

    files = sorted(logs_dir.glob(args.pattern))
    if args.exclude_merged:
        files = [p for p in files if '_MERGED' not in p.name]

    if not files:
        raise SystemExit('No matching CSV files found.')

    all_summaries: list[SegmentSummary] = []
    processed_files = 0

    for csv_path in files:
        samples = read_samples(csv_path)
        if not samples:
            continue

        summaries = summarize_segments(
            samples=samples,
            source_file=csv_path.name,
            rate_eps=max(0.0, args.rate_eps),
        )
        all_summaries.extend(summaries)
        processed_files += 1

    if not all_summaries:
        raise SystemExit('No valid samples found in matching CSV files.')

    output_path = Path(args.output)
    write_summary(output_path, all_summaries)

    separate_count = 0
    if args.separate_output_dir.strip():
        separate_dir = Path(args.separate_output_dir)
        write_separate_summaries(separate_dir, all_summaries)
        separate_count = len({s.source_file for s in all_summaries})

    print(f'Processed files: {processed_files}')
    print(f'Segments written: {len(all_summaries)}')
    print(f'Summary file: {output_path}')
    if separate_count:
        print(f'Separate files written: {separate_count}')
        print(f'Separate output dir: {args.separate_output_dir}')


if __name__ == '__main__':
    main()
