import logging
from typing import Optional

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
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

from usma.theory.wiki_viewer import show_theory_page

logger = logging.getLogger(__name__)


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
        self.wave_regions = wave_regions
        self.result_hsv_lower: Optional[List[int]] = None
        self.result_hsv_upper: Optional[List[int]] = None
        self.region_var: Optional[tk.StringVar] = None
        
        # UI Attributes for Pyre2
        self.zoom_label = cast(ttk.Label, None)
        self.preview_canvas = cast(tk.Canvas, None)
        self.h_min_var = cast(tk.IntVar, None)
        self.h_max_var = cast(tk.IntVar, None)
        self.s_min_var = cast(tk.IntVar, None)
        self.s_max_var = cast(tk.IntVar, None)
        self.v_min_var = cast(tk.IntVar, None)
        self.v_max_var = cast(tk.IntVar, None)

        # Current values (copy to avoid modifying original until Apply)
        self.hsv_lower: List[int] = list(current_hsv_lower)
        self.hsv_upper: List[int] = list(current_hsv_upper)

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
        self.after(100, lambda *args, **kwargs: self._update_preview())

    def _setup_ui(self):
        # Theory ⓘ row at the top — explains HSV filtering theory
        info_row = ttk.Frame(self)
        info_row.pack(fill=tk.X, padx=10, pady=(8, 2))
        ttk.Label(info_row, text="About HSV color filtering:",
                  font=("Segoe UI", 9)).pack(side=tk.LEFT)
        _hsv_info = tk.Label(info_row, text="\u24D8", font=("Segoe UI", 10),
                             fg="#5DADE2", cursor="hand2")
        _hsv_info.bind("<Button-1>",
                       lambda e: show_theory_page(self, "hsv_calibration"))
        _hsv_info.pack(side=tk.LEFT, padx=2)

        # Top: Region selector (if multiple wave regions)
        if len(self.wave_regions) > 1:
            selector_frame = ttk.Frame(self)
            selector_frame.pack(fill=tk.X, padx=10, pady=5)
            ttk.Label(selector_frame, text="Preview Region:").pack(side=tk.LEFT)
            r_var = tk.StringVar(value=self.selected_region_name)
            self.region_var = r_var
            region_combo = ttk.Combobox(selector_frame, textvariable=r_var,
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
        if self.region_var is not None:
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