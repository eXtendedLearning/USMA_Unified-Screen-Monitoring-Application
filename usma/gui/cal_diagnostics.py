"""Dedicated Calibration Diagnostics window.

A non-modal ``tk.Toplevel`` with its own matplotlib canvas that replaces the
cramped "Cal. Diagnostics" entry previously wedged into the Signal-source
dropdown of the Graph Viewer. Opened via the ``Cal. Diagnostics`` button in
the nav bar; singleton-managed by ``GraphViewerFrame``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional, List, Dict

import numpy as np

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from usma.models import HybridCalibrationEngine
from usma.theory.wiki_viewer import show_theory_page

logger = logging.getLogger(__name__)


class CalibrationDiagnosticsWindow(tk.Toplevel):
    """Non-modal Calibration Diagnostics window.

    Shows:

    - A header with Good/Bad/Total counts, confidence level and a Refresh
      button.
    - A left panel with a filter combobox, a sortable table of every stored
      calibration signal, and a per-signal detail plot + text readout.
    - A right panel with the three distribution histograms (with GOOD/BAD
      colours + FRF solid vs. PSD hatched fill) and an auxiliary ROC /
      convergence subplot.
    - A bottom toolbar with Export PNG and Close.

    Clicking a table row overlays that signal's metric values as thin dashed
    vertical markers on the distribution plots and fills the detail panel.
    """

    PARAM_ORDER = [
        # (signal-dict key, display title, engine-estimate key)
        ('fft_energy_ratio', 'FFT Energy Ratio', 'fft_energy_ratio_threshold'),
        ('exceedance_ratio', 'Exceedance Ratio', 'exceedance_ratio_threshold'),
        ('relative_residual_ratio', 'Relative Residual Ratio',
         'relative_residual_ratio'),
    ]

    # The metric that each histogram's X-axis actually uses. The raw metric
    # stored per-signal is ``energy_ratio`` / ``exceedance_ratio``. For the
    # third subplot there is no per-signal "relative residual ratio" metric
    # in the signal dict — the subplot shares its X-axis with exceedance.
    # We therefore plot exceedance_ratio on the third subplot but render the
    # threshold and CI for ``relative_residual_ratio``.
    METRIC_KEYS = {
        'fft_energy_ratio': 'energy_ratio',
        'exceedance_ratio': 'exceedance_ratio',
        'relative_residual_ratio': 'exceedance_ratio',
    }

    TREE_COLS = ('idx', 'hit_key', 'type', 'judgment', 'energy',
                 'exc', 'max_res')
    TREE_HEADERS = {
        'idx': '#', 'hit_key': 'Hit Key', 'type': 'Type',
        'judgment': 'Judgment', 'energy': 'Energy Ratio',
        'exc': 'Exc. Ratio', 'max_res': 'Max Residual',
    }

    def __init__(self, master, engine: Optional[HybridCalibrationEngine],
                 config_path: str = ""):
        super().__init__(master)
        self.title("Calibration Diagnostics")
        self.configure(bg='#1E1E1E')
        self.geometry("1280x800")
        self.minsize(960, 640)

        self.engine: Optional[HybridCalibrationEngine] = engine
        self.config_path: str = config_path
        self._highlight_index: Optional[int] = None
        self._filter_value: str = 'All'
        self._sort_col: Optional[str] = None
        self._sort_reverse: bool = False

        self._build_ui()
        self._redraw_all()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # A. Header bar
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=6, pady=(6, 2))

        ttk.Label(header, text="Calibration Diagnostics",
                  font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        # ⓘ help
        _info = tk.Label(header, text="\u24D8", font=("Segoe UI", 11),
                         fg="#5DADE2", cursor="hand2")
        _info.bind(
            "<Button-1>",
            lambda e: show_theory_page(self, "calibration_engine"),
        )
        _info.pack(side=tk.LEFT, padx=6)

        self.summary_var = tk.StringVar(value="Good: 0  |  Bad: 0  |  Total: 0  |  Confidence Level: 0/4")
        ttk.Label(header, textvariable=self.summary_var,
                  font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=12)

        ttk.Button(header, text="Refresh",
                   command=self._redraw_all).pack(side=tk.RIGHT, padx=4)

        # Main paned window: left = table + detail, right = plots
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=2)

        # B. Signal table — filter row + treeview
        filter_row = ttk.Frame(left)
        filter_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_row, text="Filter:").pack(side=tk.LEFT)

        self.filter_var = tk.StringVar(value='All')
        filter_box = ttk.Combobox(filter_row, textvariable=self.filter_var,
                                  values=['All', 'FRF only', 'PSD only'],
                                  state='readonly', width=12)
        filter_box.pack(side=tk.LEFT, padx=4)
        filter_box.bind("<<ComboboxSelected>>", self._on_filter_changed)

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree = ttk.Treeview(
            tree_frame, columns=self.TREE_COLS, show='headings',
            yscrollcommand=vsb.set, height=14,
        )
        vsb.config(command=self.tree.yview)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        col_widths = {'idx': 40, 'hit_key': 170, 'type': 50,
                      'judgment': 70, 'energy': 90, 'exc': 80,
                      'max_res': 90}
        for col in self.TREE_COLS:
            self.tree.heading(col, text=self.TREE_HEADERS[col],
                              command=lambda c=col: self._sort_by_column(c))
            self.tree.column(col, width=col_widths.get(col, 80),
                             anchor=tk.W if col in ('hit_key',) else tk.CENTER)

        # Colour tags
        self.tree.tag_configure('good', background='#1B4D2E',
                                foreground='white')
        self.tree.tag_configure('bad', background='#5A1D1D',
                                foreground='white')

        self.tree.bind("<<TreeviewSelect>>", self._on_row_selected)

        # C. Detail panel — mini matplotlib canvas + text summary
        detail = ttk.LabelFrame(left, text="Selected signal")
        detail.pack(fill=tk.BOTH, expand=False, pady=(4, 0))

        self.detail_fig = Figure(figsize=(4.5, 2.0), dpi=100,
                                 facecolor='#1E1E1E')
        self.detail_ax = self.detail_fig.add_subplot(111)
        self._style_axes(self.detail_ax)
        self.detail_ax.text(0.5, 0.5, 'Select a signal from the table',
                            transform=self.detail_ax.transAxes, ha='center',
                            va='center', color='white', fontsize=9)
        self.detail_canvas = FigureCanvasTkAgg(self.detail_fig, master=detail)
        self.detail_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True,
                                                padx=4, pady=2)

        self.detail_text = tk.StringVar(value="")
        ttk.Label(detail, textvariable=self.detail_text,
                  font=("Consolas", 9), anchor=tk.W,
                  justify=tk.LEFT).pack(fill=tk.X, padx=6, pady=(0, 4))

        # D. Diagnostic plots on the right
        self.fig = Figure(figsize=(12, 8), dpi=100, facecolor='#1E1E1E')
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # E. Toolbar
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(toolbar, text="Export PNG",
                   command=self._export_png).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Save All to Data Folder",
                   command=self._save_all_to_folder).pack(side=tk.LEFT, padx=6)
        self._open_folder_btn = ttk.Button(toolbar, text="Open Data Folder",
                                           command=self._open_data_folder)
        self._open_folder_btn.pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Close",
                   command=self._on_close).pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _style_axes(ax):
        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white', labelsize=7)
        for spine in ax.spines.values():
            spine.set_color('white')

    def _on_close(self):
        try:
            self.withdraw()  # keep singleton reusable
        except tk.TclError:
            pass

    # ------------------------------------------------------ public surface
    def update_engine(self, engine: Optional[HybridCalibrationEngine],
                      config_path: str = ""):
        """Replace the engine reference and redraw."""
        self.engine = engine
        if config_path:
            self.config_path = config_path
        self._redraw_all()

    # ------------------------------------------------------- table + filter
    def _on_filter_changed(self, _evt=None):
        self._filter_value = self.filter_var.get()
        # Filter change invalidates the highlight.
        self._highlight_index = None
        self._redraw_all()

    def _on_row_selected(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(self.tree.item(sel[0], 'values')[0]) - 1
        except (ValueError, IndexError):
            return
        self._highlight_index = idx
        self._update_detail_panel(idx)
        self._draw_distributions()
        self.canvas.draw_idle()

    def _sort_by_column(self, col: str):
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        self._populate_tree()

    def _visible_rows(self) -> List[dict]:
        """Rows after applying the FRF/PSD filter."""
        if self.engine is None:
            return []
        rows = self.engine.get_signal_table_data()
        if self._filter_value == 'FRF only':
            rows = [r for r in rows if r['signal_type'] == 'frf']
        elif self._filter_value == 'PSD only':
            rows = [r for r in rows if r['signal_type'] == 'psd']
        return rows

    def _estimates_for_filter(self) -> dict:
        """Return threshold estimates matching the active FRF/PSD filter."""
        if self.engine is None:
            return {}
        if self._filter_value == 'FRF only':
            return self.engine.get_estimates_for_type('frf') or {}
        if self._filter_value == 'PSD only':
            return self.engine.get_estimates_for_type('psd') or {}
        return self.engine.get_estimates() if self.engine.can_estimate else {}

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        rows = self._visible_rows()

        if self._sort_col is not None:
            key_map = {
                'idx': 'index', 'hit_key': 'hit_key', 'type': 'signal_type',
                'judgment': 'judgment', 'energy': 'energy_ratio',
                'exc': 'exceedance_ratio', 'max_res': 'max_abs_residual',
            }
            sort_key = key_map.get(self._sort_col, 'index')
            try:
                rows.sort(key=lambda r: r[sort_key],
                          reverse=self._sort_reverse)
            except TypeError:
                rows.sort(key=lambda r: str(r[sort_key]),
                          reverse=self._sort_reverse)

        for r in rows:
            tag = 'good' if r['judgment'] == 'GOOD' else 'bad'
            values = (
                r['index'] + 1,
                r['hit_key'],
                r['signal_type'].upper(),
                r['judgment'],
                f"{r['energy_ratio']:.4g}",
                f"{r['exceedance_ratio']:.3f}",
                f"{r['max_abs_residual']:.3f}",
            )
            self.tree.insert('', tk.END, values=values, tags=(tag,))

    # ------------------------------------------------------------ detail
    def _update_detail_panel(self, idx: int):
        if self.engine is None:
            return
        if idx < 0 or idx >= len(self.engine._all_signals):
            return
        sig = self.engine._all_signals[idx]
        self.detail_fig.clear()
        ax = self.detail_fig.add_subplot(111)
        self._style_axes(ax)

        signal = sig.get('signal_physical', [])
        if isinstance(signal, list):
            signal = np.asarray(signal, dtype=float)

        sig_type = sig.get('signal_type', 'unknown').upper()
        roi_name = sig.get('roi_name', f'signal_{idx}')
        judgment = sig.get('judgment', '?')

        if signal.size > 0:
            colour = {'FRF': 'cyan', 'PSD': '#FF9800',
                      'COHERENCE': '#00E676'}.get(sig_type, 'cyan')
            ax.plot(np.arange(signal.size), signal, color=colour,
                    linewidth=1.2)
            ax.set_xlabel('Pixel index', color='white', fontsize=7)
            ax.set_ylabel('signal_physical', color='white', fontsize=7)
        else:
            ax.text(0.5, 0.5, 'No signal_physical stored',
                    transform=ax.transAxes, ha='center', va='center',
                    color='white', fontsize=9)

        ax.set_title(f"[{sig_type}] {roi_name} — {judgment}",
                     color='white', fontsize=9)
        ax.grid(True, linestyle='--', alpha=0.25)
        try:
            self.detail_fig.tight_layout(pad=0.8)
        except Exception:
            pass
        self.detail_canvas.draw_idle()

        energy = sig.get('energy_ratio', 0.0) or 0.0
        exc = sig.get('exceedance_ratio', 0.0) or 0.0
        max_res = sig.get('max_abs_residual', 0.0) or 0.0
        rrr = sig.get('relative_residual_ratio', 0.0) or 0.0
        dyn_thr = rrr * max_res
        self.detail_text.set(
            f"Energy Ratio: {energy:.4g}  |  Exc. Ratio: {exc:.3f}  "
            f"|  Max |Residual|: {max_res:.3f}  |  Dynamic Thr: {dyn_thr:.4f}"
        )

    # ----------------------------------------------------- main redraw
    def _redraw_all(self):
        self._update_summary()
        self._populate_tree()
        self._draw_distributions()
        self.canvas.draw_idle()

    def _update_summary(self):
        if self.engine is None:
            self.summary_var.set("No engine — nothing to show.")
            return
        g = self.engine.good_count
        b = self.engine.bad_count
        n = self.engine.total_signals
        lvl = self.engine.confidence_level
        self.summary_var.set(
            f"Good: {g}  |  Bad: {b}  |  Total: {n}  |  "
            f"Confidence Level: {lvl}/4"
        )

    # ---------------------------------------------------- distribution plots
    def _populations_for(self, metric_key: str):
        """Return (good_frf, good_psd, bad_frf, bad_psd) value lists for a
        metric, respecting the current FRF/PSD filter.
        """
        good_frf, good_psd, bad_frf, bad_psd = [], [], [], []
        if self.engine is None:
            return good_frf, good_psd, bad_frf, bad_psd
        allow_frf = self._filter_value != 'PSD only'
        allow_psd = self._filter_value != 'FRF only'
        for sig in self.engine._all_signals:
            v = sig.get(metric_key, 0.0)
            if v is None:
                continue
            jm = sig.get('judgment')
            st = sig.get('signal_type', 'unknown')
            if jm == 'GOOD':
                if st == 'frf' and allow_frf:
                    good_frf.append(float(v))
                elif st == 'psd' and allow_psd:
                    good_psd.append(float(v))
            elif jm == 'BAD':
                if st == 'frf' and allow_frf:
                    bad_frf.append(float(v))
                elif st == 'psd' and allow_psd:
                    bad_psd.append(float(v))
        return good_frf, good_psd, bad_frf, bad_psd

    def _highlight_value(self, metric_key: str) -> Optional[float]:
        """Return the metric value for the currently highlighted row, if any."""
        if self._highlight_index is None or self.engine is None:
            return None
        if not (0 <= self._highlight_index < len(self.engine._all_signals)):
            return None
        sig = self.engine._all_signals[self._highlight_index]
        v = sig.get(metric_key)
        return float(v) if v is not None else None

    def _draw_distributions(self):
        self.fig.clear()
        if self.engine is None:
            ax = self.fig.add_subplot(111)
            self._style_axes(ax)
            ax.text(0.5, 0.5, 'No calibration engine',
                    transform=ax.transAxes, ha='center', va='center',
                    color='white', fontsize=12)
            return

        dist_data = self.engine.get_distribution_data()
        roc_data = self.engine.get_roc_data()
        bayesian_ci = self.engine.get_bayesian_ci()
        estimates = self._estimates_for_filter()

        has_roc = bool(roc_data)
        has_convergence = self.engine.total_signals >= 6

        axes = self.fig.subplots(2, 2)
        ax_dist = [axes[0, 0], axes[0, 1], axes[1, 0]]
        ax_extra = axes[1, 1]

        for i, (hkey, title, est_key) in enumerate(self.PARAM_ORDER):
            ax = ax_dist[i]
            self._style_axes(ax)
            metric_key = self.METRIC_KEYS[hkey]

            good_frf, good_psd, bad_frf, bad_psd = self._populations_for(
                metric_key)

            # Bin edges across the union so both FRF and PSD share the x-scale
            all_vals = good_frf + good_psd + bad_frf + bad_psd
            if all_vals:
                vmin = min(all_vals)
                vmax = max(all_vals)
                if vmin == vmax:
                    vmax = vmin + 1e-6
                n_bins = max(6, min(20, len(all_vals) // 2))
                bins = np.linspace(vmin, vmax, n_bins + 1)
            else:
                bins = 10

            # FRF solid, PSD hatched. Use the same green/red palette.
            if good_frf:
                ax.hist(good_frf, bins=bins, alpha=0.55, color='#2ECC71',
                        density=True, label=f'Good FRF ({len(good_frf)})')
            if good_psd:
                ax.hist(good_psd, bins=bins, alpha=0.55, color='#2ECC71',
                        density=True, hatch='//', edgecolor='#0E6A3A',
                        linewidth=0.0,
                        label=f'Good PSD ({len(good_psd)})')
            if bad_frf:
                ax.hist(bad_frf, bins=bins, alpha=0.55, color='#E74C3C',
                        density=True, label=f'Bad FRF ({len(bad_frf)})')
            if bad_psd:
                ax.hist(bad_psd, bins=bins, alpha=0.55, color='#E74C3C',
                        density=True, hatch='//', edgecolor='#7B1818',
                        linewidth=0.0,
                        label=f'Bad PSD ({len(bad_psd)})')

            # Merged threshold
            if estimates and est_key in estimates and estimates[est_key] is not None:
                ax.axvline(estimates[est_key], color='white', linewidth=1.5,
                           linestyle='-', label='Merged', alpha=0.9)

            # Bayesian 95% CI band
            if bayesian_ci and est_key in bayesian_ci:
                ci = bayesian_ci[est_key]
                ax.axvspan(ci['ci_low'], ci['ci_high'], alpha=0.15,
                           color='#3498DB', label='95% CI')

            # Highlight from selected row
            hv = self._highlight_value(metric_key)
            if hv is not None:
                ax.axvline(hv, color='#F1C40f', linestyle=':', linewidth=1.3,
                           alpha=0.95, label='Selected')

            ax.set_title(title, color='white', fontsize=9)
            ax.set_xlabel(title, color='white', fontsize=8)
            ax.set_ylabel('Density', color='white', fontsize=8)
            ax.legend(fontsize=6, facecolor='#2E2E2E', labelcolor='white',
                      loc='upper right')
            ax.grid(True, linestyle='--', alpha=0.25)

        # Auxiliary subplot: ROC > convergence > blank
        self._style_axes(ax_extra)
        if has_roc:
            self._draw_roc(ax_extra, roc_data, estimates)
        elif has_convergence:
            self._draw_convergence(ax_extra)
        else:
            ax_extra.text(0.5, 0.5,
                          'ROC appears at confidence level 3+\n'
                          'Convergence appears once ≥ 6 signals are stored',
                          transform=ax_extra.transAxes, ha='center',
                          va='center', color='white', fontsize=9)

        try:
            self.fig.tight_layout(pad=1.2)
        except Exception:
            pass

    def _draw_roc(self, ax, roc_data: dict, estimates: dict):
        colours = {
            'fft_energy_ratio': '#E74C3C',
            'exceedance_ratio': '#F39C12',
            'relative_residual_ratio': '#3498DB',
        }
        # Parameter value that each ROC dot represents, for annotation.
        param_key_map = {
            'fft_energy_ratio': 'fft_energy_ratio_threshold',
            'exceedance_ratio': 'exceedance_ratio_threshold',
            'relative_residual_ratio': 'relative_residual_ratio',
        }
        for pk, info in roc_data.items():
            c = colours.get(pk, 'white')
            label = f"{pk.replace('_', ' ').title()} (AUC={info['auc']:.2f})"
            ax.plot(info['fpr'], info['tpr'], color=c, linewidth=1.4,
                    label=label)
            ax.plot(info['optimal_fpr'], info['optimal_tpr'], 'o',
                    color=c, markersize=6)
            thr_value = estimates.get(param_key_map.get(pk, ''))
            if thr_value is not None:
                ax.annotate(
                    f"thr = {thr_value:.4g}",
                    xy=(info['optimal_fpr'], info['optimal_tpr']),
                    xytext=(8, 4), textcoords='offset points',
                    fontsize=7, color=c,
                )
        ax.plot([0, 1], [0, 1], '--', color='gray', alpha=0.5)
        ax.set_xlabel('FPR', color='white', fontsize=8)
        ax.set_ylabel('TPR', color='white', fontsize=8)
        ax.set_title('ROC Curves', color='white', fontsize=9)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=6, facecolor='#2E2E2E', labelcolor='white',
                  loc='lower right')
        ax.grid(True, linestyle='--', alpha=0.25)

    def _draw_convergence(self, ax):
        history = self.engine.get_threshold_history(include_ci=True)
        if not history['count']:
            ax.text(0.5, 0.5, 'Need ≥ 6 signals for convergence plot',
                    transform=ax.transAxes, ha='center', va='center',
                    color='white', fontsize=9)
            return
        colours = {
            'fft_energy_ratio': '#E74C3C',
            'exceedance_ratio': '#F39C12',
            'relative_residual_ratio': '#3498DB',
        }
        counts = np.asarray(history['count'])
        for key, c in colours.items():
            vals = history.get(key, [])
            if not vals or not any(v is not None for v in vals):
                continue
            y = np.array([v if v is not None else np.nan for v in vals],
                         dtype=float)
            ax.plot(counts, y, '-o', color=c, markersize=3, linewidth=1.1,
                    label=key.replace('_', ' ').title())

            lo = history.get(f'{key}_ci_low', [])
            hi = history.get(f'{key}_ci_high', [])
            if lo and hi and any(v is not None for v in lo):
                lo_arr = np.array([v if v is not None else np.nan for v in lo],
                                  dtype=float)
                hi_arr = np.array([v if v is not None else np.nan for v in hi],
                                  dtype=float)
                mask = ~(np.isnan(lo_arr) | np.isnan(hi_arr))
                if mask.any():
                    ax.fill_between(counts[mask], lo_arr[mask], hi_arr[mask],
                                    color=c, alpha=0.12, linewidth=0)
        ax.set_xlabel('Signal Count', color='white', fontsize=8)
        ax.set_ylabel('Threshold', color='white', fontsize=8)
        ax.set_title('Threshold Convergence (95% CI bands)',
                     color='white', fontsize=9)
        ax.legend(fontsize=6, facecolor='#2E2E2E', labelcolor='white')
        ax.grid(True, linestyle='--', alpha=0.25)

    # ------------------------------------------------------------- export
    def _save_all_to_folder(self):
        """Save signals CSV, per-signal PNGs and diagnostics PNG to the
        calibration_data/ folder, then offer to open it."""
        from usma import calibration_export as cal_export
        if self.engine is None or self.engine.total_signals == 0:
            messagebox.showwarning("Nothing to save",
                                   "No calibration signals are stored.",
                                   parent=self)
            return
        folder = cal_export.save_all(self.config_path, self.engine)
        if folder:
            answer = messagebox.askyesno(
                "Saved",
                f"Calibration data saved to:\n{folder}\n\n"
                "Open the folder now?",
                parent=self
            )
            if answer:
                self._open_data_folder()
        else:
            messagebox.showerror("Save failed",
                                 "Could not save calibration data. "
                                 "Check the log for details.", parent=self)

    def _open_data_folder(self):
        """Open the calibration_data/{config_stem}/ folder in the system
        file explorer (Windows Explorer / Finder / Nautilus)."""
        from usma import calibration_export as cal_export
        import subprocess, platform
        folder = cal_export.get_session_folder(self.config_path, create=False)
        if not os.path.isdir(folder):
            messagebox.showinfo("No data folder",
                                f"The folder does not exist yet:\n{folder}\n\n"
                                "Save calibration data first.",
                                parent=self)
            return
        try:
            if platform.system() == "Windows":
                os.startfile(folder)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showerror("Could not open folder",
                                 str(exc), parent=self)

    def _export_png(self):
        default_dir = os.path.join(os.getcwd(), 'image_logs')
        try:
            os.makedirs(default_dir, exist_ok=True)
        except OSError:
            default_dir = os.getcwd()
        default_name = (f"cal_diagnostics_"
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export Calibration Diagnostics",
            initialdir=default_dir,
            initialfile=default_name,
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.fig.savefig(path, dpi=200, facecolor=self.fig.get_facecolor())
            logger.info(f"[CAL] Diagnostics exported to {path}")
            messagebox.showinfo("Export complete",
                                f"Saved to:\n{path}", parent=self)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to export calibration diagnostics: {e}")
            messagebox.showerror("Export failed", str(e), parent=self)
