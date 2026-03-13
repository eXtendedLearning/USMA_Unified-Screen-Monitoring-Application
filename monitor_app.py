#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# === DPI AWARENESS (Must be set before any GUI imports) ===
import ctypes
try:
    # Per-Monitor DPI Awareness (Windows 8.1+)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 2 = Per-Monitor V2
except AttributeError:
    try:
        # Fallback: System DPI Awareness (Windows Vista+)
        ctypes.windll.user32.SetProcessDPIAware()
    except AttributeError:
        pass  # Non-Windows platform
except OSError:
    pass  # DPI awareness already set
# ===========================================================

"""
USMA (Unified Screen Monitoring Application) - v0.6.0 (Foundation Release)

A professional-grade GUI application for real-time screen monitoring, signal
analysis, and OCR designed for modal analysis workflows. Captures screen regions,
reconstructs FRF signals, performs dual-method quality classification (FFT +
Lowpass), and exports data in industry-standard formats (UNV Dataset 58).

Key Features:
- Startup dialog for config selection or new calibration workflow
- HSV color filter calibration with live preview
- Mandatory ROI type selection when drawing regions  
- Pre-load existing config in editor for modification
- Live analysis parameter adjustment during monitoring
- Scrollable main GUI with embedded console output
- Continuous logging mode for HSV debugging ("Log on Events Only" toggle)
- Dual classification: FFT energy ratio + Lowpass residual analysis
- Live graph viewer with hit navigation and multiple plot types
- Organized image logging (ROIs, Masks, Signals, FFT, Lowpass, Residual)
- UNV Dataset 58 export format
- DPI-aware rendering for high-resolution displays
- Fast screen capture via mss library with pyautogui fallback

For full version history, see README.md
"""

import cv2
import numpy as np
import time

# --- Screen Capture Configuration ---
try:
    import mss
    import mss.tools
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False
    print("Warning: mss not available, falling back to pyautogui (slower)")

import pyautogui  # Keep as fallback
import threading
import json
import os
import logging
import re
import gc
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Tuple
from contextlib import contextmanager
from PIL import Image, ImageTk
from scipy.fft import rfft, rfftfreq
from scipy.signal import butter, filtfilt
# scipy.io removed - .mat export deprecated
import matplotlib
matplotlib.use('TkAgg')  # For the embedded viewer only
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from datetime import datetime

# --- Import sounddevice with fallback ---
try:
    import sounddevice as sd
    SOUND_DEVICE_AVAILABLE = True
except (ImportError, OSError) as e:
    SOUND_DEVICE_AVAILABLE = False
    print(f"Warning: sounddevice library not found or audio device error: {e}. Audio feedback disabled.")

# --- OCR Configuration ---
try:
    import pytesseract
    import shutil

    script_dir = os.path.dirname(os.path.realpath(__file__))
    tesseract_path = os.path.join(script_dir, 'external', 'tesseract', 'tesseract.exe')

    if os.path.exists(tesseract_path):
        # Use portable/bundled Tesseract
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        OCR_AVAILABLE = True
    elif shutil.which('tesseract'):
        # Fallback: Tesseract found in system PATH
        pytesseract.pytesseract.tesseract_cmd = 'tesseract'
        OCR_AVAILABLE = True
        print("Info: Using system-installed Tesseract from PATH")
    else:
        raise FileNotFoundError("Tesseract not found in portable location or system PATH")

except (ImportError, FileNotFoundError) as e:
    OCR_AVAILABLE = False
    print(f"Warning: OCR features disabled. Error: {e}")


# --- 1. SETUP: DIRECTORY AND LOGGING CONFIGURATION ---
def setup_environment():
    """Create necessary directories for logs, configs, and organized image logs."""
    base_folders = ['logs', 'configs', 'signal_logs']
    image_subfolders = [
        'image_logs/ROIs',
        'image_logs/ColorMasks', 
        'image_logs/Signals',
        'image_logs/FFT',
        'image_logs/Lowpass',
        'image_logs/Residual',
        'image_logs/Summary',
        'image_logs/OCRs'
    ]
    for folder in base_folders + image_subfolders:
        if not os.path.exists(folder):
            os.makedirs(folder)

setup_environment()

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) 
file_handler = logging.FileHandler('logs/monitor_app.log')
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)


# --- Custom TextHandler for GUI Console ---
class TextHandler(logging.Handler):
    """Logging handler that writes to a tkinter Text widget."""
    
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setFormatter(log_formatter)
    
    def emit(self, record):
        try:
            msg = self.format(record) + '\n'
            # Schedule the update on the main thread
            self.text_widget.after(0, self._append_text, msg)
        except Exception:
            self.handleError(record)
    
    def _append_text(self, msg):
        try:
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg)
            self.text_widget.see(tk.END)  # Auto-scroll to bottom
            # Limit lines to prevent memory issues (keep last 1000 lines)
            line_count = int(self.text_widget.index('end-1c').split('.')[0])
            if line_count > 1000:
                self.text_widget.delete('1.0', f'{line_count - 1000}.0')
            self.text_widget.configure(state='disabled')
        except tk.TclError:
            pass  # Widget was destroyed


# --- 2. DATA CLASSES: CORE DATA STRUCTURES ---
@dataclass
class ImageLogOptions:
    include_screenshot: bool = False
    include_color_filter: bool = False
    include_signal_plot: bool = False
    include_fft_plot: bool = False
    include_lowpass_plot: bool = False
    include_residual_plot: bool = False
    include_summary_chart: bool = False
    include_ocr_images: bool = False

@dataclass
class VerboseLogOptions:
    """Options for verbose console logging categories."""
    log_config_values: bool = True
    log_mask_debug: bool = True
    log_ocr_output: bool = True
    log_fft_data: bool = True
    log_lowpass_data: bool = True
    log_classification: bool = True
    log_file_saves: bool = True

@dataclass
class DataLogOptions:
    log_unv: bool = False

@dataclass
class PointsInfo:
    """Stores parsed measurement point metadata."""
    run: str = "Run 1"
    hammer_point: str = "P1"
    hammer_dir: str = "-Z"
    response_point: str = "P1"
    response_dir: str = "-Z"

@dataclass
class MonitoringRegion:
    name: str
    x: int
    y: int
    width: int
    height: int
    roi_type: str
    enabled: bool = field(default=True)
    x_axis_min: float = field(default=0.0)
    x_axis_max: float = field(default=1024.0)
    y_axis_min: float = field(default=0.0)
    y_axis_max: float = field(default=1.0)
    y_axis_unit: str = field(default="g/N")
    resp_node: int = field(default=1)
    resp_dof: int = field(default=3)
    ref_node: int = field(default=1)
    ref_dof: int = field(default=3)
    overlay_color: str = field(default="")  # Custom overlay color (hex). Empty = use type default.

@dataclass
class FRFAnalysisResult:
    """Extended to include lowpass residual analysis results in physical units."""
    is_high_frequency: bool
    energy_ratio: float
    high_freq_energy: float
    signal_vector: np.ndarray
    fft_freqs: np.ndarray
    fft_mags: np.ndarray
    roi_image: np.ndarray
    color_mask: np.ndarray
    total_energy: float = 0.0
    signal_physical: Optional[np.ndarray] = None
    filtered_physical: Optional[np.ndarray] = None
    residual_physical: Optional[np.ndarray] = None
    exceedance_count: int = 0
    exceedance_ratio: float = 0.0
    lowpass_is_bad_hit: bool = False
    y_axis_unit: str = "g/N"

@dataclass
class LightweightHitData:
    """Lightweight version of hit data for history storage - avoids memory bloat."""
    signal_physical: np.ndarray
    filtered_physical: Optional[np.ndarray]
    residual_physical: Optional[np.ndarray]
    fft_freqs: np.ndarray
    fft_mags: np.ndarray
    energy_ratio: float
    is_high_frequency: bool
    exceedance_count: int
    exceedance_ratio: float
    lowpass_is_bad_hit: bool
    total_energy: float
    high_freq_energy: float
    y_axis_unit: str
    x_axis_min: float
    x_axis_max: float
    hit_key: str
    run: str

# ---------------------------------------------------------------------------
# NEW v0.6.0 DATACLASSES
# ---------------------------------------------------------------------------

@dataclass
class CalibrationSignal:
    """Single calibration signal with expert judgment and raw analysis data."""
    signal_physical: np.ndarray
    fft_freqs: np.ndarray
    fft_mags: np.ndarray
    residual_physical: np.ndarray
    energy_ratio: float
    exceedance_ratio: float
    exceedance_count: int
    total_energy: float
    high_freq_energy: float
    judgment: str  # "GOOD", "BAD", or "IGNORE"
    timestamp: str
    roi_name: str
    source: str = "calibration_phase"  # "calibration_phase" or "live_monitoring"

@dataclass
class CalibrationSession:
    """Complete calibration session: signals, estimated params, and metadata."""
    signals: List['CalibrationSignal'] = field(default_factory=list)
    estimated_params: Optional[dict] = None
    config_name: str = ""
    created_at: str = ""
    last_updated: str = ""
    confidence_level: int = 0  # 0-4 per §2.6.6

@dataclass
class CoherenceAnalysisResult:
    """Result of coherence analysis for a single captured snapshot."""
    signal_physical: np.ndarray          # Raw coherence values (0 to 1)
    inverted_signal: np.ndarray          # (1 - coherence) values
    badness_integral: float              # ∫(1−γ²)df over full band
    normalized_badness: float            # badness_integral / freq_span
    mean_coherence: float                # Average coherence value
    min_coherence: float                 # Minimum coherence value
    band_badness: List[float]            # Per-band (4 bands) badness values
    freq_axis: np.ndarray                # Frequency axis
    roi_image: Optional[np.ndarray] = None
    color_mask: Optional[np.ndarray] = None

@dataclass
class CoherenceTrackingState:
    """Tracks coherence evolution within a run across multiple hits."""
    run_name: str = ""
    hit_count: int = 0
    snapshots: List[CoherenceAnalysisResult] = field(default_factory=list)
    trend: str = "UNKNOWN"  # "IMPROVING", "STABLE", "DEGRADING", "INSUFFICIENT_DATA"

@dataclass
class LightweightCoherenceData:
    """Lightweight copy of coherence data for the graph viewer (avoids memory bloat)."""
    signal_physical: np.ndarray
    inverted_signal: np.ndarray
    normalized_badness: float
    mean_coherence: float
    freq_axis: np.ndarray
    hit_number: int
    run: str

# ---------------------------------------------------------------------------

@dataclass
class FrameAnalysisResult:
    """Holds all analysis results from a single captured frame."""
    frf_results: Dict[str, FRFAnalysisResult] = field(default_factory=dict)
    active_regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    status_text: str = "Unknown"
    overload_text: str = "Unknown"
    points_info: PointsInfo = field(default_factory=PointsInfo)
    overall_is_hf: Optional[bool] = None
    avg_energy_ratio: Optional[float] = None
    avg_high_freq_energy: Optional[float] = None
    avg_exceedance_count: Optional[float] = None
    avg_exceedance_ratio: Optional[float] = None
    overall_lowpass_bad: Optional[bool] = None
    ocr_images: Dict[str, np.ndarray] = field(default_factory=dict)
    # --- PSD results (v0.6.0) ---
    psd_results: Dict[str, FRFAnalysisResult] = field(default_factory=dict)
    psd_overall_is_hf: Optional[bool] = None
    psd_avg_energy_ratio: Optional[float] = None
    psd_avg_exceedance_count: Optional[float] = None
    psd_avg_exceedance_ratio: Optional[float] = None
    psd_overall_lowpass_bad: Optional[bool] = None
    # --- Coherence results (v0.6.0) ---
    coherence_results: Dict[str, CoherenceAnalysisResult] = field(default_factory=dict)
    current_averages: Optional[int] = None

@dataclass
class AppConfig:
    regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    hsv_lower: List[int] = field(default_factory=lambda: [0, 0, 0])
    hsv_upper: List[int] = field(default_factory=lambda: [179, 255, 240])
    screenshot_interval: float = 0.25
    fft_cutoff_frequency: float = 0.07
    fft_energy_ratio_threshold: float = 0.006
    lowpass_cutoff: float = 0.07
    lowpass_filter_order: int = 7
    residual_threshold: float = 0.005
    exceedance_ratio_threshold: float = 0.7
    # --- PSD Parameters (v0.6.0) — same defaults as FRF; tuned independently ---
    psd_fft_cutoff_frequency: float = 0.07
    psd_fft_energy_ratio_threshold: float = 0.006
    psd_lowpass_cutoff: float = 0.07
    psd_lowpass_filter_order: int = 7
    psd_residual_threshold: float = 0.005
    psd_exceedance_ratio_threshold: float = 0.7
    # --- Coherence Parameters (v0.6.0) ---
    coherence_threshold: float = 0.3          # Normalized badness threshold
    coherence_degradation_pct: float = 0.20   # % increase in badness to flag degradation
    hits_per_run: int = 5                     # Expected number of hits per run


# --- Calibration Data Structures (Phase 4 / v0.9) ---

@dataclass
class CalibrationSignal:
    """Single calibration signal with expert judgment."""
    signal_physical: np.ndarray
    fft_freqs: np.ndarray
    fft_mags: np.ndarray
    residual_physical: np.ndarray
    energy_ratio: float
    exceedance_ratio: float
    exceedance_count: int
    total_energy: float
    high_freq_energy: float
    judgment: str  # "GOOD" or "BAD"
    timestamp: str
    roi_name: str
    source: str = "calibration_phase"  # "calibration_phase" or "live_monitoring"

@dataclass
class CalibrationSession:
    """Complete calibration session data."""
    signals: List['CalibrationSignal'] = field(default_factory=list)
    estimated_params: Optional[dict] = None
    config_name: str = ""
    created_at: str = ""
    last_updated: str = ""


# --- Calibration Estimators (Phase 4 / v0.9) ---

class PercentileBoundaryEstimator:
    """
    Method A: For each parameter, finds the boundary between Good and Bad signal distributions.
    Sets threshold = midpoint between worst Good and best Bad (or 95th percentile if overlapping).
    """
    def __init__(self):
        self.good_signals = []
        self.bad_signals = []

    def add_signal(self, raw_data: dict, judgment: str):
        if judgment == "GOOD":
            self.good_signals.append(raw_data)
        elif judgment == "BAD":
            self.bad_signals.append(raw_data)

    def estimate_thresholds(self) -> Optional[dict]:
        if len(self.good_signals) < 3 or len(self.bad_signals) < 3:
            return None

        params = {}

        # --- Threshold parameters (direct boundary) ---
        good_ratios = [s['energy_ratio'] for s in self.good_signals]
        bad_ratios = [s['energy_ratio'] for s in self.bad_signals]
        max_good = max(good_ratios)
        min_bad = min(bad_ratios)
        if min_bad > max_good:
            params['fft_energy_ratio_threshold'] = (max_good + min_bad) / 2
        else:
            params['fft_energy_ratio_threshold'] = float(np.percentile(good_ratios, 95))

        good_exc = [s['exceedance_ratio'] for s in self.good_signals]
        bad_exc = [s['exceedance_ratio'] for s in self.bad_signals]
        max_good_exc = max(good_exc)
        min_bad_exc = min(bad_exc)
        if min_bad_exc > max_good_exc:
            params['exceedance_ratio_threshold'] = (max_good_exc + min_bad_exc) / 2
        else:
            params['exceedance_ratio_threshold'] = float(np.percentile(good_exc, 95))

        good_max_residuals = []
        for s in self.good_signals:
            res = s.get('residual_physical')
            if res is not None and len(res) > 0:
                good_max_residuals.append(float(np.max(np.abs(res))))
        if good_max_residuals:
            params['residual_threshold'] = float(np.percentile(good_max_residuals, 90))

        # --- Filter parameters (sweep optimization) ---
        params.update(self._optimize_filter_params())
        return params

    def _optimize_filter_params(self) -> dict:
        all_signals = self.good_signals + self.bad_signals
        labels = [1] * len(self.good_signals) + [0] * len(self.bad_signals)

        best_score = -1
        best_params = {
            'fft_cutoff_frequency': 0.07,
            'lowpass_cutoff': 0.07,
            'lowpass_filter_order': 7
        }

        for fft_cut in np.arange(0.02, 0.20, 0.01):
            ratios = []
            for sig in all_signals:
                xf = sig.get('fft_freqs', np.array([]))
                mags = sig.get('fft_mags', np.array([]))
                total = np.sum(mags ** 2)
                if total < 1e-9:
                    ratios.append(0)
                    continue
                cutoff_idx = np.where(xf >= fft_cut)[0]
                if cutoff_idx.size > 0:
                    hf = np.sum(mags[cutoff_idx[0]:] ** 2)
                    ratios.append(hf / total)
                else:
                    ratios.append(0)

            good_r = [ratios[i] for i, l in enumerate(labels) if l == 1]
            bad_r = [ratios[i] for i, l in enumerate(labels) if l == 0]
            if good_r and bad_r:
                separation = np.mean(bad_r) - np.mean(good_r)
                if separation > best_score:
                    best_score = separation
                    best_params['fft_cutoff_frequency'] = float(fft_cut)

        return best_params


class BayesianCalibrationEstimator:
    """
    Method C: Grid-based Bayesian posterior updating for threshold parameters.
    Provides uncertainty quantification via credible intervals.
    """
    def __init__(self):
        self.param_ranges = {
            'fft_energy_ratio_threshold': np.linspace(0.001, 0.1, 200),
            'exceedance_ratio_threshold': np.linspace(0.1, 0.99, 200),
            'residual_threshold': np.linspace(0.0001, 0.05, 200),
        }
        self.posteriors = {
            name: np.ones_like(grid) / len(grid)
            for name, grid in self.param_ranges.items()
        }

    def update(self, signal_data: dict, judgment: str):
        if judgment == "IGNORE":
            return
        for param_name, grid in self.param_ranges.items():
            observed = self._extract_metric(signal_data, param_name)
            if observed is None:
                continue
            if judgment == "GOOD":
                likelihood = self._sigmoid(grid - observed, steepness=50)
            else:
                likelihood = self._sigmoid(observed - grid, steepness=50)
            self.posteriors[param_name] *= likelihood
            total = np.sum(self.posteriors[param_name])
            if total > 0:
                self.posteriors[param_name] /= total

    def get_estimates(self) -> dict:
        estimates = {}
        for name, grid in self.param_ranges.items():
            posterior = self.posteriors[name]
            map_idx = np.argmax(posterior)
            estimates[name] = float(grid[map_idx])
            cumulative = np.cumsum(posterior)
            low_idx = np.searchsorted(cumulative, 0.025)
            high_idx = np.searchsorted(cumulative, 0.975)
            estimates[f'{name}_ci_low'] = float(grid[min(low_idx, len(grid) - 1)])
            estimates[f'{name}_ci_high'] = float(grid[min(high_idx, len(grid) - 1)])
        return estimates

    @staticmethod
    def _sigmoid(x, steepness=50):
        return 1 / (1 + np.exp(-steepness * x))

    def _extract_metric(self, signal_data: dict, param_name: str):
        mapping = {
            'fft_energy_ratio_threshold': 'energy_ratio',
            'exceedance_ratio_threshold': 'exceedance_ratio',
        }
        if param_name == 'residual_threshold':
            res = signal_data.get('residual_physical')
            if res is not None and len(res) > 0:
                return float(np.max(np.abs(res)))
            return None
        key = mapping.get(param_name)
        return signal_data.get(key)


