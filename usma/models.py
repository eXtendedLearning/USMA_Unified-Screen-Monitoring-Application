"""USMA data models, configuration structures, and config file loader."""

import json
import os
import logging
from datetime import datetime
import numpy as np
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Tuple, Any, get_type_hints
from scipy.signal import butter, filtfilt

logger = logging.getLogger(__name__)

# --- Version Configuration ---
APP_VERSION = "0.12.3"

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
    VERSION: str = field(default_factory=lambda: APP_VERSION)
    run: str = "Run 1"
    hammer_point: str = "P1"
    hammer_dir: str = "-Z"
    response_point: str = "P1"
    response_dir: str = "-Z"

@dataclass
class MonitorEvent:
    """Thread-safe event passed from monitoring thread to GUI thread via queue."""
    event_type: str  # "frame_update", "hit_detected", "error", "stopped"
    frame_result: Optional['FrameAnalysisResult'] = None
    lightweight_data: Optional['LightweightHitData'] = None
    run_history: Optional[Dict] = None
    error_message: Optional[str] = None

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
    y_scale_type: str = field(default="linear")  # "linear", "dB", "log", "ln" — metadata for display
    overlay_color: str = field(default="")  # Custom overlay color (hex). Empty = use type default.

@dataclass(slots=True)
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
    max_abs_residual: float = 0.0
    dynamic_threshold: float = 0.0
    y_axis_unit: str = "g/N"

@dataclass(slots=True)
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
    signal_type: str = "frf"  # "frf", "psd", or "coherence"
    max_abs_residual: float = 0.0
    dynamic_threshold: float = 0.0

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
    max_abs_residual: float = 0.0
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

# ---------------------------------------------------------------------------
# v0.9.1 CALIBRATION ENGINE CLASSES
# ---------------------------------------------------------------------------

