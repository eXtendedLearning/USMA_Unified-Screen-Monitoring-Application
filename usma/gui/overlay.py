import json
import logging
import os

import tkinter as tk

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

logger = logging.getLogger(__name__)


class RegionOverlay(tk.Toplevel):
    def __init__(self, parent, config_path):
        super().__init__(parent)
        self.config_path = config_path
        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)
            metadata = data.get('_metadata', {})
            monitor_index = int(metadata.get('monitor_index', 1))
            (left, top, width, height,
             x_offset, y_offset,
             target_width, target_height) = self._get_overlay_geometry(monitor_index)

            self.attributes("-transparentcolor", "white", "-topmost", True)
            self.overrideredirect(True)
            self.geometry(f"{width}x{height}{left:+d}{top:+d}")

            canvas = tk.Canvas(self, bg="white", highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)
            colors = {"frf": "#3498db", "psd": "#9b59b6", "coherence": "#1abc9c",
                      "averages": "#95a5a6", "status": "#2ecc71", "overload": "#e74c3c",
                      "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
            for name, region_data in data.items():
                if not name.startswith('_') and region_data.get('enabled', True):
                    x, y, w, h = region_data['x'], region_data['y'], region_data['width'], region_data['height']
                    x += x_offset
                    y += y_offset
                    # Use custom overlay_color if set, otherwise type-based default
                    color = region_data.get('overlay_color') or colors.get(region_data.get('roi_type', 'frf'), "#95a5a6")
                    canvas.create_rectangle(x-5, y-5, x+w+5, y+h+5, outline=color, width=2)
                    canvas.create_text(x-5, y-5, text=name, anchor="sw", font=("Arial", 10, "bold"), fill=color)
            canvas.create_text(x_offset + target_width - 10, y_offset + target_height - 10,
                              text=f"Config: {os.path.basename(self.config_path)}", anchor="se", fill="#333")
        except Exception as e:
            logger.error(f"Overlay Error: {e}")
            self.destroy()

    def _get_overlay_geometry(self, monitor_index: int):
        if MSS_AVAILABLE:
            with mss.mss() as sct:
                idx = monitor_index
                if idx < 0 or idx >= len(sct.monitors):
                    idx = 1
                virtual = sct.monitors[0]
                monitor = virtual if idx == 0 else sct.monitors[idx]
                virtual_left = int(virtual.get("left", 0))
                virtual_top = int(virtual.get("top", 0))
                monitor_left = int(monitor.get("left", 0))
                monitor_top = int(monitor.get("top", 0))
                return (
                    virtual_left,
                    virtual_top,
                    int(virtual["width"]),
                    int(virtual["height"]),
                    monitor_left - virtual_left,
                    monitor_top - virtual_top,
                    int(monitor["width"]),
                    int(monitor["height"]),
                )
        width = self.winfo_screenwidth()
        height = self.winfo_screenheight()
        return 0, 0, width, height, 0, 0, width, height