class ROCCalibrationEstimator:
    """
    Method D: ROC analysis with Youden's J statistic for optimal threshold selection.
    """
    def estimate_threshold_for_metric(self, good_values: list, bad_values: list) -> Optional[float]:
        if not good_values or not bad_values:
            return None
        all_values = sorted(set(good_values + bad_values))
        best_j = -1
        best_threshold = all_values[len(all_values) // 2]
        for threshold in all_values:
            tp = sum(1 for v in good_values if v <= threshold)
            fn = sum(1 for v in good_values if v > threshold)
            fp = sum(1 for v in bad_values if v <= threshold)
            tn = sum(1 for v in bad_values if v > threshold)
            tpr = tp / (tp + fn) if (tp + fn) > 0 else 0
            fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
            j = tpr - fpr
            if j > best_j:
                best_j = j
                best_threshold = threshold
        return best_threshold


class HybridCalibrationEstimator:
    """
    Recommended calibration approach combining Methods A, C, and D.

    Confidence levels:
      Level 0: NO CALIBRATION (< 3+3 signals)
      Level 1: PRELIMINARY (6-7 signals) — Method A only
      Level 2: BASIC (8-11 signals) — Methods A+C
      Level 3: SOLID (12-15 signals) — Methods A+C+D
      Level 4: ROBUST (16+ signals) — All methods converged
    """
    def __init__(self):
        self.percentile_estimator = PercentileBoundaryEstimator()
        self.bayesian_estimator = BayesianCalibrationEstimator()
        self.roc_estimator = ROCCalibrationEstimator()
        self.good_count = 0
        self.bad_count = 0
        self.all_signals = []

    def add_signal(self, raw_data: dict, judgment: str):
        if judgment == "GOOD":
            self.good_count += 1
        elif judgment == "BAD":
            self.bad_count += 1
        else:
            return  # IGNORE
        self.all_signals.append({'data': raw_data, 'judgment': judgment})
        self.percentile_estimator.add_signal(raw_data, judgment)
        self.bayesian_estimator.update(raw_data, judgment)

    @property
    def total_signals(self) -> int:
        return self.good_count + self.bad_count

    @property
    def meets_minimum(self) -> bool:
        return self.good_count >= 3 and self.bad_count >= 3

    @property
    def confidence_level(self) -> int:
        if not self.meets_minimum:
            return 0
        n = self.total_signals
        if n <= 7:
            return 1
        elif n <= 11:
            return 2
        elif n <= 15:
            return 3
        else:
            return 4

    def get_estimates(self) -> Optional[dict]:
        if not self.meets_minimum:
            return None
        level = self.confidence_level
        if level == 1:
            return self.percentile_estimator.estimate_thresholds()
        elif level == 2:
            bayes = self.bayesian_estimator.get_estimates()
            percentile = self.percentile_estimator.estimate_thresholds()
            return self._merge_estimates(bayes, percentile)
        else:
            bayes = self.bayesian_estimator.get_estimates()
            percentile = self.percentile_estimator.estimate_thresholds()
            roc = self._compute_roc_estimates()
            return self._merge_all(bayes, percentile, roc)

    def _merge_estimates(self, bayes: Optional[dict], percentile: Optional[dict]) -> dict:
        result = {}
        if percentile:
            result.update(percentile)
        if bayes:
            for key in ('fft_energy_ratio_threshold', 'exceedance_ratio_threshold', 'residual_threshold'):
                if key in bayes and key in result:
                    result[key] = 0.6 * bayes[key] + 0.4 * result[key]
                elif key in bayes:
                    result[key] = bayes[key]
            for key in bayes:
                if key.endswith('_ci_low') or key.endswith('_ci_high'):
                    result[key] = bayes[key]
        return result

    def _compute_roc_estimates(self) -> Optional[dict]:
        good_sigs = [s['data'] for s in self.all_signals if s['judgment'] == 'GOOD']
        bad_sigs = [s['data'] for s in self.all_signals if s['judgment'] == 'BAD']
        if len(good_sigs) < 3 or len(bad_sigs) < 2:
            return None
        result = {}
        for metric in ('energy_ratio', 'exceedance_ratio'):
            good_vals = [s[metric] for s in good_sigs if metric in s]
            bad_vals = [s[metric] for s in bad_sigs if metric in s]
            thr = self.roc_estimator.estimate_threshold_for_metric(good_vals, bad_vals)
            if thr is not None:
                if metric == 'energy_ratio':
                    result['fft_energy_ratio_threshold'] = thr
                else:
                    result['exceedance_ratio_threshold'] = thr
        return result

    def _merge_all(self, bayes: Optional[dict], percentile: Optional[dict], roc: Optional[dict]) -> dict:
        result = self._merge_estimates(bayes, percentile)
        if roc:
            for key in ('fft_energy_ratio_threshold', 'exceedance_ratio_threshold'):
                if key in roc and key in result:
                    result[key] = (result[key] + roc[key]) / 2
                elif key in roc:
                    result[key] = roc[key]
        return result

    def get_status_info(self) -> dict:
        return {
            'good_count': self.good_count,
            'bad_count': self.bad_count,
            'total_signals': self.total_signals,
            'meets_minimum': self.meets_minimum,
            'confidence_level': self.confidence_level,
        }


def check_signal_similarity(new_signal: np.ndarray, new_fft_mags: np.ndarray,
                            stored_signals: list) -> Tuple[bool, Optional[int]]:
    """Check if a new signal is too similar to previously stored calibration signals."""
    for i, stored in enumerate(stored_signals):
        stored_sig = stored.get('signal_physical')
        stored_mags = stored.get('fft_mags')
        if stored_sig is None or stored_mags is None:
            continue
        # Match lengths for correlation
        min_len = min(len(new_signal), len(stored_sig))
        if min_len < 5:
            continue
        try:
            ncc = np.corrcoef(new_signal[:min_len], stored_sig[:min_len])[0, 1]
        except Exception:
            ncc = 0
        min_fft = min(len(new_fft_mags), len(stored_mags))
        if min_fft > 0:
            cos_sim = np.dot(new_fft_mags[:min_fft], stored_mags[:min_fft]) / (
                np.linalg.norm(new_fft_mags[:min_fft]) * np.linalg.norm(stored_mags[:min_fft]) + 1e-12)
        else:
            cos_sim = 0
        new_energy = np.sum(new_fft_mags ** 2)
        stored_energy = np.sum(stored_mags ** 2)
        energy_diff = abs(new_energy - stored_energy) / (max(new_energy, stored_energy) + 1e-12)
        if ncc > 0.95 and cos_sim > 0.95 and energy_diff < 0.1:
            return True, i
    return False, None


@contextmanager
def styled_figure(figsize=(10, 5), dpi=150):
    """
    Context manager for creating consistently styled matplotlib figures.
    Handles proper cleanup on exit.

    Usage:
        with styled_figure() as (fig, ax):
            ax.plot(x, y)
            fig.savefig('output.png', facecolor=fig.get_facecolor())
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = None
    try:
        fig = Figure(figsize=figsize, dpi=dpi, facecolor='#1E1E1E')
        _ = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        # Apply common dark theme styling
        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white')
        ax.grid(True, linestyle='--', alpha=0.3)
        for spine in ax.spines.values():
            spine.set_color('white')

        yield fig, ax

    finally:
        if fig is not None:
            fig.clf()
            del fig


# --- 3. CORE LOGIC: THE SCREEN MONITOR ENGINE ---
class ScreenMonitor:
    """Handles the core task of capturing and analyzing the screen."""
    
    def __init__(self, config_path, update_callback=None, plot_callback=None):
        self.running = False
        self.thread = None
        self.config_path = config_path
        self.app_config = self._load_config(self.config_path)
        self.update_callback = update_callback
        self.plot_callback = plot_callback
        self.frame_count = 0
        self.verbose_logging_enabled = True
        self.image_logging_enabled = True
        self.image_log_options = ImageLogOptions()
        self.data_log_options = DataLogOptions()
        self.verbose_log_options = VerboseLogOptions()
        self.last_logged_ratio: Optional[float] = None
        self.last_logged_energy: Optional[float] = None
        self.audio_feedback_enabled = False
        self.audio_stream = None
        self.audio_phase = 0
        self.audio_frequency = 400
        self.audio_lock = threading.Lock()
        self.sample_rate = 44100
        self.hit_counters: Dict[str, int] = {}
        self.manual_points_info: Optional[PointsInfo] = None
        self.last_known_status: str = "Unknown"
        self.last_known_overload: str = "Unknown"
        
        # Continuous logging mode (when log_events_only is False)
        self.log_events_only: bool = True
        self.continuous_log_interval: float = 1.0  # seconds
        self.last_continuous_log_time: float = 0.0
        self.continuous_log_counter: int = 0
        
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self.sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        
        self.run_history: Dict[str, Dict] = {}
        self.current_run: str = "Run 1"

        # Calibration mode (Phase 4 / v0.9)
        self.calibration_mode: bool = False
        self.calibration_callback = None  # Called when a new signal is detected in cal mode
        self._pending_calibration_data: Optional[dict] = None
        self._calibration_judgment_received: bool = True  # Start as True so first signal proceeds
        # Phase 3: coherence tracking per run
        self.coherence_tracking: Dict[str, CoherenceTrackingState] = {}

    def _capture_screen(self) -> np.ndarray:
        """
        Capture the screen using the fastest available method.
        Returns BGR numpy array compatible with OpenCV.
        """
        if MSS_AVAILABLE:
            with mss.mss() as sct:
                # Capture primary monitor
                monitor = sct.monitors[2]  # 1 = primary monitor (0 = all monitors combined)
                screenshot = sct.grab(monitor)
                # mss returns BGRA format - use proper OpenCV conversion
                # This is more reliable than just slicing [:, :, :3]
                img = np.array(screenshot, dtype=np.uint8)
                # Convert BGRA to BGR using OpenCV for consistent color handling
                return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        else:
            # Fallback to pyautogui
            screenshot = pyautogui.screenshot()
            # pyautogui returns RGB PIL Image, convert to BGR numpy array
            return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    def start(self, verbose_logging=True, image_logging=True, image_log_options=None,
              data_log_options=None, verbose_log_options=None, manual_points=None):
        if not self.app_config.regions:
            messagebox.showerror("Error", "Cannot start. Please load a valid configuration.")
            return False
        if not OCR_AVAILABLE and any(r.roi_type in ['status', 'overload', 'run', 'hammer', 'response'] 
                                      for r in self.app_config.regions.values()):
            logger.warning("Config uses OCR regions, but pytesseract is not available.")
        self.verbose_logging_enabled = verbose_logging
        self.image_logging_enabled = image_logging
        self.image_log_options = image_log_options if image_log_options else ImageLogOptions()
        self.data_log_options = data_log_options if data_log_options else DataLogOptions()
        self.verbose_log_options = verbose_log_options if verbose_log_options else VerboseLogOptions()
        self.manual_points_info = manual_points
        self.frame_count = 0
        self.last_logged_ratio = None
        self.last_logged_energy = None
        self.hit_counters.clear()
        self.run_history.clear()
        self.running = True
        self.last_known_status = "Unknown"
        self.last_known_overload = "Unknown"
        self.thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.thread.start()
        logger.info("Screen monitoring thread started for USMA v0.9")
        return True

    def stop(self):
        self.running = False
        self._stop_audio_feedback()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
        logger.info("Screen monitoring stopped.")

    def update_config(self, new_config_path):
        self.config_path = new_config_path
        self.app_config = self._load_config(new_config_path)
        logger.info(f"Configuration updated to {new_config_path}")

    def set_calibration_mode(self, enabled: bool, callback=None):
        """Enable/disable calibration mode."""
        self.calibration_mode = enabled
        self.calibration_callback = callback
        self._pending_calibration_data = None
        self._calibration_judgment_received = True

    def receive_calibration_judgment(self):
        """Called by GUI after user provides judgment. Resumes logging state tracking."""
        self._calibration_judgment_received = True
        # Update last_logged so the same signal isn't re-detected
        if self._pending_calibration_data:
            # Use first signal's energy ratio as the logged value
            first = self._pending_calibration_data[0]
            self.last_logged_ratio = first.get('energy_ratio')
            self.last_logged_energy = first.get('high_freq_energy')
        self._pending_calibration_data = None

    def set_audio_feedback(self, enabled: bool):
        self.audio_feedback_enabled = enabled
        if not enabled:
            self._stop_audio_feedback()

    def _load_config(self, path: str) -> AppConfig:
        try:
            logger.info(f"Loading config from: {path}")
            if not os.path.exists(path):
                logger.warning(f"Config file does not exist: {path}")
                return AppConfig()
            
            with open(path, 'r') as f:
                config_data = json.load(f)
            
            config = AppConfig()
            metadata = config_data.get('_metadata', {})
            
            # Debug: Log what we found in metadata
            logger.info(f"Config _metadata keys: {list(metadata.keys()) if metadata else 'NONE'}")
            if 'hsv_lower' in metadata:
                logger.info(f"  Found hsv_lower in config: {metadata['hsv_lower']}")
            else:
                logger.warning(f"  hsv_lower NOT FOUND in config _metadata - using defaults")
            if 'hsv_upper' in metadata:
                logger.info(f"  Found hsv_upper in config: {metadata['hsv_upper']}")
            else:
                logger.warning(f"  hsv_upper NOT FOUND in config _metadata - using defaults")
            
            config.hsv_lower = metadata.get('hsv_lower', config.hsv_lower)
            config.hsv_upper = metadata.get('hsv_upper', config.hsv_upper)
            config.screenshot_interval = metadata.get('screenshot_interval', config.screenshot_interval)
            config.fft_cutoff_frequency = metadata.get('fft_cutoff_frequency', config.fft_cutoff_frequency)
            config.fft_energy_ratio_threshold = metadata.get('fft_energy_ratio_threshold', config.fft_energy_ratio_threshold)
            config.lowpass_cutoff = metadata.get('lowpass_cutoff', config.lowpass_cutoff)
            config.lowpass_filter_order = metadata.get('lowpass_filter_order', config.lowpass_filter_order)
            config.residual_threshold = metadata.get('residual_threshold', config.residual_threshold)
            config.exceedance_ratio_threshold = metadata.get('exceedance_ratio_threshold', config.exceedance_ratio_threshold)
            # --- New v0.6.0 fields (backward-compatible: all have dataclass defaults) ---
            config.psd_fft_cutoff_frequency = metadata.get('psd_fft_cutoff_frequency', config.psd_fft_cutoff_frequency)
            config.psd_fft_energy_ratio_threshold = metadata.get('psd_fft_energy_ratio_threshold', config.psd_fft_energy_ratio_threshold)
            config.psd_lowpass_cutoff = metadata.get('psd_lowpass_cutoff', config.psd_lowpass_cutoff)
            config.psd_lowpass_filter_order = metadata.get('psd_lowpass_filter_order', config.psd_lowpass_filter_order)
            config.psd_residual_threshold = metadata.get('psd_residual_threshold', config.psd_residual_threshold)
            config.psd_exceedance_ratio_threshold = metadata.get('psd_exceedance_ratio_threshold', config.psd_exceedance_ratio_threshold)
            config.coherence_threshold = metadata.get('coherence_threshold', config.coherence_threshold)
            config.coherence_degradation_pct = metadata.get('coherence_degradation_pct', config.coherence_degradation_pct)
            config.hits_per_run = metadata.get('hits_per_run', config.hits_per_run)
            
            logger.info(f"Config loaded - HSV Lower: {config.hsv_lower}, HSV Upper: {config.hsv_upper}")
            
            region_fields = MonitoringRegion.__annotations__.keys()
            for name, data in config_data.items():
                if not name.startswith('_') and isinstance(data, dict):
                    filtered_data = {k: v for k, v in data.items() if k in region_fields}
                    if 'name' in filtered_data:
                        config.regions[name] = MonitoringRegion(**filtered_data)
            
            logger.info(f"Config regions loaded: {list(config.regions.keys())}")
            return config
        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AppConfig()

    def _audio_callback(self, outdata, frames, time_info, status):
        try:
            if status:
                logger.warning(f"Audio stream status: {status}")
            t = (self.audio_phase + np.arange(frames)) / self.sample_rate
            t = t.reshape(-1, 1)
            amplitude = np.iinfo(np.int16).max * 0.3
            outdata[:] = amplitude * np.sin(2 * np.pi * self.audio_frequency * t)
            self.audio_phase += frames
        except Exception as e:
            logger.error(f"Audio callback error: {e}")
            outdata.fill(0)

    def _start_audio_feedback(self):
        if not SOUND_DEVICE_AVAILABLE or self.audio_stream is not None:
            return
        try:
            self.audio_phase = 0
            self.audio_stream = sd.OutputStream(
                samplerate=self.sample_rate, channels=1, 
                callback=self._audio_callback, dtype='int16'
            )
            self.audio_stream.start()
            logger.info("Continuous audio feedback started.")
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}")
            self.audio_stream = None

    def _stop_audio_feedback(self):
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
                logger.info("Continuous audio feedback stopped.")
            except Exception as e:
                logger.error(f"Error stopping audio stream: {e}")
            finally:
                self.audio_stream = None
            
    def _monitoring_loop(self):
        while self.running:
            start_time = time.time()
            try:
                image = self._capture_screen()
                frame_result, all_rois = self._process_frame(image)
                
                if self.update_callback:
                    self.update_callback(frame_result)
                
                if self.audio_feedback_enabled:
                    with self.audio_lock:
                        is_hf = frame_result.overall_is_hf if frame_result.overall_is_hf is not None else False
                        if is_hf and self.audio_stream is None:
                            self._start_audio_feedback()
                        elif not is_hf and self.audio_stream is not None:
                            self._stop_audio_feedback()
                
                # Handle logging: event-based or continuous
                if self.log_events_only:
                    self._handle_logging(frame_result, all_rois)
                else:
                    # Continuous logging mode - log every continuous_log_interval seconds
                    current_time = time.time()
                    if current_time - self.last_continuous_log_time >= self.continuous_log_interval:
                        self._handle_continuous_logging(frame_result, all_rois)
                        self.last_continuous_log_time = current_time
                
                elapsed_time = time.time() - start_time
                sleep_duration = self.app_config.screenshot_interval - elapsed_time
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(1)

    def _process_frame(self, image: np.ndarray) -> Tuple[FrameAnalysisResult, Dict[str, np.ndarray]]:
        frame_result = FrameAnalysisResult()
        ocr_points_active = False
        all_rois: Dict[str, np.ndarray] = {}
        
        frame_result.points_info = PointsInfo()
        
        for name, region in self.app_config.regions.items():
            if not region.enabled:
                continue
            roi = image[region.y:region.y+region.height, region.x:region.x+region.width]
            if roi.size == 0:
                continue
            
            all_rois[name] = roi.copy()
            
            if region.roi_type == 'frf':
                analysis_result = self._analyze_wave_pattern(roi, region)
                if analysis_result:
                    frame_result.frf_results[name] = analysis_result
                    frame_result.active_regions[name] = region
            elif region.roi_type == 'psd':
                psd_analysis = self._analyze_wave_pattern(roi, region, param_prefix='psd_')
                if psd_analysis:
                    frame_result.psd_results[name] = psd_analysis
                    frame_result.active_regions[name] = region
            elif region.roi_type == 'coherence':
                coh_result = self._analyze_coherence_signal(roi, region)
                if coh_result:
                    frame_result.coherence_results[name] = coh_result
                    frame_result.active_regions[name] = region
            elif OCR_AVAILABLE:
                if region.roi_type == 'averages':
                    avg = self._analyze_averages_robust(roi)
                    if avg:
                        frame_result.current_averages = avg
                elif region.roi_type == 'status':
                    text, diag_imgs = self._analyze_status_robust(roi)
                    frame_result.status_text = text
                    frame_result.ocr_images.update(diag_imgs)
                elif region.roi_type == 'overload':
                    text, diag_imgs = self._analyze_overload_robust(roi)
                    frame_result.overload_text = text
                    frame_result.ocr_images.update(diag_imgs)
                elif region.roi_type == 'run':
                    run_str, diag_imgs = self._analyze_run_robust(roi)
                    if run_str:
                        frame_result.points_info.run = run_str
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True
                elif region.roi_type == 'hammer':
                    point, direction, diag_imgs = self._analyze_point_and_dir_robust(roi, "hammer")
                    if point:
                        frame_result.points_info.hammer_point = point
                    if direction:
                        frame_result.points_info.hammer_dir = direction
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True
                elif region.roi_type == 'response':
                    point, direction, diag_imgs = self._analyze_point_and_dir_robust(roi, "response")
                    if point:
                        frame_result.points_info.response_point = point
                    if direction:
                        frame_result.points_info.response_dir = direction
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True

        current_status = frame_result.status_text
        if current_status != "Unknown" and current_status != self.last_known_status:
            if self.verbose_logging_enabled:
                logger.info(f"STATUS UPDATE: '{self.last_known_status}' -> '{current_status}'")
            self.last_known_status = current_status
            
        current_overload = frame_result.overload_text
        if current_overload != "Unknown" and current_overload != self.last_known_overload:
            if self.verbose_logging_enabled:
                logger.info(f"OVERLOAD UPDATE: '{self.last_known_overload}' -> '{current_overload}'")
            self.last_known_overload = current_overload

        if not ocr_points_active and self.manual_points_info:
            frame_result.points_info = self.manual_points_info

        if frame_result.frf_results:
            classifications = [res.is_high_frequency for res in frame_result.frf_results.values()]
            frame_result.overall_is_hf = sum(classifications) > len(classifications) / 2 if classifications else False
            frame_result.avg_energy_ratio = np.mean([res.energy_ratio for res in frame_result.frf_results.values()])
            frame_result.avg_high_freq_energy = np.mean([res.high_freq_energy for res in frame_result.frf_results.values()])
            frame_result.avg_exceedance_count = np.mean([res.exceedance_count for res in frame_result.frf_results.values()])
            frame_result.avg_exceedance_ratio = np.mean([res.exceedance_ratio for res in frame_result.frf_results.values()])
            lp_classifications = [res.lowpass_is_bad_hit for res in frame_result.frf_results.values()]
            frame_result.overall_lowpass_bad = sum(lp_classifications) > len(lp_classifications) / 2 if lp_classifications else False

        if frame_result.psd_results:
            psd_class = [res.is_high_frequency for res in frame_result.psd_results.values()]
            frame_result.psd_overall_is_hf = sum(psd_class) > len(psd_class) / 2
            frame_result.psd_avg_energy_ratio = float(np.mean([res.energy_ratio for res in frame_result.psd_results.values()]))
            frame_result.psd_avg_exceedance_count = float(np.mean([res.exceedance_count for res in frame_result.psd_results.values()]))
            frame_result.psd_avg_exceedance_ratio = float(np.mean([res.exceedance_ratio for res in frame_result.psd_results.values()]))
            psd_lp_class = [res.lowpass_is_bad_hit for res in frame_result.psd_results.values()]
            frame_result.psd_overall_lowpass_bad = sum(psd_lp_class) > len(psd_lp_class) / 2
        
        return frame_result, all_rois

    def _handle_logging(self, frame_result: FrameAnalysisResult, all_rois: Dict[str, np.ndarray]):
        if not frame_result.frf_results and not frame_result.psd_results:
            return

        has_changed = (frame_result.avg_energy_ratio is not None and
                       (self.last_logged_ratio is None or
                        not np.isclose(frame_result.avg_energy_ratio, self.last_logged_ratio, atol=1e-5) or
                        not np.isclose(frame_result.avg_high_freq_energy, self.last_logged_energy, atol=1e-5)))

        if has_changed:
            # --- Calibration mode: pause and wait for judgment ---
            if self.calibration_mode and self.calibration_callback:
                # Collect raw analysis data from all FRF and PSD results
                cal_data_list = []
                for frf_name, frf_result in frame_result.frf_results.items():
                    cal_data_list.append({
                        'signal_physical': frf_result.signal_physical.copy() if frf_result.signal_physical is not None else np.array([]),
                        'fft_freqs': frf_result.fft_freqs.copy(),
                        'fft_mags': frf_result.fft_mags.copy(),
                        'residual_physical': frf_result.residual_physical.copy() if frf_result.residual_physical is not None else np.array([]),
                        'energy_ratio': frf_result.energy_ratio,
                        'exceedance_ratio': frf_result.exceedance_ratio,
                        'exceedance_count': frf_result.exceedance_count,
                        'total_energy': frf_result.total_energy,
                        'high_freq_energy': frf_result.high_freq_energy,
                        'roi_name': frf_name,
                        'signal_type': 'frf',
                    })
                for psd_name, psd_result in frame_result.psd_results.items():
                    cal_data_list.append({
                        'signal_physical': psd_result.signal_physical.copy() if psd_result.signal_physical is not None else np.array([]),
                        'fft_freqs': psd_result.fft_freqs.copy(),
                        'fft_mags': psd_result.fft_mags.copy(),
                        'residual_physical': psd_result.residual_physical.copy() if psd_result.residual_physical is not None else np.array([]),
                        'energy_ratio': psd_result.energy_ratio,
                        'exceedance_ratio': psd_result.exceedance_ratio,
                        'exceedance_count': psd_result.exceedance_count,
                        'total_energy': psd_result.total_energy,
                        'high_freq_energy': psd_result.high_freq_energy,
                        'roi_name': psd_name,
                        'signal_type': 'psd',
                    })
                if cal_data_list:
                    self._pending_calibration_data = cal_data_list
                    self._calibration_judgment_received = False
                    self.calibration_callback(cal_data_list, frame_result)
                    # Don't update last_logged until judgment is received
                    return

            points = frame_result.points_info

            classification, _color = self.classify_hit(frame_result)


            if self.verbose_logging_enabled:
                logger.info(f"--- WAVE EVENT DETECTED ---")
                logger.info(f"  OCR Status: '{frame_result.status_text}'")
                logger.info(f"  OCR Overload: '{frame_result.overload_text}'")
                logger.info(f"  OCR Run: '{points.run}'")
                logger.info(f"  OCR Hammer: '{points.hammer_point}' Dir: '{points.hammer_dir}'")
                logger.info(f"  OCR Response: '{points.response_point}' Dir: '{points.response_dir}'")
                if frame_result.frf_results:
                    logger.info(f"  [FRF] FFT Energy Ratio: {frame_result.avg_energy_ratio:.3e} (thr: {self.app_config.fft_energy_ratio_threshold:.3e}) -> {'BAD' if frame_result.overall_is_hf else 'OK'}")
                    logger.info(f"  [FRF] LP Exceedances: {frame_result.avg_exceedance_count:.0f} ({frame_result.avg_exceedance_ratio:.1%}) -> {'BAD' if frame_result.overall_lowpass_bad else 'OK'}")
                if frame_result.psd_results:
                    logger.info(f"  [PSD] FFT Energy Ratio: {frame_result.psd_avg_energy_ratio:.3e} (thr: {self.app_config.psd_fft_energy_ratio_threshold:.3e}) -> {'BAD' if frame_result.psd_overall_is_hf else 'OK'}")
                    logger.info(f"  [PSD] LP Exceedances: {frame_result.psd_avg_exceedance_count:.0f} ({frame_result.psd_avg_exceedance_ratio:.1%}) -> {'BAD' if frame_result.psd_overall_lowpass_bad else 'OK'}")
                logger.info(f"  CLASSIFICATION: {classification}")
                logger.info(f"----------------------------")
            
            self.last_logged_ratio = frame_result.avg_energy_ratio
            self.last_logged_energy = frame_result.avg_high_freq_energy
            
            counter_key = f"{points.hammer_point}{points.response_point}"
            current_hit = self.hit_counters.get(counter_key, 0) + 1
            self.hit_counters[counter_key] = current_hit
            
            hit_key = f"{counter_key}_{current_hit}"
            self.current_run = points.run
            if self.current_run not in self.run_history:
                self.run_history[self.current_run] = {}
            
            for frf_name, frf_result in frame_result.frf_results.items():
                base_filename = f"{frf_name}_{counter_key}_{current_hit}"
                region = frame_result.active_regions[frf_name]
                
                self.run_history[self.current_run][hit_key] = {
                    'exceedance_count': frf_result.exceedance_count,
                    'exceedance_ratio': frf_result.exceedance_ratio,
                    'energy_ratio': frf_result.energy_ratio,
                    'is_hf': frf_result.is_high_frequency,
                    'lowpass_bad': frf_result.lowpass_is_bad_hit
                }
                
                if self.image_logging_enabled: 
                    self._create_visual_logs(frf_result, frame_result, frf_name, base_filename, all_rois)
                if self.data_log_options.log_unv: 
                    self._save_unv_log(frf_result, frame_result, frf_name, base_filename)
                
                if self.plot_callback:
                    # Create lightweight copy for plot callback
                    lightweight_data = LightweightHitData(
                        signal_physical=frf_result.signal_physical.copy() if frf_result.signal_physical is not None else np.array([]),
                        filtered_physical=frf_result.filtered_physical.copy() if frf_result.filtered_physical is not None else None,
                        residual_physical=frf_result.residual_physical.copy() if frf_result.residual_physical is not None else None,
                        fft_freqs=frf_result.fft_freqs.copy(),
                        fft_mags=frf_result.fft_mags.copy(),
                        energy_ratio=frf_result.energy_ratio,
                        is_high_frequency=frf_result.is_high_frequency,
                        exceedance_count=frf_result.exceedance_count,
                        exceedance_ratio=frf_result.exceedance_ratio,
                        lowpass_is_bad_hit=frf_result.lowpass_is_bad_hit,
                        total_energy=frf_result.total_energy,
                        high_freq_energy=frf_result.high_freq_energy,
                        y_axis_unit=region.y_axis_unit,
                        x_axis_min=region.x_axis_min,
                        x_axis_max=region.x_axis_max,
                        hit_key=hit_key,
                        run=points.run
                    )
                    self.plot_callback(lightweight_data, self.run_history.copy())

            # --- PSD results: fire separate plot_callback with psd_ prefix ---
            for psd_name, psd_result in frame_result.psd_results.items():
                region = frame_result.active_regions[psd_name]
                psd_base = f"psd_{psd_name}_{counter_key}_{current_hit}"
                hit_key_psd = f"psd_{counter_key}_{current_hit}"

                self.run_history[self.current_run][hit_key_psd] = {
                    'exceedance_count': psd_result.exceedance_count,
                    'exceedance_ratio': psd_result.exceedance_ratio,
                    'energy_ratio': psd_result.energy_ratio,
                    'is_hf': psd_result.is_high_frequency,
                    'lowpass_bad': psd_result.lowpass_is_bad_hit,
                    'signal_type': 'psd',
                }

                if self.image_logging_enabled:
                    self._create_visual_logs(psd_result, frame_result, psd_name, psd_base, all_rois)

                if self.plot_callback:
                    psd_lightweight = LightweightHitData(
                        signal_physical=psd_result.signal_physical.copy() if psd_result.signal_physical is not None else np.array([]),
                        filtered_physical=psd_result.filtered_physical.copy() if psd_result.filtered_physical is not None else None,
                        residual_physical=psd_result.residual_physical.copy() if psd_result.residual_physical is not None else None,
                        fft_freqs=psd_result.fft_freqs.copy(),
                        fft_mags=psd_result.fft_mags.copy(),
                        energy_ratio=psd_result.energy_ratio,
                        is_high_frequency=psd_result.is_high_frequency,
                        exceedance_count=psd_result.exceedance_count,
                        exceedance_ratio=psd_result.exceedance_ratio,
                        lowpass_is_bad_hit=psd_result.lowpass_is_bad_hit,
                        total_energy=psd_result.total_energy,
                        high_freq_energy=psd_result.high_freq_energy,
                        y_axis_unit=region.y_axis_unit,
                        x_axis_min=region.x_axis_min,
                        x_axis_max=region.x_axis_max,
                        hit_key=hit_key_psd,
                        run=points.run
                    )
                    self.plot_callback(psd_lightweight, self.run_history.copy())

    def _handle_continuous_logging(self, frame_result: FrameAnalysisResult, all_rois: Dict[str, np.ndarray]):
        """
        Continuous logging mode - saves images and logs every interval regardless of wave events.
        Used for debugging HSV calibration and signal detection issues.
        """
        self.continuous_log_counter += 1
        timestamp = datetime.now().strftime("%H%M%S")
        base_filename = f"continuous_{timestamp}_{self.continuous_log_counter}"
        
        if self.verbose_logging_enabled:
            logger.info(f"--- CONTINUOUS LOG #{self.continuous_log_counter} ---")
            logger.info(f"  HSV Lower: {self.app_config.hsv_lower}")
            logger.info(f"  HSV Upper: {self.app_config.hsv_upper}")
            logger.info(f"  Status: '{frame_result.status_text}'")
            logger.info(f"  Wave Results: {len(frame_result.frf_results)} regions detected")
            if frame_result.avg_energy_ratio is not None:
                logger.info(f"  Avg Energy Ratio: {frame_result.avg_energy_ratio:.3e}")
                logger.info(f"  Avg Exceedance: {frame_result.avg_exceedance_count}")
        
        # Save images for all wave regions regardless of detection
        if self.image_logging_enabled:
            for region_name, region in self.app_config.regions.items():
                if region.roi_type in ('frf', 'psd') and region.enabled:
                    if region_name in all_rois:
                        roi = all_rois[region_name]
                        
                        # Always save ROI screenshot in continuous mode
                        if self.image_log_options.include_screenshot:
                            cv2.imwrite(f"image_logs/ROIs/{base_filename}_{region_name}_ROI.jpg", roi)
                        
                        # Create and save mask image
                        if self.image_log_options.include_color_filter:
                            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                            mask = cv2.inRange(hsv, np.array(self.app_config.hsv_lower), np.array(self.app_config.hsv_upper))
                            cv2.imwrite(f"image_logs/ColorMasks/{base_filename}_{region_name}_mask.jpg", mask)
                            
                            # Log mask statistics
                            total_px = mask.shape[0] * mask.shape[1]
                            white_px = np.count_nonzero(mask)
                            if self.verbose_logging_enabled:
                                logger.info(f"  [{region_name}] Mask: {white_px}/{total_px} ({100*white_px/total_px:.2f}%) white pixels")

    # =========================================================================
    # ENHANCED OCR METHODS
    # =========================================================================
    
    def _preprocess_for_ocr(self, roi: np.ndarray, scale_factor: int = 4, 
                            use_clahe: bool = True, use_sharpen: bool = True,
                            use_morphology: bool = False, invert: bool = True) -> np.ndarray:
        if roi.size == 0:
            return np.array([])
        
        width = int(roi.shape[1] * scale_factor)
        height = int(roi.shape[0] * scale_factor)
        resized = cv2.resize(roi, (width, height), interpolation=cv2.INTER_CUBIC)
        
        if len(resized.shape) == 3:
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        else:
            gray = resized
        
        if use_clahe:
            gray = self.clahe.apply(gray)
        
        if use_sharpen:
            gray = cv2.filter2D(gray, -1, self.sharpen_kernel)
        
        if invert:
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        else:
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        
        if use_morphology:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        return thresh

    def _run_ocr_with_config(self, image: np.ndarray, psm: int = 7, 
                              whitelist: Optional[str] = None, 
                              load_dawgs: bool = True) -> str:
        try:
            custom_config = f'--oem 3 --psm {psm}'
            if whitelist:
                custom_config += f' -c tessedit_char_whitelist={whitelist}'
            if not load_dawgs:
                custom_config += ' -c load_system_dawg=false -c load_freq_dawg=false'
            
            text = pytesseract.image_to_string(image, config=custom_config)
            return text.strip()
        except Exception as e:
            logger.debug(f"OCR failed: {e}")
            return ""

    def _analyze_status_robust(self, roi: np.ndarray) -> Tuple[str, Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True, 'invert': True},
            {'scale_factor': 3, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': False},
        ]
        
        best_text = ""
        best_img = None
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
                
            for psm in [7, 6]:
                text = self._run_ocr_with_config(preprocessed, psm=psm)
                text_lower = text.lower()
                
                if 'wait' in text_lower or 'trigger' in text_lower:
                    diag_imgs['status_preprocessed'] = preprocessed
                    return "Waiting for Trigger...", diag_imgs
                if 'measur' in text_lower:
                    diag_imgs['status_preprocessed'] = preprocessed
                    return "Measuring...", diag_imgs
                if 'ready' in text_lower:
                    diag_imgs['status_preprocessed'] = preprocessed
                    return "Ready", diag_imgs
                
                if len(text) > len(best_text):
                    best_text = text
                    best_img = preprocessed
        
        mean_color = np.mean(roi, axis=(0, 1))
        if best_img is not None:
            diag_imgs['status_preprocessed'] = best_img
        
        if mean_color[1] > 120 and mean_color[1] > mean_color[2]:
            return "Measuring... (color)", diag_imgs
        if mean_color[2] > 120 and mean_color[2] > mean_color[1]:
            return "Waiting for Trigger... (color)", diag_imgs
        if mean_color[0] > 120:
            return "Ready (color)", diag_imgs
        
        return f"Unknown (OCR: '{best_text[:20]}')" if best_text else "Unknown", diag_imgs

    def _analyze_overload_robust(self, roi: np.ndarray) -> Tuple[str, Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        mean_color = np.mean(roi, axis=(0, 1))
        is_red = mean_color[2] > 150 and mean_color[1] < 100 and mean_color[0] < 100
        
        if not is_red:
            return "No Overload", diag_imgs
        
        preprocessed = self._preprocess_for_ocr(roi, scale_factor=4)
        diag_imgs['overload_preprocessed'] = preprocessed
        
        whitelist = '0123456789ChannelinOverload '
        text = self._run_ocr_with_config(preprocessed, psm=7, whitelist=whitelist)
        
        match = re.search(r'(\d+)\s*Channel', text, re.IGNORECASE)
        if match:
            return f"{match.group(1)} Channel in Overload", diag_imgs
        
        return "Channel in Overload", diag_imgs

    def _analyze_run_robust(self, roi: np.ndarray) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True, 'invert': True},
            {'scale_factor': 6, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
        ]
        
        whitelist = 'Run0123456789 '
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
            
            diag_imgs[f'run_attempt_{i}'] = preprocessed
            
            for psm in [7, 8, 6]:
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                run_match = re.search(r'[Rr][Uu][Nn]\s*(\d+)', text)
                if run_match:
                    run_str = f"Run {run_match.group(1)}"
                    return run_str, diag_imgs
                
                num_match = re.search(r'^(\d+)$', text.strip())
                if num_match:
                    run_str = f"Run {num_match.group(1)}"
                    return run_str, diag_imgs
        
        return None, diag_imgs

    def _analyze_point_and_dir_robust(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        point, direction = None, None
        diag_imgs = {}
        
        try:
            width = roi.shape[1]
            split_ratios = [0.65, 0.70, 0.75, 0.60]
            
            for split_ratio in split_ratios:
                split_x = int(width * split_ratio)
                point_roi = roi[:, :split_x]
                dir_roi = roi[:, split_x:]
                
                point_result = self._analyze_point_only(point_roi, f"{name}_point")
                if point_result[0]:
                    point = point_result[0]
                    diag_imgs.update(point_result[1])
                    
                    dir_result = self._analyze_direction_only(dir_roi, f"{name}_dir")
                    if dir_result[0]:
                        direction = dir_result[0]
                    diag_imgs.update(dir_result[1])
                    break
            
            if not point:
                full_result = self._analyze_full_point_roi(roi, name)
                point = full_result[0]
                direction = full_result[1]
                diag_imgs.update(full_result[2])
                
        except Exception as e:
            logger.error(f"Failed to analyze point/dir for {name}: {e}")
        
        return point, direction, diag_imgs

    def _analyze_point_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False},
            {'scale_factor': 6, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True},
            {'scale_factor': 4, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False},
        ]
        
        whitelist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789 '
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
            
            diag_imgs[f'{name}_attempt_{i}'] = preprocessed
            
            for psm in [7, 8, 13]:
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                match = re.search(r'([A-Za-z])\s*[:\s]\s*(\d+)', text)
                if match:
                    point = f"{match.group(1).upper()}{match.group(2)}"
                    return point, diag_imgs
                
                match = re.search(r'([A-Za-z])(\d+)', text)
                if match:
                    point = f"{match.group(1).upper()}{match.group(2)}"
                    return point, diag_imgs
                
                match = re.search(r'^[\s:]*(\d+)\s*$', text)
                if match:
                    point = f"P{match.group(1)}"
                    return point, diag_imgs
        
        return None, diag_imgs

    def _analyze_direction_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 6, 'use_clahe': True, 'use_sharpen': True},
            {'scale_factor': 5, 'use_clahe': False, 'use_sharpen': True},
        ]
        
        whitelist = '+-XYZxyz'
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
            
            diag_imgs[f'{name}_attempt_{i}'] = preprocessed
            
            for psm in [10, 8, 13]:
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                match = re.search(r'([+\-])?\s*([XYZxyz])', text)
                if match:
                    sign = match.group(1) if match.group(1) else '+'
                    axis = match.group(2).upper()
                    direction = f"{sign}{axis}"
                    return direction, diag_imgs
        
        return None, diag_imgs

    def _analyze_full_point_roi(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        point, direction = None, None
        
        preprocessed = self._preprocess_for_ocr(roi, scale_factor=5, use_clahe=True, use_sharpen=True)
        if preprocessed.size == 0:
            return None, None, diag_imgs
        
        diag_imgs[f'{name}_full'] = preprocessed
        
        whitelist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789+-XYZxyz '
        text = self._run_ocr_with_config(preprocessed, psm=7, whitelist=whitelist, load_dawgs=False)
        
        full_match = re.search(r'([A-Za-z])\s*[:\s]\s*(\d+)\s*([+\-]?\s*[XYZxyz])?', text)
        if full_match:
            point = f"{full_match.group(1).upper()}{full_match.group(2)}"
            if full_match.group(3):
                dir_text = full_match.group(3).replace(' ', '')
                if len(dir_text) == 1:
                    direction = f"+{dir_text.upper()}"
                else:
                    direction = f"{dir_text[0]}{dir_text[1].upper()}"
        
        return point, direction, diag_imgs

    # =========================================================================
    # COHERENCE ANALYSIS METHODS (Phase 3)
    # =========================================================================

    def _analyze_averages_robust(self, roi: np.ndarray) -> int:
        """OCR the 'averages' ROI to extract current hit/average count."""
        if not OCR_AVAILABLE:
            return 0
        try:
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            text = self._run_ocr_with_config(thresh, psm=7, whitelist='0123456789', load_dawgs=False)
            digits = ''.join(c for c in text if c.isdigit())
            return int(digits) if digits else 0
        except Exception:
            return 0

    def _analyze_coherence_signal(self, roi: np.ndarray, region: MonitoringRegion) -> Optional['CoherenceAnalysisResult']:
        """Analyze a coherence signal ROI using the same HSV-based extraction pipeline.
        Coherence is always 0..1; badness = integral(1-gamma^2) / freq_span."""
        try:
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            lower = np.array(self.app_config.hsv_lower)
            upper = np.array(self.app_config.hsv_upper)
            mask = cv2.inRange(hsv, lower, upper)
            if not self._validate_signal_quality(mask):
                return None

            # Build signal vector (same as FRF)
            signal_pixels = np.zeros(roi.shape[1], dtype=np.float64)
            for col in range(roi.shape[1]):
                col_pixels = np.where(mask[:, col] > 0)[0]
                if len(col_pixels) > 0:
                    signal_pixels[col] = float(roi.shape[0] - int(np.mean(col_pixels))) / roi.shape[0]
                else:
                    signal_pixels[col] = np.nan

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
            badness_integral = float(np.trapz(inverted_signal, freq_axis))
            normalized_badness = badness_integral / freq_span

            # Per-band (4 bands) badness
            band_size = n // 4
            band_badness = []
            for i in range(4):
                start_i = i * band_size
                end_i = (i + 1) * band_size if i < 3 else n
                band_inv = inverted_signal[start_i:end_i]
                band_freq = freq_axis[start_i:end_i]
                band_badness.append(float(np.trapz(band_inv, band_freq)) / max(band_freq[-1] - band_freq[0], 1.0))

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

    def _analyze_wave_pattern(
        self,
        roi: np.ndarray,
        region: MonitoringRegion,
        param_prefix: str = "",
    ) -> Optional[FRFAnalysisResult]:
        if roi.size == 0:
            return None

        # Debug: Log HSV filter values being used periodically
        if (
            self.verbose_logging_enabled
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

        # --- FFT analysis (uses param_prefix to select FRF or PSD parameters) ---
        fft_cutoff = getattr(self.app_config, f"{param_prefix}fft_cutoff_frequency")
        fft_threshold = getattr(self.app_config, f"{param_prefix}fft_energy_ratio_threshold")
        lp_cutoff = getattr(self.app_config, f"{param_prefix}lowpass_cutoff")
        lp_order = getattr(self.app_config, f"{param_prefix}lowpass_filter_order")
        res_threshold = getattr(self.app_config, f"{param_prefix}residual_threshold")
        exc_threshold = getattr(self.app_config, f"{param_prefix}exceedance_ratio_threshold")

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

    def classify_hit(
        self, frame_result: FrameAnalysisResult
    ) -> Tuple[str, str]:
        """
        Determine hit quality classification from a FrameAnalysisResult.

        Considers FRF dual-method results and PSD (Phase 2). Coherence
        contribution added in Phase 3.

        Returns:
            (classification_text, severity_color) where severity_color is one of
            "green", "orange", or "red".
        """
        frf_fft_bad = frame_result.overall_is_hf or False
        frf_lp_bad = frame_result.overall_lowpass_bad or False
        psd_fft_bad = frame_result.psd_overall_is_hf or False
        psd_lp_bad = frame_result.psd_overall_lowpass_bad or False

        any_bad = frf_fft_bad or frf_lp_bad or psd_fft_bad or psd_lp_bad
        all_bad = (frf_fft_bad or frf_lp_bad) and (psd_fft_bad or psd_lp_bad) if (
            frame_result.frf_results and frame_result.psd_results
        ) else (frf_fft_bad and frf_lp_bad) or (psd_fft_bad and psd_lp_bad)

        if not any_bad:
            return "GOOD HIT", "green"

        # Build detail string
        reasons = []
        if frf_fft_bad:
            reasons.append("FRF-FFT")
        if frf_lp_bad:
            reasons.append("FRF-LP")
        if psd_fft_bad:
            reasons.append("PSD-FFT")
        if psd_lp_bad:
            reasons.append("PSD-LP")
        detail = "+".join(reasons)

        if all_bad:
            return f"BAD HIT ({detail})", "red"
        else:
            return f"SUSPECT ({detail})", "orange"


    # =========================================================================
    # VISUAL LOGGING - THREAD-SAFE (Using Figure directly, not pyplot)
    # =========================================================================

    def _create_visual_logs(self, frf_result: FRFAnalysisResult, frame_result: FrameAnalysisResult, 
                           frf_name: str, base_filename: str, all_rois: Dict[str, np.ndarray]):
        try:
            region = frame_result.active_regions[frf_name]
            
            title_info = (f"{frame_result.points_info.run} | H: {frame_result.points_info.hammer_point}"
                         f"{frame_result.points_info.hammer_dir} R: {frame_result.points_info.response_point}"
                         f"{frame_result.points_info.response_dir} | Overload: {frame_result.overload_text}")

            if self.image_log_options.include_screenshot:
                for r_name, r_img in all_rois.items():
                    try:
                        r_type = self.app_config.regions[r_name].roi_type
                        if r_type == 'frf':
                            cv2.imwrite(f"image_logs/ROIs/{base_filename}_{r_name}.jpg", r_img)
                    except KeyError:
                        pass
                    except Exception as e:
                        logger.error(f"Failed to save ROI image for '{r_name}': {e}")

            if self.image_log_options.include_color_filter: 
                cv2.imwrite(f"image_logs/ColorMasks/{base_filename}_mask.jpg", frf_result.color_mask)
            
            if self.image_log_options.include_signal_plot:
                self._create_signal_plot_safe(frf_result, region, title_info, 
                                             f"image_logs/Signals/{base_filename}_signal.png")
            
            if self.image_log_options.include_fft_plot:
                self._create_fft_plot_safe(frf_result, region, title_info, 
                                          f"image_logs/FFT/{base_filename}_fft.png")
            
            if self.image_log_options.include_lowpass_plot:
                self._create_lowpass_comparison_plot_safe(frf_result, region, title_info, 
                                                         f"image_logs/Lowpass/{base_filename}_lowpass.png")
            
            if self.image_log_options.include_residual_plot:
                self._create_residual_plot_safe(frf_result, region, title_info, 
                                               f"image_logs/Residual/{base_filename}_residual.png")
            
            if self.image_log_options.include_summary_chart and self.current_run in self.run_history:
                self._create_run_summary_chart_safe(self.run_history[self.current_run], 
                                                    f"image_logs/Summary/{base_filename}_summary.png")
            
            if self.image_log_options.include_ocr_images and frame_result.ocr_images:
                for ocr_name, ocr_img in frame_result.ocr_images.items():
                    if ocr_img is not None and ocr_img.size > 0:
                        cv2.imwrite(f"image_logs/OCRs/{base_filename}_{ocr_name}.jpg", ocr_img)
                
        except Exception as e:
            logger.error(f"Failed to create visual logs for {frf_name}: {e}")

    def _create_signal_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion, 
                                 title_info: str, filename: str):
        """Thread-safe signal plot creation using Figure directly."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            num_points = len(frf_result.signal_physical)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            
            ax.plot(freq_axis, frf_result.signal_physical, color='cyan', linewidth=1.5)
            ax.set_xlabel('Frequency (Hz)', color='white')
            ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
            ax.set_title(f'Reconstructed Signal\n{title_info}', color='white', fontsize=10)
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_fft_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion, 
                              title_info: str, filename: str):
        """Thread-safe FFT plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            ax.plot(frf_result.fft_freqs, frf_result.fft_mags, color='magenta', linewidth=1)
            ax.axvline(x=self.app_config.fft_cutoff_frequency, color='yellow', 
                      linestyle='--', linewidth=1, label=f'Cutoff: {self.app_config.fft_cutoff_frequency:.2f}')
            ax.set_xlim(0, 0.5)
            ax.set_xlabel('Normalized Frequency', color='white')
            ax.set_ylabel('Magnitude (A.U.)', color='white')
            
            fft_info = (f'Total E: {frf_result.total_energy:.2e} | '
                       f'HF E: {frf_result.high_freq_energy:.2e} | '
                       f'Ratio: {frf_result.energy_ratio:.3e}')
            classification = "HF (Bad)" if frf_result.is_high_frequency else "LF (Good)"
            ax.set_title(f'FFT Magnitude Spectrum - {classification}\n{title_info}\n{fft_info}', 
                        color='white', fontsize=9)
            
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.legend(facecolor='#2E2E2E', labelcolor='white')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_lowpass_comparison_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion,
                                             title_info: str, filename: str):
        """Thread-safe lowpass comparison plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            if frf_result.signal_physical is None or frf_result.filtered_physical is None:
                ax.text(0.5, 0.5, 'No lowpass data available', 
                       transform=ax.transAxes, ha='center', va='center', color='white', fontsize=14)
                fig.savefig(filename, facecolor=fig.get_facecolor())
                return
            
            num_points = len(frf_result.signal_physical)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            
            ax.plot(freq_axis, frf_result.signal_physical, 'w-', linewidth=2, label='Original', alpha=0.9)
            ax.plot(freq_axis, frf_result.filtered_physical, 'g--', linewidth=1.5, label='Lowpass Filtered')
            
            filter_info = f'Cutoff: {self.app_config.lowpass_cutoff:.3f} | Order: {self.app_config.lowpass_filter_order}'
            ax.set_title(f'Lowpass Filter Comparison\n{title_info}\n{filter_info}', 
                        color='white', fontsize=9)
            ax.set_xlabel('Frequency (Hz)', color='white')
            ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.legend(facecolor='#2E2E2E', labelcolor='white')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_residual_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion,
                                   title_info: str, filename: str):
        """Thread-safe residual plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            if frf_result.residual_physical is None:
                ax.text(0.5, 0.5, 'No residual data available', 
                       transform=ax.transAxes, ha='center', va='center', color='white', fontsize=14)
                fig.savefig(filename, facecolor=fig.get_facecolor())
                return
            
            num_points = len(frf_result.residual_physical)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            threshold = self.app_config.residual_threshold
            
            ax.plot(freq_axis, frf_result.residual_physical, 'c-', linewidth=1, label='Residual (HF Content)')
            ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
            ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.5, 
                      label=f'Threshold (±{threshold} {region.y_axis_unit})')
            ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.5)
            
            exceedances = np.abs(frf_result.residual_physical) > threshold
            if np.any(exceedances):
                ax.scatter(freq_axis[exceedances], frf_result.residual_physical[exceedances], 
                          c='red', s=15, zorder=5, alpha=0.7)
            
            classification = "BAD HIT" if frf_result.lowpass_is_bad_hit else "GOOD HIT"
            residual_info = (f'Exceedances: {frf_result.exceedance_count} ({frf_result.exceedance_ratio:.1%}) | '
                            f'Threshold: {self.app_config.exceedance_ratio_threshold:.1%} | {classification}')
            ax.set_title(f'Residual Analysis\n{title_info}\n{residual_info}', 
                        color='white', fontsize=9)
            ax.set_xlabel('Frequency (Hz)', color='white')
            ax.set_ylabel(f'Residual ({region.y_axis_unit})', color='white')
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.legend(facecolor='#2E2E2E', labelcolor='white', loc='upper right')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_run_summary_chart_safe(self, hit_data: Dict, filename: str):
        """Thread-safe run summary chart creation."""
        if not hit_data:
            return
        
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            import matplotlib.cm as cm
            
            fig = Figure(figsize=(12, 6), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            hits = list(hit_data.keys())
            values = [hit_data[h]['exceedance_count'] for h in hits]
            
            bars = ax.bar(range(len(hits)), values, edgecolor='white', linewidth=0.5)
            
            cmap = cm.jet
            vmin, vmax = min(values), max(values)
            if vmin == vmax:
                vmin, vmax = vmin - 1, vmax + 1
            
            for bar, val in zip(bars, values):
                normalized = (val - vmin) / (vmax - vmin) if vmax > vmin else 0.5
                bar.set_color(cmap(normalized))
            
            ax.set_xticks(range(len(hits)))
            ax.set_xticklabels(hits, rotation=45, ha='right', fontsize=8, color='white')
            ax.set_xlabel('Hit (Point Combination)', color='white')
            ax.set_ylabel('Exceedances', color='white')
            ax.set_title(f'Run Summary - Exceedance Counts\n{self.current_run}', color='white', fontsize=11)
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.grid(True, linestyle='--', alpha=0.3, axis='y')
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()
    
    # =========================================================================
    # DATA FILE LOGGING
    # =========================================================================
    

    def _save_unv_log(self, frf_result: FRFAnalysisResult, frame_result: FrameAnalysisResult, 
                      frf_name: str, base_filename: str):
        def parse_point(point_str: str) -> int: 
            return int(re.sub(r'\D', '', point_str)) if point_str and re.sub(r'\D', '', point_str) else 1
        
        def parse_dof(dir_str: str) -> int: 
            if not dir_str or len(dir_str) < 1:
                return 3
            last_char = dir_str.upper()[-1]
            return {'X': 1, 'Y': 2, 'Z': 3}.get(last_char, 3)
        
        try:
            filename = f"signal_logs/{base_filename}.unv"
            region = frame_result.active_regions[frf_name]
            points = frame_result.points_info
            num_points = len(frf_result.signal_physical)
            
            if num_points < 2:
                logger.warning(f"Skipping UNV log for {frf_name}: insufficient data points ({num_points})")
                return
            
            start_freq = region.x_axis_min
            freq_step = (region.x_axis_max - region.x_axis_min) / (num_points - 1) if num_points > 1 else 0
            
            resp_node = parse_point(points.response_point)
            resp_dof = parse_dof(points.response_dir)
            ref_node = parse_point(points.hammer_point)
            ref_dof = parse_dof(points.hammer_dir)
            timestamp = datetime.now().strftime('%d-%b-%y %H:%M:%S')
            
            with open(filename, 'w') as f:
                f.write(f"    -1{' ' * 74}\n")
                f.write(f"    58{' ' * 74}\n")
                
                id_line1 = f"FRF for {points.response_point}:{points.response_dir}/{points.hammer_point}:{points.hammer_dir}"
                f.write(f"{id_line1[:80]:<80}\n")
                
                id_line2 = "USMA v0.9 - Screen Reconstruction"
                f.write(f"{id_line2[:80]:<80}\n")
                
                f.write(f"{timestamp:<80}\n")
                
                id_line4 = f"Reconstructed from {points.run}, region \"{frf_name}\""
                f.write(f"{id_line4[:80]:<80}\n")
                
                dir_char = {1: 'X', 2: 'Y', 3: 'Z'}.get(resp_dof, 'Z')
                id_line5 = f"FRF\\\\{points.response_point}:+{dir_char}"
                f.write(f"{id_line5[:80]:<80}\n")
                
                func_type = 4
                func_id = 0
                version = 0
                load_case = 0
                resp_node_name = "C"
                ref_node_name = "C"
                
                dof_line = (
                    f"{func_type:5d}"
                    f"{func_id:10d}"
                    f"{version:5d}"
                    f"{load_case:10d}"
                    f"{resp_node_name:10s}"
                    f"{resp_node:10d}"
                    f"{resp_dof:5d}"
                    f"{ref_node_name:10s}"
                    f"{ref_node:10d}"
                    f"{ref_dof:5d}\n"
                )
                f.write(dof_line)
                
                data_type = 2
                spacing = 1
                z_value = 0.0
                
                f.write(
                    f"{data_type:10d}"
                    f"{num_points:10d}"
                    f"{spacing:10d}"
                    f"{start_freq:13.5E}"
                    f"{freq_step:13.5E}"
                    f"{z_value:13.5E}\n"
                )
                
                f.write(f"{18:10d}{0:5d}{0:5d}{0:5d}{'X-axis':20s}{'Hz':20s}\n")
                f.write(f"{12:10d}{0:5d}{0:5d}{0:5d}{'Y-axis':20s}{region.y_axis_unit:20s}\n")
                f.write(f"{13:10d}{0:5d}{0:5d}{0:5d}{'Z-axis':20s}{'NONE':20s}\n")
                f.write(f"{0:10d}{0:5d}{0:5d}{0:5d}{'NONE':20s}{'NONE':20s}\n")
                
                for val in frf_result.signal_physical:
                    real_part = val
                    imag_part = 0.0
                    f.write(f"  {real_part:13.6E}  {imag_part:13.6E}\n")
                
                f.write("    -1\n")
                
            logger.info(f"UNV file saved: {filename}")
            
        except Exception as e: 
            logger.error(f"Failed to save .unv file for {frf_name}: {e}")


# --- 4. VISUALIZATION & CONFIGURATION ---

# --- 4a. STARTUP DIALOG ---
class StartupDialog(tk.Toplevel):
    """
    Startup dialog for config selection or new calibration.

    Attributes:
        result: str or None - Selected config path, "NEW_CALIBRATION", or None if cancelled
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("USMA v0.9 - Startup")
        self.result = None

        # Don't use transient() with hidden parent - causes display issues on Windows
        # self.transient(parent)  # REMOVED

        # Scan for config files first to determine window size
        self.config_files = self._scan_configs()

        # Set window size based on content
        # Larger window when configs exist to show all buttons
        window_height = 320 if self.config_files else 250
        self.geometry(f"450x{window_height}")
        self.resizable(False, False)

        self._setup_ui()

        # Center dialog
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_width()) // 2
        y = (self.winfo_screenheight() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

        # Make modal - grab_set AFTER geometry is set
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Ensure window is visible before grabbing
        self.deiconify()
        self.lift()
        self.focus_force()
        self.grab_set()

        self.wait_window(self)

    def _scan_configs(self):
        """Scan configs directory for .json files."""
        config_dir = "configs"
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
            return []
        return [f for f in os.listdir(config_dir) if f.endswith('.json')]

    def _setup_ui(self):
        # Title
        title_frame = ttk.Frame(self)
        title_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
        ttk.Label(title_frame, text="USMA - Unified Screen Monitoring Application",
                  font=("Segoe UI", 11, "bold")).pack()
        ttk.Label(title_frame, text="v0.9 - Calibration Phase Release",
                  font=("Segoe UI", 9)).pack()

        separator = ttk.Separator(self, orient=tk.HORIZONTAL)
        separator.pack(fill=tk.X, padx=20, pady=10)

        if self.config_files:
            # Show config selection
            select_frame = ttk.LabelFrame(self, text="Load Existing Configuration")
            select_frame.pack(fill=tk.X, padx=20, pady=5)

            combo_frame = ttk.Frame(select_frame)
            combo_frame.pack(fill=tk.X, padx=10, pady=10)

            ttk.Label(combo_frame, text="Select Config:").pack(side=tk.LEFT, padx=(0, 5))
            self.config_var = tk.StringVar(value=self.config_files[0])
            config_combo = ttk.Combobox(combo_frame, textvariable=self.config_var,
                                        values=self.config_files, state='readonly', width=30)
            config_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

            ttk.Button(combo_frame, text="Load", command=self._on_load, width=10).pack(side=tk.LEFT)

            # "Or" label
            ttk.Label(self, text="— Or —", font=("Segoe UI", 9)).pack(pady=5)
        else:
            # No configs found
            info_frame = ttk.Frame(self)
            info_frame.pack(fill=tk.X, padx=20, pady=10)
            ttk.Label(info_frame, text="No configuration files found.",
                      font=("Segoe UI", 10)).pack()
            ttk.Label(info_frame, text="Let's create your first calibration!",
                      font=("Segoe UI", 9)).pack()

        # Create new calibration button
        new_cal_btn = ttk.Button(self, text="Create New Calibration",
                                 command=self._on_new_calibration, width=30)
        new_cal_btn.pack(pady=(5, 20))

        # Cancel button at bottom
        if self.config_files:
            ttk.Button(self, text="Cancel", command=self._on_cancel, width=10).pack(pady=(0, 10))

    def _on_load(self):
        """Set result to selected config path."""
        selected = self.config_var.get()
        if selected:
            self.result = os.path.join("configs", selected)
            self.destroy()

    def _on_new_calibration(self):
        """Set result to trigger new calibration."""
        self.result = "NEW_CALIBRATION"
        self.destroy()

    def _on_cancel(self):
        """User closed dialog without selection."""
        self.result = None
        self.destroy()


# --- 4a2. CALIBRATION CHOICE DIALOG (Phase 4 / v0.9) ---
class CalibrationChoiceDialog(tk.Toplevel):
    """
    Prompts user to either use default parameters or enter calibration mode.
    Shown after config selection/creation.
    """
    def __init__(self, parent, config_name: str):
        super().__init__(parent)
        self.title("Parameter Selection")
        self.result = None

        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        main_frame = ttk.Frame(self, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Parameter Selection",
                  font=("Segoe UI", 12, "bold")).pack(pady=(0, 5))
        ttk.Label(main_frame, text=f"Configuration: {config_name}",
                  font=("Segoe UI", 9)).pack(pady=(0, 15))

        ttk.Label(main_frame, text="How would you like to set analysis parameters?",
                  wraplength=350).pack(pady=(0, 15))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)

        default_frame = ttk.Frame(btn_frame)
        default_frame.pack(fill=tk.X, pady=5)
        default_btn = ttk.Button(default_frame, text="Use Default Parameters",
                                 command=self._on_default)
        default_btn.pack(fill=tk.X, ipady=8)
        ttk.Label(default_frame, text="Use the current hardcoded/saved parameter values",
                  font=("Segoe UI", 8), foreground="gray").pack()

        cal_frame = ttk.Frame(btn_frame)
        cal_frame.pack(fill=tk.X, pady=5)
        cal_btn = ttk.Button(cal_frame, text="Calibrate with Expert Feedback",
                             command=self._on_calibrate)
        cal_btn.pack(fill=tk.X, ipady=8)
        ttk.Label(cal_frame, text="Run hits and classify Good/Bad to optimize parameters",
                  font=("Segoe UI", 8), foreground="gray").pack()

        ttk.Button(main_frame, text="Cancel", command=self._on_cancel).pack(pady=(15, 0))

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0,x)}+{max(0,y)}")
        self.wait_window()

    def _on_default(self):
        self.result = "DEFAULT"
        self.destroy()

    def _on_calibrate(self):
        self.result = "CALIBRATE"
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


