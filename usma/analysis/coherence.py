import logging
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import cv2

from usma.models import MonitoringRegion, CoherenceAnalysisResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# COHERENCE ANALYZER
# ═══════════════════════════════════════════════════════════════
class CoherenceAnalyzer:
    def __init__(self, app_config, signal_analyzer):
        self.app_config = app_config
        self.signal_analyzer = signal_analyzer

    def analyze(self, roi: np.ndarray, region: MonitoringRegion) -> Optional['CoherenceAnalysisResult']:
        """Analyze a coherence signal ROI using the same HSV-based extraction pipeline.
        Coherence is always 0..1; badness = integral(1-gamma^2) / freq_span."""
        try:
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            lower = np.array(self.app_config.hsv_lower)
            upper = np.array(self.app_config.hsv_upper)
            mask = cv2.inRange(hsv, lower, upper)
            if not self.signal_analyzer._validate_signal_quality(mask):
                return None

            # Build signal vector (same as FRF)
            signal_pixels_list: List[float] = []
            for col in range(roi.shape[1]):
                col_pixels = np.where(mask[:, col] > 0)[0]
                if len(col_pixels) > 0:
                    signal_pixels_list.append(float(roi.shape[0] - int(np.mean(col_pixels))) / roi.shape[0])
                else:
                    signal_pixels_list.append(np.nan)
        
            signal_pixels = np.array(signal_pixels_list, dtype=np.float64)

            # Fill NaN gaps
            nans = np.isnan(signal_pixels)
            if np.all(nans):
                return None
            indices = np.arange(len(signal_pixels))
            signal_pixels[nans] = np.interp(indices[nans], indices[~nans], signal_pixels[~nans])

            # Map to physical coherence [0, 1]
            y_min = region.y_axis_min if region.y_axis_min != 0 else 0.0
            y_max = region.y_axis_max if region.y_axis_max != 0 else 1.0
            signal_physical = y_min + signal_pixels * (y_max - y_min)
            signal_physical = np.clip(signal_physical, 0.0, 1.0)

            # Build frequency axis
            n = len(signal_physical)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, n)

            inverted_signal = 1.0 - signal_physical
            mean_coherence = float(np.mean(signal_physical))
            min_coherence = float(np.min(signal_physical))

            # Badness = area under (1 - gamma^2) / span
            freq_span = max(region.x_axis_max - region.x_axis_min, 1.0)
            badness_integral = float(np.trapezoid(inverted_signal, freq_axis))
            normalized_badness = badness_integral / freq_span

            # Per-band (4 bands) badness
            band_size = n // 4
            band_badness = []
            for i in range(4):
                start_i = i * band_size
                end_i = (i + 1) * band_size if i < 3 else n
                band_inv = inverted_signal[start_i:end_i]
                band_freq = freq_axis[start_i:end_i]
                band_badness.append(float(np.trapezoid(band_inv, band_freq)) / max(band_freq[-1] - band_freq[0], 1.0))

            return CoherenceAnalysisResult(
                signal_physical=signal_physical,
                inverted_signal=inverted_signal,
                badness_integral=badness_integral,
                normalized_badness=normalized_badness,
                mean_coherence=mean_coherence,
                min_coherence=min_coherence,
                band_badness=band_badness,
                freq_axis=freq_axis,
                roi_image=roi.copy(),
                color_mask=mask.copy()
            )
        except Exception as e:
            logger.error(f"Coherence analysis error: {e}")
            return None

    # =========================================================================
    # WAVE ANALYSIS METHODS
    # =========================================================================

