import gc
import logging
from typing import Dict, List, Optional

import numpy as np

import tkinter as tk
from tkinter import ttk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.pyplot as plt

from usma.models import LightweightHitData, AppConfig, HybridCalibrationEngine
from usma.utils import TextHandler

logger = logging.getLogger(__name__)


class GraphViewerFrame(ttk.LabelFrame):
    """Embedded matplotlib graph viewer with hit navigation and console output."""
    
    PLOT_TYPES = ['Signal', 'FFT', 'Lowpass Comparison', 'Residual Analysis', 'Run Summary']
    SIGNAL_SOURCES = ['All', 'FRF', 'PSD', 'Coherence']
    MAX_HISTORY = 25  # Reduced from 50 to limit memory usage

    def __init__(self, parent):
        super().__init__(parent, text="Live Graph & Console")
        self.current_plot_index = 0
        self.current_hit_index = -1
        self.hit_history: List[LightweightHitData] = []
        self.run_history = {}
        self.app_config = None
        self.text_handler = None  # Will be set up after console is created
        self.signal_filter = "All"  # Current signal source filter
        self.calibration_engine: Optional[HybridCalibrationEngine] = None

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

        # Signal Source filter with arrows
        source_frame = ttk.LabelFrame(nav_frame, text="Signal")
        source_frame.pack(side=tk.LEFT, padx=10)

        self.prev_source_btn = ttk.Button(source_frame, text="<", command=self._prev_signal_source, width=3)
        self.prev_source_btn.pack(side=tk.LEFT, padx=2)

        self.signal_source_var = tk.StringVar(value="All")
        self.signal_source_selector = ttk.Combobox(source_frame, textvariable=self.signal_source_var,
                                                    values=self.SIGNAL_SOURCES, state='readonly', width=10)
        self.signal_source_selector.pack(side=tk.LEFT, padx=2)
        self.signal_source_selector.bind("<<ComboboxSelected>>", self._on_signal_source_changed)

        self.next_source_btn = ttk.Button(source_frame, text=">", command=self._next_signal_source, width=3)
        self.next_source_btn.pack(side=tk.LEFT, padx=2)

        # Dedicated Cal. Diagnostics window launcher. Disabled until the
        # calibration engine has accumulated at least 6 signals.
        self.cal_diag_btn = ttk.Button(nav_frame, text="Cal. Diagnostics",
                                       command=self._open_cal_diagnostics,
                                       state=tk.DISABLED)
        self.cal_diag_btn.pack(side=tk.RIGHT, padx=5)

        self.hit_info_label = ttk.Label(nav_frame, text="No data", font=("Segoe UI", 9))
        self.hit_info_label.pack(side=tk.RIGHT, padx=10)

        # Singleton handle for the Cal. Diagnostics Toplevel window.
        self._cal_diag_window = None

        # Graph area (takes all remaining space)
        self.figure = Figure(figsize=(6, 4), dpi=100, facecolor='#1E1E1E')
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        self.ax.set_title('Waiting for data...', color='white')

        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Console is now created externally via setup_console()
        self.console_text = None
        self.text_handler = None

        self._update_navigation_state()

    def setup_console(self, parent):
        """Create the console widget in an external parent frame.

        Called by the main window to place the console in a collapsible panel.
        """
        console_inner = ttk.Frame(parent)
        console_inner.pack(fill=tk.BOTH, expand=True)

        console_scroll = ttk.Scrollbar(console_inner, orient=tk.VERTICAL)
        console_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.console_text = tk.Text(console_inner, wrap=tk.WORD, bg='#1E1E1E', fg='#00FF00',
                                    font=('Consolas', 9), state='disabled',
                                    yscrollcommand=console_scroll.set, height=8)
        self.console_text.pack(fill=tk.BOTH, expand=True)
        console_scroll.config(command=self.console_text.yview)

        self.text_handler = TextHandler(self.console_text)
        logger.addHandler(self.text_handler)

        clear_btn = ttk.Button(parent, text="Clear", command=self._clear_console)
        clear_btn.pack(side=tk.RIGHT, padx=2, pady=2)
    
    def _clear_console(self):
        """Clear the console text widget."""
        if self.console_text is None:
            return
        self.console_text.configure(state='normal')
        self.console_text.delete('1.0', tk.END)
        self.console_text.configure(state='disabled')
    
    def _prev_hit(self):
        for i in range(self.current_hit_index - 1, -1, -1):
            if self._matches_filter(self.hit_history[i]):
                self.current_hit_index = i
                self._update_plot()
                self._update_navigation_state()
                return

    def _next_hit(self):
        for i in range(self.current_hit_index + 1, len(self.hit_history)):
            if self._matches_filter(self.hit_history[i]):
                self.current_hit_index = i
                self._update_plot()
                self._update_navigation_state()
                return
        
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

    def _on_signal_source_changed(self, event=None):
        self.signal_filter = self.signal_source_var.get()
        # Find last hit of this type in the full history
        for i in range(len(self.hit_history) - 1, -1, -1):
            if self._matches_filter(self.hit_history[i]):
                self.current_hit_index = i
                break
        self._update_plot()
        self._update_navigation_state()

    def _prev_signal_source(self):
        idx = self.SIGNAL_SOURCES.index(self.signal_source_var.get())
        idx = (idx - 1) % len(self.SIGNAL_SOURCES)
        self.signal_source_var.set(self.SIGNAL_SOURCES[idx])
        self._on_signal_source_changed()

    def _next_signal_source(self):
        idx = self.SIGNAL_SOURCES.index(self.signal_source_var.get())
        idx = (idx + 1) % len(self.SIGNAL_SOURCES)
        self.signal_source_var.set(self.SIGNAL_SOURCES[idx])
        self._on_signal_source_changed()

    def _matches_filter(self, hit_data: LightweightHitData) -> bool:
        if self.signal_filter == "All":
            return True
        filter_map = {"FRF": "frf", "PSD": "psd", "Coherence": "coherence"}
        return hit_data.signal_type == filter_map.get(self.signal_filter, "")

    def _get_filtered_history(self) -> List[LightweightHitData]:
        return [h for h in self.hit_history if self._matches_filter(h)]
    
    def _update_navigation_state(self):
        # Build filtered index list (positions in self.hit_history that match filter)
        # NOTE: Uses index-based lookup to avoid numpy array __eq__ crash
        filtered_indices = [i for i, h in enumerate(self.hit_history) if self._matches_filter(h)]
        total = len(filtered_indices)

        # Find current position within filtered list
        current_pos = 0
        if total > 0 and 0 <= self.current_hit_index < len(self.hit_history):
            if self.current_hit_index in filtered_indices:
                current_pos = filtered_indices.index(self.current_hit_index) + 1

        self.hit_label.config(text=f"{current_pos}/{total}")

        # Enable/disable based on whether there are prev/next filtered hits
        has_prev = any(i < self.current_hit_index for i in filtered_indices)
        has_next = any(i > self.current_hit_index for i in filtered_indices)
        self.prev_hit_btn.config(state=tk.NORMAL if has_prev else tk.DISABLED)
        self.next_hit_btn.config(state=tk.NORMAL if has_next else tk.DISABLED)

        if 0 <= self.current_hit_index < len(self.hit_history):
            hit_data = self.hit_history[self.current_hit_index]
            type_tag = hit_data.signal_type.upper()
            self.hit_info_label.config(text=f"[{type_tag}] {hit_data.hit_key}")
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

        # Only auto-advance to this new signal if it matches the current filter
        # (sticky selection: user's chosen filter persists across events)
        new_idx = len(self.hit_history) - 1
        if self._matches_filter(lightweight_data):
            self.current_hit_index = new_idx

        self._update_plot()
        self._update_navigation_state()
        
    def update_calibration_diagnostics(self, engine: HybridCalibrationEngine,
                                       config_path: str = ""):
        """Update engine reference, toggle the Cal. Diagnostics button state,
        and forward the update to the open diagnostics window if any.
        """
        self.calibration_engine = engine
        if config_path:
            self._calibration_config_path = config_path
        can_show = engine is not None and engine.total_signals >= 1
        try:
            self.cal_diag_btn.config(state=tk.NORMAL if can_show else tk.DISABLED)
        except tk.TclError:
            pass

        # Push to the open diagnostics window if it exists and is alive.
        try:
            if (self._cal_diag_window is not None
                    and self._cal_diag_window.winfo_exists()):
                self._cal_diag_window.update_engine(
                    engine, getattr(self, '_calibration_config_path', ""))
        except tk.TclError:
            self._cal_diag_window = None

    def _open_cal_diagnostics(self):
        """Open (or bring to front) the dedicated Cal. Diagnostics window."""
        from usma.gui.cal_diagnostics import CalibrationDiagnosticsWindow
        cfg_path = getattr(self, '_calibration_config_path', "")
        try:
            if (self._cal_diag_window is not None
                    and self._cal_diag_window.winfo_exists()):
                self._cal_diag_window.update_engine(self.calibration_engine,
                                                    cfg_path)
                self._cal_diag_window.deiconify()
                self._cal_diag_window.lift()
                return
        except tk.TclError:
            self._cal_diag_window = None
        self._cal_diag_window = CalibrationDiagnosticsWindow(
            self.winfo_toplevel(), self.calibration_engine,
            config_path=cfg_path
        )

    def _update_plot(self):
        if not self.hit_history:
            return

        # Guard: snap current_hit_index into range or onto a filter match.
        if self.current_hit_index < 0 or self.current_hit_index >= len(self.hit_history):
            self.current_hit_index = len(self.hit_history) - 1
        if not self._matches_filter(self.hit_history[self.current_hit_index]):
            # Search for the nearest-filter-matching hit, preferring later hits.
            found = -1
            for i in range(len(self.hit_history) - 1, -1, -1):
                if self._matches_filter(self.hit_history[i]):
                    found = i
                    break
            if found < 0:
                return
            self.current_hit_index = found

        hit_data = self.hit_history[self.current_hit_index]
        plot_type = self.plot_type_var.get()

        try:
            # Special case: "All" + "Signal" → stacked subplots for each signal type
            if self.signal_filter == "All" and plot_type == "Signal":
                self._plot_all_signals_stacked()
            else:
                self.figure.clear()
                self.ax = self.figure.add_subplot(111)
                self.ax.set_facecolor('#2E2E2E')
                self.ax.tick_params(axis='both', colors='white')

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
            self.figure.clear()
            self.ax = self.figure.add_subplot(111)
            self.ax.set_facecolor('#2E2E2E')
            self.ax.text(0.5, 0.5, f'Error: {str(e)[:50]}',
                        transform=self.ax.transAxes, ha='center', va='center', color='red')

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _plot_all_signals_stacked(self):
        """When filter=All and plot=Signal, show all signal types stacked
        vertically.

        The currently selected hit (``self.current_hit_index``) is always
        displayed at its correct slot in the stack. The two other signal
        types show their most recent entry, so the user sees context while
        navigating.
        """
        if not self.hit_history:
            return

        hits_by_type: Dict[str, LightweightHitData] = {}
        selected: Optional[LightweightHitData] = None
        if 0 <= self.current_hit_index < len(self.hit_history):
            selected = self.hit_history[self.current_hit_index]
            hits_by_type[selected.signal_type] = selected

        # Fill in the other types with their latest hit — but do not
        # overwrite the selected hit.
        for h in reversed(self.hit_history):
            if h.signal_type not in hits_by_type:
                hits_by_type[h.signal_type] = h
            if len(hits_by_type) >= 3:
                break

        type_order = [t for t in ["frf", "psd", "coherence"] if t in hits_by_type]
        n = len(type_order)
        latest_by_type = hits_by_type
        color_map = {"frf": "cyan", "psd": "#FF9800", "coherence": "#00E676"}

        self.figure.clear()
        for idx, sig_type in enumerate(type_order):
            ax = self.figure.add_subplot(n, 1, idx + 1)
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white', labelsize=7)
            for spine in ax.spines.values():
                spine.set_color('white')

            hit = latest_by_type[sig_type]
            is_selected = selected is not None and hit is selected
            if hit.signal_physical is not None and len(hit.signal_physical) > 0:
                num_pts = len(hit.signal_physical)
                freq_ax = np.linspace(hit.x_axis_min, hit.x_axis_max, num_pts)
                ax.plot(freq_ax, hit.signal_physical,
                        color=color_map.get(sig_type, "cyan"),
                        linewidth=1.6 if is_selected else 1.2)
                if sig_type == "coherence":
                    ax.set_ylim(-0.05, 1.05)
            title_prefix = "\u2605 " if is_selected else ""
            title_color = "#F1C40F" if is_selected else "white"
            ax.set_title(f'{title_prefix}[{sig_type.upper()}] {hit.hit_key}',
                         color=title_color, fontsize=8)
            ax.set_ylabel(hit.y_axis_unit, color='white', fontsize=7)
            if idx == n - 1:
                ax.set_xlabel('Frequency (Hz)', color='white', fontsize=7)
            ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_signal(self, hit_data: LightweightHitData):
        if hit_data.signal_physical is None or len(hit_data.signal_physical) == 0:
            self.ax.text(0.5, 0.5, 'No signal data', transform=self.ax.transAxes,
                        ha='center', va='center', color='white')
            return

        num_points = len(hit_data.signal_physical)
        freq_axis = np.linspace(hit_data.x_axis_min, hit_data.x_axis_max, num_points)

        type_tag = hit_data.signal_type.upper()
        color_map = {"frf": "cyan", "psd": "#FF9800", "coherence": "#00E676"}
        line_color = color_map.get(hit_data.signal_type, "cyan")

        self.ax.plot(freq_axis, hit_data.signal_physical, color=line_color, linewidth=1.5)
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Amplitude ({hit_data.y_axis_unit})', color='white')
        self.ax.set_title(f'[{type_tag}] Reconstructed Signal - {hit_data.hit_key}', color='white')
        if hit_data.signal_type == "coherence":
            self.ax.set_ylim(-0.05, 1.05)
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_fft(self, hit_data: LightweightHitData):
        self.ax.plot(hit_data.fft_freqs, hit_data.fft_mags, color='magenta', linewidth=1)
        
        if self.app_config:
            prefix = "psd_" if hit_data.signal_type == "psd" else ""
            cutoff = getattr(self.app_config, f"{prefix}fft_cutoff_frequency")
            self.ax.axvline(x=cutoff, color='yellow',
                           linestyle='--', linewidth=1, label=f'Cutoff: {cutoff:.2f}')
        
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
        self.ax.set_ylabel('Amplitude (pixels)', color='white')

        if self.app_config:
            prefix = "psd_" if hit_data.signal_type == "psd" else ""
            lp_cutoff = getattr(self.app_config, f"{prefix}lowpass_cutoff")
            title = f'Lowpass - {hit_data.hit_key} - Cutoff: {lp_cutoff:.3f}'
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
        
        # Use the dynamic threshold computed at analysis time
        threshold = hit_data.dynamic_threshold

        self.ax.plot(freq_axis, hit_data.residual_physical, 'c-', linewidth=1, label='Residual (px)')
        self.ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
        if threshold > 0:
            self.ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.2, label=f'±{threshold:.1f} px')
            self.ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.2)

        exceedances = np.abs(hit_data.residual_physical) > threshold
        if np.any(exceedances):
            self.ax.scatter(freq_axis[exceedances], hit_data.residual_physical[exceedances],
                          c='red', s=12, zorder=5, alpha=0.7)

        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel('Residual (pixels)', color='white')
        classification = "BAD" if hit_data.lowpass_is_bad_hit else "GOOD"
        ratio_pct = (hit_data.dynamic_threshold / hit_data.max_abs_residual * 100) if hit_data.max_abs_residual > 1e-9 else 0
        self.ax.set_title(
            f'Residual (px) - {hit_data.hit_key} - Thr: {ratio_pct:.0f}% of max '
            f'- Exc: {hit_data.exceedance_count} ({hit_data.exceedance_ratio:.1%}) - {classification}',
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