# --- 4a3. LIVE CALIBRATION SAVE DIALOG (Phase 4 / v0.9) ---
class LiveCalibrationSaveDialog(tk.Toplevel):
    """
    Shown after stopping monitoring if live calibration signals were collected.
    Lets user save or discard live calibration data.
    """
    def __init__(self, parent, live_signal_count: int,
                 good_count: int, bad_count: int,
                 old_params: dict, new_params: dict,
                 old_level: int, new_level: int):
        super().__init__(parent)
        self.title("Update Calibration?")
        self.result = None

        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        main_frame = ttk.Frame(self, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Live Calibration Signals Collected",
                  font=("Segoe UI", 11, "bold")).pack(pady=(0, 10))

        summary = (f"During this monitoring session, you classified "
                   f"{live_signal_count} additional signals "
                   f"({good_count} Good, {bad_count} Bad).")
        ttk.Label(main_frame, text=summary, wraplength=400).pack(pady=(0, 10))

        level_names = {0: "Not Calibrated", 1: "Preliminary", 2: "Basic",
                       3: "Solid", 4: "Robust"}
        level_text = f"Confidence: Level {old_level} ({level_names.get(old_level, '?')})"
        if new_level != old_level:
            level_text += f" -> Level {new_level} ({level_names.get(new_level, '?')})"
        ttk.Label(main_frame, text=level_text, font=("Segoe UI", 9, "bold")).pack(pady=(0, 5))

        # Parameter comparison table
        if old_params and new_params:
            table_frame = ttk.LabelFrame(main_frame, text="Parameter Changes")
            table_frame.pack(fill=tk.X, pady=5)
            param_display = [
                ('fft_energy_ratio_threshold', 'FFT E.Ratio'),
                ('exceedance_ratio_threshold', 'Exc.Ratio'),
                ('residual_threshold', 'Res.Thr'),
                ('fft_cutoff_frequency', 'FFT Cutoff'),
            ]
            for key, label in param_display:
                old_v = old_params.get(key, '?')
                new_v = new_params.get(key, '?')
                if isinstance(old_v, float) and isinstance(new_v, float):
                    row = ttk.Frame(table_frame)
                    row.pack(fill=tk.X, padx=5, pady=1)
                    ttk.Label(row, text=f"{label}:", width=14).pack(side=tk.LEFT)
                    ttk.Label(row, text=f"{old_v:.4f} -> {new_v:.4f}").pack(side=tk.LEFT)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(15, 0))
        ttk.Button(btn_frame, text="Save & Update Calibration",
                   command=self._on_save).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(btn_frame, text="Discard",
                   command=self._on_discard).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(btn_frame, text="Cancel",
                   command=self._on_cancel).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0,x)}+{max(0,y)}")
        self.wait_window()

    def _on_save(self):
        self.result = "SAVE"
        self.destroy()

    def _on_discard(self):
        self.result = "DISCARD"
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


