"""CalibrationDataManager — persists calibration data to an on-disk folder.

Folder layout
-------------
calibration_data/
  {config_stem}/
    metadata.json         Engine state, confidence level, thresholds, timestamp.
    signals.csv           All stored signals in tabular form (easy to open in
                          Excel / Python / R for further analysis or publication).
    diagnostics.png       2×2 matplotlib diagnostic panel (distributions, ROC /
                          convergence) — same content as Cal. Diagnostics window.
    signals/
      001_frf_good.png    Per-signal waveform + FFT subplot.
      002_frf_bad.png
      ...

The folder is created / overwritten on every save, so it always reflects the
latest calibration state.  It is listed in .gitignore so it does not pollute
the repo but remains easy to share or archive.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from usma.models import HybridCalibrationEngine

logger = logging.getLogger(__name__)

# Root folder for all calibration exports, relative to the working directory.
CAL_DATA_ROOT = "calibration_data"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_session_folder(config_path: str, create: bool = True) -> str:
    """Return (and create if needed) the per-config calibration folder path."""
    stem = Path(config_path).stem if config_path else "default"
    folder = os.path.join(CAL_DATA_ROOT, stem)
    if create:
        os.makedirs(folder, exist_ok=True)
        os.makedirs(os.path.join(folder, "signals"), exist_ok=True)
    return folder


def save_all(config_path: str, engine: "HybridCalibrationEngine") -> str:
    """Save metadata, CSV, per-signal PNGs and the diagnostics panel.

    Returns the folder path on success, empty string on failure.
    """
    if engine is None or engine.total_signals == 0:
        return ""
    try:
        folder = get_session_folder(config_path)
        _save_metadata(folder, config_path, engine)
        _save_csv(folder, engine)
        _save_signal_plots(folder, engine)
        _save_diagnostics_figure(folder, engine)
        logger.info(f"[CAL-EXPORT] Saved {engine.total_signals} signals to {folder}")
        return folder
    except Exception as exc:
        logger.error(f"[CAL-EXPORT] Failed to save calibration data: {exc}")
        return ""


def clear_folder(config_path: str) -> bool:
    """Delete the per-config calibration folder.  Returns True on success."""
    try:
        stem = Path(config_path).stem if config_path else "default"
        folder = os.path.join(CAL_DATA_ROOT, stem)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
            logger.info(f"[CAL-EXPORT] Deleted calibration data folder: {folder}")
        return True
    except Exception as exc:
        logger.error(f"[CAL-EXPORT] Failed to clear calibration folder: {exc}")
        return False


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _save_metadata(folder: str, config_path: str, engine: "HybridCalibrationEngine"):
    estimates = engine.get_estimates() if engine.can_estimate else {}
    meta = {
        "config_path": config_path,
        "saved_at": datetime.now().isoformat(),
        "total_signals": engine.total_signals,
        "good_count": engine.good_count,
        "bad_count": engine.bad_count,
        "confidence_level": engine.confidence_level,
        "estimates": {k: round(float(v), 8) for k, v in (estimates or {}).items()
                      if v is not None},
    }
    path = os.path.join(folder, "metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _save_csv(folder: str, engine: "HybridCalibrationEngine"):
    """Write all stored signals as a flat CSV."""
    path = os.path.join(folder, "signals.csv")
    rows = engine.get_signal_table_data()
    if not rows:
        return
    fieldnames = ["index", "hit_key", "signal_type", "judgment",
                  "energy_ratio", "exceedance_ratio", "max_abs_residual",
                  "total_energy", "high_freq_energy", "exceedance_count",
                  "relative_residual_ratio", "timestamp"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        # get_signal_table_data returns minimal rows; enrich from _all_signals
        for row in rows:
            idx = row.get("index", 0)
            if 0 <= idx < len(engine._all_signals):
                full = engine._all_signals[idx]
                merged = {
                    "index": idx + 1,
                    "hit_key": row.get("hit_key", ""),
                    "signal_type": row.get("signal_type", ""),
                    "judgment": row.get("judgment", ""),
                    "energy_ratio": row.get("energy_ratio", ""),
                    "exceedance_ratio": row.get("exceedance_ratio", ""),
                    "max_abs_residual": row.get("max_abs_residual", ""),
                    "total_energy": full.get("total_energy", ""),
                    "high_freq_energy": full.get("high_freq_energy", ""),
                    "exceedance_count": full.get("exceedance_count", ""),
                    "relative_residual_ratio": full.get("relative_residual_ratio", ""),
                    "timestamp": full.get("timestamp", ""),
                }
                writer.writerow(merged)


def _save_signal_plots(folder: str, engine: "HybridCalibrationEngine"):
    """Save a 2-subplot figure (waveform + FFT) for every stored signal."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg  # type: ignore

    sig_folder = os.path.join(folder, "signals")
    os.makedirs(sig_folder, exist_ok=True)

    for idx, sig in enumerate(engine._all_signals):
        sig_type = sig.get("signal_type", "unknown")
        judgment = sig.get("judgment", "unknown")
        roi_name = sig.get("roi_name", f"signal_{idx}")
        energy = sig.get("energy_ratio", 0.0) or 0.0
        exc = sig.get("exceedance_ratio", 0.0) or 0.0
        max_res = sig.get("max_abs_residual", 0.0) or 0.0

        phys = sig.get("signal_physical") or []
        fft_freqs = sig.get("fft_freqs") or []
        fft_mags = sig.get("fft_mags") or []

        if isinstance(phys, list):
            phys = np.asarray(phys, dtype=float)
        if isinstance(fft_freqs, list):
            fft_freqs = np.asarray(fft_freqs, dtype=float)
        if isinstance(fft_mags, list):
            fft_mags = np.asarray(fft_mags, dtype=float)

        fig = Figure(figsize=(10, 4), dpi=120, facecolor="#1E1E1E")
        FigureCanvasAgg(fig)
        axes = fig.subplots(1, 2)

        colour = {"frf": "cyan", "psd": "#FF9800", "coherence": "#00E676"}.get(
            sig_type, "cyan"
        )

        # Left: waveform
        ax0 = axes[0]
        ax0.set_facecolor("#2E2E2E")
        ax0.tick_params(colors="white")
        for sp in ax0.spines.values():
            sp.set_color("white")
        if phys.size > 0:
            ax0.plot(np.arange(phys.size), phys, color=colour, linewidth=1.2)
        else:
            ax0.text(0.5, 0.5, "No waveform data", transform=ax0.transAxes,
                     ha="center", va="center", color="white")
        ax0.set_xlabel("Pixel index", color="white", fontsize=8)
        ax0.set_ylabel("signal_physical", color="white", fontsize=8)
        ax0.set_title("Waveform", color="white", fontsize=9)
        ax0.grid(True, linestyle="--", alpha=0.25)

        # Right: FFT
        ax1 = axes[1]
        ax1.set_facecolor("#2E2E2E")
        ax1.tick_params(colors="white")
        for sp in ax1.spines.values():
            sp.set_color("white")
        if fft_freqs.size > 0 and fft_mags.size > 0:
            ax1.plot(fft_freqs, fft_mags, color="magenta", linewidth=1.0)
        else:
            ax1.text(0.5, 0.5, "No FFT data", transform=ax1.transAxes,
                     ha="center", va="center", color="white")
        ax1.set_xlabel("Normalized freq.", color="white", fontsize=8)
        ax1.set_ylabel("Magnitude", color="white", fontsize=8)
        ax1.set_title("FFT Spectrum", color="white", fontsize=9)
        ax1.grid(True, linestyle="--", alpha=0.25)

        title = (
            f"[{sig_type.upper()}] {roi_name} — {judgment}  |  "
            f"E.Ratio={energy:.4g}  Exc={exc:.3f}  MaxRes={max_res:.3f}"
        )
        fig.suptitle(title, color="white", fontsize=9, y=1.01)

        try:
            fig.tight_layout(pad=1.0)
        except Exception:
            pass

        fname = f"{idx + 1:03d}_{sig_type}_{judgment.lower()}.png"
        save_path = os.path.join(sig_folder, fname)
        try:
            fig.savefig(save_path, dpi=120, facecolor=fig.get_facecolor(),
                        bbox_inches="tight")
        except Exception as e:
            logger.warning(f"[CAL-EXPORT] Could not save signal plot {fname}: {e}")
        finally:
            fig.clf()
            del fig


