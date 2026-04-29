import os
import logging
from typing import Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from usma.models import APP_VERSION

logger = logging.getLogger(__name__)


class StartupDialog(tk.Toplevel):
    """
    Startup dialog for config selection or new ROI definition.

    Attributes:
        result: str or None - Selected config path, "NEW_CALIBRATION", or None if cancelled
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title(f"USMA v{APP_VERSION} - Startup")
        self.result: Optional[str] = None
        self.config_var: Optional[tk.StringVar] = None

        # Don't use transient() with hidden parent - causes display issues on Windows
        # self.transient(parent)  # REMOVED

        # Scan for config files first to determine window size
        self.config_files = self._scan_configs()

        # Set window size based on content
        # Larger window when configs exist to show all buttons
        window_height = 450 if self.config_files else 350
        self.geometry(f"450x{window_height}")
        self.resizable(True, True)

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
        ttk.Label(title_frame, text=f"v{APP_VERSION}",
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
            c_var = tk.StringVar(value=self.config_files[0])
            self.config_var = c_var
            config_combo = ttk.Combobox(combo_frame, textvariable=c_var,
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
            ttk.Label(info_frame, text="Let's create your first ROI definition!",
                      font=("Segoe UI", 9)).pack()

        # Create new ROI definition button
        new_cal_btn = ttk.Button(self, text="Create New ROI Definition",
                                 command=self._on_new_calibration, width=30)
        new_cal_btn.pack(pady=(5, 20))

        # Cancel button at bottom
        if self.config_files:
            ttk.Button(self, text="Cancel", command=self._on_cancel, width=10).pack(pady=(0, 10))

    def _on_load(self):
        """Set result to selected config path."""
        c_var = self.config_var
        if c_var is not None:
            selected = c_var.get()
            if selected:
                self.result = os.path.join("configs", selected)
                self.destroy()

    def _on_new_calibration(self):
        """Set result to trigger new ROI definition."""
        self.result = "NEW_CALIBRATION"
        self.destroy()

    def _on_cancel(self):
        """User closed dialog without selection."""
        self.result = None
        self.destroy()


# --- 4a2. CALIBRATION CHOICE DIALOG ---
class CalibrationChoiceDialog(tk.Toplevel):
    """
    Post-config-load dialog asking user to choose between
    default parameters or calibration mode.

    Attributes:
        result: str - "DEFAULT" or "CALIBRATE" or None (cancelled)
    """

    def __init__(self, parent, config_name: str = ""):
        super().__init__(parent)
        self.title(f"USMA v{APP_VERSION} - Parameter Selection")
        self.result: Optional[str] = None

        self.geometry("500x400")
        self.resizable(True, True)

        # Center on screen
        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - 210
        y = (self.winfo_screenheight() // 2) - 160
        self.geometry(f"+{x}+{y}")

        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Title
        title_frame = ttk.Frame(self)
        title_frame.pack(fill=tk.X, padx=20, pady=(20, 5))
        ttk.Label(title_frame, text="Parameter Selection",
                  font=("Segoe UI", 13, "bold")).pack()

        if config_name:
            ttk.Label(title_frame, text=f"Config: {os.path.basename(config_name)}",
                      font=("Segoe UI", 9)).pack(pady=(2, 0))

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=20, pady=10)

        # Option 1: Default Parameters
        default_frame = ttk.Frame(self)
        default_frame.pack(fill=tk.X, padx=20, pady=5)
        btn_default = tk.Button(default_frame, text="Use Default Parameters",
                                font=("Segoe UI", 11, "bold"),
                                bg="#3498DB", fg="white", activebackground="#2980B9",
                                height=2, command=self._on_default)
        btn_default.pack(fill=tk.X)
        ttk.Label(default_frame,
                  text="Start monitoring with the saved analysis parameters.\nBest when parameters are already tuned for your setup.",
                  font=("Segoe UI", 8), justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        # Option 2: Calibrate
        cal_frame = ttk.Frame(self)
        cal_frame.pack(fill=tk.X, padx=20, pady=(10, 5))
        btn_cal = tk.Button(cal_frame, text="Calibrate with Expert Feedback",
                            font=("Segoe UI", 11, "bold"),
                            bg="#E67E22", fg="white", activebackground="#D35400",
                            height=2, command=self._on_calibrate)
        btn_cal.pack(fill=tk.X)
        ttk.Label(cal_frame,
                  text="Classify signals as Good/Bad to auto-tune thresholds.\nRecommended for new setups or changed test conditions.",
                  font=("Segoe UI", 8), justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

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


# --- 4b. ROI TYPE SELECTION DIALOG ---
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

        self.geometry("500x400")
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