# --- 4b. HSV CALIBRATION WINDOW ---
class HSVCalibrationWindow(tk.Toplevel):
    """
    HSV color filter calibration with live preview.
    
    Features:
    - Vertical stacking of preview images (Original, Mask, Filtered)
    - Sliders AND manual text entry for HSV min/max values
    - Mouse wheel zoom on preview canvas
    - Zoom slider for precise control

    Args:
        parent: Parent window
        screenshot: Full screenshot numpy array (BGR)
        wave_regions: Dict of wave MonitoringRegion objects
        current_hsv_lower: Current [H, S, V] lower bounds
        current_hsv_upper: Current [H, S, V] upper bounds
    """

    def __init__(self, parent, screenshot, wave_regions, current_hsv_lower, current_hsv_upper):
        super().__init__(parent)
        self.title("HSV Color Filter Calibration")
        self.screenshot = screenshot
        self.wave_regions = wave_regions
        self.result_hsv_lower = None
        self.result_hsv_upper = None

        # Current values (copy to avoid modifying original until Apply)
        self.hsv_lower = list(current_hsv_lower)
        self.hsv_upper = list(current_hsv_upper)

        # Selected region for preview
        self.selected_region_name = list(wave_regions.keys())[0] if wave_regions else None
        
        # Zoom level (1.0 = fit to canvas, >1.0 = zoomed in)
        self.zoom_level = tk.DoubleVar(value=1.0)
        self.pan_x = 0  # Pan offset for zoomed view
        self.pan_y = 0
        self._drag_start = None

        self.geometry("700x900")  # Taller for vertical layout
        self._setup_ui()

        self.transient(parent)
        self.grab_set()

        # Initial preview update (delayed to ensure canvas is ready)
        self.after(100, self._update_preview)

    def _setup_ui(self):
        # Top: Region selector (if multiple wave regions)
        if len(self.wave_regions) > 1:
            selector_frame = ttk.Frame(self)
            selector_frame.pack(fill=tk.X, padx=10, pady=5)
            ttk.Label(selector_frame, text="Preview Region:").pack(side=tk.LEFT)
            self.region_var = tk.StringVar(value=self.selected_region_name)
            region_combo = ttk.Combobox(selector_frame, textvariable=self.region_var,
                                        values=list(self.wave_regions.keys()), state='readonly', width=20)
            region_combo.pack(side=tk.LEFT, padx=5)
            region_combo.bind("<<ComboboxSelected>>", self._on_region_changed)

        # Preview frame with vertical stacking
        preview_frame = ttk.LabelFrame(self, text="Preview (Vertical: Original → Mask → Filtered)")
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Zoom controls
        zoom_frame = ttk.Frame(preview_frame)
        zoom_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(zoom_frame, text="Zoom:").pack(side=tk.LEFT)
        zoom_slider = ttk.Scale(zoom_frame, from_=0.5, to=4.0, variable=self.zoom_level,
                                orient=tk.HORIZONTAL, length=150, command=lambda _: self._update_preview())
        zoom_slider.pack(side=tk.LEFT, padx=5)
        self.zoom_label = ttk.Label(zoom_frame, text="100%")
        self.zoom_label.pack(side=tk.LEFT)
        ttk.Button(zoom_frame, text="Fit", width=4, command=self._reset_zoom).pack(side=tk.LEFT, padx=5)
        ttk.Label(zoom_frame, text="(Mouse wheel to zoom, drag to pan)", font=("Segoe UI", 8)).pack(side=tk.RIGHT)

        self.preview_canvas = tk.Canvas(preview_frame, bg='black')
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Bind mouse events for zoom and pan
        self.preview_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.preview_canvas.bind("<Button-1>", self._on_drag_start)
        self.preview_canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_drag_end)

        # Sliders frame with manual entry
        sliders_frame = ttk.LabelFrame(self, text="HSV Ranges (Hue: 0-179, Saturation/Value: 0-255)")
        sliders_frame.pack(fill=tk.X, padx=10, pady=5)

        # Create slider variables
        self.h_min_var = tk.IntVar(value=self.hsv_lower[0])
        self.h_max_var = tk.IntVar(value=self.hsv_upper[0])
        self.s_min_var = tk.IntVar(value=self.hsv_lower[1])
        self.s_max_var = tk.IntVar(value=self.hsv_upper[1])
        self.v_min_var = tk.IntVar(value=self.hsv_lower[2])
        self.v_max_var = tk.IntVar(value=self.hsv_upper[2])

        # Layout sliders with entry boxes in grid
        self._create_slider_row(sliders_frame, 0, "Hue", self.h_min_var, self.h_max_var, 0, 179)
        self._create_slider_row(sliders_frame, 1, "Saturation", self.s_min_var, self.s_max_var, 0, 255)
        self._create_slider_row(sliders_frame, 2, "Value", self.v_min_var, self.v_max_var, 0, 255)

        # Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(button_frame, text="Reset to Default", command=self._on_reset).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Apply", command=self._on_apply).pack(side=tk.RIGHT, padx=5)

    def _create_slider_row(self, parent, row, label, min_var, max_var, range_min, range_max):
        """Create a row with label, min slider + entry, max slider + entry."""
        ttk.Label(parent, text=f"{label}:", width=10).grid(row=row, column=0, padx=5, pady=5, sticky=tk.W)

        # Min controls
        ttk.Label(parent, text="Min:").grid(row=row, column=1, padx=2)
        min_slider = ttk.Scale(parent, from_=range_min, to=range_max, variable=min_var,
                               orient=tk.HORIZONTAL, length=150, command=lambda _: self._on_slider_changed())
        min_slider.grid(row=row, column=2, padx=2)
        
        # Min entry box
        min_entry = ttk.Entry(parent, textvariable=min_var, width=5)
        min_entry.grid(row=row, column=3, padx=2)
        min_entry.bind('<Return>', lambda e: self._on_entry_changed(min_var, range_min, range_max))
        min_entry.bind('<FocusOut>', lambda e: self._on_entry_changed(min_var, range_min, range_max))

        # Max controls
        ttk.Label(parent, text="Max:").grid(row=row, column=4, padx=(10, 2))
        max_slider = ttk.Scale(parent, from_=range_min, to=range_max, variable=max_var,
                               orient=tk.HORIZONTAL, length=150, command=lambda _: self._on_slider_changed())
        max_slider.grid(row=row, column=5, padx=2)
        
        # Max entry box
        max_entry = ttk.Entry(parent, textvariable=max_var, width=5)
        max_entry.grid(row=row, column=6, padx=2)
        max_entry.bind('<Return>', lambda e: self._on_entry_changed(max_var, range_min, range_max))
        max_entry.bind('<FocusOut>', lambda e: self._on_entry_changed(max_var, range_min, range_max))
    
    def _on_entry_changed(self, var, range_min, range_max):
        """Validate and apply manual entry value."""
        try:
            val = int(var.get())
            val = max(range_min, min(range_max, val))  # Clamp to valid range
            var.set(val)
        except (ValueError, tk.TclError):
            pass  # Invalid input, keep current value
        self._on_slider_changed()

    def _on_slider_changed(self):
        """Update preview when any slider changes."""
        self.hsv_lower = [self.h_min_var.get(), self.s_min_var.get(), self.v_min_var.get()]
        self.hsv_upper = [self.h_max_var.get(), self.s_max_var.get(), self.v_max_var.get()]
        self._update_preview()
    
    def _reset_zoom(self):
        """Reset zoom to fit."""
        self.zoom_level.set(1.0)
        self.pan_x = 0
        self.pan_y = 0
        self._update_preview()
    
    def _on_mousewheel(self, event):
        """Handle mouse wheel for zooming."""
        # Get current zoom
        current = self.zoom_level.get()
        
        # Zoom in/out by 10%
        if event.delta > 0:
            new_zoom = min(4.0, current * 1.1)
        else:
            new_zoom = max(0.5, current / 1.1)
        
        self.zoom_level.set(new_zoom)
        self._update_preview()
    
    def _on_drag_start(self, event):
        """Start pan drag."""
        self._drag_start = (event.x, event.y)
    
    def _on_drag_motion(self, event):
        """Handle pan drag motion."""
        if self._drag_start and self.zoom_level.get() > 1.0:
            dx = event.x - self._drag_start[0]
            dy = event.y - self._drag_start[1]
            self.pan_x += dx
            self.pan_y += dy
            self._drag_start = (event.x, event.y)
            self._update_preview()
    
    def _on_drag_end(self, event):
        """End pan drag."""
        self._drag_start = None

    def _update_preview(self):
        """Update the preview canvas with current HSV filter applied (vertical stack)."""
        if not self.selected_region_name or self.screenshot is None:
            return

        region = self.wave_regions[self.selected_region_name]
        roi = self.screenshot[region.y:region.y+region.height, region.x:region.x+region.width]

        if roi.size == 0:
            return

        # Apply HSV filter
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(self.hsv_lower), np.array(self.hsv_upper))

        # Create visualization images
        roi_rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
        mask_rgb = cv2.cvtColor(mask, cv2.COLOR_GRAY2RGB)
        filtered = cv2.bitwise_and(roi_rgb, roi_rgb, mask=mask)
        
        # Add labels to each image
        label_height = 25
        img_h, img_w = roi_rgb.shape[:2]
        
        def add_label(img, text):
            """Add a label bar above the image."""
            label_bar = np.zeros((label_height, img_w, 3), dtype=np.uint8)
            label_bar[:] = (40, 40, 40)  # Dark gray background
            cv2.putText(label_bar, text, (10, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            return np.vstack([label_bar, img])
        
        roi_labeled = add_label(roi_rgb, "Original")
        mask_labeled = add_label(mask_rgb, "Mask")
        filtered_labeled = add_label(filtered, "Filtered")

        # Combine vertically
        combined = np.vstack([roi_labeled, mask_labeled, filtered_labeled])

        # Get canvas dimensions
        canvas_w = self.preview_canvas.winfo_width()
        canvas_h = self.preview_canvas.winfo_height()
        if canvas_w < 10 or canvas_h < 10:
            canvas_w, canvas_h = 660, 500

        img_h, img_w = combined.shape[:2]
        
        # Calculate base scale to fit canvas
        base_scale = min(canvas_w / img_w, canvas_h / img_h, 1.0)
        
        # Apply zoom
        zoom = self.zoom_level.get()
        scale = base_scale * zoom
        
        # Update zoom label
        self.zoom_label.config(text=f"{int(zoom * 100)}%")
        
        new_w, new_h = int(img_w * scale), int(img_h * scale)

        if new_w > 0 and new_h > 0:
            resized = cv2.resize(combined, (new_w, new_h), interpolation=cv2.INTER_LINEAR if zoom > 1 else cv2.INTER_AREA)
            self.preview_photo = ImageTk.PhotoImage(image=Image.fromarray(resized))

            self.preview_canvas.delete("all")
            
            # Calculate position with pan offset
            x_offset = (canvas_w - new_w) // 2 + self.pan_x
            y_offset = (canvas_h - new_h) // 2 + self.pan_y
            
            self.preview_canvas.create_image(x_offset, y_offset, image=self.preview_photo, anchor=tk.NW)

    def _on_region_changed(self, event=None):
        self.selected_region_name = self.region_var.get()
        self.pan_x = 0
        self.pan_y = 0
        self._update_preview()

    def _on_apply(self):
        self.result_hsv_lower = self.hsv_lower.copy()
        self.result_hsv_upper = self.hsv_upper.copy()
        self.destroy()

    def _on_cancel(self):
        self.destroy()

    def _on_reset(self):
        """Reset to default HSV values."""
        self.h_min_var.set(0)
        self.h_max_var.set(179)
        self.s_min_var.set(0)
        self.s_max_var.set(255)
        self.v_min_var.set(0)
        self.v_max_var.set(240)
        self._on_slider_changed()


# --- 4c. ROI TYPE SELECTION DIALOG ---
class ROITypeDialog(tk.Toplevel):
    """
    Simple dialog to select ROI type after drawing a region.
    """

    ROI_TYPES = ['frf', 'psd', 'coherence', 'averages', 'status', 'overload', 'run', 'hammer', 'response']

    def __init__(self, parent, region_name: str):
        super().__init__(parent)
        self.title("Select Region Type")
        self.result = None
        self.region_name = region_name

        self.geometry("350x280")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._setup_ui()

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Center on parent
        self.update_idletasks()
        if parent.winfo_exists():
            x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
            self.geometry(f"+{x}+{y}")

    def _setup_ui(self):
        ttk.Label(self, text=f"Select type for region '{self.region_name}':",
                  font=("Segoe UI", 10, "bold")).pack(pady=(20, 15))

        self.type_var = tk.StringVar(value='frf')

        type_frame = ttk.Frame(self)
        type_frame.pack(pady=10)

        for i, roi_type in enumerate(self.ROI_TYPES):
            ttk.Radiobutton(type_frame, text=roi_type.capitalize(),
                           variable=self.type_var, value=roi_type).grid(
                               row=i//3, column=i%3, padx=15, pady=5, sticky=tk.W)

        button_frame = ttk.Frame(self)
        button_frame.pack(pady=20)

        ttk.Button(button_frame, text="OK", command=self._on_ok, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self._on_cancel, width=12).pack(side=tk.LEFT, padx=5)

    def _on_ok(self):
        self.result = self.type_var.get()
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


class RegionOverlay(tk.Toplevel):
    def __init__(self, parent, config_path):
        super().__init__(parent)
        self.config_path = config_path
        self.attributes("-transparentcolor", "white", "-topmost", True)
        self.overrideredirect(True)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)
            canvas = tk.Canvas(self, bg="white", highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)
            colors = {"frf": "#3498db", "psd": "#9b59b6", "coherence": "#1abc9c",
                      "averages": "#95a5a6", "status": "#2ecc71", "overload": "#e74c3c",
                      "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
            for name, region_data in data.items():
                if not name.startswith('_') and region_data.get('enabled', True):
                    x, y, w, h = region_data['x'], region_data['y'], region_data['width'], region_data['height']
                    # Use custom overlay_color if set, otherwise type-based default
                    color = region_data.get('overlay_color') or colors.get(region_data.get('roi_type', 'frf'), "#95a5a6")
                    canvas.create_rectangle(x-5, y-5, x+w+5, y+h+5, outline=color, width=2)
                    canvas.create_text(x-5, y-5, text=name, anchor="sw", font=("Arial", 10, "bold"), fill=color)
            canvas.create_text(self.winfo_screenwidth()-10, self.winfo_screenheight()-10, 
                              text=f"Config: {os.path.basename(self.config_path)}", anchor="se", fill="#333")
        except Exception as e:
            logger.error(f"Overlay Error: {e}")
            self.destroy()


class ConfigToolWindow(tk.Toplevel):
    """Advanced Region & Color Configuration Tool with scrollable right panel."""

    def __init__(self, parent, main_root, is_new_calibration=False, preload_config_path=None):
        super().__init__(parent)
        self.title("Advanced Region & Color Configuration Tool")
        self.main_root = main_root
        self.is_new_calibration = is_new_calibration
        self.saved_config_path = None  # Track if/where config was saved
        self.current_config_path = None

        # Initialize app_config - load from preload_config_path if provided
        if preload_config_path and os.path.exists(preload_config_path):
            # Load existing config
            self.app_config = ScreenMonitor(preload_config_path).app_config
            self.current_config_path = preload_config_path
        else:
            self.app_config = AppConfig()

        self.screenshot = None
        self.photo = None
        self.scale = 1.0
        self.drawing = False
        self.start_x = 0
        self.start_y = 0
        self.selected_region_name = None
        self.resize_timer = None
        self.x_offset = 0
        self.y_offset = 0
        self.state('zoomed')
        self._setup_gui()
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.after(200, self._take_screenshot)

        # If we preloaded a config, update UI after screenshot is taken
        if self.current_config_path:
            self.after(400, self._update_ui_from_data)

    def _on_closing(self):
        self.main_root.deiconify()
        self.destroy()

    def _setup_gui(self):
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(toolbar, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Load Config", command=self._load_config).pack(side=tk.LEFT, padx=2)
        
        main_frame = ttk.Frame(self, padding=5)
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        canvas_frame = ttk.LabelFrame(main_frame, text="Screenshot Preview")
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.canvas = tk.Canvas(canvas_frame, bg="black")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        right_outer_frame = ttk.Frame(main_frame, width=550)
        right_outer_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        right_outer_frame.pack_propagate(False)
        right_outer_frame.grid_propagate(False)
        
        self.right_canvas = tk.Canvas(right_outer_frame, highlightthickness=0, width=380)
        v_scrollbar = ttk.Scrollbar(right_outer_frame, orient=tk.VERTICAL, command=self.right_canvas.yview)
        h_scrollbar = ttk.Scrollbar(right_outer_frame, orient=tk.HORIZONTAL, command=self.right_canvas.xview)
        self.right_scrollable_frame = ttk.Frame(self.right_canvas)
        
        self.right_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.right_canvas.configure(scrollregion=self.right_canvas.bbox("all"))
        )
        
        self.right_canvas.create_window((0, 0), window=self.right_scrollable_frame, anchor="nw")
        self.right_canvas.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        self.right_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.right_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self._build_right_panel_content(self.right_scrollable_frame)
        
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<B1-Motion>", self._update_selection)
        self.canvas.bind("<ButtonRelease-1>", self._end_selection)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
    
    def _on_mousewheel(self, event):
        self.right_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    
    def _build_right_panel_content(self, parent):
        capture_frame = ttk.LabelFrame(parent, text="Capture")
        capture_frame.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(capture_frame, text="Take Screenshot", command=self._take_screenshot).pack(pady=5, padx=5, fill=tk.X)

        # HSV Calibration button
        self.hsv_cal_btn = ttk.Button(capture_frame, text="Calibrate Color Filter",
                                       command=self._open_hsv_calibration)
        self.hsv_cal_btn.pack(fill=tk.X, pady=(0, 5), padx=5)
        self.hsv_cal_btn.config(state=tk.DISABLED)  # Disabled by default

        list_frame = ttk.LabelFrame(parent, text="Defined Regions")
        list_frame.pack(fill=tk.X, pady=5, padx=5)
        
        list_inner = ttk.Frame(list_frame)
        list_inner.pack(fill=tk.X, padx=5, pady=5)
        
        self.region_listbox = tk.Listbox(list_inner, height=5, exportselection=False)
        self.region_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll = ttk.Scrollbar(list_inner, orient=tk.VERTICAL, command=self.region_listbox.yview)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.region_listbox.config(yscrollcommand=list_scroll.set)
        self.region_listbox.bind("<<ListboxSelect>>", self._on_listbox_select)
        
        editor_frame = ttk.LabelFrame(parent, text="Region Editor")
        editor_frame.pack(fill=tk.X, pady=5, padx=5)
        
        self.editor_vars = {
            'name': tk.StringVar(), 'x': tk.IntVar(), 'y': tk.IntVar(),
            'width': tk.IntVar(), 'height': tk.IntVar(), 'roi_type': tk.StringVar(),
            'enabled': tk.BooleanVar(), 'x_axis_min': tk.DoubleVar(), 'x_axis_max': tk.DoubleVar(),
            'y_axis_min': tk.DoubleVar(), 'y_axis_max': tk.DoubleVar(), 'y_axis_unit': tk.StringVar(),
            'resp_node': tk.IntVar(), 'resp_dof': tk.IntVar(), 'ref_node': tk.IntVar(), 'ref_dof': tk.IntVar(),
            'overlay_color': tk.StringVar(value='')
        }
        # Trace ROI type changes to update axis scaling visibility
        self.editor_vars['roi_type'].trace_add('write', lambda *_: self._update_axis_scaling_visibility())
        
        f1 = ttk.Frame(editor_frame)
        f1.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f1, text="Name:").pack(side=tk.LEFT)
        ttk.Entry(f1, textvariable=self.editor_vars['name'], width=15).pack(side=tk.LEFT, padx=2)
        ttk.Label(f1, text="Type:").pack(side=tk.LEFT, padx=(10,0))
        ttk.Combobox(f1, textvariable=self.editor_vars['roi_type'],
                     values=['frf', 'psd', 'coherence', 'averages', 'status', 'overload', 'run', 'hammer', 'response'],
                     state='readonly', width=10).pack(side=tk.LEFT, padx=2)

        # Color picker row
        f_color = ttk.Frame(editor_frame)
        f_color.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_color, text="Overlay Color:").pack(side=tk.LEFT)
        self.color_preview_btn = ttk.Button(f_color, text="  Pick Color  ", command=self._pick_overlay_color)
        self.color_preview_btn.pack(side=tk.LEFT, padx=4)
        self.color_preview_label = ttk.Label(f_color, text="(default)")
        self.color_preview_label.pack(side=tk.LEFT)
        ttk.Button(f_color, text="Reset", command=self._reset_overlay_color, width=6).pack(side=tk.LEFT, padx=2)
        
        f_geom = ttk.Frame(editor_frame)
        f_geom.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_geom, text="x:").pack(side=tk.LEFT)
        ttk.Entry(f_geom, textvariable=self.editor_vars['x'], width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(f_geom, text="y:").pack(side=tk.LEFT)
        ttk.Entry(f_geom, textvariable=self.editor_vars['y'], width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(f_geom, text="w:").pack(side=tk.LEFT)
        ttk.Entry(f_geom, textvariable=self.editor_vars['width'], width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(f_geom, text="h:").pack(side=tk.LEFT)
        ttk.Entry(f_geom, textvariable=self.editor_vars['height'], width=5).pack(side=tk.LEFT, padx=2)

        self.f_scale = ttk.LabelFrame(editor_frame, text="Physical Axis Scaling (wave)")
        self.f_scale.pack(fill=tk.X, pady=5, padx=5)

        g = ttk.Frame(self.f_scale)
        g.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(g, text="X-Min:").pack(side=tk.LEFT)
        ttk.Entry(g, textvariable=self.editor_vars['x_axis_min'], width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(g, text="X-Max:").pack(side=tk.LEFT)
        ttk.Entry(g, textvariable=self.editor_vars['x_axis_max'], width=8).pack(side=tk.LEFT, padx=2)

        g2 = ttk.Frame(self.f_scale)
        g2.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(g2, text="Y-Min:").pack(side=tk.LEFT)
        ttk.Entry(g2, textvariable=self.editor_vars['y_axis_min'], width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(g2, text="Y-Max:").pack(side=tk.LEFT)
        ttk.Entry(g2, textvariable=self.editor_vars['y_axis_max'], width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(g2, text="Unit:").pack(side=tk.LEFT, padx=(5,0))
        ttk.Entry(g2, textvariable=self.editor_vars['y_axis_unit'], width=6).pack(side=tk.LEFT, padx=2)

        # Unit hint label (shown below axis scaling, changes per ROI type)
        self.axis_unit_hint_label = ttk.Label(self.f_scale, text="", foreground="gray", font=("Segoe UI", 8, "italic"))
        self.axis_unit_hint_label.pack(anchor=tk.W, padx=5, pady=(0,2))

        f_buttons = ttk.Frame(editor_frame)
        f_buttons.pack(fill=tk.X, pady=5, padx=5)
        ttk.Checkbutton(f_buttons, text="Enabled", variable=self.editor_vars['enabled']).pack(side=tk.LEFT)
        ttk.Button(f_buttons, text="Update", command=self._update_region_from_editor).pack(side=tk.LEFT, padx=5)
        ttk.Button(f_buttons, text="Delete", command=self._delete_selected_region).pack(side=tk.LEFT)
        
        params_frame = ttk.LabelFrame(parent, text="Analysis Parameters")
        params_frame.pack(fill=tk.X, pady=5, padx=5)
        
        self.param_vars = {
            'fft_cutoff_frequency': tk.DoubleVar(value=self.app_config.fft_cutoff_frequency),
            'fft_energy_ratio_threshold': tk.DoubleVar(value=self.app_config.fft_energy_ratio_threshold),
            'lowpass_cutoff': tk.DoubleVar(value=self.app_config.lowpass_cutoff),
            'lowpass_filter_order': tk.IntVar(value=self.app_config.lowpass_filter_order),
            'residual_threshold': tk.DoubleVar(value=self.app_config.residual_threshold),
            'exceedance_ratio_threshold': tk.DoubleVar(value=self.app_config.exceedance_ratio_threshold)
        }
        
        g_fft = ttk.LabelFrame(params_frame, text="FFT Method")
        g_fft.pack(fill=tk.X, pady=2, padx=5)
        
        fft_row = ttk.Frame(g_fft)
        fft_row.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(fft_row, text="Cutoff:").pack(side=tk.LEFT)
        ttk.Spinbox(fft_row, from_=0.0, to=0.5, increment=0.01, 
                   textvariable=self.param_vars['fft_cutoff_frequency'], width=7).pack(side=tk.LEFT, padx=2)
        ttk.Label(fft_row, text="E.Ratio:").pack(side=tk.LEFT)
        ttk.Spinbox(fft_row, from_=0.0, to=1.0, increment=0.001, 
                   textvariable=self.param_vars['fft_energy_ratio_threshold'], width=7).pack(side=tk.LEFT, padx=2)
        
        g_lp = ttk.LabelFrame(params_frame, text="Lowpass Residual Method")
        g_lp.pack(fill=tk.X, pady=2, padx=5)
        
        lp_row1 = ttk.Frame(g_lp)
        lp_row1.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(lp_row1, text="Cutoff:").pack(side=tk.LEFT)
        ttk.Spinbox(lp_row1, from_=0.01, to=0.5, increment=0.01, 
                   textvariable=self.param_vars['lowpass_cutoff'], width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(lp_row1, text="Order:").pack(side=tk.LEFT)
        ttk.Spinbox(lp_row1, from_=1, to=10, increment=1, 
                   textvariable=self.param_vars['lowpass_filter_order'], width=4).pack(side=tk.LEFT, padx=2)
        
        lp_row2 = ttk.Frame(g_lp)
        lp_row2.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(lp_row2, text="Res.Thr:").pack(side=tk.LEFT)
        ttk.Spinbox(lp_row2, from_=0.0001, to=0.1, increment=0.0005, 
                   textvariable=self.param_vars['residual_threshold'], width=8, format="%.4f").pack(side=tk.LEFT, padx=2)
        ttk.Label(lp_row2, text="Exc.Ratio:").pack(side=tk.LEFT)
        ttk.Spinbox(lp_row2, from_=0.01, to=0.99, increment=0.01, 
                   textvariable=self.param_vars['exceedance_ratio_threshold'], width=6).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(params_frame, text="Apply Parameters", command=self._apply_params).pack(fill=tk.X, pady=5, padx=5)

    def _on_canvas_resize(self, event):
        if self.resize_timer:
            self.after_cancel(self.resize_timer)
        self.resize_timer = self.after(150, self._redraw_canvas_content)

    def _take_screenshot(self):
        self.withdraw()
        self.main_root.iconify()
        time.sleep(0.5)
        self.screenshot = cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2BGR)
        self.deiconify()
        self.lift()
        self.focus_force()
        self._redraw_canvas_content()

    def _redraw_canvas_content(self):
        if self.screenshot is None:
            return
        canvas_w, canvas_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if canvas_w < 2 or canvas_h < 2:
            return
        self.canvas.delete("all")
        img_h, img_w = self.screenshot.shape[:2]
        self.scale = min(canvas_w / img_w, canvas_h / img_h)
        disp_w, disp_h = int(img_w * self.scale), int(img_h * self.scale)
        img_resized = Image.fromarray(cv2.cvtColor(self.screenshot, cv2.COLOR_BGR2RGB)).resize((disp_w, disp_h), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image=img_resized)
        self.x_offset, self.y_offset = (canvas_w - disp_w) // 2, (canvas_h - disp_h) // 2
        self.canvas.create_image(self.x_offset, self.y_offset, image=self.photo, anchor=tk.NW, tags="screenshot")
        self._redraw_regions_on_canvas()

    def _on_canvas_click(self, event):
        self.drawing = True
        self.start_x = event.x
        self.start_y = event.y
        self.canvas.delete("selection_rect")

    def _update_selection(self, event):
        if self.drawing:
            self.canvas.delete("selection_rect")
            self.canvas.create_rectangle(self.start_x, self.start_y, event.x, event.y, outline="red", width=2, tags="selection_rect")

    def _end_selection(self, event):
        if not self.drawing:
            return
        self.drawing = False
        x1_c, y1_c = min(self.start_x, event.x), min(self.start_y, event.y)
        x2_c, y2_c = max(self.start_x, event.x), max(self.start_y, event.y)

        # Check minimum size
        if abs(x2_c - x1_c) < 10 or abs(y2_c - y1_c) < 10:
            self.canvas.delete("selection_rect")
            return

        x1 = int((x1_c - self.x_offset) / self.scale)
        y1 = int((y1_c - self.y_offset) / self.scale)
        x2 = int((x2_c - self.x_offset) / self.scale)
        y2 = int((y2_c - self.y_offset) / self.scale)

        name = f"region_{len(self.app_config.regions)+1}"

        # Show type selection dialog
        type_dialog = ROITypeDialog(self, name)
        self.wait_window(type_dialog)

        self.canvas.delete("selection_rect")

        if type_dialog.result is None:
            # User cancelled - don't create region
            return

        new_region = MonitoringRegion(
            name=name, x=x1, y=y1, width=x2-x1, height=y2-y1,
            roi_type=type_dialog.result
        )
        self.app_config.regions[name] = new_region

        self._update_ui_from_data()
        self._update_hsv_button_state()  # Update HSV button state

        # Select new region in listbox
        new_idx = sorted(self.app_config.regions.keys()).index(name)
        self.region_listbox.selection_clear(0, tk.END)
        self.region_listbox.selection_set(new_idx)
        self.region_listbox.activate(new_idx)
        self._on_listbox_select(None)

    def _pick_overlay_color(self):
        """Open color picker and store the chosen hex color."""
        initial = self.editor_vars['overlay_color'].get() or None
        result = colorchooser.askcolor(color=initial, title="Pick ROI overlay color", parent=self)
        if result and result[1]:
            hex_color = result[1]
            self.editor_vars['overlay_color'].set(hex_color)
            self.color_preview_label.config(text=hex_color, foreground=hex_color)

    def _reset_overlay_color(self):
        """Remove custom color (back to type-based default)."""
        self.editor_vars['overlay_color'].set('')
        self.color_preview_label.config(text="(default)", foreground="")

    def _update_axis_scaling_visibility(self):
        """Show/hide axis scaling section and update unit hint based on ROI type."""
        roi_type = self.editor_vars['roi_type'].get()
        WAVE_TYPES = {'frf', 'psd', 'coherence'}
        UNIT_HINTS = {
            'frf': 'Unit hint: e.g. g/N  (FRF amplitude, complex)',
            'psd': 'Unit hint: N²/Hz  (Power Spectral Density)',
            'coherence': 'Unit hint: 0–1 adimensional  (no unit, keep Y-Max=1)',
        }
        if roi_type in WAVE_TYPES:
            self.f_scale.pack(fill=tk.X, pady=5, padx=5)
            self.axis_unit_hint_label.config(text=UNIT_HINTS.get(roi_type, ''))
        else:
            self.f_scale.pack_forget()

    def _update_hsv_button_state(self):
        """Enable HSV calibration button only if wave regions exist."""
        wave_regions = {name: r for name, r in self.app_config.regions.items()
                        if r.roi_type in ('frf', 'psd', 'coherence')}
        state = tk.NORMAL if wave_regions else tk.DISABLED
        self.hsv_cal_btn.config(state=state)

    def _open_hsv_calibration(self):
        """Open HSV calibration window."""
        if self.screenshot is None:
            messagebox.showwarning("Warning", "Please take a screenshot first.", parent=self)
            return

        wave_regions = {name: r for name, r in self.app_config.regions.items()
                        if r.roi_type in ('frf', 'psd', 'coherence')}

        if not wave_regions:
            messagebox.showwarning("Warning", "No wave regions defined.", parent=self)
            return

        hsv_window = HSVCalibrationWindow(
            self,
            self.screenshot,
            wave_regions,
            self.app_config.hsv_lower,
            self.app_config.hsv_upper
        )

        # Wait for window to close
        self.wait_window(hsv_window)

        # Apply results if user clicked Apply
        if hsv_window.result_hsv_lower is not None:
            self.app_config.hsv_lower = hsv_window.result_hsv_lower
            self.app_config.hsv_upper = hsv_window.result_hsv_upper
            messagebox.showinfo("Success", "HSV color filter updated.", parent=self)

    def _apply_params(self):
        self.app_config.fft_cutoff_frequency = self.param_vars['fft_cutoff_frequency'].get()
        self.app_config.fft_energy_ratio_threshold = self.param_vars['fft_energy_ratio_threshold'].get()
        self.app_config.lowpass_cutoff = self.param_vars['lowpass_cutoff'].get()
        self.app_config.lowpass_filter_order = self.param_vars['lowpass_filter_order'].get()
        self.app_config.residual_threshold = self.param_vars['residual_threshold'].get()
        self.app_config.exceedance_ratio_threshold = self.param_vars['exceedance_ratio_threshold'].get()
        messagebox.showinfo("Success", "Analysis parameters updated.", parent=self)

    def _update_ui_from_data(self):
        sel_name = self.selected_region_name
        sel_idx = -1
        if sel_name:
            try:
                sel_idx = sorted(self.app_config.regions.keys()).index(sel_name)
            except ValueError:
                sel_name = None

        self.region_listbox.delete(0, tk.END)
        for i, name in enumerate(sorted(self.app_config.regions.keys())):
            disp = f"{name}" if self.app_config.regions[name].enabled else f"{name} (Disabled)"
            self.region_listbox.insert(tk.END, disp)
        
        if sel_idx != -1:
            self.region_listbox.selection_set(sel_idx)

        self.param_vars['fft_cutoff_frequency'].set(self.app_config.fft_cutoff_frequency)
        self.param_vars['fft_energy_ratio_threshold'].set(self.app_config.fft_energy_ratio_threshold)
        self.param_vars['lowpass_cutoff'].set(self.app_config.lowpass_cutoff)
        self.param_vars['lowpass_filter_order'].set(self.app_config.lowpass_filter_order)
        self.param_vars['residual_threshold'].set(self.app_config.residual_threshold)
        self.param_vars['exceedance_ratio_threshold'].set(self.app_config.exceedance_ratio_threshold)
        self._redraw_regions_on_canvas()
        self._update_hsv_button_state()  # Update HSV button state

    def _redraw_regions_on_canvas(self):
        self.canvas.delete("region")
        colors = {"frf": "#3498db", "psd": "#9b59b6", "coherence": "#1abc9c",
                  "averages": "#95a5a6", "status": "#2ecc71", "overload": "#e74c3c",
                  "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
        if not hasattr(self, 'x_offset'):
            return
        for name, r in self.app_config.regions.items():
            x1 = r.x * self.scale + self.x_offset
            y1 = r.y * self.scale + self.y_offset
            x2 = (r.x + r.width) * self.scale + self.x_offset
            y2 = (r.y + r.height) * self.scale + self.y_offset
            color = (r.overlay_color or colors.get(r.roi_type, "white")) if r.enabled else "gray"
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2, tags=("region", name))
            self.canvas.create_text(x1+5, y1+5, text=name, fill=color, anchor="nw", tags=("region", name))
    
    def _on_listbox_select(self, _):
        if not self.region_listbox.curselection():
            return
        self.selected_region_name = self.region_listbox.get(self.region_listbox.curselection()).replace(" (Disabled)", "")
        region_data = self.app_config.regions[self.selected_region_name]
        for key, var in self.editor_vars.items():
            if hasattr(region_data, key):
                var.set(getattr(region_data, key))
        # Update color preview
        custom_color = self.editor_vars['overlay_color'].get()
        if custom_color:
            self.color_preview_label.config(text=custom_color, foreground=custom_color)
        else:
            self.color_preview_label.config(text="(default)", foreground="")
        # Update axis scaling visibility
        self._update_axis_scaling_visibility()

    def _update_region_from_editor(self):
        if not self.selected_region_name:
            return messagebox.showerror("Error", "No region selected.", parent=self)
        old_name = self.selected_region_name
        new_name = self.editor_vars['name'].get()
        if new_name != old_name and new_name in self.app_config.regions:
            return messagebox.showerror("Error", "Region name must be unique.", parent=self)
        try:
            new_data = {k: v.get() for k, v in self.editor_vars.items()}
            del self.app_config.regions[old_name]
            updated_region = MonitoringRegion(**new_data)
            self.app_config.regions[new_name] = updated_region
            self.selected_region_name = new_name
            self._update_ui_from_data()
        except (tk.TclError, Exception) as e: 
            messagebox.showerror("Input Error", f"Invalid input value: {e}", parent=self)
            if old_name not in self.app_config.regions:
                self.app_config.regions[old_name] = MonitoringRegion(**{k: v.get() for k, v in self.editor_vars.items() if k != 'name'}) 
                self.editor_vars['name'].set(old_name)
                
    def _delete_selected_region(self):
        if not self.selected_region_name:
            return messagebox.showerror("Error", "No region selected.", parent=self)
        if messagebox.askyesno("Confirm Delete", f"Delete '{self.selected_region_name}'?", parent=self):
            del self.app_config.regions[self.selected_region_name]
            self.selected_region_name = None
            for key, var in self.editor_vars.items():
                if isinstance(var, (tk.IntVar, tk.DoubleVar)):
                    var.set(0)
                elif isinstance(var, tk.BooleanVar):
                    var.set(False)
                else:
                    var.set("")
            self._update_ui_from_data()

    def _save_config(self):
        initial_file = os.path.basename(self.current_config_path) if self.current_config_path else "new_config.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialdir="configs",
            initialfile=initial_file,
            parent=self
        )
        if not path:
            return
        try:
            self._apply_params()
            data = {n: asdict(r) for n, r in self.app_config.regions.items()}
            data['_metadata'] = {
                'hsv_lower': self.app_config.hsv_lower,
                'hsv_upper': self.app_config.hsv_upper,
                'screenshot_interval': self.app_config.screenshot_interval,
                'fft_cutoff_frequency': self.app_config.fft_cutoff_frequency,
                'fft_energy_ratio_threshold': self.app_config.fft_energy_ratio_threshold,
                'lowpass_cutoff': self.app_config.lowpass_cutoff,
                'lowpass_filter_order': self.app_config.lowpass_filter_order,
                'residual_threshold': self.app_config.residual_threshold,
                'exceedance_ratio_threshold': self.app_config.exceedance_ratio_threshold,
                # --- v0.6.0 PSD parameters ---
                'psd_fft_cutoff_frequency': self.app_config.psd_fft_cutoff_frequency,
                'psd_fft_energy_ratio_threshold': self.app_config.psd_fft_energy_ratio_threshold,
                'psd_lowpass_cutoff': self.app_config.psd_lowpass_cutoff,
                'psd_lowpass_filter_order': self.app_config.psd_lowpass_filter_order,
                'psd_residual_threshold': self.app_config.psd_residual_threshold,
                'psd_exceedance_ratio_threshold': self.app_config.psd_exceedance_ratio_threshold,
                # --- v0.6.0 Coherence parameters ---
                'coherence_threshold': self.app_config.coherence_threshold,
                'coherence_degradation_pct': self.app_config.coherence_degradation_pct,
                'hits_per_run': self.app_config.hits_per_run,
            }
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)

            self.saved_config_path = path  # Track that we saved
            self.current_config_path = path
            messagebox.showinfo("Success", f"Saved to {os.path.basename(path)}", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}", parent=self)

    def _load_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], initialdir="configs", parent=self)
        if not path:
            return
        try:
            self.app_config = ScreenMonitor(path).app_config
            self._update_ui_from_data()
            if self.screenshot:
                self._redraw_regions_on_canvas()
            messagebox.showinfo("Success", f"Loaded {os.path.basename(path)}", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}", parent=self)


# --- 5. LIVE GRAPH VIEWER ---
class GraphViewerFrame(ttk.LabelFrame):
    """Embedded matplotlib graph viewer with hit navigation and console output."""
    
    PLOT_TYPES = ['Signal', 'FFT', 'Lowpass Comparison', 'Residual Analysis', 'Run Summary']
    MAX_HISTORY = 25  # Reduced from 50 to limit memory usage
    
    def __init__(self, parent):
        super().__init__(parent, text="Live Graph & Console")
        self.current_plot_index = 0
        self.current_hit_index = -1
        self.hit_history: List[LightweightHitData] = []
        self.run_history = {}
        self.app_config = None
        self.text_handler = None  # Will be set up after console is created
        
        self._setup_ui()
        
    def _setup_ui(self):
        # Navigation frame at top
        nav_frame = ttk.Frame(self)
        nav_frame.pack(fill=tk.X, padx=5, pady=5)
        
        hit_nav_frame = ttk.LabelFrame(nav_frame, text="Hit")
        hit_nav_frame.pack(side=tk.LEFT, padx=5)
        
        self.prev_hit_btn = ttk.Button(hit_nav_frame, text="<<", command=self._prev_hit, width=4)
        self.prev_hit_btn.pack(side=tk.LEFT, padx=2)
        
        self.hit_label = ttk.Label(hit_nav_frame, text="--/--", width=12, anchor=tk.CENTER)
        self.hit_label.pack(side=tk.LEFT, padx=5)
        
        self.next_hit_btn = ttk.Button(hit_nav_frame, text=">>", command=self._next_hit, width=4)
        self.next_hit_btn.pack(side=tk.LEFT, padx=2)
        
        plot_nav_frame = ttk.LabelFrame(nav_frame, text="Plot Type")
        plot_nav_frame.pack(side=tk.LEFT, padx=10)
        
        self.prev_plot_btn = ttk.Button(plot_nav_frame, text="<", command=self._prev_plot, width=3)
        self.prev_plot_btn.pack(side=tk.LEFT, padx=2)
        
        self.plot_type_var = tk.StringVar(value=self.PLOT_TYPES[0])
        self.plot_selector = ttk.Combobox(plot_nav_frame, textvariable=self.plot_type_var, 
                                          values=self.PLOT_TYPES, state='readonly', width=18)
        self.plot_selector.pack(side=tk.LEFT, padx=2)
        self.plot_selector.bind("<<ComboboxSelected>>", self._on_plot_selected)
        
        self.next_plot_btn = ttk.Button(plot_nav_frame, text=">", command=self._next_plot, width=3)
        self.next_plot_btn.pack(side=tk.LEFT, padx=2)
        
        self.hit_info_label = ttk.Label(nav_frame, text="No data", font=("Segoe UI", 9))
        self.hit_info_label.pack(side=tk.RIGHT, padx=10)
        
        # Horizontal PanedWindow for split view (Graph | Console)
        self.paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left side: Graph
        graph_frame = ttk.LabelFrame(self.paned, text="Live Graph")
        self.paned.add(graph_frame, weight=3)
        
        self.figure = Figure(figsize=(6, 3), dpi=100, facecolor='#1E1E1E')
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        self.ax.set_title('Waiting for data...', color='white')
        
        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Right side: Console
        console_frame = ttk.LabelFrame(self.paned, text="Console Output")
        self.paned.add(console_frame, weight=2)
        
        # Console text widget with scrollbar
        console_inner = ttk.Frame(console_frame)
        console_inner.pack(fill=tk.BOTH, expand=True)
        
        console_scroll = ttk.Scrollbar(console_inner, orient=tk.VERTICAL)
        console_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.console_text = tk.Text(console_inner, wrap=tk.WORD, bg='#1E1E1E', fg='#00FF00',
                                    font=('Consolas', 9), state='disabled',
                                    yscrollcommand=console_scroll.set)
        self.console_text.pack(fill=tk.BOTH, expand=True)
        console_scroll.config(command=self.console_text.yview)
        
        # Add text handler to logger
        self.text_handler = TextHandler(self.console_text)
        logger.addHandler(self.text_handler)
        
        # Clear console button
        clear_btn = ttk.Button(console_frame, text="Clear Console", command=self._clear_console)
        clear_btn.pack(pady=2)
        
        self._update_navigation_state()
    
    def _clear_console(self):
        """Clear the console text widget."""
        self.console_text.configure(state='normal')
        self.console_text.delete('1.0', tk.END)
        self.console_text.configure(state='disabled')
    
    def _prev_hit(self):
        if self.current_hit_index > 0:
            self.current_hit_index -= 1
            self._update_plot()
            self._update_navigation_state()
    
    def _next_hit(self):
        if self.current_hit_index < len(self.hit_history) - 1:
            self.current_hit_index += 1
            self._update_plot()
            self._update_navigation_state()
        
    def _prev_plot(self):
        self.current_plot_index = (self.current_plot_index - 1) % len(self.PLOT_TYPES)
        self.plot_type_var.set(self.PLOT_TYPES[self.current_plot_index])
        self._update_plot()
        
    def _next_plot(self):
        self.current_plot_index = (self.current_plot_index + 1) % len(self.PLOT_TYPES)
        self.plot_type_var.set(self.PLOT_TYPES[self.current_plot_index])
        self._update_plot()
        
    def _on_plot_selected(self, event=None):
        self.current_plot_index = self.PLOT_TYPES.index(self.plot_type_var.get())
        self._update_plot()
    
    def _update_navigation_state(self):
        total = len(self.hit_history)
        current = self.current_hit_index + 1 if total > 0 else 0
        
        self.hit_label.config(text=f"{current}/{total}")
        
        self.prev_hit_btn.config(state=tk.NORMAL if self.current_hit_index > 0 else tk.DISABLED)
        self.next_hit_btn.config(state=tk.NORMAL if self.current_hit_index < total - 1 else tk.DISABLED)
        
        if total > 0 and 0 <= self.current_hit_index < total:
            hit_data = self.hit_history[self.current_hit_index]
            self.hit_info_label.config(text=f"Hit: {hit_data.hit_key}")
        else:
            self.hit_info_label.config(text="No data")
        
    def update_data(self, lightweight_data: LightweightHitData, run_history: Dict, app_config: AppConfig = None):
        """Update with lightweight hit data."""
        self.hit_history.append(lightweight_data)
        
        # Enforce history limit
        if len(self.hit_history) > self.MAX_HISTORY:
            self.hit_history = self.hit_history[-self.MAX_HISTORY:]
        
        self.run_history = run_history
        if app_config:
            self.app_config = app_config
        
        self.current_hit_index = len(self.hit_history) - 1
        
        self._update_plot()
        self._update_navigation_state()
        
    def _update_plot(self):
        if not self.hit_history or self.current_hit_index < 0:
            return
        
        if self.current_hit_index >= len(self.hit_history):
            self.current_hit_index = len(self.hit_history) - 1
            
        hit_data = self.hit_history[self.current_hit_index]
        
        self.ax.clear()
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        
        plot_type = self.plot_type_var.get()
        
        try:
            if plot_type == 'Signal':
                self._plot_signal(hit_data)
            elif plot_type == 'FFT':
                self._plot_fft(hit_data)
            elif plot_type == 'Lowpass Comparison':
                self._plot_lowpass_comparison(hit_data)
            elif plot_type == 'Residual Analysis':
                self._plot_residual(hit_data)
            elif plot_type == 'Run Summary':
                self._plot_run_summary(hit_data)
                
            for spine in self.ax.spines.values():
                spine.set_color('white')
                
        except Exception as e:
            logger.error(f"Error plotting {plot_type}: {e}")
            self.ax.text(0.5, 0.5, f'Error: {str(e)[:50]}', 
                        transform=self.ax.transAxes, ha='center', va='center', color='red')
            
        self.figure.tight_layout()
        self.canvas.draw_idle()  # Use draw_idle instead of draw for efficiency
        
    def _plot_signal(self, hit_data: LightweightHitData):
        if hit_data.signal_physical is None or len(hit_data.signal_physical) == 0:
            self.ax.text(0.5, 0.5, 'No signal data', transform=self.ax.transAxes, 
                        ha='center', va='center', color='white')
            return
            
        num_points = len(hit_data.signal_physical)
        freq_axis = np.linspace(hit_data.x_axis_min, hit_data.x_axis_max, num_points)
        
        self.ax.plot(freq_axis, hit_data.signal_physical, color='cyan', linewidth=1.5)
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Amplitude ({hit_data.y_axis_unit})', color='white')
        self.ax.set_title(f'Reconstructed Signal - {hit_data.hit_key}', color='white')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_fft(self, hit_data: LightweightHitData):
        self.ax.plot(hit_data.fft_freqs, hit_data.fft_mags, color='magenta', linewidth=1)
        
        if self.app_config:
            self.ax.axvline(x=self.app_config.fft_cutoff_frequency, color='yellow', 
                           linestyle='--', linewidth=1, label=f'Cutoff: {self.app_config.fft_cutoff_frequency:.2f}')
        
        self.ax.set_xlim(0, 0.5)
        self.ax.set_xlabel('Normalized Frequency', color='white')
        self.ax.set_ylabel('Magnitude', color='white')
        
        classification = "HF (Bad)" if hit_data.is_high_frequency else "LF (Good)"
        self.ax.set_title(f'FFT - {hit_data.hit_key} - Ratio: {hit_data.energy_ratio:.3e} - {classification}', 
                         color='white', fontsize=10)
        self.ax.legend(facecolor='#2E2E2E', labelcolor='white')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_lowpass_comparison(self, hit_data: LightweightHitData):
        if hit_data.signal_physical is None or hit_data.filtered_physical is None:
            self.ax.text(0.5, 0.5, 'No lowpass data available', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        num_points = len(hit_data.signal_physical)
        freq_axis = np.linspace(hit_data.x_axis_min, hit_data.x_axis_max, num_points)
        
        self.ax.plot(freq_axis, hit_data.signal_physical, 'w-', linewidth=1.5, label='Original', alpha=0.9)
        self.ax.plot(freq_axis, hit_data.filtered_physical, 'g--', linewidth=1.2, label='Lowpass Filtered')
        
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Amplitude ({hit_data.y_axis_unit})', color='white')
        
        if self.app_config:
            title = f'Lowpass - {hit_data.hit_key} - Cutoff: {self.app_config.lowpass_cutoff:.3f}'
        else:
            title = f'Lowpass Comparison - {hit_data.hit_key}'
        self.ax.set_title(title, color='white', fontsize=10)
        self.ax.legend(facecolor='#2E2E2E', labelcolor='white')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_residual(self, hit_data: LightweightHitData):
        if hit_data.residual_physical is None:
            self.ax.text(0.5, 0.5, 'No residual data available', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        num_points = len(hit_data.residual_physical)
        freq_axis = np.linspace(hit_data.x_axis_min, hit_data.x_axis_max, num_points)
        
        if self.app_config:
            threshold = self.app_config.residual_threshold
        else:
            threshold = 0.005
        
        self.ax.plot(freq_axis, hit_data.residual_physical, 'c-', linewidth=1, label='Residual')
        self.ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
        self.ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.2, label=f'±{threshold}')
        self.ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.2)
        
        exceedances = np.abs(hit_data.residual_physical) > threshold
        if np.any(exceedances):
            self.ax.scatter(freq_axis[exceedances], hit_data.residual_physical[exceedances], 
                          c='red', s=12, zorder=5, alpha=0.7)
        
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Residual ({hit_data.y_axis_unit})', color='white')
        classification = "BAD" if hit_data.lowpass_is_bad_hit else "GOOD"
        self.ax.set_title(f'Residual - {hit_data.hit_key} - Exc: {hit_data.exceedance_count} ({hit_data.exceedance_ratio:.1%}) - {classification}', 
                         color='white', fontsize=10)
        self.ax.legend(facecolor='#2E2E2E', labelcolor='white', loc='upper right')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_run_summary(self, hit_data: LightweightHitData):
        if not self.run_history:
            self.ax.text(0.5, 0.5, 'No run history available', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        current_run = hit_data.run
        if current_run in self.run_history:
            run_data = self.run_history[current_run]
        else:
            run_data = {}
            for rd in self.run_history.values():
                run_data.update(rd)
        
        if not run_data:
            self.ax.text(0.5, 0.5, 'No hits recorded yet', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        hits = list(run_data.keys())
        values = [run_data[h]['exceedance_count'] for h in hits]
        
        bars = self.ax.bar(range(len(hits)), values, edgecolor='white', linewidth=0.5)
        
        cmap = plt.cm.jet
        vmin, vmax = min(values), max(values)
        if vmin == vmax:
            vmin, vmax = vmin - 1, vmax + 1
        
        for bar, val in zip(bars, values):
            normalized = (val - vmin) / (vmax - vmin) if vmax > vmin else 0.5
            bar.set_color(cmap(normalized))
        
        self.ax.set_xticks(range(len(hits)))
        self.ax.set_xticklabels(hits, rotation=45, ha='right', fontsize=7, color='white')
        self.ax.set_xlabel('Hit', color='white')
        self.ax.set_ylabel('Exceedance Count', color='white')
        self.ax.set_title(f'Run Summary - {current_run} ({len(hits)} hits)', color='white')
        self.ax.grid(True, linestyle='--', alpha=0.3, axis='y')
        
    def clear(self):
        self.hit_history = []
        self.current_hit_index = -1
        self.run_history = {}
        
        self.ax.clear()
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        self.ax.set_title('Waiting for data...', color='white')
        self.canvas.draw_idle()
        
        self._update_navigation_state()
        gc.collect()


# --- 6. MAIN GUI ---
class MonitorControlGUI:
    def __init__(self, root, config_path=None, calibration_mode=False):
        self.root = root
        self.root.title("USMA v0.9 - Calibration Phase Release")
        self.root.geometry("1000x750")
        self.calibration_mode = calibration_mode

        # Use provided config path or default
        default_config = "configs/default_config.json" if config_path is None else config_path
        self.config_path = tk.StringVar(value=default_config)
        self.is_monitoring = tk.BooleanVar(value=False)
        self.is_overlay_on = tk.BooleanVar(value=False)
        self.verbose_logging_on = tk.BooleanVar(value=False)
        
        # Verbose log option variables (all default to False like image logging)
        self.vlog_opt_config = tk.BooleanVar(value=False)
        self.vlog_opt_mask = tk.BooleanVar(value=False)
        self.vlog_opt_ocr = tk.BooleanVar(value=False)
        self.vlog_opt_fft = tk.BooleanVar(value=False)
        self.vlog_opt_lowpass = tk.BooleanVar(value=False)
        self.vlog_opt_classification = tk.BooleanVar(value=False)
        self.vlog_opt_filesave = tk.BooleanVar(value=False)
        self.image_logging_on = tk.BooleanVar(value=False)
        self.log_opt_screenshot = tk.BooleanVar(value=False)
        self.log_opt_color_filter = tk.BooleanVar(value=False)
        self.log_opt_signal_plot = tk.BooleanVar(value=False)
        self.log_opt_fft_plot = tk.BooleanVar(value=False)
        self.log_opt_lowpass_plot = tk.BooleanVar(value=False)
        self.log_opt_residual_plot = tk.BooleanVar(value=False)
        self.log_opt_summary_chart = tk.BooleanVar(value=False)
        self.log_opt_ocr_images = tk.BooleanVar(value=False)
        self.log_events_only = tk.BooleanVar(value=True)  # When False, logs every ~1 second
        
        self.audio_feedback_on = tk.BooleanVar(value=False)
        self.log_to_unv = tk.BooleanVar(value=False)
        
        self.monitor = ScreenMonitor(self.config_path.get(), self.update_feedback_panel, self._on_plot_data)
        
        self.manual_points_vars = {
            'run': tk.StringVar(value='Run 1'),
            'hammer_point': tk.StringVar(value='P1'),
            'hammer_dir': tk.StringVar(value='-Z'),
            'response_point': tk.StringVar(value='P1'),
            'response_dir': tk.StringVar(value='-Z')
        }
        
        initial_freq = 1.0/self.monitor.app_config.screenshot_interval if self.monitor.app_config.screenshot_interval > 0 else 4.0
        self.sample_frequency = tk.DoubleVar(value=round(initial_freq, 2))
        
        self.overlay = None

        # Calibration state (Phase 4 / v0.9)
        self.calibration_estimator: Optional[HybridCalibrationEstimator] = None
        self.calibration_session: Optional[CalibrationSession] = None
        self.live_calibration_buffer: List[dict] = []
        self.live_cal_good_count = 0
        self.live_cal_bad_count = 0
        self._pending_signal: Optional[list] = None
        self._pending_frame_result = None
        self.auto_update_cal = tk.BooleanVar(value=True)
        self._cal_signal_count = 0

        # Load existing calibration from config if present
        self._load_calibration_from_config()

        self._setup_main_gui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
    
    def _setup_main_gui(self):
        # Create main container with scrollbars
        main_container = ttk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # Canvas for scrolling
        self.main_canvas = tk.Canvas(main_container, highlightthickness=0)
        v_scrollbar = ttk.Scrollbar(main_container, orient=tk.VERTICAL, command=self.main_canvas.yview)
        h_scrollbar = ttk.Scrollbar(main_container, orient=tk.HORIZONTAL, command=self.main_canvas.xview)
        
        # Scrollable frame inside canvas
        self.scrollable_frame = ttk.Frame(self.main_canvas, padding=10)
        
        # Configure canvas scrolling
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))
        )
        
        self.canvas_window = self.main_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.main_canvas.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Bind mouse wheel scrolling
        self.main_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        # Layout scrollbars and canvas
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.main_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Make canvas resize with window
        main_container.bind("<Configure>", self._on_main_resize)
        
        # Now build content inside scrollable_frame
        frame = self.scrollable_frame
        
        config_frame = ttk.LabelFrame(frame, text="Configuration")
        config_frame.pack(fill=tk.X, pady=5)
        ttk.Entry(config_frame, textvariable=self.config_path, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        self.load_button = ttk.Button(config_frame, text="Load...", command=self._load_config)
        self.load_button.pack(side=tk.LEFT, padx=5)
        self.edit_button = ttk.Button(config_frame, text="Edit Config...", command=self._launch_config_tool)
        self.edit_button.pack(side=tk.LEFT, padx=5)
        
        feedback_frame = ttk.LabelFrame(frame, text="Live Analysis Feedback")
        feedback_frame.pack(fill=tk.X, pady=5)
        
        self.status_light = tk.Canvas(feedback_frame, width=40, height=40, bg="gray", highlightthickness=0)
        self.status_light.grid(row=0, column=0, rowspan=2, padx=10, pady=5)
        
        self.class_var = tk.StringVar(value="Overall: --")
        self.hf_ratio_var = tk.StringVar(value="FFT Ratio: --")
        self.exceedance_var = tk.StringVar(value="LP Exc: --")
        self.status_var = tk.StringVar(value="Status: --")
        self.overload_var = tk.StringVar(value="Overload: --")
        self.points_var = tk.StringVar(value="Points: --")
        
        ttk.Label(feedback_frame, textvariable=self.class_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.hf_ratio_var, font=("Segoe UI", 10)).grid(row=0, column=2, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.exceedance_var, font=("Segoe UI", 10)).grid(row=0, column=3, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.status_var, font=("Segoe UI", 10)).grid(row=1, column=1, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.overload_var, font=("Segoe UI", 10)).grid(row=1, column=2, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.points_var, font=("Segoe UI", 10)).grid(row=1, column=3, columnspan=2, sticky=tk.W, padx=5)
        feedback_frame.columnconfigure(3, weight=1)
        
        # Graph viewer with reduced minimum height
        self.graph_viewer = GraphViewerFrame(frame)
        self.graph_viewer.pack(fill=tk.BOTH, expand=True, pady=5)
        self.graph_viewer.configure(height=200)  # Set minimum height

        bottom_panel = ttk.Frame(frame)
        bottom_panel.pack(fill=tk.X, pady=5)
        
        control_frame = ttk.LabelFrame(bottom_panel, text="Controls")
        control_frame.pack(fill=tk.Y, side=tk.LEFT, pady=5)
        
        self.start_stop_button = ttk.Button(control_frame, text="Start Monitoring", command=self._toggle_monitoring)
        self.start_stop_button.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)
        
        self.overlay_check = ttk.Checkbutton(control_frame, text="Show Overlay", variable=self.is_overlay_on, command=self._toggle_overlay)
        self.overlay_check.pack(padx=10, pady=(0,5))
        
        # Log on Events Only checkbox (new)
        self.events_only_check = ttk.Checkbutton(control_frame, text="Log on Events Only", 
                                                  variable=self.log_events_only,
                                                  command=self._toggle_events_only)
        self.events_only_check.pack(padx=10, pady=(0,5))
        
        params_frame = ttk.LabelFrame(control_frame, text="Parameters")
        params_frame.pack(padx=5, pady=5, fill=tk.Y)
        
        freq_frame = ttk.Frame(params_frame)
        ttk.Label(freq_frame, text="Sample Freq (Hz):").pack(side=tk.LEFT, padx=(5,2))
        self.freq_spinbox = ttk.Spinbox(freq_frame, from_=0.1, to=30.0, increment=0.1, textvariable=self.sample_frequency, width=6)
        self.freq_spinbox.pack(side=tk.LEFT, padx=(0,5))
        freq_frame.pack(pady=5)
        
        self.audio_check = ttk.Checkbutton(params_frame, text="Audio Feedback", variable=self.audio_feedback_on, command=self._toggle_audio_feedback)
        self.audio_check.pack(anchor=tk.W, padx=5, pady=(0, 5))
        if not SOUND_DEVICE_AVAILABLE:
            self.audio_check.config(state=tk.DISABLED)
            self.audio_feedback_on.set(False)
        
        right_panel = ttk.Frame(bottom_panel)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        self.manual_points_frame = ttk.LabelFrame(right_panel, text="Manual Points of Interest (POI) Entry")
        self.manual_points_frame.pack(fill=tk.X)
        pf = self.manual_points_frame
        
        # Store widget references for selective enabling/disabling
        self.manual_run_label = ttk.Label(pf, text="Run:")
        self.manual_run_label.grid(row=0, column=0, padx=5, pady=2)
        self.manual_run_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['run'], width=8)
        self.manual_run_entry.grid(row=0, column=1)
        
        self.manual_hammer_label = ttk.Label(pf, text="Hammer:")
        self.manual_hammer_label.grid(row=0, column=2, padx=5)
        self.manual_hammer_point_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_point'], width=6)
        self.manual_hammer_point_entry.grid(row=0, column=3)
        self.manual_hammer_dir_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_dir'], width=4)
        self.manual_hammer_dir_entry.grid(row=0, column=4)
        
        self.manual_response_label = ttk.Label(pf, text="Response:")
        self.manual_response_label.grid(row=1, column=2, padx=5)
        self.manual_response_point_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['response_point'], width=6)
        self.manual_response_point_entry.grid(row=1, column=3)
        self.manual_response_dir_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['response_dir'], width=4)
        self.manual_response_dir_entry.grid(row=1, column=4)
        
        logging_controls_frame = ttk.LabelFrame(right_panel, text="Logging")
        logging_controls_frame.pack(fill=tk.X, pady=5)
        
        logging_main_frame = ttk.Frame(logging_controls_frame)
        logging_main_frame.pack(fill=tk.X, side=tk.LEFT, anchor=tk.N, padx=5)
        
        self.verbose_check = ttk.Checkbutton(logging_main_frame, text="Verbose Console Log", variable=self.verbose_logging_on, command=self._toggle_verbose_log_options_state)
        self.verbose_check.pack(anchor=tk.W, pady=2)
        
        # Verbose log options frame - horizontal 2x4 grid layout
        self.verbose_log_options_frame = ttk.Frame(logging_main_frame)
        self.verbose_log_options_frame.pack(fill=tk.X, pady=(2,5))
        
        # Row 1: Config, Mask, OCR, Classification
        vrow1 = ttk.Frame(self.verbose_log_options_frame)
        vrow1.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(vrow1, text="Config", variable=self.vlog_opt_config).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="Mask", variable=self.vlog_opt_mask).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="OCR", variable=self.vlog_opt_ocr).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="Classify", variable=self.vlog_opt_classification).pack(side=tk.LEFT, padx=2)
        
        # Row 2: FFT, Lowpass, File Saves
        vrow2 = ttk.Frame(self.verbose_log_options_frame)
        vrow2.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(vrow2, text="FFT", variable=self.vlog_opt_fft).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow2, text="Lowpass", variable=self.vlog_opt_lowpass).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow2, text="FileSave", variable=self.vlog_opt_filesave).pack(side=tk.LEFT, padx=2)
        
        self.img_log_check = ttk.Checkbutton(logging_main_frame, text="Enable Image Logs", variable=self.image_logging_on, command=self._toggle_img_log_options_state)
        self.img_log_check.pack(anchor=tk.W, pady=2)
        
        # Image log options frame - horizontal 2x4 grid layout
        self.img_log_options_frame = ttk.Frame(logging_main_frame)
        self.img_log_options_frame.pack(fill=tk.X, pady=(5,0))
        
        # Row 1: ROI, Masks, OCR, Signal
        irow1 = ttk.Frame(self.img_log_options_frame)
        irow1.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(irow1, text="ROI", variable=self.log_opt_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="Masks", variable=self.log_opt_color_filter).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="OCR", variable=self.log_opt_ocr_images).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="Signal", variable=self.log_opt_signal_plot).pack(side=tk.LEFT, padx=2)
        
        # Row 2: FFT, Lowpass, Residual, Summary
        irow2 = ttk.Frame(self.img_log_options_frame)
        irow2.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(irow2, text="FFT", variable=self.log_opt_fft_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Lowpass", variable=self.log_opt_lowpass_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Residual", variable=self.log_opt_residual_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Summary", variable=self.log_opt_summary_chart).pack(side=tk.LEFT, padx=2)
        
        data_logging_frame = ttk.Frame(logging_controls_frame)
        data_logging_frame.pack(fill=tk.X, side=tk.LEFT, padx=10, anchor=tk.N)
        ttk.Checkbutton(data_logging_frame, text="Log to .unv", variable=self.log_to_unv).pack(anchor=tk.W, pady=2)

        # --- Calibration Status Bar (always visible, Phase 4 / v0.9) ---
        self.cal_status_canvas = tk.Canvas(right_panel, height=24, highlightthickness=0)
        self.cal_status_canvas.pack(fill=tk.X, padx=5, pady=(10, 2))
        self._update_calibration_status_bar()

        if self.calibration_mode:
            self._setup_calibration_panel(right_panel)
        else:
            self._setup_normal_params_panel(right_panel)

        self.status_label = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        self._toggle_img_log_options_state()
        self._toggle_verbose_log_options_state()
        self._update_manual_points_state()
    
    def _on_mousewheel(self, event):
        """Handle mouse wheel scrolling."""
        self.main_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    
    def _on_main_resize(self, event):
        """Resize canvas window when main window resizes."""
        # Update the width of the canvas window to match canvas width
        canvas_width = event.width
        self.main_canvas.itemconfig(self.canvas_window, width=canvas_width)
    
    def _toggle_events_only(self):
        """Toggle between event-based and continuous logging."""
        self.monitor.log_events_only = self.log_events_only.get()
        if not self.log_events_only.get():
            self.monitor.last_continuous_log_time = 0  # Reset timer to log immediately
            self.monitor.continuous_log_counter = 0
            logger.info("Continuous logging mode ENABLED - logging every ~1 second")
        else:
            logger.info("Event-based logging mode ENABLED - logging only on wave events")

    def _toggle_audio_feedback(self):
        self.monitor.set_audio_feedback(self.audio_feedback_on.get())
        
    def _toggle_img_log_options_state(self):
        state = tk.NORMAL if self.image_logging_on.get() else tk.DISABLED
        for col in self.img_log_options_frame.winfo_children():
            for child in col.winfo_children():
                child.configure(state=state)

    def _toggle_verbose_log_options_state(self):
        """Enable/disable verbose log checkboxes based on master toggle."""
        state = tk.NORMAL if self.verbose_logging_on.get() else tk.DISABLED
        for col in self.verbose_log_options_frame.winfo_children():
            for child in col.winfo_children():
                child.configure(state=state)

    def _apply_params_live(self):
        """Apply parameter changes to the running monitor immediately."""
        try:
            self.monitor.app_config.fft_cutoff_frequency = self.param_vars['fft_cutoff_frequency'].get()
            self.monitor.app_config.fft_energy_ratio_threshold = self.param_vars['fft_energy_ratio_threshold'].get()
            self.monitor.app_config.lowpass_cutoff = self.param_vars['lowpass_cutoff'].get()
            self.monitor.app_config.lowpass_filter_order = self.param_vars['lowpass_filter_order'].get()
            self.monitor.app_config.residual_threshold = self.param_vars['residual_threshold'].get()
            self.monitor.app_config.exceedance_ratio_threshold = self.param_vars['exceedance_ratio_threshold'].get()

            # Also update the graph viewer's reference
            self.graph_viewer.app_config = self.monitor.app_config

            if self.verbose_logging_on.get():
                logger.info(f"Parameters updated live: FFT_cut={self.monitor.app_config.fft_cutoff_frequency:.3f}, "
                           f"FFT_thr={self.monitor.app_config.fft_energy_ratio_threshold:.4f}, "
                           f"LP_cut={self.monitor.app_config.lowpass_cutoff:.3f}")
        except tk.TclError as e:
            logger.warning(f"Invalid parameter value: {e}")

    def update_feedback_panel(self, result: FrameAnalysisResult):
        self.root.after(0, self._update_feedback_ui, result)
        
    def _update_feedback_ui(self, result: FrameAnalysisResult):
        # --- Classification status light (uses combined FRF+PSD logic) ---
        if result.overall_is_hf is not None or result.psd_overall_is_hf is not None:
            classification, color = self.monitor.classify_hit(result)
            self.class_var.set(f"Overall: {classification}")
            self.status_light.config(bg=color)

        # --- FRF readout ---
        if result.avg_energy_ratio is not None:
            self.hf_ratio_var.set(f"FFT Ratio (FRF): {result.avg_energy_ratio:.3e}")
        if result.avg_exceedance_count is not None:
            self.exceedance_var.set(f"LP Exc (FRF): {result.avg_exceedance_count:.0f} ({result.avg_exceedance_ratio:.1%})")

        # --- PSD readout (if PSD regions exist) ---
        if result.psd_avg_energy_ratio is not None:
            self.hf_ratio_var.set(f"FFT Ratio (PSD): {result.psd_avg_energy_ratio:.3e}")
        if result.psd_avg_exceedance_count is not None and result.psd_avg_exceedance_ratio is not None:
            self.exceedance_var.set(f"LP Exc (PSD): {result.psd_avg_exceedance_count:.0f} ({result.psd_avg_exceedance_ratio:.1%})")

        # --- Coherence readout (if coherence ROIs exist) ---
        if result.coherence_results:
            coh_values = list(result.coherence_results.values())
            avg_coh = float(sum(c.mean_coherence for c in coh_values) / len(coh_values))
            avg_bad = float(sum(c.normalized_badness for c in coh_values) / len(coh_values))
            self.status_var.set(f"Coherence: {avg_coh:.3f} | Badness: {avg_bad:.4f}")
            if result.current_averages is not None:
                self.overload_var.set(f"Averages: {result.current_averages}")
            else:
                self.overload_var.set(f"Overload: {result.overload_text}")
        else:
            self.status_var.set(f"Status: {result.status_text}")
            self.overload_var.set(f"Overload: {result.overload_text}")

        p = result.points_info
        self.points_var.set(f"Points: {p.run} | H: {p.hammer_point}{p.hammer_dir} | R: {p.response_point}{p.response_dir}")
        
    def _on_plot_data(self, lightweight_data: LightweightHitData, run_history: Dict):
        """Thread-safe callback to update graph viewer."""
        self.root.after(0, self._update_graph_viewer, lightweight_data, run_history)
            
    def _update_graph_viewer(self, lightweight_data: LightweightHitData, run_history: Dict):
        self.graph_viewer.update_data(lightweight_data, run_history, self.monitor.app_config)
        
    def _reset_feedback_ui(self):
        self.class_var.set("Overall: --")
        self.status_light.config(bg="gray")
        self.hf_ratio_var.set("FFT Ratio: --")
        self.exceedance_var.set("LP Exc: --")
        self.status_var.set("Status: --")
        self.overload_var.set("Overload: --")
        self.points_var.set("Points: --")
        self.graph_viewer.clear()
    
    def _update_manual_points_state(self):
        """Selectively enable/disable manual entry fields based on which OCR ROIs exist.
        
        If a specific ROI type is defined and enabled, disable its corresponding entry.
        If missing, allow manual entry for that field.
        """
        # Check which ROI types are defined
        has_run_roi = any(r.roi_type == 'run' and r.enabled 
                          for r in self.monitor.app_config.regions.values())
        has_hammer_roi = any(r.roi_type == 'hammer' and r.enabled 
                             for r in self.monitor.app_config.regions.values())
        has_response_roi = any(r.roi_type == 'response' and r.enabled 
                               for r in self.monitor.app_config.regions.values())
        
        # Set states for each group of widgets
        run_state = tk.DISABLED if has_run_roi else tk.NORMAL
        hammer_state = tk.DISABLED if has_hammer_roi else tk.NORMAL
        response_state = tk.DISABLED if has_response_roi else tk.NORMAL
        
        # Apply states to Run widgets
        self.manual_run_label.configure(state=run_state)
        self.manual_run_entry.configure(state=run_state)
        
        # Apply states to Hammer widgets
        self.manual_hammer_label.configure(state=hammer_state)
        self.manual_hammer_point_entry.configure(state=hammer_state)
        self.manual_hammer_dir_entry.configure(state=hammer_state)
        
        # Apply states to Response widgets
        self.manual_response_label.configure(state=response_state)
        self.manual_response_point_entry.configure(state=response_state)
        self.manual_response_dir_entry.configure(state=response_state)

    # --- Calibration Panel Setup Methods (Phase 4 / v0.9) ---

    def _setup_calibration_panel(self, parent):
        """Build calibration-mode UI with read-only parameter estimates + Good/Bad/Ignore buttons."""
        cal_frame = ttk.LabelFrame(parent, text="Calibration Estimates")
        cal_frame.pack(fill=tk.X, pady=(5, 0))

        # Read-only parameter display
        self.cal_param_labels = {}
        param_names = [
            ('fft_cutoff_frequency', 'FFT Cutoff'),
            ('fft_energy_ratio_threshold', 'FFT E.Ratio'),
            ('lowpass_cutoff', 'LP Cutoff'),
            ('lowpass_filter_order', 'LP Order'),
            ('residual_threshold', 'Res.Thr'),
            ('exceedance_ratio_threshold', 'Exc.Ratio'),
        ]
        row = 0
        for key, label in param_names:
            ttk.Label(cal_frame, text=f"{label}:", width=14).grid(row=row, column=0, padx=5, pady=1, sticky=tk.W)
            val_label = ttk.Label(cal_frame, text="--", width=12, relief=tk.SUNKEN)
            val_label.grid(row=row, column=1, padx=5, pady=1)
            self.cal_param_labels[key] = val_label
            row += 1

        # Signal Judgment frame
        judge_frame = ttk.LabelFrame(parent, text="Signal Judgment")
        judge_frame.pack(fill=tk.X, pady=5)

        self.cal_signal_label = ttk.Label(judge_frame, text="Waiting for signal...",
                                           font=("Segoe UI", 9))
        self.cal_signal_label.pack(pady=5)

        btn_row = ttk.Frame(judge_frame)
        btn_row.pack(pady=5)

        self.cal_good_btn = tk.Button(btn_row, text="Good", bg="#2ECC71", fg="white",
                                      width=8, font=("Segoe UI", 10, "bold"),
                                      state=tk.DISABLED, command=self._on_cal_good_click)
        self.cal_good_btn.pack(side=tk.LEFT, padx=5)

        self.cal_bad_btn = tk.Button(btn_row, text="Bad", bg="#E74C3C", fg="white",
                                     width=8, font=("Segoe UI", 10, "bold"),
                                     state=tk.DISABLED, command=self._on_cal_bad_click)
        self.cal_bad_btn.pack(side=tk.LEFT, padx=5)

        self.cal_ignore_btn = tk.Button(btn_row, text="Ignore", bg="#95A5A6", fg="white",
                                        width=8, font=("Segoe UI", 10, "bold"),
                                        state=tk.DISABLED, command=self._on_cal_ignore_click)
        self.cal_ignore_btn.pack(side=tk.LEFT, padx=5)

        # Counter label
        self.cal_counter_label = ttk.Label(judge_frame,
                                            text="Signals: 0 Good, 0 Bad (need 3+3)",
                                            font=("Segoe UI", 8))
        self.cal_counter_label.pack(pady=(0, 5))

        # Finish Calibration button
        self.finish_cal_btn = ttk.Button(parent, text="Finish Calibration",
                                          command=self._finish_calibration)
        self.finish_cal_btn.pack(fill=tk.X, padx=5, pady=5)

        # Initialize estimator if not loaded from config
        if self.calibration_estimator is None:
            self.calibration_estimator = HybridCalibrationEstimator()
            self.calibration_session = CalibrationSession(
                config_name=os.path.basename(self.config_path.get()),
                created_at=datetime.now().isoformat()
            )

        # Also build hidden param_vars for _apply_params_live / _sync_param_ui_from_config
        self.param_vars = {
            'fft_cutoff_frequency': tk.DoubleVar(value=self.monitor.app_config.fft_cutoff_frequency),
            'fft_energy_ratio_threshold': tk.DoubleVar(value=self.monitor.app_config.fft_energy_ratio_threshold),
            'lowpass_cutoff': tk.DoubleVar(value=self.monitor.app_config.lowpass_cutoff),
            'lowpass_filter_order': tk.IntVar(value=self.monitor.app_config.lowpass_filter_order),
            'residual_threshold': tk.DoubleVar(value=self.monitor.app_config.residual_threshold),
            'exceedance_ratio_threshold': tk.DoubleVar(value=self.monitor.app_config.exceedance_ratio_threshold)
        }

    def _setup_normal_params_panel(self, parent):
        """Build normal-mode Analysis Parameters panel with optional live calibration buttons."""
        params_outer_frame = ttk.LabelFrame(parent, text="Analysis Parameters (Live)")
        params_outer_frame.pack(fill=tk.X, pady=(5, 0))

        self.param_vars = {
            'fft_cutoff_frequency': tk.DoubleVar(value=self.monitor.app_config.fft_cutoff_frequency),
            'fft_energy_ratio_threshold': tk.DoubleVar(value=self.monitor.app_config.fft_energy_ratio_threshold),
            'lowpass_cutoff': tk.DoubleVar(value=self.monitor.app_config.lowpass_cutoff),
            'lowpass_filter_order': tk.IntVar(value=self.monitor.app_config.lowpass_filter_order),
            'residual_threshold': tk.DoubleVar(value=self.monitor.app_config.residual_threshold),
            'exceedance_ratio_threshold': tk.DoubleVar(value=self.monitor.app_config.exceedance_ratio_threshold)
        }

        fft_frame = ttk.Frame(params_outer_frame)
        fft_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(fft_frame, text="FFT Cutoff:", width=12).pack(side=tk.LEFT)
        fft_cutoff_spin = ttk.Spinbox(fft_frame, from_=0.01, to=0.5, increment=0.01,
                                      textvariable=self.param_vars['fft_cutoff_frequency'],
                                      width=7, command=self._apply_params_live)
        fft_cutoff_spin.pack(side=tk.LEFT, padx=2)
        fft_cutoff_spin.bind('<Return>', lambda e: self._apply_params_live())
        ttk.Label(fft_frame, text="E.Ratio:", width=8).pack(side=tk.LEFT, padx=(10, 0))
        fft_ratio_spin = ttk.Spinbox(fft_frame, from_=0.001, to=0.5, increment=0.001,
                                     textvariable=self.param_vars['fft_energy_ratio_threshold'],
                                     width=7, command=self._apply_params_live)
        fft_ratio_spin.pack(side=tk.LEFT, padx=2)
        fft_ratio_spin.bind('<Return>', lambda e: self._apply_params_live())

        lp_frame = ttk.Frame(params_outer_frame)
        lp_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(lp_frame, text="LP Cutoff:", width=12).pack(side=tk.LEFT)
        lp_cutoff_spin = ttk.Spinbox(lp_frame, from_=0.01, to=0.5, increment=0.01,
                                     textvariable=self.param_vars['lowpass_cutoff'],
                                     width=7, command=self._apply_params_live)
        lp_cutoff_spin.pack(side=tk.LEFT, padx=2)
        lp_cutoff_spin.bind('<Return>', lambda e: self._apply_params_live())
        ttk.Label(lp_frame, text="Order:", width=8).pack(side=tk.LEFT, padx=(10, 0))
        lp_order_spin = ttk.Spinbox(lp_frame, from_=1, to=10, increment=1,
                                    textvariable=self.param_vars['lowpass_filter_order'],
                                    width=4, command=self._apply_params_live)
        lp_order_spin.pack(side=tk.LEFT, padx=2)
        lp_order_spin.bind('<Return>', lambda e: self._apply_params_live())

        res_frame = ttk.Frame(params_outer_frame)
        res_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(res_frame, text="Res.Thr:", width=12).pack(side=tk.LEFT)
        res_thr_spin = ttk.Spinbox(res_frame, from_=0.0001, to=0.1, increment=0.0005,
                                   textvariable=self.param_vars['residual_threshold'],
                                   width=8, format="%.4f", command=self._apply_params_live)
        res_thr_spin.pack(side=tk.LEFT, padx=2)
        res_thr_spin.bind('<Return>', lambda e: self._apply_params_live())
        ttk.Label(res_frame, text="Exc.Ratio:", width=8).pack(side=tk.LEFT, padx=(10, 0))
        exc_ratio_spin = ttk.Spinbox(res_frame, from_=0.01, to=0.99, increment=0.01,
                                     textvariable=self.param_vars['exceedance_ratio_threshold'],
                                     width=7, command=self._apply_params_live)
        exc_ratio_spin.pack(side=tk.LEFT, padx=2)
        exc_ratio_spin.bind('<Return>', lambda e: self._apply_params_live())

        # Live Calibration buttons (shown if calibration estimator exists)
        if self.calibration_estimator is not None:
            live_cal_frame = ttk.LabelFrame(params_outer_frame, text="Live Calibration")
            live_cal_frame.pack(fill=tk.X, padx=5, pady=5)

            self.live_cal_label = ttk.Label(live_cal_frame, text="Waiting for signal...",
                                             font=("Segoe UI", 8))
            self.live_cal_label.pack(side=tk.LEFT, padx=5)

            self.live_bad_btn = tk.Button(live_cal_frame, text="Bad", bg="#E74C3C", fg="white",
                                          width=5, state=tk.DISABLED, command=self._on_live_bad_click)
            self.live_bad_btn.pack(side=tk.RIGHT, padx=2, pady=2)
            self.live_good_btn = tk.Button(live_cal_frame, text="Good", bg="#2ECC71", fg="white",
                                           width=5, state=tk.DISABLED, command=self._on_live_good_click)
            self.live_good_btn.pack(side=tk.RIGHT, padx=2, pady=2)

            ttk.Checkbutton(live_cal_frame, text="Auto-update", variable=self.auto_update_cal).pack(side=tk.RIGHT, padx=5)

    def _update_calibration_status_bar(self):
        """Update the calibration status bar color and text."""
        level = 0
        total = 0
        if self.calibration_estimator:
            level = self.calibration_estimator.confidence_level
            total = self.calibration_estimator.total_signals

        colors = {0: '#E74C3C', 1: '#F39C12', 2: '#F1C40F', 3: '#3498DB', 4: '#2ECC71'}
        labels = {
            0: "Not Calibrated",
            1: f"Preliminary ({total} signals)",
            2: f"Basic ({total} signals)",
            3: f"Solid ({total} signals)",
            4: f"Robust ({total} signals)"
        }
        color = colors.get(level, '#E74C3C')
        text = labels.get(level, "Unknown")

        self.cal_status_canvas.delete("all")
        self.cal_status_canvas.create_rectangle(0, 0, 9999, 24, fill=color, outline='')
        self.cal_status_canvas.create_text(10, 12, text=text, anchor='w',
                                            font=("Segoe UI", 9, "bold"), fill='white')

    def _on_calibration_signal(self, cal_data_list, frame_result):
        """Callback from ScreenMonitor when a signal is detected in calibration mode."""
        self.root.after(0, self._handle_calibration_signal, cal_data_list, frame_result)

    def _handle_calibration_signal(self, cal_data_list, frame_result):
        """Handle a new calibration signal on the GUI thread."""
        self._pending_signal = cal_data_list
        self._pending_frame_result = frame_result
        self._cal_signal_count += 1

        # Check similarity
        if self.calibration_estimator and cal_data_list:
            first = cal_data_list[0]
            stored = [{'signal_physical': s.signal_physical, 'fft_mags': s.fft_mags}
                      for s in (self.calibration_session.signals if self.calibration_session else [])]
            is_similar, sim_idx = check_signal_similarity(
                first.get('signal_physical', np.array([])),
                first.get('fft_mags', np.array([])),
                stored
            )
            if is_similar:
                result = messagebox.askyesno(
                    "Similar Signal",
                    f"This signal is very similar to calibration signal #{sim_idx + 1}.\n"
                    "Use a different hit condition for better calibration.\n\n"
                    "Classify anyway?")
                if not result:
                    self._pending_signal = None
                    self.monitor.receive_calibration_judgment()
                    return

        if self.calibration_mode:
            self.cal_signal_label.config(text=f"Signal #{self._cal_signal_count} detected - classify it")
            self.cal_good_btn.config(state=tk.NORMAL)
            self.cal_bad_btn.config(state=tk.NORMAL)
            self.cal_ignore_btn.config(state=tk.NORMAL)
        elif hasattr(self, 'live_good_btn'):
            self.live_cal_label.config(text=f"Signal #{self._cal_signal_count}")
            self.live_good_btn.config(state=tk.NORMAL)
            self.live_bad_btn.config(state=tk.NORMAL)

    def _process_judgment(self, judgment: str, source: str = "calibration_phase"):
        """Process a Good/Bad/Ignore judgment for the pending signal."""
        if not self._pending_signal:
            return

        for sig_data in self._pending_signal:
            if judgment != "IGNORE" and self.calibration_estimator:
                self.calibration_estimator.add_signal(sig_data, judgment)

                cal_signal = CalibrationSignal(
                    signal_physical=sig_data.get('signal_physical', np.array([])),
                    fft_freqs=sig_data.get('fft_freqs', np.array([])),
                    fft_mags=sig_data.get('fft_mags', np.array([])),
                    residual_physical=sig_data.get('residual_physical', np.array([])),
                    energy_ratio=sig_data.get('energy_ratio', 0),
                    exceedance_ratio=sig_data.get('exceedance_ratio', 0),
                    exceedance_count=sig_data.get('exceedance_count', 0),
                    total_energy=sig_data.get('total_energy', 0),
                    high_freq_energy=sig_data.get('high_freq_energy', 0),
                    judgment=judgment,
                    timestamp=datetime.now().isoformat(),
                    roi_name=sig_data.get('roi_name', ''),
                    source=source
                )
                if self.calibration_session:
                    self.calibration_session.signals.append(cal_signal)

        self._pending_signal = None
        self.monitor.receive_calibration_judgment()

        # Update UI
        self._update_calibration_status_bar()
        self._update_calibration_estimates_display()

    def _on_cal_good_click(self):
        self._process_judgment("GOOD", "calibration_phase")
        self.cal_good_btn.config(state=tk.DISABLED)
        self.cal_bad_btn.config(state=tk.DISABLED)
        self.cal_ignore_btn.config(state=tk.DISABLED)
        self.cal_signal_label.config(text="Waiting for signal...")
        self._update_cal_counter()

    def _on_cal_bad_click(self):
        self._process_judgment("BAD", "calibration_phase")
        self.cal_good_btn.config(state=tk.DISABLED)
        self.cal_bad_btn.config(state=tk.DISABLED)
        self.cal_ignore_btn.config(state=tk.DISABLED)
        self.cal_signal_label.config(text="Waiting for signal...")
        self._update_cal_counter()

    def _on_cal_ignore_click(self):
        self._process_judgment("IGNORE", "calibration_phase")
        self.cal_good_btn.config(state=tk.DISABLED)
        self.cal_bad_btn.config(state=tk.DISABLED)
        self.cal_ignore_btn.config(state=tk.DISABLED)
        self.cal_signal_label.config(text="Waiting for signal...")

    def _on_live_good_click(self):
        if self._pending_signal:
            for sig_data in self._pending_signal:
                self.live_calibration_buffer.append({'data': sig_data, 'judgment': 'GOOD'})
            self.live_cal_good_count += 1
            if self.auto_update_cal.get() and self.calibration_estimator:
                self._process_judgment("GOOD", "live_monitoring")
            else:
                self._pending_signal = None
                self.monitor.receive_calibration_judgment()
        if hasattr(self, 'live_good_btn'):
            self.live_good_btn.config(state=tk.DISABLED)
            self.live_bad_btn.config(state=tk.DISABLED)
            self.live_cal_label.config(text=f"Pending: {len(self.live_calibration_buffer)} ({self.live_cal_good_count}G, {self.live_cal_bad_count}B)")

    def _on_live_bad_click(self):
        if self._pending_signal:
            for sig_data in self._pending_signal:
                self.live_calibration_buffer.append({'data': sig_data, 'judgment': 'BAD'})
            self.live_cal_bad_count += 1
            if self.auto_update_cal.get() and self.calibration_estimator:
                self._process_judgment("BAD", "live_monitoring")
            else:
                self._pending_signal = None
                self.monitor.receive_calibration_judgment()
        if hasattr(self, 'live_good_btn'):
            self.live_good_btn.config(state=tk.DISABLED)
            self.live_bad_btn.config(state=tk.DISABLED)
            self.live_cal_label.config(text=f"Pending: {len(self.live_calibration_buffer)} ({self.live_cal_good_count}G, {self.live_cal_bad_count}B)")

    def _update_cal_counter(self):
        if self.calibration_estimator:
            info = self.calibration_estimator.get_status_info()
            g = info['good_count']
            b = info['bad_count']
            need_msg = ""
            if g < 3:
                need_msg = f" (need {3 - g} more Good)"
            elif b < 3:
                need_msg = f" (need {3 - b} more Bad)"
            self.cal_counter_label.config(text=f"Signals: {g} Good, {b} Bad{need_msg}")

    def _update_calibration_estimates_display(self):
        """Update read-only calibration estimates in calibration panel."""
        if not self.calibration_estimator or not self.calibration_mode:
            return
        if not hasattr(self, 'cal_param_labels'):
            return
        estimates = self.calibration_estimator.get_estimates()
        if estimates is None:
            for label in self.cal_param_labels.values():
                label.config(text="--")
            return
        for key, label in self.cal_param_labels.items():
            val = estimates.get(key)
            if val is not None:
                if isinstance(val, float):
                    label.config(text=f"{val:.4f}")
                else:
                    label.config(text=str(val))
            else:
                label.config(text="--")

    def _apply_estimated_params(self):
        """Push estimated params from calibration into AppConfig."""
        if not self.calibration_estimator:
            return
        estimates = self.calibration_estimator.get_estimates()
        if not estimates:
            return
        cfg = self.monitor.app_config
        if 'fft_cutoff_frequency' in estimates:
            cfg.fft_cutoff_frequency = estimates['fft_cutoff_frequency']
        if 'fft_energy_ratio_threshold' in estimates:
            cfg.fft_energy_ratio_threshold = estimates['fft_energy_ratio_threshold']
        if 'lowpass_cutoff' in estimates:
            cfg.lowpass_cutoff = estimates['lowpass_cutoff']
        if 'lowpass_filter_order' in estimates:
            cfg.lowpass_filter_order = int(estimates['lowpass_filter_order'])
        if 'residual_threshold' in estimates:
            cfg.residual_threshold = estimates['residual_threshold']
        if 'exceedance_ratio_threshold' in estimates:
            cfg.exceedance_ratio_threshold = estimates['exceedance_ratio_threshold']
        # Sync UI if in normal mode
        if hasattr(self, 'param_vars') and not self.calibration_mode:
            self._sync_param_ui_from_config()

    def _finish_calibration(self):
        """Finish calibration phase and transition to normal monitoring."""
        if not self.calibration_estimator:
            messagebox.showwarning("No Data", "No calibration data collected.")
            return

        estimates = self.calibration_estimator.get_estimates()
        level = self.calibration_estimator.confidence_level
        info = self.calibration_estimator.get_status_info()

        if estimates is None:
            result = messagebox.askyesnocancel(
                "Insufficient Data",
                f"Not enough data for calibration (have {info['good_count']}G + {info['bad_count']}B, need 3+3).\n\n"
                "Yes = Discard & Use Defaults\n"
                "No = Continue Calibrating")
            if result is True:
                self.calibration_mode = False
                self.monitor.set_calibration_mode(False)
                messagebox.showinfo("Defaults", "Using default parameters.")
            return

        level_names = {1: "Preliminary", 2: "Basic", 3: "Solid", 4: "Robust"}
        msg = (f"Calibration Level {level} ({level_names.get(level, '?')})\n"
               f"Signals: {info['good_count']} Good + {info['bad_count']} Bad\n\n"
               f"Estimated parameters:\n")
        for key, val in estimates.items():
            if not key.endswith('_ci_low') and not key.endswith('_ci_high'):
                msg += f"  {key}: {val:.4f}\n" if isinstance(val, float) else f"  {key}: {val}\n"

        result = messagebox.askyesnocancel(
            "Finish Calibration",
            msg + "\nYes = Accept & Apply\nNo = Continue Calibrating\nCancel = Discard")

        if result is True:
            self._apply_estimated_params()
            self._save_calibration_to_config()
            self.calibration_mode = False
            self.monitor.set_calibration_mode(False)
            messagebox.showinfo("Calibration Applied",
                                "Parameters have been applied and saved.\n"
                                "Switching to normal monitoring mode.")
            # Re-setup GUI would require destroying and rebuilding —
            # instead just keep calibration_mode=False, panel stays
        elif result is None:
            self.calibration_mode = False
            self.calibration_estimator = None
            self.calibration_session = None
            self.monitor.set_calibration_mode(False)

    def _show_live_calibration_save_dialog(self):
        """Show dialog to save/discard live calibration signals after stopping monitoring."""
        if not self.live_calibration_buffer or not self.calibration_estimator:
            return

        old_estimates = self.calibration_estimator.get_estimates() or {}
        old_level = self.calibration_estimator.confidence_level

        # Compute what params would be if we merge live signals
        temp_estimator = HybridCalibrationEstimator()
        # Replay existing signals
        for entry in self.calibration_estimator.all_signals:
            temp_estimator.add_signal(entry['data'], entry['judgment'])
        # Add live signals
        for entry in self.live_calibration_buffer:
            temp_estimator.add_signal(entry['data'], entry['judgment'])
        new_estimates = temp_estimator.get_estimates() or {}
        new_level = temp_estimator.confidence_level

        dialog = LiveCalibrationSaveDialog(
            self.root,
            live_signal_count=len(self.live_calibration_buffer),
            good_count=self.live_cal_good_count,
            bad_count=self.live_cal_bad_count,
            old_params=old_estimates,
            new_params=new_estimates,
            old_level=old_level,
            new_level=new_level
        )

        if dialog.result == "SAVE":
            # Merge live signals into main estimator
            for entry in self.live_calibration_buffer:
                if not self.auto_update_cal.get():
                    self.calibration_estimator.add_signal(entry['data'], entry['judgment'])
                    if self.calibration_session:
                        cal_signal = CalibrationSignal(
                            signal_physical=entry['data'].get('signal_physical', np.array([])),
                            fft_freqs=entry['data'].get('fft_freqs', np.array([])),
                            fft_mags=entry['data'].get('fft_mags', np.array([])),
                            residual_physical=entry['data'].get('residual_physical', np.array([])),
                            energy_ratio=entry['data'].get('energy_ratio', 0),
                            exceedance_ratio=entry['data'].get('exceedance_ratio', 0),
                            exceedance_count=entry['data'].get('exceedance_count', 0),
                            total_energy=entry['data'].get('total_energy', 0),
                            high_freq_energy=entry['data'].get('high_freq_energy', 0),
                            judgment=entry['judgment'],
                            timestamp=datetime.now().isoformat(),
                            roi_name=entry['data'].get('roi_name', ''),
                            source="live_monitoring"
                        )
                        self.calibration_session.signals.append(cal_signal)
            self._apply_estimated_params()
            self._save_calibration_to_config()
            self._update_calibration_status_bar()
            logger.info("Live calibration signals saved and parameters updated.")

        # Clear buffer regardless
        self.live_calibration_buffer.clear()
        self.live_cal_good_count = 0
        self.live_cal_bad_count = 0

    def _save_calibration_to_config(self):
        """Save calibration data to the config JSON file."""
        config_path = self.config_path.get()
        if not config_path or not os.path.exists(config_path):
            return
        try:
            with open(config_path, 'r') as f:
                config_data = json.load(f)

            # Save calibration section
            cal_data = {
                'created_at': self.calibration_session.created_at if self.calibration_session else datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'method': 'hybrid',
                'confidence_level': self.calibration_estimator.confidence_level if self.calibration_estimator else 0,
                'total_signals': self.calibration_estimator.total_signals if self.calibration_estimator else 0,
                'good_count': self.calibration_estimator.good_count if self.calibration_estimator else 0,
                'bad_count': self.calibration_estimator.bad_count if self.calibration_estimator else 0,
                'signals': []
            }
            if self.calibration_session:
                for sig in self.calibration_session.signals:
                    cal_data['signals'].append({
                        'judgment': sig.judgment,
                        'energy_ratio': float(sig.energy_ratio),
                        'exceedance_ratio': float(sig.exceedance_ratio),
                        'exceedance_count': int(sig.exceedance_count),
                        'total_energy': float(sig.total_energy),
                        'high_freq_energy': float(sig.high_freq_energy),
                        'timestamp': sig.timestamp,
                        'source': sig.source,
                        'roi_name': sig.roi_name,
                    })
            config_data['_calibration'] = cal_data

            # Also update _metadata with calibrated params
            estimates = self.calibration_estimator.get_estimates() if self.calibration_estimator else None
            if estimates and '_metadata' in config_data:
                for key in ('fft_cutoff_frequency', 'fft_energy_ratio_threshold',
                            'lowpass_cutoff', 'lowpass_filter_order',
                            'residual_threshold', 'exceedance_ratio_threshold'):
                    if key in estimates:
                        config_data['_metadata'][key] = estimates[key]

            with open(config_path, 'w') as f:
                json.dump(config_data, f, indent=4)
            logger.info(f"Calibration data saved to {config_path}")

        except Exception as e:
            logger.error(f"Failed to save calibration: {e}")
            messagebox.showerror("Save Error", f"Failed to save calibration:\n{e}")

    def _load_calibration_from_config(self):
        """Load calibration data from config JSON if present."""
        config_path = self.config_path.get()
        if not config_path or not os.path.exists(config_path):
            return
        try:
            with open(config_path, 'r') as f:
                config_data = json.load(f)

            cal_data = config_data.get('_calibration')
            if not cal_data or not cal_data.get('signals'):
                return

            self.calibration_estimator = HybridCalibrationEstimator()
            self.calibration_session = CalibrationSession(
                config_name=os.path.basename(config_path),
                created_at=cal_data.get('created_at', ''),
                last_updated=cal_data.get('last_updated', '')
            )

            for sig_entry in cal_data['signals']:
                judgment = sig_entry.get('judgment', 'IGNORE')
                raw_data = {
                    'energy_ratio': sig_entry.get('energy_ratio', 0),
                    'exceedance_ratio': sig_entry.get('exceedance_ratio', 0),
                    'exceedance_count': sig_entry.get('exceedance_count', 0),
                    'total_energy': sig_entry.get('total_energy', 0),
                    'high_freq_energy': sig_entry.get('high_freq_energy', 0),
                    'signal_physical': np.array([]),
                    'fft_freqs': np.array([]),
                    'fft_mags': np.array([]),
                    'residual_physical': np.array([]),
                }
                self.calibration_estimator.add_signal(raw_data, judgment)

                cal_signal = CalibrationSignal(
                    signal_physical=np.array([]),
                    fft_freqs=np.array([]),
                    fft_mags=np.array([]),
                    residual_physical=np.array([]),
                    energy_ratio=sig_entry.get('energy_ratio', 0),
                    exceedance_ratio=sig_entry.get('exceedance_ratio', 0),
                    exceedance_count=sig_entry.get('exceedance_count', 0),
                    total_energy=sig_entry.get('total_energy', 0),
                    high_freq_energy=sig_entry.get('high_freq_energy', 0),
                    judgment=judgment,
                    timestamp=sig_entry.get('timestamp', ''),
                    roi_name=sig_entry.get('roi_name', ''),
                    source=sig_entry.get('source', 'calibration_phase')
                )
                self.calibration_session.signals.append(cal_signal)

            logger.info(f"Loaded calibration: {self.calibration_estimator.total_signals} signals, "
                        f"Level {self.calibration_estimator.confidence_level}")

        except Exception as e:
            logger.warning(f"Failed to load calibration data: {e}")

    def _load_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], initialdir="configs", title="Select Config")
        if not path:
            return
        self.config_path.set(path)
        self.monitor.update_config(path)
        try:
            self.sample_frequency.set(round(1.0 / self.monitor.app_config.screenshot_interval, 2))
        except (ZeroDivisionError, TypeError, AttributeError):
            self.sample_frequency.set(4.0)
        if self.is_overlay_on.get():
            self._toggle_overlay()
            self._toggle_overlay()
        self.status_label.config(text=f"Loaded: {os.path.basename(path)}")
        self._update_manual_points_state()
        self._sync_param_ui_from_config()  # Sync parameter UI

    def _sync_param_ui_from_config(self):
        """Sync parameter UI variables from current monitor config."""
        cfg = self.monitor.app_config
        self.param_vars['fft_cutoff_frequency'].set(cfg.fft_cutoff_frequency)
        self.param_vars['fft_energy_ratio_threshold'].set(cfg.fft_energy_ratio_threshold)
        self.param_vars['lowpass_cutoff'].set(cfg.lowpass_cutoff)
        self.param_vars['lowpass_filter_order'].set(cfg.lowpass_filter_order)
        self.param_vars['residual_threshold'].set(cfg.residual_threshold)
        self.param_vars['exceedance_ratio_threshold'].set(cfg.exceedance_ratio_threshold)
    
    def _launch_config_tool(self):
        self.root.iconify()

        # Pass current config path if one is loaded
        current_path = self.config_path.get() if os.path.exists(self.config_path.get()) else None

        config_tool = ConfigToolWindow(self.root, self.root, preload_config_path=current_path)
        config_tool.grab_set()
        config_tool.wait_window()

        # If config was saved, reload it
        if config_tool.saved_config_path:
            self.config_path.set(config_tool.saved_config_path)
            self.monitor.update_config(config_tool.saved_config_path)
            self._update_manual_points_state()
            self._sync_param_ui_from_config()
            # Update sample frequency from loaded config
            try:
                self.sample_frequency.set(round(1.0 / self.monitor.app_config.screenshot_interval, 2))
            except (ZeroDivisionError, TypeError):
                pass

        self.root.deiconify()
        
    def _toggle_monitoring(self):
        if self.is_monitoring.get():
            # Check for pending live calibration signals before stopping
            if self.live_calibration_buffer and not self.calibration_mode:
                self._show_live_calibration_save_dialog()

            self.monitor.stop()
            self.is_monitoring.set(False)
            self.start_stop_button.config(text="Start Monitoring")
            self.status_label.config(text="Stopped.")
            self._reset_feedback_ui()
            self.load_button.config(state=tk.NORMAL)
            self.edit_button.config(state=tk.NORMAL)
        else:
            if not os.path.exists(self.config_path.get()):
                return messagebox.showerror("Error", "Config file not found.")
            try:
                freq = self.sample_frequency.get()
                if freq <= 0:
                    return messagebox.showerror("Error", "Sample frequency must be positive.")
            except tk.TclError:
                return messagebox.showerror("Error", "Invalid sample frequency.")

            self.monitor.app_config.screenshot_interval = 1.0 / self.sample_frequency.get()
            self.monitor.set_audio_feedback(self.audio_feedback_on.get())

            # Set calibration mode on monitor
            if self.calibration_mode or self.calibration_estimator:
                self.monitor.set_calibration_mode(True, self._on_calibration_signal)

            img_log_opts = ImageLogOptions(
                self.log_opt_screenshot.get(),
                self.log_opt_color_filter.get(),
                self.log_opt_signal_plot.get(),
                self.log_opt_fft_plot.get(),
                self.log_opt_lowpass_plot.get(),
                self.log_opt_residual_plot.get(),
                self.log_opt_summary_chart.get(),
                self.log_opt_ocr_images.get()
            )
            data_log_opts = DataLogOptions(self.log_to_unv.get())
            verbose_log_opts = VerboseLogOptions(
                self.vlog_opt_config.get(), self.vlog_opt_mask.get(),
                self.vlog_opt_ocr.get(), self.vlog_opt_fft.get(),
                self.vlog_opt_lowpass.get(), self.vlog_opt_classification.get(),
                self.vlog_opt_filesave.get()
            )
            manual_points = PointsInfo(**{k: v.get() for k, v in self.manual_points_vars.items()})

            if self.monitor.start(self.verbose_logging_on.get(), self.image_logging_on.get(),
                                  img_log_opts, data_log_opts, verbose_log_opts, manual_points):
                self.is_monitoring.set(True)
                self.start_stop_button.config(text="Stop Monitoring")
                mode_text = "Calibration active..." if self.calibration_mode else "Monitoring active..."
                self.status_label.config(text=mode_text)
                self.load_button.config(state=tk.DISABLED)
                self.edit_button.config(state=tk.DISABLED)

    def _toggle_overlay(self):
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.destroy()
            self.overlay = None
        if self.is_overlay_on.get():
            path = self.config_path.get()
            if not os.path.exists(path):
                messagebox.showerror("Error", "Config file not found.")
                self.is_overlay_on.set(False)
                return
            try:
                with open(path, 'r') as f:
                    json.load(f)
            except json.JSONDecodeError:
                messagebox.showerror("Error", "Invalid config file format.")
                self.is_overlay_on.set(False)
                return
            self.overlay = RegionOverlay(self.root, path)
            
    def _on_closing(self):
        if self.is_monitoring.get():
            self.monitor.stop()
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.destroy()
        self.root.destroy()