class PercentileBoundaryEstimator:
    """
    Level 1+ estimator. Finds threshold boundaries between Good/Bad distributions.

    For threshold params: midpoint if cleanly separated, else 95th percentile of Good.
    For filter params: sweep candidates and pick best Good/Bad separation.
    """

    def __init__(self):
        self.good_signals: List[dict] = []
        self.bad_signals: List[dict] = []

    def add_signal(self, signal_data: dict, judgment: str):
        """Add a classified signal. signal_data must contain: energy_ratio,
        exceedance_ratio, exceedance_count, total_energy, high_freq_energy,
        fft_freqs (np.ndarray), fft_mags (np.ndarray),
        residual_physical (np.ndarray), signal_physical (np.ndarray)."""
        if judgment == "GOOD":
            self.good_signals.append(signal_data)
        elif judgment == "BAD":
            self.bad_signals.append(signal_data)

    @property
    def can_estimate(self) -> bool:
        return len(self.good_signals) >= 3 and len(self.bad_signals) >= 3

    def estimate(self) -> Optional[dict]:
        """Return estimated parameters or None if insufficient data."""
        if not self.can_estimate:
            return None

        params = {}

        # --- 1. FFT Energy Ratio Threshold ---
        good_ratios = [s['energy_ratio'] for s in self.good_signals]
        bad_ratios = [s['energy_ratio'] for s in self.bad_signals]
        params['fft_energy_ratio_threshold'] = self._find_boundary(good_ratios, bad_ratios)

        # --- 2. Exceedance Ratio Threshold ---
        good_exc = [s['exceedance_ratio'] for s in self.good_signals]
        bad_exc = [s['exceedance_ratio'] for s in self.bad_signals]
        params['exceedance_ratio_threshold'] = self._find_boundary(good_exc, bad_exc)

        # --- 3. Relative Residual Ratio ---
        # Sweep candidate ratios to find best Good/Bad separation on exceedance_ratio.
        params['relative_residual_ratio'] = self._sweep_relative_residual_ratio()

        # --- 4. Filter Parameter Sweep ---
        params.update(self._sweep_filter_params())

        return params

    def _sweep_relative_residual_ratio(self) -> float:
        """Sweep relative_residual_ratio to find best Good/Bad separation.

        For each candidate ratio, recompute exceedance_ratio for all signals
        and pick the ratio that maximises mean separation (bad_mean - good_mean).
        """
        all_signals = self.good_signals + self.bad_signals
        labels = np.array([1] * len(self.good_signals) + [0] * len(self.bad_signals))

        best_ratio = 0.10
        best_score = -np.inf

        for candidate in np.arange(0.02, 0.50, 0.01):
            exc_ratios = []
            for sig in all_signals:
                residual = np.array(sig['residual_physical'])
                max_abs = float(np.max(np.abs(residual))) if len(residual) > 0 else 0.0
                if max_abs < 1e-9:
                    exc_ratios.append(0.0)
                    continue
                dynamic_thr = candidate * max_abs
                exc = np.sum(np.abs(residual) > dynamic_thr) / len(residual)
                exc_ratios.append(exc)

            exc_ratios = np.array(exc_ratios)
            good_mean = np.mean(exc_ratios[labels == 1])
            bad_mean = np.mean(exc_ratios[labels == 0])
            separation = bad_mean - good_mean
            if separation > best_score:
                best_score = separation
                best_ratio = float(candidate)

        return best_ratio

    def _find_boundary(self, good_values: list, bad_values: list) -> float:
        """Find threshold separating Good (low) from Bad (high) populations.

        If cleanly separated: midpoint between max(Good) and min(Bad).
        If overlapping: 95th percentile of Good (allows 5% false positive on Good).
        """
        max_good = max(good_values)
        min_bad = min(bad_values)

        if min_bad > max_good:
            # Clean separation — midpoint
            return (max_good + min_bad) / 2.0
        else:
            # Overlapping — use 95th percentile of Good distribution
            return float(np.percentile(good_values, 95))

    def _sweep_filter_params(self) -> dict:
        """Sweep FFT cutoff and lowpass cutoff to find best separation.

        For each candidate cutoff value, recompute the energy ratio for
        all signals and measure the distance between Good and Bad means.
        Pick the cutoff that maximises this distance.
        """
        best_fft_cutoff = 0.07  # default fallback
        best_fft_score = -np.inf

        all_signals = self.good_signals + self.bad_signals
        labels = np.array([1]*len(self.good_signals) + [0]*len(self.bad_signals))

        # Sweep FFT cutoff frequency
        for fft_cut in np.arange(0.02, 0.25, 0.005):
            ratios = []
            for sig in all_signals:
                xf = sig['fft_freqs']
                mags = sig['fft_mags']
                total = np.sum(mags ** 2)
                if total < 1e-9:
                    ratios.append(0.0)
                    continue
                cutoff_idx = np.where(xf >= fft_cut)[0]
                if cutoff_idx.size > 0:
                    hf = np.sum(mags[cutoff_idx[0]:] ** 2)
                    ratios.append(hf / total)
                else:
                    ratios.append(0.0)

            ratios = np.array(ratios)
            good_mean = np.mean(ratios[labels == 1])
            bad_mean = np.mean(ratios[labels == 0])
            # We want bad_mean > good_mean (larger gap = better separation)
            separation = bad_mean - good_mean
            if separation > best_fft_score:
                best_fft_score = separation
                best_fft_cutoff = float(fft_cut)

        # Sweep lowpass cutoff (coarser grid — recomputing residuals is expensive)
        best_lp_cutoff = 0.07
        best_lp_score = -np.inf

        for lp_cut in np.arange(0.03, 0.20, 0.01):
            exc_ratios = []
            for sig in all_signals:
                # Prefer pixel-space signal; fall back to physical
                signal = sig.get('signal_vector', sig.get('signal_physical'))
                if signal is None or len(signal) < 15:
                    exc_ratios.append(0.0)
                    continue
                try:
                    detrended = signal - np.mean(signal)
                    sos = butter(7, min(lp_cut, 0.99), btype='low', output='sos')
                    from scipy.signal import sosfiltfilt as _sosfiltfilt
                    filtered = _sosfiltfilt(sos, detrended)
                    residual = detrended - filtered
                    max_abs = np.max(np.abs(residual))
                    test_thr = 0.10 * max_abs if max_abs > 1e-9 else 0.0
                    exc = np.sum(np.abs(residual) > test_thr) / len(residual)
                    exc_ratios.append(exc)
                except Exception:
                    exc_ratios.append(0.0)

            exc_ratios = np.array(exc_ratios)
            good_mean = np.mean(exc_ratios[labels == 1])
            bad_mean = np.mean(exc_ratios[labels == 0])
            separation = bad_mean - good_mean
            if separation > best_lp_score:
                best_lp_score = separation
                best_lp_cutoff = float(lp_cut)

        return {
            'fft_cutoff_frequency': best_fft_cutoff,
            'lowpass_cutoff': best_lp_cutoff,
        }