def _save_diagnostics_figure(folder: str, engine: "HybridCalibrationEngine"):
    """Re-render the 2×2 diagnostics panel to a PNG (non-interactive backend)."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg  # type: ignore

    PARAM_ORDER = [
        ("fft_energy_ratio", "FFT Energy Ratio", "fft_energy_ratio_threshold"),
        ("exceedance_ratio", "Exceedance Ratio", "exceedance_ratio_threshold"),
        ("relative_residual_ratio", "Relative Residual Ratio", "relative_residual_ratio"),
    ]
    METRIC_KEYS = {
        "fft_energy_ratio": "energy_ratio",
        "exceedance_ratio": "exceedance_ratio",
        "relative_residual_ratio": "exceedance_ratio",
    }

    def _populations(metric_key):
        gf, gp, bf, bp = [], [], [], []
        for sig in engine._all_signals:
            v = sig.get(metric_key)
            if v is None:
                continue
            jm = sig.get("judgment")
            st = sig.get("signal_type", "")
            if jm == "GOOD":
                (gf if st == "frf" else gp).append(float(v))
            elif jm == "BAD":
                (bf if st == "frf" else bp).append(float(v))
        return gf, gp, bf, bp

    fig = Figure(figsize=(14, 9), dpi=130, facecolor="#1E1E1E")
    FigureCanvasAgg(fig)

    dist_data = engine.get_distribution_data()
    roc_data = engine.get_roc_data()
    bayesian_ci = engine.get_bayesian_ci()
    estimates = engine.get_estimates() if engine.can_estimate else {}

    axes = fig.subplots(2, 2)
    ax_dist = [axes[0, 0], axes[0, 1], axes[1, 0]]
    ax_extra = axes[1, 1]

    def _style(ax):
        ax.set_facecolor("#2E2E2E")
        ax.tick_params(colors="white", labelsize=7)
        for sp in ax.spines.values():
            sp.set_color("white")

    for i, (hkey, title, est_key) in enumerate(PARAM_ORDER):
        ax = ax_dist[i]
        _style(ax)
        mk = METRIC_KEYS[hkey]
        gf, gp, bf, bp = _populations(mk)
        all_vals = gf + gp + bf + bp
        if all_vals:
            vmin, vmax = min(all_vals), max(all_vals)
            if vmin == vmax:
                vmax = vmin + 1e-6
            n_bins = max(6, min(20, len(all_vals) // 2))
            bins = np.linspace(vmin, vmax, n_bins + 1)
        else:
            bins = 10
        if gf:
            ax.hist(gf, bins=bins, alpha=0.55, color="#2ECC71", density=True,
                    label=f"Good FRF ({len(gf)})")
        if gp:
            ax.hist(gp, bins=bins, alpha=0.55, color="#2ECC71", density=True,
                    hatch="//", edgecolor="#0E6A3A", linewidth=0.0,
                    label=f"Good PSD ({len(gp)})")
        if bf:
            ax.hist(bf, bins=bins, alpha=0.55, color="#E74C3C", density=True,
                    label=f"Bad FRF ({len(bf)})")
        if bp:
            ax.hist(bp, bins=bins, alpha=0.55, color="#E74C3C", density=True,
                    hatch="//", edgecolor="#7B1818", linewidth=0.0,
                    label=f"Bad PSD ({len(bp)})")
        if estimates and est_key in estimates and estimates[est_key] is not None:
            ax.axvline(estimates[est_key], color="white", linewidth=1.5,
                       linestyle="-", label="Merged", alpha=0.9)
        if bayesian_ci and est_key in bayesian_ci:
            ci = bayesian_ci[est_key]
            ax.axvspan(ci["ci_low"], ci["ci_high"], alpha=0.15, color="#3498DB",
                       label="95% CI")
        ax.set_title(title, color="white", fontsize=9)
        ax.set_xlabel(title, color="white", fontsize=8)
        ax.set_ylabel("Density", color="white", fontsize=8)
        ax.legend(fontsize=6, facecolor="#2E2E2E", labelcolor="white",
                  loc="upper right")
        ax.grid(True, linestyle="--", alpha=0.25)

    # Auxiliary subplot
    _style(ax_extra)
    if roc_data:
        colours = {
            "fft_energy_ratio": "#E74C3C",
            "exceedance_ratio": "#F39C12",
            "relative_residual_ratio": "#3498DB",
        }
        param_key_map = {
            "fft_energy_ratio": "fft_energy_ratio_threshold",
            "exceedance_ratio": "exceedance_ratio_threshold",
            "relative_residual_ratio": "relative_residual_ratio",
        }
        for pk, info in roc_data.items():
            c = colours.get(pk, "white")
            ax_extra.plot(info["fpr"], info["tpr"], color=c, linewidth=1.4,
                          label=f"{pk.replace('_',' ').title()} (AUC={info['auc']:.2f})")
            ax_extra.plot(info["optimal_fpr"], info["optimal_tpr"], "o",
                          color=c, markersize=6)
            thr_val = (estimates or {}).get(param_key_map.get(pk, ""))
            if thr_val is not None:
                ax_extra.annotate(f"thr={thr_val:.4g}",
                                  xy=(info["optimal_fpr"], info["optimal_tpr"]),
                                  xytext=(6, 3), textcoords="offset points",
                                  fontsize=7, color=c)
        ax_extra.plot([0, 1], [0, 1], "--", color="gray", alpha=0.5)
        ax_extra.set_xlabel("FPR", color="white", fontsize=8)
        ax_extra.set_ylabel("TPR", color="white", fontsize=8)
        ax_extra.set_title("ROC Curves", color="white", fontsize=9)
        ax_extra.set_xlim(0, 1)
        ax_extra.set_ylim(0, 1.05)
        ax_extra.legend(fontsize=6, facecolor="#2E2E2E", labelcolor="white",
                        loc="lower right")
        ax_extra.grid(True, linestyle="--", alpha=0.25)
    elif engine.total_signals >= 6:
        hist = engine.get_threshold_history(include_ci=True)
        colours2 = {
            "fft_energy_ratio": "#E74C3C",
            "exceedance_ratio": "#F39C12",
            "relative_residual_ratio": "#3498DB",
        }
        counts = np.asarray(hist.get("count", []))
        for key, c in colours2.items():
            vals = hist.get(key, [])
            if not vals:
                continue
            y = np.array([v if v is not None else np.nan for v in vals], dtype=float)
            ax_extra.plot(counts, y, "-o", color=c, markersize=3, linewidth=1.1,
                          label=key.replace("_", " ").title())
            lo = hist.get(f"{key}_ci_low", [])
            hi = hist.get(f"{key}_ci_high", [])
            if lo and hi:
                lo_a = np.array([v if v is not None else np.nan for v in lo], dtype=float)
                hi_a = np.array([v if v is not None else np.nan for v in hi], dtype=float)
                mask = ~(np.isnan(lo_a) | np.isnan(hi_a))
                if mask.any():
                    ax_extra.fill_between(counts[mask], lo_a[mask], hi_a[mask],
                                          color=c, alpha=0.12, linewidth=0)
        ax_extra.set_xlabel("Signal Count", color="white", fontsize=8)
        ax_extra.set_ylabel("Threshold", color="white", fontsize=8)
        ax_extra.set_title("Threshold Convergence", color="white", fontsize=9)
        ax_extra.legend(fontsize=6, facecolor="#2E2E2E", labelcolor="white")
        ax_extra.grid(True, linestyle="--", alpha=0.25)
    else:
        ax_extra.text(0.5, 0.5,
                      "ROC: need confidence level 3+\n"
                      "Convergence: need ≥ 6 signals",
                      transform=ax_extra.transAxes, ha="center", va="center",
                      color="white", fontsize=9)

    lvl = engine.confidence_level
    total = engine.total_signals
    fig.suptitle(
        f"Calibration Diagnostics — Level {lvl}/4  |  "
        f"{engine.good_count}G + {engine.bad_count}B = {total} signals  |  "
        f"Saved {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        color="white", fontsize=10,
    )

    try:
        fig.tight_layout(pad=1.2)
    except Exception:
        pass

    save_path = os.path.join(folder, "diagnostics.png")
    try:
        fig.savefig(save_path, dpi=130, facecolor=fig.get_facecolor(),
                    bbox_inches="tight")
    finally:
        fig.clf()
        del fig