# --- 7. APPLICATION ENTRY POINT ---
if __name__ == "__main__":
    import sys

    # Create hidden root for startup dialog
    temp_root = tk.Tk()
    temp_root.withdraw()

    # Ensure configs directory exists
    if not os.path.exists("configs"):
        os.makedirs("configs")

    # Show startup dialog
    startup = StartupDialog(temp_root)
    startup_result = startup.result  # Store result before destroying

    if startup_result is None:
        # User cancelled - exit application
        temp_root.destroy()
        sys.exit(0)
    elif startup_result == "NEW_CALIBRATION":
        # Destroy temp root, create new root for config tool
        temp_root.destroy()

        main_root = tk.Tk()
        main_root.withdraw()  # Hide main GUI initially

        # Open config tool for new calibration
        config_tool = ConfigToolWindow(main_root, main_root, is_new_calibration=True)
        config_tool.wait_window()

        # After config tool closes, check if a config was saved
        saved_path = config_tool.saved_config_path  # Store before any potential issues

        if saved_path and os.path.exists(saved_path):
            # Show CalibrationChoiceDialog
            cal_choice = CalibrationChoiceDialog(main_root, os.path.basename(saved_path))
            cal_mode = cal_choice.result == "CALIBRATE"
            if cal_choice.result is None:
                main_root.destroy()
                sys.exit(0)
            main_root.deiconify()
            app = MonitorControlGUI(main_root, config_path=saved_path, calibration_mode=cal_mode)
            main_root.mainloop()
        else:
            # No config was saved - exit
            main_root.destroy()
            sys.exit(0)
    else:
        # Config file selected
        config_path = startup_result  # Store the path

        # Show CalibrationChoiceDialog
        cal_choice = CalibrationChoiceDialog(temp_root, os.path.basename(config_path))
        cal_mode = cal_choice.result == "CALIBRATE"
        if cal_choice.result is None:
            temp_root.destroy()
            sys.exit(0)
        temp_root.destroy()

        main_root = tk.Tk()
        app = MonitorControlGUI(main_root, config_path=config_path, calibration_mode=cal_mode)
        main_root.mainloop()