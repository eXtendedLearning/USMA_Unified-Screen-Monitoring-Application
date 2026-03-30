import logging
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import cv2
from scipy.signal import butter, filtfilt
from scipy.fft import rfft, rfftfreq

from usma.models import (
    MonitoringRegion, AnalysisParams, FRFAnalysisResult,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SIGNAL ANALYZER
# ═══════════════════════════════════════════════════════════════
class SignalAnalyzer:
    def __init__(self, app_config):
        self.app_config = app_config
        self.verbose_logging_enabled = False
        self.verbose_log_options = None
        self.frame_count = 0
    def _validate_signal_quality(self, color_mask: np.ndarray) -> bool:
        height, width = color_mask.shape
        if height == 0 or width == 0:
            return False
        total_pixels = height * width
        if total_pixels == 0:
            return False
        signal_pixels = np.count_nonzero(color_mask)
        coverage_ratio = signal_pixels / total_pixels
        if not (0.0005 < coverage_ratio < 0.4):
            return False
        cols_with_signal = np.count_nonzero(np.sum(color_mask, axis=0) > 0)
        continuity_ratio = cols_with_signal / width
        if continuity_ratio < 0.15:
            return False
        return True

    def _apply_lowpass_filter(self, signal: np.ndarray) -> np.ndarray:
        if len(signal) < 15:
            return signal.copy()
    
        cutoff = min(self.app_config.lowpass_cutoff, 0.99)
        order = self.app_config.lowpass_filter_order
    
        try:
            b, a = butter(order, cutoff, btype='low')
            filtered = filtfilt(b, a, signal)
            return filtered
        except Exception as e:
            logger.warning(f"Lowpass filter failed: {e}. Returning original signal.")
            return signal.copy()

    def _calculate_exceedances(self, residual: np.ndarray, threshold: float) -> Tuple[int, float]:
        exceedance_mask = np.abs(residual) > threshold
        count = np.sum(exceedance_mask)
        ratio = count / len(residual) if len(residual) > 0 else 0.0
        return int(count), ratio

    def _reconstruct_signal_from_roi(
        self, roi: np.ndarray, region: MonitoringRegion
    ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """
        Shared signal reconstruction pipeline used by FRF, PSD, and Coherence analysis.

        Applies HSV colour filter, validates signal quality, reconstructs the 1-D
        pixel signal from the colour mask, and converts it to physical units.

        Returns:
            (signal_physical, color_mask, signal_pixels) on success, or None if the
            signal quality check fails.
        """
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array(self.app_config.hsv_lower),
            np.array(self.app_config.hsv_upper),
        )

        # Debug: Log mask statistics periodically
        if (
            self.verbose_logging_enabled
            and self.verbose_log_options is not None
            and self.verbose_log_options.log_mask_debug
            and self.frame_count % 20 == 0
        ):
            total_px = mask.shape[0] * mask.shape[1]
            white_px = np.count_nonzero(mask)
            logger.info(
                f"[MASK DEBUG] White pixels: {white_px}/{total_px} "
                f"({100 * white_px / total_px:.2f}%)"
            )

        if not self._validate_signal_quality(mask):
            return None

        y_coords, x_coords = np.nonzero(mask)
        height, width = mask.shape

        if len(x_coords) == 0:
            signal_pixels = np.full(width, height / 2)
        else:
            unique_x, anchor_y_idx = np.unique(x_coords, return_inverse=True)
            sum_y = np.bincount(anchor_y_idx, weights=y_coords)
            count_y = np.bincount(anchor_y_idx)
            anchor_y = sum_y / count_y
            if len(unique_x) < 2:
                signal_pixels = np.full(
                    width, anchor_y[0] if anchor_y.size > 0 else height / 2
                )
            else:
                signal_pixels = np.interp(np.arange(width), unique_x, anchor_y)

        # Invert y-axis: pixel 0 is top of image, but signal values increase upward
        signal_pixels = height - signal_pixels

        if signal_pixels.size < 2:
            return None

        # Convert to physical units using region axis calibration
        y_range = region.y_axis_max - region.y_axis_min
        signal_physical = region.y_axis_min + (signal_pixels / height) * y_range

        return signal_physical, mask, signal_pixels




    def analyze(
        self,
        roi: np.ndarray,
        region: MonitoringRegion,
        params: AnalysisParams = None,
    ) -> Optional[FRFAnalysisResult]:
        if roi.size == 0:
            return None

        # Debug: Log HSV filter values being used periodically
        if (
            self.verbose_logging_enabled
            and self.verbose_log_options is not None
            and self.verbose_log_options.log_config_values
            and self.frame_count % 20 == 0
        ):
            logger.info(
                f"[HSV DEBUG] Lower: {self.app_config.hsv_lower}, Upper: {self.app_config.hsv_upper}"
            )

        # Delegate shared reconstruction to helper
        reconstruction = self._reconstruct_signal_from_roi(roi, region)
        if reconstruction is None:
            return None
        signal_physical, mask, signal_pixels = reconstruction

        height = mask.shape[0]

        if params is None:
            params = self.app_config.analysis_params.get('frf', AnalysisParams())

        # --- FFT analysis (uses params to select FRF or PSD parameters) ---
        fft_cutoff = params.fft_cutoff_frequency
        fft_threshold = params.fft_energy_ratio_threshold
        lp_cutoff = params.lowpass_cutoff
        lp_order = params.lowpass_filter_order
        res_threshold = params.residual_threshold
        exc_threshold = params.exceedance_ratio_threshold

        N = len(signal_pixels)
        detrended_pixels = signal_pixels - np.mean(signal_pixels)
        yf = rfft(detrended_pixels)
        xf = rfftfreq(N, 1)
        fft_mags = np.abs(yf)
        total_energy = np.sum(fft_mags ** 2)
        high_freq_energy, energy_ratio, is_hf = 0, 0, False

        if total_energy > 1e-9:
            cutoff_indices = np.where(xf >= fft_cutoff)[0]
            if cutoff_indices.size > 0:
                high_freq_energy = np.sum(fft_mags[cutoff_indices[0]:] ** 2)
                energy_ratio = high_freq_energy / total_energy
            is_hf = energy_ratio > fft_threshold

        # --- Lowpass residual analysis ---
        signal_mean = np.mean(signal_physical)
        signal_detrended = signal_physical - signal_mean

        # Apply lowpass filter using the selected parameter set
        if len(signal_detrended) < 15:
            filtered_detrended = signal_detrended.copy()
        else:
            try:
                b, a = butter(lp_order, min(lp_cutoff, 0.99), btype="low")
                filtered_detrended = filtfilt(b, a, signal_detrended)
            except Exception as e:
                logger.warning(f"Lowpass filter failed: {e}. Returning original signal.")
                filtered_detrended = signal_detrended.copy()

        residual_physical = signal_detrended - filtered_detrended
        filtered_physical = filtered_detrended + signal_mean

        exceedance_count, exceedance_ratio = self._calculate_exceedances(
            residual_physical, res_threshold
        )
        lowpass_is_bad = exceedance_ratio > exc_threshold

        return FRFAnalysisResult(
            is_high_frequency=is_hf,
            energy_ratio=energy_ratio,
            high_freq_energy=high_freq_energy,
            signal_vector=signal_pixels,
            fft_freqs=xf,
            fft_mags=fft_mags,
            roi_image=roi.copy(),
            color_mask=mask.copy(),
            total_energy=total_energy,
            signal_physical=signal_physical,
            filtered_physical=filtered_physical,
            residual_physical=residual_physical,
            exceedance_count=exceedance_count,
            exceedance_ratio=exceedance_ratio,
            lowpass_is_bad_hit=lowpass_is_bad,
            y_axis_unit=region.y_axis_unit,
        )


