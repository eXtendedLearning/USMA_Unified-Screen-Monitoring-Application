"""USMA utility functions, environment setup, and shared helpers."""

import os
import sys
import time
import logging
from contextlib import contextmanager
import tkinter as tk


def setup_environment():
    """Create necessary directories for logs, configs, and organized image logs."""
    base_folders = ['logs', 'configs', 'signal_logs']
    image_subfolders = [
        'image_logs/ROIs',
        'image_logs/ColorMasks',
        'image_logs/Signals',
        'image_logs/FFT',
        'image_logs/Lowpass',
        'image_logs/Residual',
        'image_logs/Summary',
        'image_logs/OCRs'
    ]
    for folder in base_folders + image_subfolders:
        if not os.path.exists(folder):
            os.makedirs(folder)


setup_environment()

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('logs/monitor_app.log')
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)


class TextHandler(logging.Handler):
    """Logging handler that writes to a tkinter Text widget."""

    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setFormatter(log_formatter)

    def emit(self, record):
        try:
            msg = self.format(record) + '\n'
            self.text_widget.after(0, self._append_text, msg)
        except Exception:
            self.handleError(record)

    def _append_text(self, msg):
        try:
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg)
            self.text_widget.see(tk.END)
            line_count = int(self.text_widget.index('end-1c').split('.')[0])
            if line_count > 1000:
                self.text_widget.delete('1.0', f'{line_count - 1000}.0')
            self.text_widget.configure(state='disabled')
        except tk.TclError:
            pass


@contextmanager
def styled_figure(figsize=(10, 5), dpi=150):
    """Context manager for creating consistently styled matplotlib figures."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = None
    try:
        fig = Figure(figsize=figsize, dpi=dpi, facecolor='#1E1E1E')
        _ = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white')
        ax.grid(True, linestyle='--', alpha=0.3)
        for spine in ax.spines.values():
            spine.set_color('white')

        yield fig, ax

    finally:
        if fig is not None:
            fig.clf()
            del fig


def load_app_config(path: str):
    """Load an AppConfig from a JSON config file. Delegates to AppConfig.from_json."""
    from usma.models import AppConfig
    return AppConfig.from_json(path)