class BayesianThresholdEstimator:
    """
    Level 2+ estimator. Maintains a posterior distribution over each
    threshold parameter using grid-based Bayesian inference.

    Prior: Uniform over a plausible range.
    Likelihood: Sigmoid — P(grid_value is correct | observation).
    Point estimate: MAP (maximum a posteriori).
    Also computes 95% credible interval for confidence display.
    """

    PARAM_GRIDS = {
        'fft_energy_ratio_threshold': (0.0005, 0.15, 300),
        'exceedance_ratio_threshold': (0.05, 0.99, 300),
        'relative_residual_ratio': (0.02, 0.50, 300),
    }

    def __init__(self):
        self.grids = {}
        self.posteriors = {}
        for name, (lo, hi, n) in self.PARAM_GRIDS.items():
            self.grids[name] = np.linspace(lo, hi, n)
            self.posteriors[name] = np.ones(n) / n  # uniform prior
        self._signal_count = 0

    def update(self, signal_data: dict, judgment: str):
        """Bayesian update with one new observation."""
        if judgment not in ("GOOD", "BAD"):
            return

        self._signal_count += 1

        metric_map = {
            'fft_energy_ratio_threshold': signal_data.get('energy_ratio'),
            'exceedance_ratio_threshold': signal_data.get('exceedance_ratio'),
            'relative_residual_ratio': signal_data.get('relative_residual_ratio'),
        }

        for param_name, grid in self.grids.items():
            observed = metric_map.get(param_name)
            if observed is None:
                continue

            grid_range = grid[-1] - grid[0]
            steepness = 20.0 / grid_range

            if judgment == "GOOD":
                # Good signal: threshold should be ABOVE the observed value
                exponent = np.clip(-steepness * (grid - observed), -500, 500)
                likelihood = 1.0 / (1.0 + np.exp(exponent))
            else:
                # Bad signal: threshold should be BELOW the observed value
                exponent = np.clip(-steepness * (observed - grid), -500, 500)
                likelihood = 1.0 / (1.0 + np.exp(exponent))

            # Avoid zeros
            likelihood = np.clip(likelihood, 1e-10, 1.0)

            # Bayesian update: posterior ∝ prior × likelihood
            self.posteriors[param_name] *= likelihood
            total = np.sum(self.posteriors[param_name])
            if total > 0:
                self.posteriors[param_name] /= total
            else:
                # Degenerate — reset to uniform
                n = len(grid)
                self.posteriors[param_name] = np.ones(n) / n

    @property
    def can_estimate(self) -> bool:
        return self._signal_count >= 6

    def estimate(self) -> Optional[dict]:
        """Return MAP estimates and 95% credible intervals."""
        if not self.can_estimate:
            return None

        result = {}
        for name, grid in self.grids.items():
            posterior = self.posteriors[name]

            # MAP estimate
            map_idx = np.argmax(posterior)
            result[name] = float(grid[map_idx])

            # 95% credible interval
            cumulative = np.cumsum(posterior)
            ci_low_idx = np.searchsorted(cumulative, 0.025)
            ci_high_idx = np.searchsorted(cumulative, 0.975)
            result[f'{name}_ci_low'] = float(grid[min(ci_low_idx, len(grid)-1)])
            result[f'{name}_ci_high'] = float(grid[min(ci_high_idx, len(grid)-1)])
            result[f'{name}_ci_width'] = result[f'{name}_ci_high'] - result[f'{name}_ci_low']

        return result


