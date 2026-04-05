"""FFT helpers for dashboard signal processing."""

import numpy as np


def compute_az_fft(az_values, effective_rate, fft_window_seconds):
    """Return frequency and amplitude arrays for the AZ signal FFT."""
    if effective_rate <= 0:
        return [], [], 0

    available_n = len(az_values)
    n_fft_window = max(64, int(effective_rate * fft_window_seconds))
    n_samples = min(n_fft_window, available_n)
    if n_samples < 64:
        return [], [], n_samples

    segment = np.array(az_values[-n_samples:], dtype=float)
    segment = segment - segment.mean()

    window = np.hanning(n_samples)
    coherent_gain = float(window.mean()) if n_samples > 0 else 1.0
    spectrum = np.fft.rfft(segment * window)
    fft_vals = np.abs(spectrum) / (n_samples * coherent_gain)
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / effective_rate)

    # Keep single-sided amplitude scaling consistent for non-DC bins.
    if len(fft_vals) > 2:
        fft_vals[1:-1] *= 2.0

    freqs = freqs[1:]
    fft_vals = fft_vals[1:]

    return freqs.tolist(), fft_vals.tolist(), n_samples


def refresh_fft_cache(
    fft_cache,
    az_values,
    effective_rate,
    fft_window_seconds,
    should_update,
):
    """Update FFT cache in place when a refresh is due and data is available."""
    if not should_update:
        return fft_cache

    freqs, fft_vals, n_samples = compute_az_fft(
        az_values,
        effective_rate,
        fft_window_seconds,
    )
    if freqs:
        fft_cache['x'] = freqs
        fft_cache['y'] = fft_vals
        fft_cache['sig'] = (len(az_values), az_values[-1], n_samples)

    return fft_cache
