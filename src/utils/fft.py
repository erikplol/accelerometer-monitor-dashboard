import numpy as np

def compute_fft(segment: np.ndarray, sample_rate_hz: float):
    """Return frequency bins and magnitude for real FFT of a segment."""
    if segment.size == 0:
        return np.array([]), np.array([])
    N = segment.size
    window = np.hanning(N)
    coherent_gain = float(window.mean()) if N > 0 else 1.0
    spectrum = np.fft.rfft(segment * window)
    fft_vals = np.abs(spectrum) / (N * coherent_gain)
    freqs = np.fft.rfftfreq(N, d=1.0 / sample_rate_hz)
    if fft_vals.size > 2:
        fft_vals[1:-1] *= 2.0
    # Drop DC
    return freqs[1:], fft_vals[1:]
