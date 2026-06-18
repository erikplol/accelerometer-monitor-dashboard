#!/usr/bin/env python3
"""Split merged vibration logs into AZ-only and VZ-only CSV files.

For each input CSV in logs/, the script creates:
1) *_az_only.csv: keeps AZ samples with original rate_hz.
2) *_vz_only.csv: keeps only VZ update rows (duplicates removed) and adds
   vz_rate_hz_calculated based on non-duplicate updates per second.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Split merged AZ/VZ log CSV files into AZ-only and deduplicated '
            'VZ-only files.'
        )
    )
    parser.add_argument(
        '--logs-dir',
        default='logs',
        help='Root logs directory to scan (default: logs).',
    )
    parser.add_argument(
        '--pattern',
        default='*.csv',
        help='Filename pattern to scan (default: *.csv).',
    )
    parser.add_argument(
        '--no-recursive',
        action='store_true',
        help='Scan only the top level of logs-dir (default: recursive scan).',
    )
    parser.add_argument(
        '--output-dir',
        default='',
        help=(
            'Optional output root folder. If empty, output is written next to '
            'each source CSV.'
        ),
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


def _target_paths(
    src_path: Path,
    logs_root: Path,
    output_root: Path | None,
) -> tuple[Path, Path]:
    stem = src_path.stem
    az_name = f'{stem}_az_only.csv'
    vz_name = f'{stem}_vz_only.csv'

    if output_root is None:
        return src_path.with_name(az_name), src_path.with_name(vz_name)

    relative_parent = src_path.parent.relative_to(logs_root)
    out_parent = output_root / relative_parent
    return out_parent / az_name, out_parent / vz_name


def _parse_csv(path: Path) -> tuple[list[list[str]], list[str], list[dict[str, str]]]:
    metadata: list[list[str]] = []
    header: list[str] | None = None
    rows: list[dict[str, str]] = []

    with path.open('r', newline='') as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue

            first = row[0].strip()
            if first.startswith('#'):
                metadata.append([cell.strip() for cell in row])
                continue

            if header is None:
                header = [cell.strip() for cell in row]
                continue

            if len(row) < len(header):
                row = row + [''] * (len(header) - len(row))

            mapped = {
                key: value.strip() for key, value in zip(header, row[: len(header)])
            }
            rows.append(mapped)

    return metadata, (header or []), rows


def _write_az_file(path: Path, metadata: list[list[str]], source_rows: list[dict[str, str]]) -> int:
    az_rows: list[dict[str, str]] = []
    for row in source_rows:
        unix_time = _safe_float(row.get('unix_time'))
        az_ms2 = _safe_float(row.get('az_ms2'))
        rate_hz = _safe_float(row.get('rate_hz'))
        if unix_time is None or az_ms2 is None or rate_hz is None:
            continue

        az_rows.append(
            {
                'counter': str(len(az_rows) + 1),
                'source_counter': row.get('counter', ''),
                'unix_time': f'{unix_time:.6f}',
                'iso_time': row.get('iso_time', ''),
                'az_ms2': f'{az_ms2:.6f}',
                'rate_hz': f'{rate_hz:.6f}',
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle)
        for line in metadata:
            writer.writerow(line)
        writer.writerow(['# Split Data', 'AZ only (original AZ rate_hz)'])
        writer.writerow(['counter', 'source_counter', 'unix_time', 'iso_time', 'az_ms2', 'rate_hz'])
        for row in az_rows:
            writer.writerow(
                [
                    row['counter'],
                    row['source_counter'],
                    row['unix_time'],
                    row['iso_time'],
                    row['az_ms2'],
                    row['rate_hz'],
                ]
            )

    return len(az_rows)


def _write_vz_file(path: Path, metadata: list[list[str]], source_rows: list[dict[str, str]]) -> int:
    dedup_rows: list[dict[str, str]] = []
    previous_vz: float | None = None

    for row in source_rows:
        unix_time = _safe_float(row.get('unix_time'))
        vz_mms = _safe_float(row.get('vz_mms'))
        if unix_time is None or vz_mms is None:
            continue

        # VZ is sampled slower than AZ, so unchanged consecutive values are
        # duplicates introduced by AZ-aligned merge.
        if previous_vz is not None and abs(vz_mms - previous_vz) <= 1e-12:
            continue

        dedup_rows.append(
            {
                'source_counter': row.get('counter', ''),
                'unix_time': unix_time,
                'iso_time': row.get('iso_time', ''),
                'vz_mms': vz_mms,
            }
        )
        previous_vz = vz_mms

    per_second_counts = Counter(int(row['unix_time']) for row in dedup_rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle)
        for line in metadata:
            writer.writerow(line)
        writer.writerow(['# Split Data', 'VZ only (deduplicated)'])
        writer.writerow([
            '# Rate Info',
            'vz_rate_hz_calculated is updates per integer second after dedup',
        ])
        writer.writerow(
            [
                'counter',
                'source_counter',
                'unix_time',
                'iso_time',
                'vz_mms',
                'vz_rate_hz_calculated',
            ]
        )

        for idx, row in enumerate(dedup_rows, start=1):
            sec = int(row['unix_time'])
            writer.writerow(
                [
                    idx,
                    row['source_counter'],
                    f"{row['unix_time']:.6f}",
                    row['iso_time'],
                    f"{row['vz_mms']:.6f}",
                    f"{float(per_second_counts[sec]):.6f}",
                ]
            )

    return len(dedup_rows)


def process_file(csv_path: Path, logs_root: Path, output_root: Path | None) -> tuple[int, int] | None:
    if csv_path.name.endswith('_az_only.csv') or csv_path.name.endswith('_vz_only.csv'):
        return None

    metadata, header, rows = _parse_csv(csv_path)
    required = {'unix_time', 'az_ms2', 'vz_mms', 'rate_hz'}
    if not required.issubset(set(header)):
        return None

    az_out, vz_out = _target_paths(csv_path, logs_root, output_root)
    az_count = _write_az_file(az_out, metadata, rows)
    vz_count = _write_vz_file(vz_out, metadata, rows)
    return az_count, vz_count


def main() -> None:
    args = parse_args()
    logs_root = Path(args.logs_dir).resolve()
    output_root = Path(args.output_dir).resolve() if args.output_dir else None

    if not logs_root.exists():
        raise SystemExit(f'Logs directory not found: {logs_root}')

    csv_paths = (
        sorted(logs_root.glob(args.pattern))
        if args.no_recursive
        else sorted(logs_root.rglob(args.pattern))
    )

    processed = 0
    skipped = 0
    total_az = 0
    total_vz = 0

    for csv_path in csv_paths:
        if not csv_path.is_file():
            continue

        result = process_file(csv_path, logs_root, output_root)
        if result is None:
            skipped += 1
            continue

        az_count, vz_count = result
        processed += 1
        total_az += az_count
        total_vz += vz_count
        print(
            f'Processed {csv_path}: AZ rows={az_count}, '
            f'VZ dedup rows={vz_count}'
        )

    print('---')
    print(f'Files scanned: {len(csv_paths)}')
    print(f'Processed: {processed}')
    print(f'Skipped: {skipped}')
    print(f'Total AZ rows written: {total_az}')
    print(f'Total VZ dedup rows written: {total_vz}')


if __name__ == '__main__':
    main()
