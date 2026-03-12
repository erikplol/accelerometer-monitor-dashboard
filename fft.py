import numpy as np
import matplotlib.pyplot as plt
import re

# ===== USER CONFIG =====
FILE_PATH = "logs/vibration_RPM2000_LOAD5000W_20260311_233531 copy.csv"
SAMPLING_FREQUENCY = 24  # Hz (adjust to your sensor sampling rate)
# =======================


def read_vibration_file(file_path):
    vz_values = []

    with open(file_path, "r") as f:
        lines = f.readlines()

    data_section = False

    for line in lines:

        # Detect start of data
        if line.strip().startswith("counter"):
            data_section = True
            continue

        if data_section:
            parts = line.strip().split(",")

            if len(parts) < 3:
                continue

            try:
                vz = float(parts[2])
                vz_values.append(vz)
            except:
                pass

    return np.array(vz_values)


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