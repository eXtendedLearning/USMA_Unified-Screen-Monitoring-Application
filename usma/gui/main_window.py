import os
import json
import logging
import queue
import threading
from typing import Dict, Optional

import numpy as np
import cv2

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

from usma.models import *
from usma.calibration import *
from usma.utils import *
from usma.audio import SOUND_DEVICE_AVAILABLE
from usma.monitor import ScreenMonitor
from usma.gui.dialogs import CalibrationChoiceDialog
from usma.gui.hsv_calibration import HSVCalibrationWindow
from usma.gui.overlay import RegionOverlay
from usma.gui.config_tool import ConfigToolWindow
from usma.gui.graph_viewer import GraphViewerFrame
from usma.theory.wiki_viewer import show_theory_page, make_info_button
import usma.calibration_export as cal_export

logger = logging.getLogger(__name__)


class MonitorControlGUI:
    def __init__(self, root, config_path=None, calibration_mode: bool = False):
        self.root = root
        self.calibration_mode = calibration_mode
        self.root.title(f"USMA v{APP_VERSION}")
        self.ui_scale = tk.DoubleVar(value=float(self.root.tk.call('tk', 'scaling')))
        self._set_initial_window_geometry()

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

        # Initialise calibration engine
        self.calibration_engine = HybridCalibrationEngine()

        # If config has saved calibration data, reconstruct the engine from it
        if config_path:
            try:
                with open(config_path, 'r') as f:
                    config_data = json.load(f)
                cal_data = config_data.get('_calibration')
                if cal_data and isinstance(cal_data, dict) and cal_data.get('signals'):
                    self.calibration_engine = HybridCalibrationEngine.from_dict(cal_data)
                    level = self.calibration_engine.confidence_level
                    total = self.calibration_engine.total_signals
                    logger.info(f"[CAL] Loaded calibration data: {total} signals, Level {level}")
            except (json.JSONDecodeError, IOError, KeyError) as e:
                logger.warning(f"[CAL] Could not load calibration data: {e}")

        self.manual_points_vars = {
            'run': tk.StringVar(value='Run 1'),
            'hammer_point': tk.StringVar(value='P1'),
            'hammer_dir': tk.StringVar(value='-Z'),
            'response_point': tk.StringVar(value='P1'),
            'response_dir': tk.StringVar(value='-Z')
        }
        initial_freq = 1.0/self.monitor.app_config.screenshot_interval if self.monitor.app_config.screenshot_interval > 0 else 4.0
        self.sample_frequency = tk.DoubleVar(value=round(initial_freq, 2))
        
        self.param_vars = {}
        self.overlay = None
        self._setup_main_gui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # Apply loaded calibration estimates now that GUI is set up
        if self.calibration_engine.can_estimate:
            estimates = self.calibration_engine.get_estimates()
            if estimates:
                self._apply_calibrated_params(estimates)
                level = self.calibration_engine.confidence_level
                total = self.calibration_engine.total_signals
                self._update_calibration_status(level, total)
    
    def _setup_main_gui(self):
        # Status bar at bottom (pack first so it stays at bottom)
        self.status_label = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # Main horizontal PanedWindow: left controls | right visualization
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        # ======================= LEFT COLUMN (Controls) =======================
        left_outer = ttk.Frame(main_pane)
        main_pane.add(left_outer, weight=2)

        # Scrollable left panel
        self.main_canvas = tk.Canvas(left_outer, highlightthickness=0, width=380)
        left_scrollbar = ttk.Scrollbar(left_outer, orient=tk.VERTICAL, command=self.main_canvas.yview)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.main_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.main_canvas.configure(yscrollcommand=left_scrollbar.set)

        self.scrollable_frame = ttk.Frame(self.main_canvas, padding=5)
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))
        )
        self.canvas_window = self.main_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.main_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        left_outer.bind("<Configure>", self._on_main_resize)

        frame = self.scrollable_frame

        # --- Configuration ---
        config_frame = ttk.LabelFrame(frame, text="Configuration")
        config_frame.pack(fill=tk.X, pady=3)
        ttk.Entry(config_frame, textvariable=self.config_path, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=3)
        self.load_button = ttk.Button(config_frame, text="Load...", command=self._load_config)
        self.load_button.pack(side=tk.LEFT, padx=2)
        self.edit_button = ttk.Button(config_frame, text="Edit...", command=self._launch_config_tool)
        self.edit_button.pack(side=tk.LEFT, padx=2)
        ttk.Button(config_frame, text="View...", command=self._open_view_settings).pack(side=tk.LEFT, padx=2)

        # --- Controls ---
        control_frame = ttk.LabelFrame(frame, text="Controls")
        control_frame.pack(fill=tk.X, pady=3)

        ctrl_row1 = ttk.Frame(control_frame)
        ctrl_row1.pack(fill=tk.X, padx=5, pady=3)
        self.start_stop_button = ttk.Button(ctrl_row1, text="Start Monitoring", command=self._toggle_monitoring)
        self.start_stop_button.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        self.overlay_check = ttk.Checkbutton(ctrl_row1, text="Overlay", variable=self.is_overlay_on, command=self._toggle_overlay)
        self.overlay_check.pack(side=tk.LEFT, padx=5)

        ctrl_row2 = ttk.Frame(control_frame)
        ctrl_row2.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(ctrl_row2, text="Sample Freq (Hz):").pack(side=tk.LEFT, padx=(2,2))
        self.freq_spinbox = ttk.Spinbox(ctrl_row2, from_=0.1, to=30.0, increment=0.1, textvariable=self.sample_frequency, width=6)
        self.freq_spinbox.pack(side=tk.LEFT, padx=(0,5))
        self.events_only_check = ttk.Checkbutton(ctrl_row2, text="Events Only",
                                                  variable=self.log_events_only,
                                                  command=self._toggle_events_only)
        self.events_only_check.pack(side=tk.LEFT, padx=5)
        self.audio_check = ttk.Checkbutton(ctrl_row2, text="Audio", variable=self.audio_feedback_on, command=self._toggle_audio_feedback)
        self.audio_check.pack(side=tk.LEFT, padx=2)
        if not SOUND_DEVICE_AVAILABLE:
            self.audio_check.config(state=tk.DISABLED)
            self.audio_feedback_on.set(False)

        # --- Manual POI ---
        self.manual_points_frame = ttk.LabelFrame(frame, text="Manual POI Entry")
        self.manual_points_frame.pack(fill=tk.X, pady=3)
        pf = self.manual_points_frame

        self.manual_run_label = ttk.Label(pf, text="Run:")
        self.manual_run_label.grid(row=0, column=0, padx=3, pady=1)
        self.manual_run_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['run'], width=8)
        self.manual_run_entry.grid(row=0, column=1)

        self.manual_hammer_label = ttk.Label(pf, text="Hammer:")
        self.manual_hammer_label.grid(row=0, column=2, padx=3)
        self.manual_hammer_point_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_point'], width=6)
        self.manual_hammer_point_entry.grid(row=0, column=3)
        self.manual_hammer_dir_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_dir'], width=4)
        self.manual_hammer_dir_entry.grid(row=0, column=4)

        self.manual_response_label = ttk.Label(pf, text="Response:")
        self.manual_response_label.grid(row=1, column=2, padx=3)
        self.manual_response_point_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['response_point'], width=6)
        self.manual_response_point_entry.grid(row=1, column=3)
        self.manual_response_dir_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['response_dir'], width=4)
        self.manual_response_dir_entry.grid(row=1, column=4)

        # --- Logging ---
        logging_controls_frame = ttk.LabelFrame(frame, text="Logging")
        logging_controls_frame.pack(fill=tk.X, pady=3)

        logging_main_frame = ttk.Frame(logging_controls_frame)
        logging_main_frame.pack(fill=tk.X, padx=5)

        log_row1 = ttk.Frame(logging_main_frame)
        log_row1.pack(fill=tk.X, pady=1)
        self.verbose_check = ttk.Checkbutton(log_row1, text="Verbose Log", variable=self.verbose_logging_on, command=self._toggle_verbose_log_options_state)
        self.verbose_check.pack(side=tk.LEFT)
        self.img_log_check = ttk.Checkbutton(log_row1, text="Image Logs", variable=self.image_logging_on, command=self._toggle_img_log_options_state)
        self.img_log_check.pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(log_row1, text=".unv", variable=self.log_to_unv).pack(side=tk.LEFT, padx=5)

        # Verbose log options
        self.verbose_log_options_frame = ttk.Frame(logging_main_frame)
        self.verbose_log_options_frame.pack(fill=tk.X, pady=1)
        vrow1 = ttk.Frame(self.verbose_log_options_frame)
        vrow1.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(vrow1, text="Config", variable=self.vlog_opt_config).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="Mask", variable=self.vlog_opt_mask).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="OCR", variable=self.vlog_opt_ocr).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="Classify", variable=self.vlog_opt_classification).pack(side=tk.LEFT, padx=2)
        vrow2 = ttk.Frame(self.verbose_log_options_frame)
        vrow2.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(vrow2, text="FFT", variable=self.vlog_opt_fft).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow2, text="Lowpass", variable=self.vlog_opt_lowpass).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow2, text="FileSave", variable=self.vlog_opt_filesave).pack(side=tk.LEFT, padx=2)

        # Image log options
        self.img_log_options_frame = ttk.Frame(logging_main_frame)
        self.img_log_options_frame.pack(fill=tk.X, pady=1)
        irow1 = ttk.Frame(self.img_log_options_frame)
        irow1.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(irow1, text="ROI", variable=self.log_opt_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="Masks", variable=self.log_opt_color_filter).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="OCR", variable=self.log_opt_ocr_images).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="Signal", variable=self.log_opt_signal_plot).pack(side=tk.LEFT, padx=2)
        irow2 = ttk.Frame(self.img_log_options_frame)
        irow2.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(irow2, text="FFT", variable=self.log_opt_fft_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Lowpass", variable=self.log_opt_lowpass_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Residual", variable=self.log_opt_residual_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Summary", variable=self.log_opt_summary_chart).pack(side=tk.LEFT, padx=2)

        # Classification lights & calibration — use frame as parent for dynamic panels
        self.right_panel = frame  # reuse for _setup_* methods that expect right_panel
        self._setup_classification_lights(frame)
        self._setup_calibration_status_bar(frame)

        if self.calibration_mode:
            self._setup_calibration_panel(frame)
        else:
            self._setup_analysis_params(frame)

        # ======================= RIGHT COLUMN (Visualization) =======================
        right_outer = ttk.Frame(main_pane)
        main_pane.add(right_outer, weight=3)

        # --- Prominent classification result banner ---
        self.class_var = tk.StringVar(value="Overall: --")

        feedback_banner = tk.Frame(right_outer, bg="gray", height=50)
        feedback_banner.pack(fill=tk.X)
        feedback_banner.pack_propagate(False)

        self.status_light = tk.Canvas(feedback_banner, width=40, height=40, bg="gray", highlightthickness=0)
        self.status_light.pack(side=tk.LEFT, padx=8, pady=5)

        tk.Label(feedback_banner, textvariable=self.class_var,
                 font=("Segoe UI", 16, "bold"), bg="gray", fg="white").pack(side=tk.LEFT, padx=5)

        # ⓘ next to the classification text — opens hit_classification page.
        # Match the banner's gray background so the label doesn't look
        # transparent against the darker TkDefault frame colour.
        _info_cls = make_info_button(feedback_banner, self.root,
                                     "hit_classification",
                                     side=tk.LEFT, padx=(10, 0))
        _info_cls.configure(bg="gray")

        # Compact feedback details
        self.hf_ratio_var = tk.StringVar(value="FFT(FRF): --")
        self.exceedance_var = tk.StringVar(value="LP(FRF): --")
        self.psd_ratio_var = tk.StringVar(value="FFT(PSD): --")
        self.psd_exceedance_var = tk.StringVar(value="LP(PSD): --")
        self.coherence_var = tk.StringVar(value="Coh: --")
        self.status_var = tk.StringVar(value="Status: --")
        self.overload_var = tk.StringVar(value="Overload: --")
        self.points_var = tk.StringVar(value="Points: --")

        detail_frame = tk.Frame(feedback_banner, bg="gray")
        detail_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        detail_row1 = tk.Frame(detail_frame, bg="gray")
        detail_row1.pack(fill=tk.X)
        tk.Label(detail_row1, textvariable=self.hf_ratio_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)
        tk.Label(detail_row1, textvariable=self.exceedance_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)
        tk.Label(detail_row1, textvariable=self.psd_ratio_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)
        tk.Label(detail_row1, textvariable=self.psd_exceedance_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)
        detail_row2 = tk.Frame(detail_frame, bg="gray")
        detail_row2.pack(fill=tk.X)
        tk.Label(detail_row2, textvariable=self.coherence_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)
        # Inline ⓘ for coherence readout (needs bg="gray" to match banner)
        _coh_info = tk.Label(detail_row2, text="\u24D8", font=("Segoe UI", 9),
                             fg="#5DADE2", bg="gray", cursor="hand2", padx=2)
        _coh_info.bind("<Button-1>",
                       lambda e: show_theory_page(self.root, "coherence_analysis"))
        _coh_info.pack(side=tk.LEFT, padx=1)
        tk.Label(detail_row2, textvariable=self.status_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)
        tk.Label(detail_row2, textvariable=self.overload_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)
        tk.Label(detail_row2, textvariable=self.points_var, font=("Segoe UI", 8), bg="gray", fg="white").pack(side=tk.LEFT, padx=3)

        # --- Graph Viewer (dominant) ---
        self.graph_viewer = GraphViewerFrame(right_outer)
        self.graph_viewer.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.graph_viewer.calibration_engine = self.calibration_engine
        self.graph_viewer.update_calibration_diagnostics(
            self.calibration_engine, self.config_path.get()
        )

        # --- Collapsible Console ---
        self._console_visible = tk.BooleanVar(value=False)
        console_toggle_frame = ttk.Frame(right_outer)
        console_toggle_frame.pack(fill=tk.X)
        self._console_toggle_btn = ttk.Checkbutton(
            console_toggle_frame, text="Show Console", variable=self._console_visible,
            command=self._toggle_console)
        self._console_toggle_btn.pack(side=tk.LEFT, padx=5, pady=2)

        self._console_frame = ttk.LabelFrame(right_outer, text="Console Output")
        # Console starts hidden — don't pack yet
        self.graph_viewer.setup_console(self._console_frame)

        self._toggle_img_log_options_state()
        self._toggle_verbose_log_options_state()
        self._update_manual_points_state()
    
    def _on_mousewheel(self, event):
        """Handle mouse wheel scrolling."""
        try:
            self.main_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        except tk.TclError:
            pass
    
    def _on_main_resize(self, event):
        """Resize canvas window when left panel resizes."""
        canvas_width = event.width
        self.main_canvas.itemconfig(self.canvas_window, width=canvas_width)

    def _set_initial_window_geometry(self):
        _left, _top, screen_w, screen_h = self._get_current_monitor_bounds()
        width = min(1280, max(900, screen_w - 80))
        height = min(800, max(650, screen_h - 100))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(900, 650)

    def _get_current_monitor_bounds(self):
        try:
            import mss
            self.root.update_idletasks()
            center_x = self.root.winfo_x() + max(1, self.root.winfo_width()) // 2
            center_y = self.root.winfo_y() + max(1, self.root.winfo_height()) // 2
            with mss.mss() as sct:
                for monitor in sct.monitors[1:]:
                    left = int(monitor.get("left", 0))
                    top = int(monitor.get("top", 0))
                    width = int(monitor["width"])
                    height = int(monitor["height"])
                    if left <= center_x < left + width and top <= center_y < top + height:
                        return left, top, width, height
        except Exception:
            pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _open_view_settings(self):
        win = tk.Toplevel(self.root)
        win.title("GUI View Settings")
        win.transient(self.root)
        win.resizable(False, False)

        width_var = tk.IntVar(value=max(self.root.winfo_width(), 900))
        height_var = tk.IntVar(value=max(self.root.winfo_height(), 650))
        scale_var = tk.DoubleVar(value=float(self.root.tk.call('tk', 'scaling')))

        body = ttk.Frame(win, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text="Window size").grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 4))
        ttk.Label(body, text="Width").grid(row=1, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        ttk.Spinbox(body, from_=900, to=3840, increment=40, textvariable=width_var, width=8).grid(row=1, column=1, sticky=tk.W, pady=2)
        ttk.Label(body, text="Height").grid(row=2, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        ttk.Spinbox(body, from_=650, to=2160, increment=40, textvariable=height_var, width=8).grid(row=2, column=1, sticky=tk.W, pady=2)

        ttk.Label(body, text="Magnification").grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(10, 4))
        ttk.Label(body, text="Scale").grid(row=4, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        ttk.Spinbox(body, from_=0.65, to=2.00, increment=0.05, textvariable=scale_var, width=8, format="%.2f").grid(row=4, column=1, sticky=tk.W, pady=2)

        presets = ttk.Frame(body)
        presets.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(10, 4))
        ttk.Button(presets, text="Fit Screen", command=lambda: self._fill_view_vars_from_screen(width_var, height_var, scale_var)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(presets, text="Compact", command=lambda: self._set_view_vars(width_var, height_var, scale_var, 1000, 700, 0.85)).pack(side=tk.LEFT)

        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(actions, text="Apply", command=lambda: self._apply_view_settings(win, width_var, height_var, scale_var)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(actions, text="Close", command=win.destroy).pack(side=tk.LEFT)

        win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{x}+{y}")
        win.grab_set()

    def _set_view_vars(self, width_var, height_var, scale_var, width, height, scale):
        width_var.set(width)
        height_var.set(height)
        scale_var.set(scale)

    def _fill_view_vars_from_screen(self, width_var, height_var, scale_var):
        _left, _top, monitor_w, monitor_h = self._get_current_monitor_bounds()
        width = max(900, monitor_w - 80)
        height = max(650, monitor_h - 100)
        scale = 0.85 if width < 1400 or height < 800 else 1.0
        self._set_view_vars(width_var, height_var, scale_var, width, height, scale)

    def _apply_view_settings(self, win, width_var, height_var, scale_var):
        try:
            width = max(900, int(width_var.get()))
            height = max(650, int(height_var.get()))
            scale = max(0.65, min(2.0, float(scale_var.get())))
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid View Settings", "Use numeric width, height, and scale values.", parent=win)
            return
        self.root.tk.call('tk', 'scaling', scale)
        self.ui_scale.set(scale)
        self.root.geometry(f"{width}x{height}")
        self.status_label.config(text=f"View updated: {width}x{height}, scale {scale:.2f}")

    def _toggle_console(self):
        """Show or hide the console panel."""
        if self._console_visible.get():
            self._console_frame.pack(fill=tk.BOTH, padx=2, pady=(0, 2))
            self._console_frame.configure(height=150)
        else:
            self._console_frame.pack_forget()

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
            cfg = self.monitor.app_config
            cfg.fft_cutoff_frequency = self.param_vars['fft_cutoff_frequency'].get()
            cfg.fft_energy_ratio_threshold = self.param_vars['fft_energy_ratio_threshold'].get()
            cfg.lowpass_cutoff = self.param_vars['lowpass_cutoff'].get()
            cfg.lowpass_filter_order = int(self.param_vars['lowpass_filter_order'].get())
            cfg.relative_residual_ratio = self.param_vars['relative_residual_ratio'].get()
            cfg.exceedance_ratio_threshold = self.param_vars['exceedance_ratio_threshold'].get()

            cfg.psd_fft_cutoff_frequency = self.param_vars['psd_fft_cutoff_frequency'].get()
            cfg.psd_fft_energy_ratio_threshold = self.param_vars['psd_fft_energy_ratio_threshold'].get()
            cfg.psd_lowpass_cutoff = self.param_vars['psd_lowpass_cutoff'].get()
            cfg.psd_lowpass_filter_order = int(self.param_vars['psd_lowpass_filter_order'].get())
            cfg.psd_relative_residual_ratio = self.param_vars['psd_relative_residual_ratio'].get()
            cfg.psd_exceedance_ratio_threshold = self.param_vars['psd_exceedance_ratio_threshold'].get()

            # Also update the graph viewer's reference
            self.graph_viewer.app_config = cfg

            logger.info(f"Parameters applied — FRF: FFT={cfg.fft_cutoff_frequency:.4f}, "
                        f"E.Ratio={cfg.fft_energy_ratio_threshold:.4f}, LP={cfg.lowpass_cutoff:.4f}, "
                        f"Order={cfg.lowpass_filter_order}, Ratio={cfg.relative_residual_ratio:.4f}, "
                        f"Exc={cfg.exceedance_ratio_threshold:.4f} | PSD: FFT={cfg.psd_fft_cutoff_frequency:.4f}, "
                        f"E.Ratio={cfg.psd_fft_energy_ratio_threshold:.4f}, LP={cfg.psd_lowpass_cutoff:.4f}, "
                        f"Order={cfg.psd_lowpass_filter_order}, Ratio={cfg.psd_relative_residual_ratio:.4f}, "
                        f"Exc={cfg.psd_exceedance_ratio_threshold:.4f}")
        except tk.TclError:
            pass  # User mid-edit, ignore

    def _setup_classification_lights(self, parent):
        self.enabled_methods = {'frf_fft': True, 'frf_lp': True, 'psd_fft': True, 'psd_lp': True}
        self.method_lights = {}
        self.last_analysis_result = None
        
        lights_frame = ttk.LabelFrame(parent, text="Classification Methods")
        lights_frame.pack(fill=tk.X, padx=5, pady=5)
        
        methods = [('FRF-FFT', 'frf_fft'), ('FRF-LP', 'frf_lp'), ('PSD-FFT', 'psd_fft'), ('PSD-LP', 'psd_lp')]
        for label_text, method_key in methods:
            container = ttk.Frame(lights_frame, cursor="hand2")
            container.pack(side=tk.LEFT, padx=5)
            
            try:
                bg_color = parent.master.winfo_toplevel().cget('bg')
            except tk.TclError:
                bg_color = 'SystemButtonFace'

            canvas = tk.Canvas(container, width=12, height=12, bg=bg_color, highlightthickness=0)
            canvas.pack(side=tk.LEFT, pady=2)
            canvas.create_oval(1, 1, 11, 11, fill="gray", tags="light")
            lbl = ttk.Label(container, text=label_text)
            lbl.pack(side=tk.LEFT, padx=(2, 0))
            self.method_lights[method_key] = canvas
            
            toggle_func = lambda e, k=method_key: self._toggle_method(k)
            container.bind("<Button-1>", toggle_func)
            canvas.bind("<Button-1>", toggle_func)
            lbl.bind("<Button-1>", toggle_func)

        # One ⓘ for the whole lights row — opens the hit_classification page
        # which explains all four methods and the voting logic together.
        info_frame = ttk.Frame(lights_frame)
        info_frame.pack(side=tk.RIGHT, padx=5)
        make_info_button(info_frame, self.root, "hit_classification",
                         side=tk.LEFT)

    def _toggle_method(self, method_key: str):
        self.enabled_methods[method_key] = not self.enabled_methods[method_key]
        if getattr(self, 'last_analysis_result', None) is not None:
            self._update_feedback_ui(self.last_analysis_result)
        else:
            self._update_method_lights(None)

    def _update_method_lights(self, result: Optional[FrameAnalysisResult]):
        for key, canvas in self.method_lights.items():
            if not self.enabled_methods.get(key, True):
                canvas.itemconfig("light", fill="dim gray")
            elif result is None:
                canvas.itemconfig("light", fill="gray")
            else:
                is_bad = False
                has_data = False
                if key == 'frf_fft' and result.overall_is_hf is not None:
                    is_bad = result.overall_is_hf
                    has_data = True
                elif key == 'frf_lp' and result.overall_lowpass_bad is not None:
                    is_bad = result.overall_lowpass_bad
                    has_data = True
                elif key == 'psd_fft' and result.psd_overall_is_hf is not None:
                    is_bad = result.psd_overall_is_hf
                    has_data = True
                elif key == 'psd_lp' and result.psd_overall_lowpass_bad is not None:
                    is_bad = result.psd_overall_lowpass_bad
                    has_data = True
                    
                if has_data:
                    canvas.itemconfig("light", fill="red" if is_bad else "green")
                else:
                    canvas.itemconfig("light", fill="gray")

    def update_feedback_panel(self, result: FrameAnalysisResult):
        self.root.after(0, self._update_feedback_ui, result)
        
    def _update_feedback_ui(self, result: FrameAnalysisResult):
        self.last_analysis_result = result
        # --- Classification status light (uses combined FRF+PSD logic) ---
        if result.overall_is_hf is not None or result.psd_overall_is_hf is not None:
            enabled = getattr(self, 'enabled_methods', None)
            classification, color = self.monitor.classify_hit(result, enabled_methods=enabled)
            self.class_var.set(f"Overall: {classification}")
            self.status_light.config(bg=color)

        if hasattr(self, '_update_method_lights'):
            self._update_method_lights(result)

        # --- FRF readout (compact labels for banner) ---
        if result.avg_energy_ratio is not None:
            self.hf_ratio_var.set(f"FFT(FRF): {result.avg_energy_ratio:.3e}")
        if result.avg_exceedance_count is not None:
            self.exceedance_var.set(f"LP(FRF): {result.avg_exceedance_count:.0f} ({result.avg_exceedance_ratio:.1%})")

        # --- PSD readout (if PSD regions exist) ---
        if result.psd_avg_energy_ratio is not None:
            self.psd_ratio_var.set(f"FFT(PSD): {result.psd_avg_energy_ratio:.3e}")
        if result.psd_avg_exceedance_count is not None and result.psd_avg_exceedance_ratio is not None:
            self.psd_exceedance_var.set(f"LP(PSD): {result.psd_avg_exceedance_count:.0f} ({result.psd_avg_exceedance_ratio:.1%})")

        # --- Coherence readout (if coherence ROIs exist) ---
        if result.coherence_results:
            coh_values = list(result.coherence_results.values())
            avg_coh = float(sum(c.mean_coherence for c in coh_values) / len(coh_values))
            avg_bad = float(sum(c.normalized_badness for c in coh_values) / len(coh_values))
            # Determine trend from tracking state
            trend = "UNKNOWN"
            for _cname, cstate in self.monitor.coherence_tracking.items():
                trend = cstate.trend
                break
            self.coherence_var.set(f"Coh: {avg_coh:.3f} ({trend}) | Bad: {avg_bad:.4f}")

        # --- Status and overload ---
        self.status_var.set(f"Status: {result.status_text}")
        if result.current_averages is not None:
            self.overload_var.set(f"Averages: {result.current_averages}")
        else:
            self.overload_var.set(f"Overload: {result.overload_text}")

        p = result.points_info
        self.points_var.set(f"Points: {p.run} | H: {p.hammer_point}{p.hammer_dir} | R: {p.response_point}{p.response_dir}")
        
    def _on_plot_data(self, lightweight_data: LightweightHitData, run_history: Dict):
        """Thread-safe callback to update graph viewer."""
        self.root.after(0, self._update_graph_viewer, lightweight_data, run_history)
            
    def _update_graph_viewer(self, lightweight_data: LightweightHitData, run_history: Dict):
        self.graph_viewer.update_data(lightweight_data, run_history, self.monitor.app_config)
        # In calibration mode, notify the calibration panel of new signal
        if self.calibration_mode and hasattr(self, 'cal_pending_signal'):
            self._cal_on_new_signal(lightweight_data)
        # In live analysis mode, notify the live calibration buttons
        elif not self.calibration_mode and hasattr(self, 'live_cal_pending_signal'):
            self._live_cal_on_new_signal(lightweight_data)
        
    def _reset_feedback_ui(self):
        self.last_analysis_result = None
        if hasattr(self, '_update_method_lights'):
            self._update_method_lights(None)
        self.class_var.set("Overall: --")
        self.status_light.config(bg="gray")
        self.hf_ratio_var.set("FFT(FRF): --")
        self.exceedance_var.set("LP(FRF): --")
        self.psd_ratio_var.set("FFT(PSD): --")
        self.psd_exceedance_var.set("LP(PSD): --")
        self.coherence_var.set("Coh: --")
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

    def _reload_calibration_from_current_config(self):
        """Rebuild the calibration engine from the currently-loaded config file.

        Called after runtime config switches (Load... / config tool save) so
        calibration status, diagnostics, and derived thresholds don't go stale.
        """
        path = self.config_path.get()
        new_engine = HybridCalibrationEngine()
        if path and os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    config_data = json.load(f)
                cal_data = config_data.get('_calibration')
                if cal_data and isinstance(cal_data, dict) and cal_data.get('signals'):
                    new_engine = HybridCalibrationEngine.from_dict(cal_data)
            except (json.JSONDecodeError, IOError, KeyError) as e:
                logger.warning(f"[CAL] Could not load calibration data on reload: {e}")

        self.calibration_engine = new_engine

        if hasattr(self, 'graph_viewer') and self.graph_viewer is not None:
            self.graph_viewer.calibration_engine = self.calibration_engine
            if hasattr(self.graph_viewer, 'update_calibration_diagnostics'):
                try:
                    self.graph_viewer.update_calibration_diagnostics(self.calibration_engine, self.config_path.get())
                except Exception as e:
                    logger.debug(f"[CAL] Could not refresh diagnostics on reload: {e}")

        level = self.calibration_engine.confidence_level
        total = self.calibration_engine.total_signals
        if hasattr(self, 'cal_status_label_bar'):
            self._update_calibration_status(level, total)

        if self.calibration_engine.can_estimate:
            estimates = self.calibration_engine.get_estimates()
            if estimates:
                self._apply_calibrated_params(estimates)
        if hasattr(self, 'param_vars'):
            self._sync_param_ui_from_config()

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
        self._reload_calibration_from_current_config()

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
            self._reload_calibration_from_current_config()
            if self.is_overlay_on.get():
                self._toggle_overlay()
                self._toggle_overlay()

        self.root.deiconify()

    def _setup_calibration_status_bar(self, parent):
        """Create the compact calibration status indicator."""
        # Wrap the status label and an ⓘ button in a single row so the info
        # button can sit to the right of the coloured status bar.
        cal_bar_frame = tk.Frame(parent)
        cal_bar_frame.pack(fill=tk.X, pady=(10, 0))

        self.cal_status_label_bar = tk.Label(cal_bar_frame,
                                             text="\u26A0 Not Calibrated",
                                             font=("Segoe UI", 9, "bold"),
                                             fg="white", bg="#E74C3C",
                                             anchor=tk.CENTER, padx=8, pady=2)
        self.cal_status_label_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        make_info_button(cal_bar_frame, self.root, "calibration_engine",
                         side=tk.RIGHT, padx=5)

        self._update_calibration_status(level=0, total_signals=0)

    def _update_calibration_status(self, level: int, total_signals: int):
        """Update the compact calibration status indicator."""
        colors = {0: '#E74C3C', 1: '#F39C12', 2: '#F1C40F', 3: '#3498DB', 4: '#2ECC71'}
        labels = {
            0: "\u26A0 Not Calibrated",
            1: f"\u26A0 Preliminary ({total_signals} signals)",
            2: f"\u25D0 Basic ({total_signals} signals)",
            3: f"\u25CF Solid ({total_signals} signals)",
            4: f"\u2713 Robust ({total_signals} signals)"
        }
        fg = "white" if level != 2 else "black"  # Yellow bg needs dark text
        self.cal_status_label_bar.config(
            text=labels.get(level, "Unknown"),
            bg=colors.get(level, '#E74C3C'),
            fg=fg
        )

    def _setup_calibration_panel(self, parent):
        """Create the compact calibration mode panel."""
        # Calibration state
        self.cal_good_count = 0
        self.cal_bad_count = 0
        self.cal_pending_signal = None

        self.cal_frame = ttk.LabelFrame(parent, text="Calibration")
        self.cal_frame.pack(fill=tk.X, pady=(5, 0))

        # "How calibration works" info row inside the label frame
        cal_info_row = ttk.Frame(self.cal_frame)
        cal_info_row.pack(fill=tk.X, padx=5, pady=(2, 0))
        ttk.Label(cal_info_row, text="How calibration works:",
                  font=("Segoe UI", 8)).pack(side=tk.LEFT)
        make_info_button(cal_info_row, self.root, "calibration_engine",
                         side=tk.LEFT)

        # Row 1: Buttons + counter (all inline)
        row1 = ttk.Frame(self.cal_frame)
        row1.pack(fill=tk.X, padx=5, pady=3)

        self.cal_good_btn = tk.Button(row1, text="\u2713 Good", font=("Segoe UI", 9, "bold"),
                                      bg="#27AE60", fg="white", width=7, state=tk.DISABLED,
                                      command=lambda: self._cal_classify("good"))
        self.cal_good_btn.pack(side=tk.LEFT, padx=2)

        self.cal_bad_btn = tk.Button(row1, text="\u2717 Bad", font=("Segoe UI", 9, "bold"),
                                     bg="#E74C3C", fg="white", width=7, state=tk.DISABLED,
                                     command=lambda: self._cal_classify("bad"))
        self.cal_bad_btn.pack(side=tk.LEFT, padx=2)

        self.cal_ignore_btn = tk.Button(row1, text="Skip", font=("Segoe UI", 9),
                                        bg="#95A5A6", fg="white", width=5, state=tk.DISABLED,
                                        command=lambda: self._cal_classify("ignore"))
        self.cal_ignore_btn.pack(side=tk.LEFT, padx=2)

        self.cal_counter_label = ttk.Label(row1, text="0G / 0B (need 3+3)",
                                           font=("Segoe UI", 8))
        self.cal_counter_label.pack(side=tk.LEFT, padx=(8, 2))

        # Row 2: Status + Finish button
        row2 = ttk.Frame(self.cal_frame)
        row2.pack(fill=tk.X, padx=5, pady=(0, 3))

        self.cal_status_label = ttk.Label(row2, text="Waiting for first signal...",
                                          font=("Segoe UI", 8, "italic"))
        self.cal_status_label.pack(side=tk.LEFT, padx=2)

        self.cal_finish_btn = tk.Button(row2, text="Finish Calibration",
                                        font=("Segoe UI", 9, "bold"),
                                        bg="#3498DB", fg="white",
                                        state=tk.DISABLED,
                                        command=self._finish_calibration)
        self.cal_finish_btn.pack(side=tk.RIGHT, padx=2)

        cal_clear_btn = tk.Button(row2, text="Clear Cal.",
                                  font=("Segoe UI", 8),
                                  bg="#95A5A6", fg="white",
                                  command=self._clear_calibration)
        cal_clear_btn.pack(side=tk.RIGHT, padx=2)

        # Initialize param_vars so _apply_params_live doesn't crash
        cfg = self.monitor.app_config
        self.param_vars = {
            'fft_cutoff_frequency': tk.DoubleVar(value=cfg.fft_cutoff_frequency),
            'fft_energy_ratio_threshold': tk.DoubleVar(value=cfg.fft_energy_ratio_threshold),
            'lowpass_cutoff': tk.DoubleVar(value=cfg.lowpass_cutoff),
            'lowpass_filter_order': tk.IntVar(value=cfg.lowpass_filter_order),
            'relative_residual_ratio': tk.DoubleVar(value=cfg.relative_residual_ratio),
            'exceedance_ratio_threshold': tk.DoubleVar(value=cfg.exceedance_ratio_threshold),
            'psd_fft_cutoff_frequency': tk.DoubleVar(value=cfg.psd_fft_cutoff_frequency),
            'psd_fft_energy_ratio_threshold': tk.DoubleVar(value=cfg.psd_fft_energy_ratio_threshold),
            'psd_lowpass_cutoff': tk.DoubleVar(value=cfg.psd_lowpass_cutoff),
            'psd_lowpass_filter_order': tk.IntVar(value=cfg.psd_lowpass_filter_order),
            'psd_relative_residual_ratio': tk.DoubleVar(value=cfg.psd_relative_residual_ratio),
            'psd_exceedance_ratio_threshold': tk.DoubleVar(value=cfg.psd_exceedance_ratio_threshold)
        }

    def _cal_on_new_signal(self, lightweight_data: LightweightHitData):
        """Called when a new signal arrives in calibration mode — enable buttons."""
        if lightweight_data.signal_type.lower() not in ('frf', 'psd'):
            return
            
        self.cal_pending_signal = lightweight_data
        self.cal_good_btn.config(state=tk.NORMAL)
        self.cal_bad_btn.config(state=tk.NORMAL)
        self.cal_ignore_btn.config(state=tk.NORMAL)
        sig_type = lightweight_data.signal_type.upper()
        self.cal_status_label.config(text=f"[{sig_type}] Signal received — classify it now")

    def _cal_classify(self, judgment: str):
        """Handle Good/Bad/Ignore button click during calibration."""
        if self.cal_pending_signal is None:
            return

        sig = self.cal_pending_signal
        self.cal_pending_signal = None

        # Disable buttons until next signal
        self.cal_good_btn.config(state=tk.DISABLED)
        self.cal_bad_btn.config(state=tk.DISABLED)
        self.cal_ignore_btn.config(state=tk.DISABLED)

        judgment_upper = judgment.upper()

        if judgment_upper in ("GOOD", "BAD"):
            # Check signal similarity before adding
            is_similar, similar_idx = self._check_signal_similarity(sig)
            if is_similar:
                answer = messagebox.askyesno(
                    "Similar Signal Detected",
                    f"This signal is very similar to calibration signal #{similar_idx + 1}.\n\n"
                    "For best results, use hits from different locations or conditions.\n"
                    "Classify anyway?",
                    parent=self.root
                )
                if not answer:
                    self.cal_status_label.config(text="Signal skipped (too similar) — waiting...")
                    return

            # Build signal data dict from LightweightHitData
            signal_data = {
                'signal_physical': sig.signal_physical,
                'fft_freqs': sig.fft_freqs,
                'fft_mags': sig.fft_mags,
                'residual_physical': sig.residual_physical,
                'energy_ratio': sig.energy_ratio,
                'exceedance_ratio': sig.exceedance_ratio,
                'exceedance_count': sig.exceedance_count,
                'total_energy': sig.total_energy,
                'high_freq_energy': sig.high_freq_energy,
                'max_abs_residual': sig.max_abs_residual,
                'relative_residual_ratio': (
                    self.monitor.app_config.psd_relative_residual_ratio
                    if sig.signal_type == 'psd'
                    else self.monitor.app_config.relative_residual_ratio
                ),
                'signal_vector': sig.residual_physical,  # pixel-space; used by LP cutoff sweep
                'roi_name': sig.hit_key,
                'signal_type': sig.signal_type,
                'source': 'calibration_phase',
            }

            # Feed to calibration engine
            self.calibration_engine.add_signal(signal_data, judgment_upper)

            if judgment_upper == "GOOD":
                self.cal_good_count += 1
                logger.info(f"[CAL] Signal {sig.hit_key} classified as GOOD "
                            f"(E.Ratio={sig.energy_ratio:.4f}, "
                            f"Exc.Ratio={sig.exceedance_ratio:.2f}) "
                            f"[{self.cal_good_count}G total]")
            else:
                self.cal_bad_count += 1
                logger.info(f"[CAL] Signal {sig.hit_key} classified as BAD "
                            f"(E.Ratio={sig.energy_ratio:.4f}, "
                            f"Exc.Ratio={sig.exceedance_ratio:.2f}) "
                            f"[{self.cal_bad_count}B total]")
        else:
            logger.info(f"[CAL] Signal {sig.hit_key} IGNORED")

        total = self.cal_good_count + self.cal_bad_count

        # Update counter label
        self.cal_counter_label.config(
            text=f"Signals: {self.cal_good_count} Good, {self.cal_bad_count} Bad"
                 f" {'(need 3+3 minimum)' if not self.calibration_engine.meets_minimum else '(ready)'}"
        )

        # Update status bar from engine's actual confidence level
        level = self.calibration_engine.confidence_level
        self._update_calibration_status(level, total)

        # Update calibration diagnostic plots
        self.graph_viewer.update_calibration_diagnostics(self.calibration_engine, self.config_path.get())

        # If we can estimate, compute and display parameters
        if self.calibration_engine.can_estimate:
            estimates = self.calibration_engine.get_estimates()
            if estimates:
                self._apply_calibrated_params(estimates)
                self.cal_status_label.config(
                    text=f"Level {level} — Parameters updated from {total} signals"
                )
            self.cal_finish_btn.config(state=tk.NORMAL)
        else:
            need_good = max(0, 3 - self.cal_good_count)
            need_bad = max(0, 3 - self.cal_bad_count)
            parts = []
            if need_good > 0:
                parts.append(f"{need_good} more Good")
            if need_bad > 0:
                parts.append(f"{need_bad} more Bad")
            if parts:
                self.cal_status_label.config(
                    text=f"Need {' and '.join(parts)} signal(s)"
                )
            else:
                self.cal_status_label.config(
                    text=f"Classified as {judgment_upper} — waiting for next signal..."
                )

    def _apply_calibrated_params(self, estimates: dict):
        """Apply estimated parameters to app_config and update GUI display."""
        cfg = self.monitor.app_config

        param_map = {
            'fft_energy_ratio_threshold': 'fft_energy_ratio_threshold',
            'exceedance_ratio_threshold': 'exceedance_ratio_threshold',
            'relative_residual_ratio': 'relative_residual_ratio',
            'fft_cutoff_frequency': 'fft_cutoff_frequency',
            'lowpass_cutoff': 'lowpass_cutoff',
        }

        applied = {}
        for est_key, cfg_attr in param_map.items():
            if est_key in estimates and estimates[est_key] is not None:
                val = estimates[est_key]
                setattr(cfg, cfg_attr, val)
                applied[est_key] = val
                # Also update PSD parameters (same calibration applies)
                psd_attr = f'psd_{cfg_attr}'
                if hasattr(cfg, psd_attr):
                    setattr(cfg, psd_attr, val)

        # Update GUI spinbox variables if they exist
        if hasattr(self, 'param_vars'):
            for key, val in applied.items():
                if key in self.param_vars:
                    try:
                        self.param_vars[key].set(round(val, 6))
                    except Exception:
                        pass

        if applied:
            logger.info(f"[CAL] Applied calibrated parameters: "
                        + ", ".join(f"{k}={v:.6f}" for k, v in applied.items()))

    def _save_calibration_to_config(self):
        """Persist calibration data to the config JSON file."""
        config_path = self.config_path.get()
        if not config_path or not os.path.exists(config_path):
            return
        try:
            with open(config_path, 'r') as f:
                config_data = json.load(f)
            config_data['_calibration'] = self.calibration_engine.to_dict()
            # Also update _metadata with calibrated threshold values
            meta = config_data.get('_metadata', {})
            estimates = self.calibration_engine.get_estimates()
            if estimates:
                for key in ('fft_energy_ratio_threshold', 'exceedance_ratio_threshold',
                            'relative_residual_ratio', 'fft_cutoff_frequency', 'lowpass_cutoff'):
                    if key in estimates:
                        meta[key] = estimates[key]
                        psd_key = f'psd_{key}'
                        if psd_key in meta or psd_key.replace('psd_', '') in meta:
                            meta[psd_key] = estimates[key]
            config_data['_metadata'] = meta
            with open(config_path, 'w') as f:
                json.dump(config_data, f, indent=2)
            logger.info(f"[CAL] Saved calibration data to {config_path}")
            # Mirror to calibration_data/ folder so the session is accessible
            # without opening the config JSON.
            cal_export.save_all(config_path, self.calibration_engine)
        except Exception as e:
            logger.error(f"[CAL] Failed to save calibration data: {e}")

    def _check_signal_similarity(self, new_signal: 'LightweightHitData') -> Tuple[bool, Optional[int]]:
        """Check if new signal is too similar to an already-classified signal.

        Uses four criteria — all must be met to flag as duplicate:
        1. Peak amplitude ratio > 0.90 (less than 10% difference)
        2. Max cross-correlation across ±10 sample lags > 0.99
        3. Cosine similarity of unit-normalised FFT spectra > 0.99
        4. FFT magnitude ratio > 0.90
        """
        if not hasattr(self, 'calibration_engine'):
            return False, None

        stored = self.calibration_engine._all_signals
        if not stored:
            return False, None

        new_phys = new_signal.signal_physical
        new_fft = new_signal.fft_mags

        for i, old in enumerate(stored):
            old_phys = old.get('signal_physical')
            old_fft = old.get('fft_mags')

            if old_phys is None or old_fft is None:
                continue

            if isinstance(old_phys, list):
                old_phys = np.array(old_phys)
            if isinstance(old_fft, list):
                old_fft = np.array(old_fft)

            if len(new_phys) != len(old_phys) or len(new_fft) != len(old_fft):
                continue

            # 1. Amplitude-based discrimination
            peak_new = np.max(np.abs(new_phys))
            peak_old = np.max(np.abs(old_phys))
            if peak_new < 1e-12 or peak_old < 1e-12:
                continue
            amp_ratio = min(peak_new, peak_old) / max(peak_new, peak_old)
            if amp_ratio < 0.90:
                continue  # Amplitudes differ by >10% — not a duplicate

            # 2. Cross-correlation with lag search (±10 samples)
            max_lag = min(10, len(new_phys) // 4)
            try:
                new_centered = new_phys - np.mean(new_phys)
                old_centered = old_phys - np.mean(old_phys)
                norm_factor = np.sqrt(np.sum(new_centered**2) * np.sum(old_centered**2))
                if norm_factor < 1e-12:
                    ncc = 0.0
                else:
                    best_ncc = 0.0
                    for lag in range(-max_lag, max_lag + 1):
                        if lag < 0:
                            cc = np.sum(new_centered[:lag] * old_centered[-lag:])
                        elif lag > 0:
                            cc = np.sum(new_centered[lag:] * old_centered[:-lag])
                        else:
                            cc = np.sum(new_centered * old_centered)
                        ncc_val = cc / norm_factor
                        if ncc_val > best_ncc:
                            best_ncc = ncc_val
                    ncc = best_ncc
            except Exception:
                ncc = 0.0

            if ncc < 0.99:
                continue  # Shape differs enough — not a duplicate

            # 3. Cosine similarity of unit-normalised FFT spectra (shape only)
            norm_new_fft = np.linalg.norm(new_fft)
            norm_old_fft = np.linalg.norm(old_fft)
            if norm_new_fft > 1e-12 and norm_old_fft > 1e-12:
                new_fft_unit = new_fft / norm_new_fft
                old_fft_unit = old_fft / norm_old_fft
                cos_sim = float(np.dot(new_fft_unit, old_fft_unit))
            else:
                cos_sim = 0.0

            if cos_sim < 0.99:
                continue  # Spectral shape differs — not a duplicate

            # 4. FFT magnitude ratio check
            fft_mag_ratio = min(norm_new_fft, norm_old_fft) / max(norm_new_fft, norm_old_fft)
            if fft_mag_ratio < 0.90:
                continue  # Spectral magnitude differs — not a duplicate

            # All criteria met — flag as duplicate
            logger.info(
                f"[CAL] Similar signal detected: signal matches stored #{i+1} "
                f"(NCC={ncc:.4f}, CosSim={cos_sim:.4f}, "
                f"AmpRatio={amp_ratio:.4f}, FFTMagRatio={fft_mag_ratio:.4f})"
            )
            return True, i

        return False, None

    def _clear_calibration(self):
        """Reset calibration engine and revert to default parameters."""
        n_signals = self.calibration_engine.total_signals if hasattr(self, 'calibration_engine') else 0
        answer = messagebox.askyesno(
            "Clear Calibration Data",
            f"This will permanently delete all {n_signals} calibration signal(s) "
            f"from the engine and revert analysis parameters to defaults.\n\n"
            "The ROI configuration will be kept.\n\n"
            "Are you sure you want to continue?",
            parent=self.root
        )
        if not answer:
            return

        # Separately ask about the on-disk calibration_data/ folder.
        config_path = self.config_path.get()
        folder = cal_export.get_session_folder(config_path, create=False) if config_path else None
        if folder and os.path.isdir(folder):
            delete_folder = messagebox.askyesno(
                "Delete Saved Calibration Files?",
                f"A calibration data folder exists at:\n{folder}\n\n"
                "This contains CSV data, signal plots, and diagnostics PNG files "
                "that you may want to keep for analysis or publication.\n\n"
                "Delete this folder too?",
                parent=self.root
            )
            if delete_folder:
                cal_export.clear_folder(config_path)

        # Reset engine
        self.calibration_engine = HybridCalibrationEngine()

        # Sync the graph viewer's reference so the diagnostic plots show the
        # empty-state view instead of continuing to render against the old
        # engine object.
        if hasattr(self, 'graph_viewer') and self.graph_viewer is not None:
            self.graph_viewer.calibration_engine = self.calibration_engine
            if hasattr(self.graph_viewer, 'update_calibration_diagnostics'):
                try:
                    self.graph_viewer.update_calibration_diagnostics(self.calibration_engine, self.config_path.get())
                except Exception as e:
                    logger.debug(f"[CAL] Could not refresh diagnostics after clear: {e}")

        # Reset counters if in calibration mode
        if hasattr(self, 'cal_good_count'):
            self.cal_good_count = 0
            self.cal_bad_count = 0
            self.cal_pending_signal = None
        if hasattr(self, 'cal_counter_label') and self.cal_counter_label.winfo_exists():
            self.cal_counter_label.config(text="0G / 0B (need 3+3)")
        if hasattr(self, 'cal_status_label') and self.cal_status_label.winfo_exists():
            self.cal_status_label.config(text="Calibration cleared — waiting for signals...")
        if hasattr(self, 'cal_finish_btn') and self.cal_finish_btn.winfo_exists():
            self.cal_finish_btn.config(state=tk.DISABLED)

        # Revert config to defaults
        cfg = self.monitor.app_config
        cfg.fft_cutoff_frequency = 0.07
        cfg.fft_energy_ratio_threshold = 0.006
        cfg.lowpass_cutoff = 0.07
        cfg.lowpass_filter_order = 7
        cfg.relative_residual_ratio = 0.10
        cfg.exceedance_ratio_threshold = 0.7
        cfg.psd_fft_cutoff_frequency = 0.07
        cfg.psd_fft_energy_ratio_threshold = 0.006
        cfg.psd_lowpass_cutoff = 0.07
        cfg.psd_lowpass_filter_order = 7
        cfg.psd_relative_residual_ratio = 0.10
        cfg.psd_exceedance_ratio_threshold = 0.7

        # Update GUI
        if hasattr(self, 'param_vars'):
            self._sync_param_ui_from_config()

        # Update status bar
        self._update_calibration_status(0, 0)

        # Remove _calibration from config file
        config_path = self.config_path.get()
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config_data = json.load(f)
                if '_calibration' in config_data:
                    del config_data['_calibration']
                    with open(config_path, 'w') as f:
                        json.dump(config_data, f, indent=2)
            except Exception as e:
                logger.warning(f"[CAL] Could not clear calibration from config: {e}")

        logger.info("[CAL] Calibration data cleared — reverted to default parameters")

    def _finish_calibration(self):
        """Transition from calibration to normal monitoring with calibrated params."""
        engine = self.calibration_engine
        level = engine.confidence_level
        total = engine.total_signals

        logger.info(f"[CAL] Finishing calibration: {engine.good_count}G + "
                    f"{engine.bad_count}B = {total} signals, Level {level}")

        # Compute final estimates
        estimates = engine.get_estimates()

        if estimates:
            # Apply to config
            self._apply_calibrated_params(estimates)

            # Log the final parameter values
            logger.info("[CAL] Final calibrated parameters:")
            for key in ('fft_energy_ratio_threshold', 'exceedance_ratio_threshold',
                        'relative_residual_ratio', 'fft_cutoff_frequency', 'lowpass_cutoff'):
                if key in estimates:
                    logger.info(f"  {key} = {estimates[key]:.6f}")

            # Show summary to user
            summary_lines = [
                f"Calibration complete — Level {level} ({total} signals)\n",
                "Estimated parameters:"
            ]
            for key in ('fft_energy_ratio_threshold', 'exceedance_ratio_threshold',
                        'relative_residual_ratio', 'fft_cutoff_frequency', 'lowpass_cutoff'):
                if key in estimates:
                    default_val = {
                        'fft_energy_ratio_threshold': 0.006,
                        'exceedance_ratio_threshold': 0.7,
                        'relative_residual_ratio': 0.10,
                        'fft_cutoff_frequency': 0.07,
                        'lowpass_cutoff': 0.07,
                    }.get(key, 0)
                    summary_lines.append(
                        f"  {key}: {estimates[key]:.6f}  (was {default_val})"
                    )

            messagebox.showinfo("Calibration Complete", "\n".join(summary_lines))
        else:
            messagebox.showwarning(
                "Calibration Incomplete",
                f"Could not compute parameters from {total} signals.\n"
                "Falling back to default values."
            )

        # Persist signals + diagnostic figures while keeping the engine intact
        # so diagnostics stay available after calibration mode ends.
        folder = cal_export.save_all(self.config_path.get(),
                                     self.calibration_engine)
        if folder:
            logger.info(f"[CAL] Session data saved to {folder}")

        # Save calibration data to config file
        self._save_calibration_to_config()

        # Keep the graph viewer's reference in sync so diagnostics remain
        # available after the calibration phase is finished.
        if hasattr(self, 'graph_viewer') and self.graph_viewer is not None:
            self.graph_viewer.calibration_engine = self.calibration_engine
            if hasattr(self.graph_viewer, 'update_calibration_diagnostics'):
                try:
                    self.graph_viewer.update_calibration_diagnostics(self.calibration_engine, self.config_path.get())
                except Exception as e:
                    logger.debug(f"[CAL] Could not refresh diagnostics after finish: {e}")

        # Destroy calibration panel
        if hasattr(self, 'cal_frame') and self.cal_frame.winfo_exists():
            self.cal_frame.destroy()

        # Exit calibration mode
        self.calibration_mode = False

        # Create standard analysis parameters panel (pre-filled with calibrated values)
        self._setup_analysis_params(self.right_panel)
        self._sync_param_ui_from_config()

        # Update calibration status bar to show achieved level
        self._update_calibration_status(level, total)

        logger.info("[CAL] Switched to normal monitoring mode with calibrated parameters")

    def _sync_param_ui_from_config(self):
        """Sync parameter UI variables from the current app_config values."""
        cfg = self.monitor.app_config
        self.param_vars['fft_cutoff_frequency'].set(cfg.fft_cutoff_frequency)
        self.param_vars['fft_energy_ratio_threshold'].set(cfg.fft_energy_ratio_threshold)
        self.param_vars['lowpass_cutoff'].set(cfg.lowpass_cutoff)
        self.param_vars['lowpass_filter_order'].set(cfg.lowpass_filter_order)
        self.param_vars['relative_residual_ratio'].set(cfg.relative_residual_ratio)
        self.param_vars['exceedance_ratio_threshold'].set(cfg.exceedance_ratio_threshold)
        self.param_vars['psd_fft_cutoff_frequency'].set(cfg.psd_fft_cutoff_frequency)
        self.param_vars['psd_fft_energy_ratio_threshold'].set(cfg.psd_fft_energy_ratio_threshold)
        self.param_vars['psd_lowpass_cutoff'].set(cfg.psd_lowpass_cutoff)
        self.param_vars['psd_lowpass_filter_order'].set(cfg.psd_lowpass_filter_order)
        self.param_vars['psd_relative_residual_ratio'].set(cfg.psd_relative_residual_ratio)
        self.param_vars['psd_exceedance_ratio_threshold'].set(cfg.psd_exceedance_ratio_threshold)

    def _setup_analysis_params(self, parent):
        """Create the Analysis Parameters (Live Tuning) frame with live calibration controls."""
        params_outer_frame = ttk.LabelFrame(parent, text="Analysis Parameters (Live)")
        params_outer_frame.pack(fill=tk.X, pady=(10, 0))

        cfg = self.monitor.app_config
        self.param_vars = {
            'fft_cutoff_frequency': tk.DoubleVar(value=cfg.fft_cutoff_frequency),
            'fft_energy_ratio_threshold': tk.DoubleVar(value=cfg.fft_energy_ratio_threshold),
            'lowpass_cutoff': tk.DoubleVar(value=cfg.lowpass_cutoff),
            'lowpass_filter_order': tk.IntVar(value=cfg.lowpass_filter_order),
            'relative_residual_ratio': tk.DoubleVar(value=cfg.relative_residual_ratio),
            'exceedance_ratio_threshold': tk.DoubleVar(value=cfg.exceedance_ratio_threshold),
            'psd_fft_cutoff_frequency': tk.DoubleVar(value=cfg.psd_fft_cutoff_frequency),
            'psd_fft_energy_ratio_threshold': tk.DoubleVar(value=cfg.psd_fft_energy_ratio_threshold),
            'psd_lowpass_cutoff': tk.DoubleVar(value=cfg.psd_lowpass_cutoff),
            'psd_lowpass_filter_order': tk.IntVar(value=cfg.psd_lowpass_filter_order),
            'psd_relative_residual_ratio': tk.DoubleVar(value=cfg.psd_relative_residual_ratio),
            'psd_exceedance_ratio_threshold': tk.DoubleVar(value=cfg.psd_exceedance_ratio_threshold)
        }

        # FRF Method Section
        frf_frame = ttk.LabelFrame(params_outer_frame, text="FRF Method")
        frf_frame.pack(fill=tk.X, padx=5, pady=5)
        self._build_param_row(frf_frame, "")

        # PSD Method Section
        psd_frame = ttk.LabelFrame(params_outer_frame, text="PSD Method")
        psd_frame.pack(fill=tk.X, padx=5, pady=5)
        self._build_param_row(psd_frame, "psd_")

        # Apply Parameters Button
        apply_btn = tk.Button(params_outer_frame, text="Apply Parameters",
                              font=("Segoe UI", 9, "bold"),
                              bg="#3498DB", fg="white",
                              command=self._apply_params_live)
        apply_btn.pack(fill=tk.X, padx=5, pady=5)

        # Live Calibration Controls
        live_cal_frame = ttk.LabelFrame(params_outer_frame, text="Live Calibration")
        live_cal_frame.pack(fill=tk.X, padx=5, pady=5)

        btn_row = ttk.Frame(live_cal_frame)
        btn_row.pack(fill=tk.X, padx=5, pady=3)

        self.live_cal_good_btn = tk.Button(btn_row, text="\u2713 Good", font=("Segoe UI", 9, "bold"),
                                           bg="#27AE60", fg="white", width=7, state=tk.DISABLED,
                                           command=lambda: self._live_cal_classify("GOOD"))
        self.live_cal_good_btn.pack(side=tk.LEFT, padx=2)

        self.live_cal_bad_btn = tk.Button(btn_row, text="\u2717 Bad", font=("Segoe UI", 9, "bold"),
                                          bg="#E74C3C", fg="white", width=7, state=tk.DISABLED,
                                          command=lambda: self._live_cal_classify("BAD"))
        self.live_cal_bad_btn.pack(side=tk.LEFT, padx=2)

        self.live_cal_status = ttk.Label(btn_row, text="Waiting for signal...",
                                         font=("Segoe UI", 8, "italic"))
        self.live_cal_status.pack(side=tk.LEFT, padx=(8, 2))

        clear_btn = tk.Button(btn_row, text="Clear Cal.",
                              font=("Segoe UI", 8),
                              bg="#95A5A6", fg="white",
                              command=self._clear_calibration)
        clear_btn.pack(side=tk.RIGHT, padx=2)

        # Info row under the buttons — link to the calibration engine theory
        # and the practical tuning guide.
        cal_live_info = ttk.Frame(live_cal_frame)
        cal_live_info.pack(fill=tk.X, padx=5, pady=(0, 2))
        ttk.Label(cal_live_info, text="About calibration:",
                  font=("Segoe UI", 8)).pack(side=tk.LEFT)
        make_info_button(cal_live_info, self.root, "calibration_engine",
                         side=tk.LEFT)
        ttk.Label(cal_live_info, text="Tuning guide:",
                  font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(8, 0))
        make_info_button(cal_live_info, self.root, "practical_tuning",
                         side=tk.LEFT)

        self.live_cal_pending_signal = None

    def _build_param_row(self, parent, prefix: str):
        """Helper to build 6 parameter spinboxes inside a given frame."""
        # Row 1: FFT Cutoff, Energy Ratio Threshold
        row1 = ttk.Frame(parent)
        row1.pack(fill=tk.X, padx=5, pady=2)
        
        ttk.Label(row1, text="FFT Cutoff:", width=10).pack(side=tk.LEFT)
        fft_spin = ttk.Spinbox(row1, from_=0.001, to=0.49, increment=0.005,
                               textvariable=self.param_vars[f'{prefix}fft_cutoff_frequency'],
                               width=10, format="%.6f", command=self._apply_params_live)
        fft_spin.pack(side=tk.LEFT, padx=2)
        fft_spin.bind('<Return>', lambda e: self._apply_params_live())
        make_info_button(row1, self.root, "fft_cutoff_frequency", side=tk.LEFT)

        ttk.Label(row1, text="E.Ratio Thr:", width=10).pack(side=tk.LEFT, padx=(5, 0))
        ratio_spin = ttk.Spinbox(row1, from_=0.0001, to=0.50, increment=0.001,
                                 textvariable=self.param_vars[f'{prefix}fft_energy_ratio_threshold'],
                                 width=10, format="%.6f", command=self._apply_params_live)
        ratio_spin.pack(side=tk.LEFT, padx=2)
        ratio_spin.bind('<Return>', lambda e: self._apply_params_live())
        make_info_button(row1, self.root, "fft_energy_ratio_threshold", side=tk.LEFT)

        # Row 2: Lowpass Cutoff, Lowpass Filter Order
        row2 = ttk.Frame(parent)
        row2.pack(fill=tk.X, padx=5, pady=2)
        
        ttk.Label(row2, text="LP Cutoff:", width=10).pack(side=tk.LEFT)
        lp_spin = ttk.Spinbox(row2, from_=0.001, to=0.49, increment=0.005,
                              textvariable=self.param_vars[f'{prefix}lowpass_cutoff'],
                              width=10, format="%.6f", command=self._apply_params_live)
        lp_spin.pack(side=tk.LEFT, padx=2)
        lp_spin.bind('<Return>', lambda e: self._apply_params_live())
        make_info_button(row2, self.root, "lowpass_cutoff", side=tk.LEFT)

        ttk.Label(row2, text="Order:", width=10).pack(side=tk.LEFT, padx=(5, 0))
        order_spin = ttk.Spinbox(row2, from_=1, to=15, increment=1,
                                 textvariable=self.param_vars[f'{prefix}lowpass_filter_order'],
                                 width=10, command=self._apply_params_live)
        order_spin.pack(side=tk.LEFT, padx=2)
        order_spin.bind('<Return>', lambda e: self._apply_params_live())
        make_info_button(row2, self.root, "lowpass_filter_order", side=tk.LEFT)

        # Row 3: Relative Residual Ratio, Exceedance Ratio Threshold
        row3 = ttk.Frame(parent)
        row3.pack(fill=tk.X, padx=5, pady=2)

        ttk.Label(row3, text="Res.Ratio:", width=10).pack(side=tk.LEFT)
        res_spin = ttk.Spinbox(row3, from_=0.01, to=0.50, increment=0.01,
                               textvariable=self.param_vars[f'{prefix}relative_residual_ratio'],
                               width=10, format="%.2f", command=self._apply_params_live)
        res_spin.pack(side=tk.LEFT, padx=2)
        res_spin.bind('<Return>', lambda e: self._apply_params_live())
        make_info_button(row3, self.root, "residual_threshold", side=tk.LEFT)

        ttk.Label(row3, text="Exc.Ratio:", width=10).pack(side=tk.LEFT, padx=(5, 0))
        exc_spin = ttk.Spinbox(row3, from_=0.01, to=0.99, increment=0.01,
                               textvariable=self.param_vars[f'{prefix}exceedance_ratio_threshold'],
                               width=10, format="%.4f", command=self._apply_params_live)
        exc_spin.pack(side=tk.LEFT, padx=2)
        exc_spin.bind('<Return>', lambda e: self._apply_params_live())
        make_info_button(row3, self.root, "exceedance_ratio_threshold", side=tk.LEFT)

    def _live_cal_on_new_signal(self, lightweight_data: LightweightHitData):
        """Called when a new signal arrives in live analysis mode — enable Good/Bad buttons."""
        if lightweight_data.signal_type.lower() not in ('frf', 'psd'):
            return
        if not hasattr(self, 'live_cal_good_btn'):
            return

        self.live_cal_pending_signal = lightweight_data
        self.live_cal_good_btn.config(state=tk.NORMAL)
        self.live_cal_bad_btn.config(state=tk.NORMAL)
        sig_type = lightweight_data.signal_type.upper()
        self.live_cal_status.config(text=f"[{sig_type}] Classify to refine calibration")

    def _live_cal_classify(self, judgment: str):
        """Handle Good/Bad button click in live analysis mode to continue calibration."""
        if not hasattr(self, 'live_cal_pending_signal') or self.live_cal_pending_signal is None:
            return

        sig = self.live_cal_pending_signal
        self.live_cal_pending_signal = None

        # Disable buttons until next signal
        self.live_cal_good_btn.config(state=tk.DISABLED)
        self.live_cal_bad_btn.config(state=tk.DISABLED)

        # Check signal similarity
        is_similar, similar_idx = self._check_signal_similarity(sig)
        if is_similar:
            answer = messagebox.askyesno(
                "Similar Signal Detected",
                f"This signal is very similar to calibration signal #{similar_idx + 1}.\n\n"
                "Classify anyway?",
                parent=self.root
            )
            if not answer:
                self.live_cal_status.config(text="Signal skipped (too similar)")
                return

        # Build signal data dict
        signal_data = {
            'signal_physical': sig.signal_physical,
            'fft_freqs': sig.fft_freqs,
            'fft_mags': sig.fft_mags,
            'residual_physical': sig.residual_physical,
            'energy_ratio': sig.energy_ratio,
            'exceedance_ratio': sig.exceedance_ratio,
            'exceedance_count': sig.exceedance_count,
            'total_energy': sig.total_energy,
            'high_freq_energy': sig.high_freq_energy,
            'roi_name': sig.hit_key,
            'signal_type': sig.signal_type,
            'source': 'live_monitoring',
        }

        # Feed to calibration engine
        self.calibration_engine.add_signal(signal_data, judgment)

        total = self.calibration_engine.total_signals
        level = self.calibration_engine.confidence_level

        logger.info(f"[CAL-LIVE] Signal {sig.hit_key} classified as {judgment} "
                    f"(E.Ratio={sig.energy_ratio:.4f}) "
                    f"[{self.calibration_engine.good_count}G + "
                    f"{self.calibration_engine.bad_count}B = {total}, Level {level}]")

        # Update params if we can estimate
        if self.calibration_engine.can_estimate:
            estimates = self.calibration_engine.get_estimates()
            if estimates:
                self._apply_calibrated_params(estimates)
                self.live_cal_status.config(
                    text=f"Level {level} — {total} signals — params updated"
                )
        else:
            self.live_cal_status.config(text=f"{judgment} — {total} signals (need 3+3)")

        # Update calibration status bar
        self._update_calibration_status(level, total)

        # Update calibration diagnostic plots
        self.graph_viewer.update_calibration_diagnostics(self.calibration_engine, self.config_path.get())

        # Persist every live calibration addition so diagnostics and the
        # calibration_data folder stay in step with the session.
        self._save_calibration_to_config()

    def _poll_monitor_queue(self):
        if not self.monitor:
            return

        try:
            # Process up to 10 events per tick to prevent GUI freeze
            for _ in range(10):
                event: MonitorEvent = self.monitor.event_queue.get_nowait()

                if event.event_type == "frame_update":
                    frame_res = event.frame_result
                    if frame_res is not None:
                        self._update_feedback_ui(frame_res)
                elif event.event_type == "hit_detected":
                    lw_data = event.lightweight_data
                    if lw_data is not None:
                        self._update_graph_viewer(lw_data, event.run_history or {})
                elif event.event_type == "error":
                    err_msg = event.error_message
                    if err_msg is not None:
                        self.status_var.set(f"Stop: {err_msg}")
                        self.status_label.config(text=f"ERROR: {err_msg}")
                    self.start_stop_button.config(text="Start Monitoring")
                    self.is_monitoring.set(False)
                    self.load_button.config(state=tk.NORMAL)
                    self.edit_button.config(state=tk.NORMAL)
                elif event.event_type == "stopped":
                    # Loop exited cleanly; keep draining until the queue is empty,
                    # then let the poll loop terminate naturally.
                    pass
        except queue.Empty:
            pass

        # Keep polling until BOTH the worker thread has stopped AND the queue
        # has been drained — otherwise final "error"/"stopped" events can be lost.
        thread = getattr(self.monitor, 'thread', None)
        thread_alive = thread is not None and thread.is_alive()
        queue_has_events = not self.monitor.event_queue.empty()
        if thread_alive or queue_has_events:
            self.root.after(50, self._poll_monitor_queue)

    def _toggle_monitoring(self):
        if self.is_monitoring.get():
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
                self.status_label.config(text="Monitoring active...")
                self.load_button.config(state=tk.DISABLED)
                self.edit_button.config(state=tk.DISABLED)
                
                # Start polling the event queue
                self.root.after(50, self._poll_monitor_queue)

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
        # Save calibration data if any signals were collected
        if self.calibration_engine.total_signals > 0:
            self._save_calibration_to_config()
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.destroy()
        self.root.destroy()


# --- 7. APPLICATION ENTRY POINT ---
