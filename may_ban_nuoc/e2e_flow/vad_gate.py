"""Noise-relative energy gate used alongside WebRTC VAD, never instead of it."""
import numpy as np


def dbfs(pcm):
    samples = np.asarray(pcm, dtype=np.float64) / 32768.0
    return float(20 * np.log10(max(1e-9, np.sqrt(np.mean(samples * samples)))))


class NoiseGate:
    def __init__(self, calibration_levels, margin_db=8.0, minimum_dbfs=-55.0):
        if not calibration_levels:
            raise ValueError('Noise calibration requires audio frames')
        self.noise_dbfs = float(np.median(calibration_levels))
        self.threshold_dbfs = max(minimum_dbfs, self.noise_dbfs + margin_db)
        if self.threshold_dbfs >= -3:
            raise RuntimeError('Microphone background is too loud; cannot separate speech from noise')

    def is_speech(self, raw_vad, level_dbfs):
        return bool(raw_vad and level_dbfs >= self.threshold_dbfs)
