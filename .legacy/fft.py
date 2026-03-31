import csv

import matplotlib.pyplot as plt
import numpy as np

# ===== USER CONFIG =====
FILE_PATH = "logs/vibration_RPM2000_LOAD5000W_20260311_233531 copy.csv"
SIGNAL_COLUMN = "vz_mm_s"
SAMPLING_FREQUENCY = 150  # Hz (match the sensor / poll rate used during logging)
# =======================


def read_vibration_file(file_path):
    values = []

    with open(file_path, "r", newline="") as handle:
        rows = [line for line in handle if not line.startswith("#") and line.strip()]

    if not rows:
        return np.array(values)

    reader = csv.DictReader(rows)
    for row in reader:
        try:
            values.append(float(row[SIGNAL_COLUMN]))
        except (KeyError, TypeError, ValueError):
            if not row:
                continue

    return np.array(values)


def perform_fft(signal, fs):

    n = len(signal)

    # Remove DC offset
    signal = signal - np.mean(signal)

    # FFT
    fft_vals = np.fft.fft(signal)
    fft_vals = np.abs(fft_vals) / n

    # Frequency axis
    freq = np.fft.fftfreq(n, 1/fs)

    # Take only positive frequencies
    mask = freq >= 0
    return freq[mask], fft_vals[mask]


def main():

    signal = read_vibration_file(FILE_PATH)

    if len(signal) == 0:
        print("No vibration data found")
        return

    print("Samples:", len(signal))

    freq, amplitude = perform_fft(signal, SAMPLING_FREQUENCY)

    # Plot
    plt.figure(figsize=(10,5))
    plt.plot(freq, amplitude)
    plt.title("FFT Spectrum")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Amplitude")
    plt.grid(True)

    plt.show()


if __name__ == "__main__":
    main()