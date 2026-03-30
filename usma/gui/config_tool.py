import os
import json
import logging
from typing import Dict, Optional
from dataclasses import asdict

import numpy as np
import cv2

try:
    import mss
    import mss.tools
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    import pyautogui
except ImportError:
    pyautogui = None

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from PIL import Image, ImageTk

from usma.models import *
from usma.utils import load_app_config
from usma.gui.dialogs import ROITypeDialog
from usma.gui.hsv_calibration import HSVCalibrationWindow
from usma.gui.overlay import RegionOverlay

logger = logging.getLogger(__name__)


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
            self.app_config = load_app_config(preload_config_path)
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
        try:
            self.right_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        except tk.TclError:
            pass
    
    def _build_right_panel_content(self, parent):
        capture_frame = ttk.LabelFrame(parent, text="Capture")
        capture_frame.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(capture_frame, text="Take Screenshot", command=self._take_screenshot).pack(pady=5, padx=5, fill=tk.X)

        # HSV Calibration button
        self.hsv_cal_btn = ttk.Button(capture_frame, text="Calibrate Color Filter",
                                       command=self._open_hsv_calibration)
        self.hsv_cal_btn.pack(fill=tk.X, pady=(0, 5), padx=5)
        self.hsv_cal_btn.config(state=tk.DISABLED)  # Disabled by default

        monitor_frame = ttk.Frame(capture_frame)
        monitor_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(monitor_frame, text="Monitor:").pack(side=tk.LEFT)
        self.monitor_index_var = tk.IntVar(value=self.app_config.monitor_index)
        ttk.Spinbox(monitor_frame, from_=0, to=5, increment=1,
                    textvariable=self.monitor_index_var, width=4).pack(side=tk.LEFT, padx=2)
        ttk.Label(monitor_frame, text="(0=all, 1=primary, 2+=secondary)").pack(side=tk.LEFT)

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
            'y_scale_type': tk.StringVar(value='linear'),
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
        ttk.Label(g2, text="Scale:").pack(side=tk.LEFT, padx=(5,0))
        ttk.Combobox(g2, textvariable=self.editor_vars['y_scale_type'],
                     values=['linear', 'dB', 'log', 'ln'], state='readonly', width=6).pack(side=tk.LEFT, padx=2)

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
        
        if MSS_AVAILABLE:
            with mss.mss() as sct:
                idx = self.monitor_index_var.get()
                if idx >= len(sct.monitors):
                    idx = 1
                monitor = sct.monitors[idx]
                sct_img = sct.grab(monitor)
                self.screenshot = cv2.cvtColor(np.array(sct_img, dtype=np.uint8), cv2.COLOR_BGRA2BGR)
        else:
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
        lower = hsv_window.result_hsv_lower
        upper = hsv_window.result_hsv_upper
        if lower is not None and upper is not None:
            self.app_config.hsv_lower = lower
            self.app_config.hsv_upper = upper
            messagebox.showinfo("Success", "HSV color filter updated.", parent=self)

    def _apply_params(self):
        self.app_config.fft_cutoff_frequency = self.param_vars['fft_cutoff_frequency'].get()
        self.app_config.fft_energy_ratio_threshold = self.param_vars['fft_energy_ratio_threshold'].get()
        self.app_config.lowpass_cutoff = self.param_vars['lowpass_cutoff'].get()
        self.app_config.lowpass_filter_order = self.param_vars['lowpass_filter_order'].get()
        self.app_config.residual_threshold = self.param_vars['residual_threshold'].get()
        self.app_config.exceedance_ratio_threshold = self.param_vars['exceedance_ratio_threshold'].get()
        self.app_config.monitor_index = self.monitor_index_var.get()
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
                'monitor_index': self.app_config.monitor_index,
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
            self.app_config = load_app_config(path)
            self._update_ui_from_data()
            if self.screenshot:
                self._redraw_regions_on_canvas()
            messagebox.showinfo("Success", f"Loaded {os.path.basename(path)}", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}", parent=self)


# --- 5. LIVE GRAPH VIEWER ---