import json
import logging
import os

import tkinter as tk

from usma.models import MonitoringRegion

logger = logging.getLogger(__name__)


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