class ROCYoudenEstimator:
    """
    Level 3+ estimator. For each threshold metric, sweeps candidate values
    and picks the one that maximises Youden's J = TPR - FPR.

    Convention: 'Good' signals should have metric values BELOW threshold,
    'Bad' signals should have metric values ABOVE threshold.
    """

    def __init__(self):
        self.good_signals: List[dict] = []
        self.bad_signals: List[dict] = []

    def add_signal(self, signal_data: dict, judgment: str):
        if judgment == "GOOD":
            self.good_signals.append(signal_data)
        elif judgment == "BAD":
            self.bad_signals.append(signal_data)

    @property
    def can_estimate(self) -> bool:
        return len(self.good_signals) >= 5 and len(self.bad_signals) >= 5

    def estimate(self) -> Optional[dict]:
        if not self.can_estimate:
            return None

        result = {}

        # Energy ratio
        good_er = [s['energy_ratio'] for s in self.good_signals]
        bad_er = [s['energy_ratio'] for s in self.bad_signals]
        thr, j_stat, auc = self._youden_threshold(good_er, bad_er)
        result['fft_energy_ratio_threshold'] = thr
        result['fft_energy_ratio_threshold_j'] = j_stat
        result['fft_energy_ratio_threshold_auc'] = auc

        # Exceedance ratio
        good_exc = [s['exceedance_ratio'] for s in self.good_signals]
        bad_exc = [s['exceedance_ratio'] for s in self.bad_signals]
        thr, j_stat, auc = self._youden_threshold(good_exc, bad_exc)
        result['exceedance_ratio_threshold'] = thr
        result['exceedance_ratio_threshold_j'] = j_stat
        result['exceedance_ratio_threshold_auc'] = auc

        # Relative residual ratio — sweep candidate ratios, find Youden-optimal
        candidate_ratios = np.linspace(0.02, 0.50, 100)
        best_ratio = 0.10
        best_j = -1.0
        best_auc = 0.5

        for ratio in candidate_ratios:
            good_exc = []
            for s in self.good_signals:
                res = np.array(s['residual_physical'])
                mx = float(np.max(np.abs(res))) if len(res) > 0 else 0.0
                thr_val = ratio * mx if mx > 1e-9 else 0.0
                good_exc.append(np.sum(np.abs(res) > thr_val) / len(res) if len(res) > 0 else 0.0)

            bad_exc = []
            for s in self.bad_signals:
                res = np.array(s['residual_physical'])
                mx = float(np.max(np.abs(res))) if len(res) > 0 else 0.0
                thr_val = ratio * mx if mx > 1e-9 else 0.0
                bad_exc.append(np.sum(np.abs(res) > thr_val) / len(res) if len(res) > 0 else 0.0)

            _thr, j_stat, auc = self._youden_threshold(good_exc, bad_exc)
            if j_stat > best_j:
                best_j = j_stat
                best_ratio = float(ratio)
                best_auc = auc

        result['relative_residual_ratio'] = best_ratio
        result['relative_residual_ratio_j'] = best_j
        result['relative_residual_ratio_auc'] = best_auc

        return result

    def _youden_threshold(self, good_values: list, bad_values: list
                          ) -> Tuple[float, float, float]:
        """Find threshold maximising Youden's J. Returns (threshold, J, AUC)."""
        all_vals = sorted(set(good_values + bad_values))
        if len(all_vals) < 2:
            return all_vals[0] if all_vals else 0.0, 0.0, 0.5

        margin = (all_vals[-1] - all_vals[0]) * 0.05
        candidates = [all_vals[0] - margin] + all_vals + [all_vals[-1] + margin]

        n_good = len(good_values)
        n_bad = len(bad_values)

        best_j = -1.0
        best_thr = candidates[len(candidates) // 2]

        roc_points = []

        for thr in candidates:
            tp = sum(1 for v in good_values if v <= thr)
            fp = sum(1 for v in bad_values if v <= thr)

            tpr = tp / n_good if n_good > 0 else 0.0
            fpr = fp / n_bad if n_bad > 0 else 0.0

            roc_points.append((fpr, tpr))

            j = tpr - fpr
            if j > best_j:
                best_j = j
                best_thr = thr

        # AUC via trapezoidal rule on sorted ROC points
        roc_points.sort(key=lambda p: (p[0], p[1]))
        if len(roc_points) >= 2:
            fprs = [p[0] for p in roc_points]
            tprs = [p[1] for p in roc_points]
            auc = float(np.trapz(tprs, fprs))
            auc = max(0.0, min(1.0, abs(auc)))
        else:
            auc = 0.5

        return float(best_thr), float(best_j), auc


class HybridCalibrationEngine:
    """
    Orchestrates the three sub-estimators and merges their results based
    on the current confidence level.
    """

    def __init__(self):
        self.percentile = PercentileBoundaryEstimator()
        self.bayesian = BayesianThresholdEstimator()
        self.roc = ROCYoudenEstimator()
        self.good_count: int = 0
        self.bad_count: int = 0
        self._all_signals: List[dict] = []

    def clear_signals(self):
        """Clear the stored signal pool while preserving computed thresholds.

        Called after ``_finish_calibration()`` so that subsequent live-calibration
        similarity checks do not compare new signals against the already-committed
        calibration population. Sub-estimators keep their fitted state — only the
        raw signal pool and Good/Bad counters are purged.
        """
        self._all_signals.clear()
        self.good_count = 0
        self.bad_count = 0

    def add_signal(self, signal_data: dict, judgment: str):
        """Feed a classified signal to all sub-estimators."""
        if judgment == "GOOD":
            self.good_count += 1
        elif judgment == "BAD":
            self.bad_count += 1
        else:
            return  # IGNORE — don't feed to estimators

        # Store a serialisable copy for persistence
        stored = {k: v for k, v in signal_data.items()
                  if not isinstance(v, np.ndarray)}
        for k, v in signal_data.items():
            if isinstance(v, np.ndarray):
                stored[k] = v.tolist()
        stored['judgment'] = judgment
        stored['timestamp'] = datetime.now().isoformat()
        self._all_signals.append(stored)

        # Feed to all estimators
        self.percentile.add_signal(signal_data, judgment)
        self.bayesian.update(signal_data, judgment)
        self.roc.add_signal(signal_data, judgment)

    @property
    def total_signals(self) -> int:
        return self.good_count + self.bad_count

    @property
    def meets_minimum(self) -> bool:
        return self.good_count >= 3 and self.bad_count >= 3

    @property
    def can_estimate(self) -> bool:
        return self.meets_minimum

    @property
    def confidence_level(self) -> int:
        """0-4 confidence scale."""
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
        """Return the best merged parameter estimates for the current level."""
        if not self.can_estimate:
            return None

        level = self.confidence_level

        # Level 1: Percentile only
        if level == 1:
            return self.percentile.estimate()

        # Level 2: Bayesian primary, Percentile fallback
        if level == 2:
            bayes = self.bayesian.estimate()
            perc = self.percentile.estimate()
            if bayes and perc:
                return self._merge_two(perc, bayes, bayes_weight=0.6)
            return perc or bayes

        # Level 3-4: All three, cross-validated
        perc = self.percentile.estimate()
        bayes = self.bayesian.estimate()
        roc = self.roc.estimate()
        return self._merge_all(perc, bayes, roc, level)

    def _merge_two(self, perc: Dict[str, Any], bayes: Dict[str, Any], bayes_weight: float = 0.6) -> Dict[str, Any]:
        """Weighted average of Percentile and Bayesian estimates."""
        merged: Dict[str, Any] = {}
        pw = 1.0 - bayes_weight

        threshold_params = [
            'fft_energy_ratio_threshold',
            'exceedance_ratio_threshold',
            'relative_residual_ratio'
        ]

        for key in threshold_params:
            p_val = perc.get(key)
            b_val = bayes.get(key)
            if p_val is not None and b_val is not None:
                merged[key] = pw * p_val + bayes_weight * b_val
            elif p_val is not None:
                merged[key] = p_val
            elif b_val is not None:
                merged[key] = b_val

        # Filter params come from percentile sweep only
        for key in ('fft_cutoff_frequency', 'lowpass_cutoff'):
            if key in perc:
                merged[key] = perc[key]

        # Pass through Bayesian CI metadata
        for key, val in bayes.items():
            if '_ci_' in key or '_width' in key:
                merged[key] = val

        return merged

    def _merge_all(self, perc: Optional[Dict[str, Any]], bayes: Optional[Dict[str, Any]],
                   roc: Optional[Dict[str, Any]], level: int) -> Dict[str, Any]:
        """Merge all three estimators with cross-validation."""
        merged: Dict[str, Any] = {}
        agreement_pct = 0.15 if level >= 4 else 0.20

        threshold_params = [
            'fft_energy_ratio_threshold',
            'exceedance_ratio_threshold',
            'relative_residual_ratio'
        ]

        p_dict = perc if perc is not None else {}
        b_dict = bayes if bayes is not None else {}
        r_dict = roc if roc is not None else {}

        for key in threshold_params:
            estimates = []
            sources = []

            if key in p_dict and p_dict[key] is not None:
                estimates.append(p_dict[key])
                sources.append('percentile')
            if key in b_dict and b_dict[key] is not None:
                estimates.append(b_dict[key])
                sources.append('bayesian')
            if key in r_dict and r_dict[key] is not None:
                estimates.append(r_dict[key])
                sources.append('roc')

            if not estimates:
                continue

            if len(estimates) == 1:
                merged[key] = estimates[0]
            elif len(estimates) == 2:
                merged[key] = float(np.mean(estimates))
            else:
                # Three estimates — check agreement
                mean_val = np.mean(estimates)
                if mean_val > 0:
                    deviations = [abs(e - mean_val) / mean_val for e in estimates]
                    if all(d < agreement_pct for d in deviations):
                        merged[key] = float(mean_val)
                    else:
                        outlier_idx = int(np.argmax(deviations))
                        kept = [e for i, e in enumerate(estimates) if i != outlier_idx]
                        merged[key] = float(np.mean(kept))
                        logger.info(
                            f"[CAL] {key}: dropped {sources[outlier_idx]} estimate "
                            f"({estimates[outlier_idx]:.6f}) as outlier. "
                            f"Using mean of remaining: {merged[key]:.6f}"
                        )
                else:
                    merged[key] = float(np.mean(estimates))

        # Filter params from percentile
        for key in ('fft_cutoff_frequency', 'lowpass_cutoff'):
            if key in p_dict:
                merged[key] = p_dict[key]

        # Metadata from Bayesian and ROC
        for key, val in b_dict.items():
            if '_ci_' in key or '_width' in key:
                merged[key] = val
        for key, val in r_dict.items():
            if '_j' in key or '_auc' in key:
                merged[key] = val

        return merged

    def get_distribution_data(self) -> dict:
        """Return Good/Bad feature values for diagnostic plotting."""
        good_er = [s['energy_ratio'] for s in self.percentile.good_signals]
        bad_er = [s['energy_ratio'] for s in self.percentile.bad_signals]
        good_exc = [s['exceedance_ratio'] for s in self.percentile.good_signals]
        bad_exc = [s['exceedance_ratio'] for s in self.percentile.bad_signals]
        good_res = [float(s['max_abs_residual'])
                    for s in self.percentile.good_signals
                    if 'max_abs_residual' in s]
        bad_res = [float(s['max_abs_residual'])
                   for s in self.percentile.bad_signals
                   if 'max_abs_residual' in s]
        return {
            'fft_energy_ratio': {'good': good_er, 'bad': bad_er},
            'exceedance_ratio': {'good': good_exc, 'bad': bad_exc},
            'relative_residual_ratio': {'good': good_res, 'bad': bad_res},
        }

    def get_roc_data(self) -> Optional[dict]:
        """Return ROC curve data for each parameter (Level 3+)."""
        if not self.roc.can_estimate:
            return None
        result = {}
        params = [
            ('fft_energy_ratio', 'energy_ratio'),
            ('exceedance_ratio', 'exceedance_ratio'),
            ('relative_residual_ratio', 'max_abs_residual'),
        ]
        for label, key in params:
            good_vals = [s[key] for s in self.roc.good_signals if key in s]
            bad_vals = [s[key] for s in self.roc.bad_signals if key in s]
            if not good_vals or not bad_vals:
                continue
            all_vals = sorted(set(good_vals + bad_vals))
            if len(all_vals) < 2:
                continue
            margin = (all_vals[-1] - all_vals[0]) * 0.05
            candidates = [all_vals[0] - margin] + all_vals + [all_vals[-1] + margin]
            n_good, n_bad = len(good_vals), len(bad_vals)
            fprs, tprs = [], []
            best_j, best_thr = -1.0, 0.0
            for thr in candidates:
                tp = sum(1 for v in good_vals if v <= thr)
                fp = sum(1 for v in bad_vals if v <= thr)
                tpr = tp / n_good if n_good else 0.0
                fpr = fp / n_bad if n_bad else 0.0
                fprs.append(fpr)
                tprs.append(tpr)
                j = tpr - fpr
                if j > best_j:
                    best_j = j
                    best_thr = thr
            # Sort for plotting
            pts = sorted(zip(fprs, tprs))
            fprs_sorted = [p[0] for p in pts]
            tprs_sorted = [p[1] for p in pts]
            auc = float(np.trapz(tprs_sorted, fprs_sorted))
            auc = max(0.0, min(1.0, abs(auc)))
            # Find optimal point
            opt_tp = sum(1 for v in good_vals if v <= best_thr) / n_good if n_good else 0
            opt_fp = sum(1 for v in bad_vals if v <= best_thr) / n_bad if n_bad else 0
            result[label] = {
                'fpr': fprs_sorted, 'tpr': tprs_sorted, 'auc': auc,
                'youden_j': best_j, 'optimal_fpr': opt_fp, 'optimal_tpr': opt_tp,
            }
        return result

    def get_threshold_history(self, include_ci: bool = False) -> dict:
        """Return threshold estimates at each signal count for convergence plotting.

        When ``include_ci`` is True, also returns per-step 95 % Bayesian credible
        interval bands for each primary parameter under keys ``<param>_ci_low``
        and ``<param>_ci_high``. These are aligned with ``count`` and can be used
        to draw confidence bands around the convergence lines.
        """
        history = {'count': [], 'fft_energy_ratio': [], 'exceedance_ratio': [], 'relative_residual_ratio': []}
        if include_ci:
            for k in ('fft_energy_ratio', 'exceedance_ratio', 'relative_residual_ratio'):
                history[f'{k}_ci_low'] = []
                history[f'{k}_ci_high'] = []
        if len(self._all_signals) < 6:
            return history
        # Replay signals through a temporary engine to get estimates at each step
        temp = HybridCalibrationEngine()
        ci_keymap = {
            'fft_energy_ratio': 'fft_energy_ratio_threshold',
            'exceedance_ratio': 'exceedance_ratio_threshold',
            'relative_residual_ratio': 'relative_residual_ratio',
        }
        for sig in self._all_signals:
            judgment = sig.get('judgment', 'IGNORE')
            sig_copy = dict(sig)
            for key in ('fft_freqs', 'fft_mags', 'residual_physical', 'signal_physical'):
                if key in sig_copy and isinstance(sig_copy[key], list):
                    sig_copy[key] = np.array(sig_copy[key])
            temp.add_signal(sig_copy, judgment)
            if temp.can_estimate:
                est = temp.get_estimates()
                if est:
                    history['count'].append(temp.total_signals)
                    history['fft_energy_ratio'].append(est.get('fft_energy_ratio_threshold'))
                    history['exceedance_ratio'].append(est.get('exceedance_ratio_threshold'))
                    history['relative_residual_ratio'].append(est.get('relative_residual_ratio'))
                    if include_ci:
                        ci = temp.get_bayesian_ci() if temp.bayesian.can_estimate else None
                        for hkey, bayes_key in ci_keymap.items():
                            if ci and bayes_key in ci:
                                history[f'{hkey}_ci_low'].append(ci[bayes_key]['ci_low'])
                                history[f'{hkey}_ci_high'].append(ci[bayes_key]['ci_high'])
                            else:
                                history[f'{hkey}_ci_low'].append(None)
                                history[f'{hkey}_ci_high'].append(None)
        return history

    def get_signal_table_data(self) -> List[dict]:
        """Return per-signal summary rows for the Cal. Diagnostics table.

        Each row contains the minimal fields needed to render the table and to
        cross-reference a signal back into ``_all_signals`` (via ``index``).
        """
        rows = []
        for i, sig in enumerate(self._all_signals):
            rows.append({
                'index': i,
                'hit_key': sig.get('roi_name', f'signal_{i}'),
                'signal_type': sig.get('signal_type', 'unknown'),
                'judgment': sig.get('judgment', '?'),
                'energy_ratio': float(sig.get('energy_ratio', 0.0) or 0.0),
                'exceedance_ratio': float(sig.get('exceedance_ratio', 0.0) or 0.0),
                'max_abs_residual': float(sig.get('max_abs_residual', 0.0) or 0.0),
            })
        return rows

    def get_bayesian_ci(self) -> Optional[dict]:
        """Return Bayesian posterior grids and credible intervals for plotting."""
        if not self.bayesian.can_estimate:
            return None
        result = {}
        for name, grid in self.bayesian.grids.items():
            posterior = self.bayesian.posteriors[name]
            cumulative = np.cumsum(posterior)
            ci_low_idx = np.searchsorted(cumulative, 0.025)
            ci_high_idx = np.searchsorted(cumulative, 0.975)
            result[name] = {
                'grid': grid.tolist(),
                'posterior': posterior.tolist(),
                'ci_low': float(grid[min(ci_low_idx, len(grid)-1)]),
                'ci_high': float(grid[min(ci_high_idx, len(grid)-1)]),
            }
        return result

    def to_dict(self) -> dict:
        """Serialise the engine state for JSON persistence."""
        return {
            'good_count': self.good_count,
            'bad_count': self.bad_count,
            'confidence_level': self.confidence_level,
            'total_signals': self.total_signals,
            'signals': self._all_signals,
            'method': 'hybrid',
            'created_at': (self._all_signals[0]['timestamp']
                          if self._all_signals else ''),
            'last_updated': (self._all_signals[-1]['timestamp']
                            if self._all_signals else ''),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'HybridCalibrationEngine':
        """Reconstruct engine from saved _calibration JSON data."""
        engine = cls()
        signals = data.get('signals', [])
        for sig in signals:
            judgment = sig.get('judgment', 'IGNORE')
            sig_copy = dict(sig)
            for key in ('fft_freqs', 'fft_mags', 'residual_physical', 'signal_physical'):
                if key in sig_copy and isinstance(sig_copy[key], list):
                    sig_copy[key] = np.array(sig_copy[key])
            engine.add_signal(sig_copy, judgment)
        return engine

# ---------------------------------------------------------------------------

@dataclass(slots=True)
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

@dataclass(slots=True)
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

@dataclass(slots=True)
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

@dataclass(slots=True)
class AnalysisParams:
    """Analysis parameters for a single signal type (FRF, PSD, etc.)."""
    fft_cutoff_frequency: float = 0.07
    fft_energy_ratio_threshold: float = 0.006
    lowpass_cutoff: float = 0.07
    lowpass_filter_order: int = 7
    relative_residual_ratio: float = 0.10
    exceedance_ratio_threshold: float = 0.7

@dataclass
class AppConfig:
    regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    hsv_lower: List[int] = field(default_factory=lambda: [0, 0, 0])
    hsv_upper: List[int] = field(default_factory=lambda: [179, 255, 240])
    screenshot_interval: float = 0.25
    
    # Per-signal-type analysis parameters
    analysis_params: Dict[str, AnalysisParams] = field(default_factory=lambda: {
        'frf': AnalysisParams(),
        'psd': AnalysisParams(),
    })

    # --- Coherence Parameters (v0.6.0) ---
    coherence_threshold: float = 0.3          # Normalized badness threshold
    coherence_degradation_pct: float = 0.20   # % increase in badness to flag degradation
    hits_per_run: int = 5                     # Expected number of hits per run
    monitor_index: int = 1                    # mss monitor index: 0=all, 1=primary, 2+=secondary

    # --- Backward-compatible accessors (FRF) ---
    @property
    def fft_cutoff_frequency(self): return self.analysis_params['frf'].fft_cutoff_frequency
    @fft_cutoff_frequency.setter
    def fft_cutoff_frequency(self, v): self.analysis_params['frf'].fft_cutoff_frequency = v
    
    @property
    def fft_energy_ratio_threshold(self): return self.analysis_params['frf'].fft_energy_ratio_threshold
    @fft_energy_ratio_threshold.setter
    def fft_energy_ratio_threshold(self, v): self.analysis_params['frf'].fft_energy_ratio_threshold = v
    
    @property
    def lowpass_cutoff(self): return self.analysis_params['frf'].lowpass_cutoff
    @lowpass_cutoff.setter
    def lowpass_cutoff(self, v): self.analysis_params['frf'].lowpass_cutoff = v
    
    @property
    def lowpass_filter_order(self): return self.analysis_params['frf'].lowpass_filter_order
    @lowpass_filter_order.setter
    def lowpass_filter_order(self, v): self.analysis_params['frf'].lowpass_filter_order = v
    
    @property
    def relative_residual_ratio(self): return self.analysis_params['frf'].relative_residual_ratio
    @relative_residual_ratio.setter
    def relative_residual_ratio(self, v): self.analysis_params['frf'].relative_residual_ratio = v

    # Backward-compat alias — removed field, redirect to new name
    @property
    def residual_threshold(self): return self.analysis_params['frf'].relative_residual_ratio
    @residual_threshold.setter
    def residual_threshold(self, v): self.analysis_params['frf'].relative_residual_ratio = v

    @property
    def exceedance_ratio_threshold(self): return self.analysis_params['frf'].exceedance_ratio_threshold
    @exceedance_ratio_threshold.setter
    def exceedance_ratio_threshold(self, v): self.analysis_params['frf'].exceedance_ratio_threshold = v

    # --- Backward-compatible accessors (PSD) ---
    @property
    def psd_fft_cutoff_frequency(self): return self.analysis_params['psd'].fft_cutoff_frequency
    @psd_fft_cutoff_frequency.setter
    def psd_fft_cutoff_frequency(self, v): self.analysis_params['psd'].fft_cutoff_frequency = v
    
    @property
    def psd_fft_energy_ratio_threshold(self): return self.analysis_params['psd'].fft_energy_ratio_threshold
    @psd_fft_energy_ratio_threshold.setter
    def psd_fft_energy_ratio_threshold(self, v): self.analysis_params['psd'].fft_energy_ratio_threshold = v
    
    @property
    def psd_lowpass_cutoff(self): return self.analysis_params['psd'].lowpass_cutoff
    @psd_lowpass_cutoff.setter
    def psd_lowpass_cutoff(self, v): self.analysis_params['psd'].lowpass_cutoff = v
    
    @property
    def psd_lowpass_filter_order(self): return self.analysis_params['psd'].lowpass_filter_order
    @psd_lowpass_filter_order.setter
    def psd_lowpass_filter_order(self, v): self.analysis_params['psd'].lowpass_filter_order = v
    
    @property
    def psd_relative_residual_ratio(self): return self.analysis_params['psd'].relative_residual_ratio
    @psd_relative_residual_ratio.setter
    def psd_relative_residual_ratio(self, v): self.analysis_params['psd'].relative_residual_ratio = v

    # Backward-compat alias
    @property
    def psd_residual_threshold(self): return self.analysis_params['psd'].relative_residual_ratio
    @psd_residual_threshold.setter
    def psd_residual_threshold(self, v): self.analysis_params['psd'].relative_residual_ratio = v

    @property
    def psd_exceedance_ratio_threshold(self): return self.analysis_params['psd'].exceedance_ratio_threshold
    @psd_exceedance_ratio_threshold.setter
    def psd_exceedance_ratio_threshold(self, v): self.analysis_params['psd'].exceedance_ratio_threshold = v

    @classmethod
    def from_json(cls, path: str) -> 'AppConfig':
        """Load an AppConfig from a JSON config file.

        This is a standalone loader that does NOT require instantiating ScreenMonitor.
        """
        try:
            if not os.path.exists(path):
                logger.warning(f"Config file does not exist: {path}")
                return cls()

            with open(path, 'r') as f:
                config_data = json.load(f)

            config = cls()
            metadata = config_data.get('_metadata', {})

            config.hsv_lower = metadata.get('hsv_lower', config.hsv_lower)
            config.hsv_upper = metadata.get('hsv_upper', config.hsv_upper)
            config.screenshot_interval = metadata.get('screenshot_interval', config.screenshot_interval)
            
            for sig_type in ('frf', 'psd'):
                prefix = '' if sig_type == 'frf' else f'{sig_type}_'
                params = config.analysis_params.get(sig_type, AnalysisParams())
                params.fft_cutoff_frequency = metadata.get(f'{prefix}fft_cutoff_frequency', params.fft_cutoff_frequency)
                params.fft_energy_ratio_threshold = metadata.get(f'{prefix}fft_energy_ratio_threshold', params.fft_energy_ratio_threshold)
                params.lowpass_cutoff = metadata.get(f'{prefix}lowpass_cutoff', params.lowpass_cutoff)
                params.lowpass_filter_order = metadata.get(f'{prefix}lowpass_filter_order', params.lowpass_filter_order)
                # Migration: old absolute residual_threshold → new dimensionless relative_residual_ratio
                if f'{prefix}relative_residual_ratio' in metadata:
                    params.relative_residual_ratio = metadata[f'{prefix}relative_residual_ratio']
                elif f'{prefix}residual_threshold' in metadata:
                    params.relative_residual_ratio = 0.10
                    logger.info(f"Migrated config: {prefix}residual_threshold → {prefix}relative_residual_ratio (default 0.10)")
                params.exceedance_ratio_threshold = metadata.get(f'{prefix}exceedance_ratio_threshold', params.exceedance_ratio_threshold)
                config.analysis_params[sig_type] = params
            config.coherence_threshold = metadata.get('coherence_threshold', config.coherence_threshold)
            config.coherence_degradation_pct = metadata.get('coherence_degradation_pct', config.coherence_degradation_pct)
            config.hits_per_run = metadata.get('hits_per_run', config.hits_per_run)
            config.monitor_index = metadata.get('monitor_index', config.monitor_index)

            region_fields = get_type_hints(MonitoringRegion).keys()
            for name, data in config_data.items():
                if not name.startswith('_') and isinstance(data, dict):
                    filtered_data = {k: v for k, v in data.items() if k in region_fields}
                    if 'name' in filtered_data:
                        config.regions[name] = MonitoringRegion(**filtered_data)

            logger.info(f"Config loaded from {path}: {len(config.regions)} regions, "
                         f"HSV [{config.hsv_lower}]-[{config.hsv_upper}]")
            return config

        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return cls()

