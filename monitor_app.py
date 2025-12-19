#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# === DPI AWARENESS (Must be set before any GUI imports) ===
import ctypes
try:
    # Per-Monitor DPI Awareness (Windows 8.1+)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 2 = Per-Monitor V2
except AttributeError:
    try:
        # Fallback: System DPI Awareness (Windows Vista+)
        ctypes.windll.user32.SetProcessDPIAware()
    except AttributeError:
        pass  # Non-Windows platform
except OSError:
    pass  # DPI awareness already set
# ===========================================================

"""
USMA (Unified Screen Monitoring Application) - v0.5.2 (UI Enhancement Release)

A professional-grade GUI application for real-time screen monitoring, signal
analysis, and OCR designed for modal analysis workflows. Captures screen regions,
reconstructs FRF signals, performs dual-method quality classification (FFT +
Lowpass), and exports data in industry-standard formats (UNV Dataset 58).

Key Features:
- Startup dialog for config selection or new calibration workflow
- HSV color filter calibration with live preview
- Mandatory ROI type selection when drawing regions  
- Pre-load existing config in editor for modification
- Live analysis parameter adjustment during monitoring
- Scrollable main GUI with embedded console output
- Continuous logging mode for HSV debugging ("Log on Events Only" toggle)
- Dual classification: FFT energy ratio + Lowpass residual analysis
- Live graph viewer with hit navigation and multiple plot types
- Organized image logging (ROIs, Masks, Signals, FFT, Lowpass, Residual)
- UNV Dataset 58 export format
- DPI-aware rendering for high-resolution displays
- Fast screen capture via mss library with pyautogui fallback

For full version history, see README.md
"""

import cv2
import numpy as np
import time

# --- Screen Capture Configuration ---
try:
    import mss
    import mss.tools
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False
    print("Warning: mss not available, falling back to pyautogui (slower)")

import pyautogui  # Keep as fallback
import threading
import json
import os
import logging
import re
import gc
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Tuple
from contextlib import contextmanager
from PIL import Image, ImageTk
from scipy.fft import rfft, rfftfreq
from scipy.signal import butter, filtfilt
# scipy.io removed - .mat export deprecated
import matplotlib
matplotlib.use('TkAgg')  # For the embedded viewer only
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from datetime import datetime

# --- Import sounddevice with fallback ---
try:
    import sounddevice as sd
    SOUND_DEVICE_AVAILABLE = True
except (ImportError, OSError) as e:
    SOUND_DEVICE_AVAILABLE = False
    print(f"Warning: sounddevice library not found or audio device error: {e}. Audio feedback disabled.")

# --- OCR Configuration ---
try:
    import pytesseract
    import shutil

    script_dir = os.path.dirname(os.path.realpath(__file__))
    tesseract_path = os.path.join(script_dir, 'external', 'tesseract', 'tesseract.exe')

    if os.path.exists(tesseract_path):
        # Use portable/bundled Tesseract
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        OCR_AVAILABLE = True
    elif shutil.which('tesseract'):
        # Fallback: Tesseract found in system PATH
        pytesseract.pytesseract.tesseract_cmd = 'tesseract'
        OCR_AVAILABLE = True
        print("Info: Using system-installed Tesseract from PATH")
    else:
        raise FileNotFoundError("Tesseract not found in portable location or system PATH")

except (ImportError, FileNotFoundError) as e:
    OCR_AVAILABLE = False
    print(f"Warning: OCR features disabled. Error: {e}")


# --- 1. SETUP: DIRECTORY AND LOGGING CONFIGURATION ---
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


# --- Custom TextHandler for GUI Console ---
class TextHandler(logging.Handler):
    """Logging handler that writes to a tkinter Text widget."""
    
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setFormatter(log_formatter)
    
    def emit(self, record):
        try:
            msg = self.format(record) + '\n'
            # Schedule the update on the main thread
            self.text_widget.after(0, self._append_text, msg)
        except Exception:
            self.handleError(record)
    
    def _append_text(self, msg):
        try:
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg)
            self.text_widget.see(tk.END)  # Auto-scroll to bottom
            # Limit lines to prevent memory issues (keep last 1000 lines)
            line_count = int(self.text_widget.index('end-1c').split('.')[0])
            if line_count > 1000:
                self.text_widget.delete('1.0', f'{line_count - 1000}.0')
            self.text_widget.configure(state='disabled')
        except tk.TclError:
            pass  # Widget was destroyed


# --- 2. DATA CLASSES: CORE DATA STRUCTURES ---
@dataclass
class ImageLogOptions:
    include_screenshot: bool = False
    include_color_filter: bool = False
    include_signal_plot: bool = False
    include_fft_plot: bool = False
    include_lowpass_plot: bool = False
    include_residual_plot: bool = False
    include_summary_chart: bool = False
    include_ocr_images: bool = False

@dataclass
class VerboseLogOptions:
    """Options for verbose console logging categories."""
    log_config_values: bool = True
    log_mask_debug: bool = True
    log_ocr_output: bool = True
    log_fft_data: bool = True
    log_lowpass_data: bool = True
    log_classification: bool = True
    log_file_saves: bool = True

@dataclass
class DataLogOptions:
    log_unv: bool = False

@dataclass
class PointsInfo:
    """Stores parsed measurement point metadata."""
    run: str = "Run 1"
    hammer_point: str = "P1"
    hammer_dir: str = "-Z"
    response_point: str = "P1"
    response_dir: str = "-Z"

@dataclass
class MonitoringRegion:
    name: str
    x: int
    y: int
    width: int
    height: int
    roi_type: str
    enabled: bool = field(default=True)
    x_axis_min: float = field(default=0.0)
    x_axis_max: float = field(default=1024.0)
    y_axis_min: float = field(default=0.0)
    y_axis_max: float = field(default=1.0)
    y_axis_unit: str = field(default="g/N")
    resp_node: int = field(default=1)
    resp_dof: int = field(default=3)
    ref_node: int = field(default=1)
    ref_dof: int = field(default=3)

@dataclass
class FRFAnalysisResult:
    """Extended to include lowpass residual analysis results in physical units."""
    is_high_frequency: bool
    energy_ratio: float
    high_freq_energy: float
    signal_vector: np.ndarray
    fft_freqs: np.ndarray
    fft_mags: np.ndarray
    roi_image: np.ndarray
    color_mask: np.ndarray
    total_energy: float = 0.0
    signal_physical: Optional[np.ndarray] = None
    filtered_physical: Optional[np.ndarray] = None
    residual_physical: Optional[np.ndarray] = None
    exceedance_count: int = 0
    exceedance_ratio: float = 0.0
    lowpass_is_bad_hit: bool = False
    y_axis_unit: str = "g/N"

@dataclass
class LightweightHitData:
    """Lightweight version of hit data for history storage - avoids memory bloat."""
    signal_physical: np.ndarray
    filtered_physical: Optional[np.ndarray]
    residual_physical: Optional[np.ndarray]
    fft_freqs: np.ndarray
    fft_mags: np.ndarray
    energy_ratio: float
    is_high_frequency: bool
    exceedance_count: int
    exceedance_ratio: float
    lowpass_is_bad_hit: bool
    total_energy: float
    high_freq_energy: float
    y_axis_unit: str
    x_axis_min: float
    x_axis_max: float
    hit_key: str
    run: str

@dataclass
class FrameAnalysisResult:
    """Holds all analysis results from a single captured frame."""
    frf_results: Dict[str, FRFAnalysisResult] = field(default_factory=dict)
    active_regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    status_text: str = "Unknown"
    overload_text: str = "Unknown"
    points_info: PointsInfo = field(default_factory=PointsInfo)
    overall_is_hf: Optional[bool] = None
    avg_energy_ratio: Optional[float] = None
    avg_high_freq_energy: Optional[float] = None
    avg_exceedance_count: Optional[float] = None
    avg_exceedance_ratio: Optional[float] = None
    overall_lowpass_bad: Optional[bool] = None
    ocr_images: Dict[str, np.ndarray] = field(default_factory=dict)

@dataclass
class AppConfig:
    regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    hsv_lower: List[int] = field(default_factory=lambda: [0, 0, 0])
    hsv_upper: List[int] = field(default_factory=lambda: [179, 255, 240])
    screenshot_interval: float = 0.25
    fft_cutoff_frequency: float = 0.07
    fft_energy_ratio_threshold: float = 0.006
    lowpass_cutoff: float = 0.07
    lowpass_filter_order: int = 7
    residual_threshold: float = 0.005
    exceedance_ratio_threshold: float = 0.7


@contextmanager
def styled_figure(figsize=(10, 5), dpi=150):
    """
    Context manager for creating consistently styled matplotlib figures.
    Handles proper cleanup on exit.

    Usage:
        with styled_figure() as (fig, ax):
            ax.plot(x, y)
            fig.savefig('output.png', facecolor=fig.get_facecolor())
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = None
    try:
        fig = Figure(figsize=figsize, dpi=dpi, facecolor='#1E1E1E')
        _ = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        # Apply common dark theme styling
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


# --- 3. CORE LOGIC: THE SCREEN MONITOR ENGINE ---
class ScreenMonitor:
    """Handles the core task of capturing and analyzing the screen."""
    
    def __init__(self, config_path, update_callback=None, plot_callback=None):
        self.running = False
        self.thread = None
        self.config_path = config_path
        self.app_config = self._load_config(self.config_path)
        self.update_callback = update_callback
        self.plot_callback = plot_callback
        self.frame_count = 0
        self.verbose_logging_enabled = True
        self.image_logging_enabled = True
        self.image_log_options = ImageLogOptions()
        self.data_log_options = DataLogOptions()
        self.verbose_log_options = VerboseLogOptions()
        self.last_logged_ratio: Optional[float] = None
        self.last_logged_energy: Optional[float] = None
        self.audio_feedback_enabled = False
        self.audio_stream = None
        self.audio_phase = 0
        self.audio_frequency = 400
        self.audio_lock = threading.Lock()
        self.sample_rate = 44100
        self.hit_counters: Dict[str, int] = {}
        self.manual_points_info: Optional[PointsInfo] = None
        self.last_known_status: str = "Unknown"
        self.last_known_overload: str = "Unknown"
        
        # Continuous logging mode (when log_events_only is False)
        self.log_events_only: bool = True
        self.continuous_log_interval: float = 1.0  # seconds
        self.last_continuous_log_time: float = 0.0
        self.continuous_log_counter: int = 0
        
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self.sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        
        self.run_history: Dict[str, Dict] = {}
        self.current_run: str = "Run 1"

    def _capture_screen(self) -> np.ndarray:
        """
        Capture the screen using the fastest available method.
        Returns BGR numpy array compatible with OpenCV.
        """
        if MSS_AVAILABLE:
            with mss.mss() as sct:
                # Capture primary monitor
                monitor = sct.monitors[2]  # 1 = primary monitor (0 = all monitors combined)
                screenshot = sct.grab(monitor)
                # mss returns BGRA format - use proper OpenCV conversion
                # This is more reliable than just slicing [:, :, :3]
                img = np.array(screenshot, dtype=np.uint8)
                # Convert BGRA to BGR using OpenCV for consistent color handling
                return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        else:
            # Fallback to pyautogui
            screenshot = pyautogui.screenshot()
            # pyautogui returns RGB PIL Image, convert to BGR numpy array
            return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    def start(self, verbose_logging=True, image_logging=True, image_log_options=None,
              data_log_options=None, verbose_log_options=None, manual_points=None):
        if not self.app_config.regions:
            messagebox.showerror("Error", "Cannot start. Please load a valid configuration.")
            return False
        if not OCR_AVAILABLE and any(r.roi_type in ['status', 'overload', 'run', 'hammer', 'response'] 
                                      for r in self.app_config.regions.values()):
            logger.warning("Config uses OCR regions, but pytesseract is not available.")
        self.verbose_logging_enabled = verbose_logging
        self.image_logging_enabled = image_logging
        self.image_log_options = image_log_options if image_log_options else ImageLogOptions()
        self.data_log_options = data_log_options if data_log_options else DataLogOptions()
        self.verbose_log_options = verbose_log_options if verbose_log_options else VerboseLogOptions()
        self.manual_points_info = manual_points
        self.frame_count = 0
        self.last_logged_ratio = None
        self.last_logged_energy = None
        self.hit_counters.clear()
        self.run_history.clear()
        self.running = True
        self.last_known_status = "Unknown"
        self.last_known_overload = "Unknown"
        self.thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.thread.start()
        logger.info("Screen monitoring thread started for USMA v0.5.0")
        return True

    def stop(self):
        self.running = False
        self._stop_audio_feedback()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
        logger.info("Screen monitoring stopped.")

    def update_config(self, new_config_path):
        self.config_path = new_config_path
        self.app_config = self._load_config(new_config_path)
        logger.info(f"Configuration updated to {new_config_path}")

    def set_audio_feedback(self, enabled: bool):
        self.audio_feedback_enabled = enabled
        if not enabled:
            self._stop_audio_feedback()

    def _load_config(self, path: str) -> AppConfig:
        try:
            logger.info(f"Loading config from: {path}")
            if not os.path.exists(path):
                logger.warning(f"Config file does not exist: {path}")
                return AppConfig()
            
            with open(path, 'r') as f:
                config_data = json.load(f)
            
            config = AppConfig()
            metadata = config_data.get('_metadata', {})
            
            # Debug: Log what we found in metadata
            logger.info(f"Config _metadata keys: {list(metadata.keys()) if metadata else 'NONE'}")
            if 'hsv_lower' in metadata:
                logger.info(f"  Found hsv_lower in config: {metadata['hsv_lower']}")
            else:
                logger.warning(f"  hsv_lower NOT FOUND in config _metadata - using defaults")
            if 'hsv_upper' in metadata:
                logger.info(f"  Found hsv_upper in config: {metadata['hsv_upper']}")
            else:
                logger.warning(f"  hsv_upper NOT FOUND in config _metadata - using defaults")
            
            config.hsv_lower = metadata.get('hsv_lower', config.hsv_lower)
            config.hsv_upper = metadata.get('hsv_upper', config.hsv_upper)
            config.screenshot_interval = metadata.get('screenshot_interval', config.screenshot_interval)
            config.fft_cutoff_frequency = metadata.get('fft_cutoff_frequency', config.fft_cutoff_frequency)
            config.fft_energy_ratio_threshold = metadata.get('fft_energy_ratio_threshold', config.fft_energy_ratio_threshold)
            config.lowpass_cutoff = metadata.get('lowpass_cutoff', config.lowpass_cutoff)
            config.lowpass_filter_order = metadata.get('lowpass_filter_order', config.lowpass_filter_order)
            config.residual_threshold = metadata.get('residual_threshold', config.residual_threshold)
            config.exceedance_ratio_threshold = metadata.get('exceedance_ratio_threshold', config.exceedance_ratio_threshold)
            
            logger.info(f"Config loaded - HSV Lower: {config.hsv_lower}, HSV Upper: {config.hsv_upper}")
            
            region_fields = MonitoringRegion.__annotations__.keys()
            for name, data in config_data.items():
                if not name.startswith('_') and isinstance(data, dict):
                    filtered_data = {k: v for k, v in data.items() if k in region_fields}
                    if 'name' in filtered_data:
                        config.regions[name] = MonitoringRegion(**filtered_data)
            
            logger.info(f"Config regions loaded: {list(config.regions.keys())}")
            return config
        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AppConfig()

    def _audio_callback(self, outdata, frames, time_info, status):
        try:
            if status:
                logger.warning(f"Audio stream status: {status}")
            t = (self.audio_phase + np.arange(frames)) / self.sample_rate
            t = t.reshape(-1, 1)
            amplitude = np.iinfo(np.int16).max * 0.3
            outdata[:] = amplitude * np.sin(2 * np.pi * self.audio_frequency * t)
            self.audio_phase += frames
        except Exception as e:
            logger.error(f"Audio callback error: {e}")
            outdata.fill(0)

    def _start_audio_feedback(self):
        if not SOUND_DEVICE_AVAILABLE or self.audio_stream is not None:
            return
        try:
            self.audio_phase = 0
            self.audio_stream = sd.OutputStream(
                samplerate=self.sample_rate, channels=1, 
                callback=self._audio_callback, dtype='int16'
            )
            self.audio_stream.start()
            logger.info("Continuous audio feedback started.")
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}")
            self.audio_stream = None

    def _stop_audio_feedback(self):
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
                logger.info("Continuous audio feedback stopped.")
            except Exception as e:
                logger.error(f"Error stopping audio stream: {e}")
            finally:
                self.audio_stream = None
            
    def _monitoring_loop(self):
        while self.running:
            start_time = time.time()
            try:
                image = self._capture_screen()
                frame_result, all_rois = self._process_frame(image)
                
                if self.update_callback:
                    self.update_callback(frame_result)
                
                if self.audio_feedback_enabled:
                    with self.audio_lock:
                        is_hf = frame_result.overall_is_hf if frame_result.overall_is_hf is not None else False
                        if is_hf and self.audio_stream is None:
                            self._start_audio_feedback()
                        elif not is_hf and self.audio_stream is not None:
                            self._stop_audio_feedback()
                
                # Handle logging: event-based or continuous
                if self.log_events_only:
                    self._handle_logging(frame_result, all_rois)
                else:
                    # Continuous logging mode - log every continuous_log_interval seconds
                    current_time = time.time()
                    if current_time - self.last_continuous_log_time >= self.continuous_log_interval:
                        self._handle_continuous_logging(frame_result, all_rois)
                        self.last_continuous_log_time = current_time
                
                elapsed_time = time.time() - start_time
                sleep_duration = self.app_config.screenshot_interval - elapsed_time
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(1)

    def _process_frame(self, image: np.ndarray) -> Tuple[FrameAnalysisResult, Dict[str, np.ndarray]]:
        frame_result = FrameAnalysisResult()
        ocr_points_active = False
        all_rois: Dict[str, np.ndarray] = {}
        
        frame_result.points_info = PointsInfo()
        
        for name, region in self.app_config.regions.items():
            if not region.enabled:
                continue
            roi = image[region.y:region.y+region.height, region.x:region.x+region.width]
            if roi.size == 0:
                continue
            
            all_rois[name] = roi.copy()
            
            if region.roi_type == 'frf':
                analysis_result = self._analyze_wave_pattern(roi, region)
                if analysis_result:
                    frame_result.frf_results[name] = analysis_result
                    frame_result.active_regions[name] = region
            elif OCR_AVAILABLE:
                if region.roi_type == 'status':
                    text, diag_imgs = self._analyze_status_robust(roi)
                    frame_result.status_text = text
                    frame_result.ocr_images.update(diag_imgs)
                elif region.roi_type == 'overload':
                    text, diag_imgs = self._analyze_overload_robust(roi)
                    frame_result.overload_text = text
                    frame_result.ocr_images.update(diag_imgs)
                elif region.roi_type == 'run':
                    run_str, diag_imgs = self._analyze_run_robust(roi)
                    if run_str:
                        frame_result.points_info.run = run_str
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True
                elif region.roi_type == 'hammer':
                    point, direction, diag_imgs = self._analyze_point_and_dir_robust(roi, "hammer")
                    if point:
                        frame_result.points_info.hammer_point = point
                    if direction:
                        frame_result.points_info.hammer_dir = direction
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True
                elif region.roi_type == 'response':
                    point, direction, diag_imgs = self._analyze_point_and_dir_robust(roi, "response")
                    if point:
                        frame_result.points_info.response_point = point
                    if direction:
                        frame_result.points_info.response_dir = direction
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True

        current_status = frame_result.status_text
        if current_status != "Unknown" and current_status != self.last_known_status:
            if self.verbose_logging_enabled:
                logger.info(f"STATUS UPDATE: '{self.last_known_status}' -> '{current_status}'")
            self.last_known_status = current_status
            
        current_overload = frame_result.overload_text
        if current_overload != "Unknown" and current_overload != self.last_known_overload:
            if self.verbose_logging_enabled:
                logger.info(f"OVERLOAD UPDATE: '{self.last_known_overload}' -> '{current_overload}'")
            self.last_known_overload = current_overload

        if not ocr_points_active and self.manual_points_info:
            frame_result.points_info = self.manual_points_info

        if frame_result.frf_results:
            classifications = [res.is_high_frequency for res in frame_result.frf_results.values()]
            frame_result.overall_is_hf = sum(classifications) > len(classifications) / 2 if classifications else False
            frame_result.avg_energy_ratio = np.mean([res.energy_ratio for res in frame_result.frf_results.values()])
            frame_result.avg_high_freq_energy = np.mean([res.high_freq_energy for res in frame_result.frf_results.values()])
            frame_result.avg_exceedance_count = np.mean([res.exceedance_count for res in frame_result.frf_results.values()])
            frame_result.avg_exceedance_ratio = np.mean([res.exceedance_ratio for res in frame_result.frf_results.values()])
            lp_classifications = [res.lowpass_is_bad_hit for res in frame_result.frf_results.values()]
            frame_result.overall_lowpass_bad = sum(lp_classifications) > len(lp_classifications) / 2 if lp_classifications else False
        
        return frame_result, all_rois

    def _handle_logging(self, frame_result: FrameAnalysisResult, all_rois: Dict[str, np.ndarray]):
        if not frame_result.frf_results:
            return

        has_changed = (frame_result.avg_energy_ratio is not None and 
                       (self.last_logged_ratio is None or 
                        not np.isclose(frame_result.avg_energy_ratio, self.last_logged_ratio, atol=1e-9) or
                        not np.isclose(frame_result.avg_high_freq_energy, self.last_logged_energy, atol=1e-9)))

        if has_changed:
            points = frame_result.points_info
            
            fft_bad = frame_result.overall_is_hf
            lp_bad = frame_result.overall_lowpass_bad if frame_result.overall_lowpass_bad is not None else False
            
            if fft_bad and lp_bad:
                classification = "BAD HIT (Both FFT and Lowpass)"
            elif fft_bad:
                classification = "SUSPECT (FFT only)"
            elif lp_bad:
                classification = "SUSPECT (Lowpass only)"
            else:
                classification = "GOOD HIT"
            
            if self.verbose_logging_enabled:
                logger.info(f"--- WAVE EVENT DETECTED ---")
                logger.info(f"  OCR Status: '{frame_result.status_text}'")
                logger.info(f"  OCR Overload: '{frame_result.overload_text}'")
                logger.info(f"  OCR Run: '{points.run}'")
                logger.info(f"  OCR Hammer: '{points.hammer_point}' Dir: '{points.hammer_dir}'")
                logger.info(f"  OCR Response: '{points.response_point}' Dir: '{points.response_dir}'")
                logger.info(f"  FFT Energy Ratio: {frame_result.avg_energy_ratio:.3e} (threshold: {self.app_config.fft_energy_ratio_threshold:.3e}) -> {'BAD' if fft_bad else 'OK'}")
                logger.info(f"  LP Exceedances: {frame_result.avg_exceedance_count:.0f} ({frame_result.avg_exceedance_ratio:.1%}) (threshold: {self.app_config.exceedance_ratio_threshold:.1%}) -> {'BAD' if lp_bad else 'OK'}")
                logger.info(f"  CLASSIFICATION: {classification}")
                logger.info(f"----------------------------")
            
            self.last_logged_ratio = frame_result.avg_energy_ratio
            self.last_logged_energy = frame_result.avg_high_freq_energy
            
            counter_key = f"{points.hammer_point}{points.response_point}"
            current_hit = self.hit_counters.get(counter_key, 0) + 1
            self.hit_counters[counter_key] = current_hit
            
            hit_key = f"{counter_key}_{current_hit}"
            self.current_run = points.run
            if self.current_run not in self.run_history:
                self.run_history[self.current_run] = {}
            
            for frf_name, frf_result in frame_result.frf_results.items():
                base_filename = f"{frf_name}_{counter_key}_{current_hit}"
                region = frame_result.active_regions[frf_name]
                
                self.run_history[self.current_run][hit_key] = {
                    'exceedance_count': frf_result.exceedance_count,
                    'exceedance_ratio': frf_result.exceedance_ratio,
                    'energy_ratio': frf_result.energy_ratio,
                    'is_hf': frf_result.is_high_frequency,
                    'lowpass_bad': frf_result.lowpass_is_bad_hit
                }
                
                if self.image_logging_enabled: 
                    self._create_visual_logs(frf_result, frame_result, frf_name, base_filename, all_rois)
                if self.data_log_options.log_unv: 
                    self._save_unv_log(frf_result, frame_result, frf_name, base_filename)
                
                if self.plot_callback:
                    # Create lightweight copy for plot callback
                    lightweight_data = LightweightHitData(
                        signal_physical=frf_result.signal_physical.copy() if frf_result.signal_physical is not None else np.array([]),
                        filtered_physical=frf_result.filtered_physical.copy() if frf_result.filtered_physical is not None else None,
                        residual_physical=frf_result.residual_physical.copy() if frf_result.residual_physical is not None else None,
                        fft_freqs=frf_result.fft_freqs.copy(),
                        fft_mags=frf_result.fft_mags.copy(),
                        energy_ratio=frf_result.energy_ratio,
                        is_high_frequency=frf_result.is_high_frequency,
                        exceedance_count=frf_result.exceedance_count,
                        exceedance_ratio=frf_result.exceedance_ratio,
                        lowpass_is_bad_hit=frf_result.lowpass_is_bad_hit,
                        total_energy=frf_result.total_energy,
                        high_freq_energy=frf_result.high_freq_energy,
                        y_axis_unit=region.y_axis_unit,
                        x_axis_min=region.x_axis_min,
                        x_axis_max=region.x_axis_max,
                        hit_key=hit_key,
                        run=points.run
                    )
                    self.plot_callback(lightweight_data, self.run_history.copy())

    def _handle_continuous_logging(self, frame_result: FrameAnalysisResult, all_rois: Dict[str, np.ndarray]):
        """
        Continuous logging mode - saves images and logs every interval regardless of wave events.
        Used for debugging HSV calibration and signal detection issues.
        """
        self.continuous_log_counter += 1
        timestamp = datetime.now().strftime("%H%M%S")
        base_filename = f"continuous_{timestamp}_{self.continuous_log_counter}"
        
        if self.verbose_logging_enabled:
            logger.info(f"--- CONTINUOUS LOG #{self.continuous_log_counter} ---")
            logger.info(f"  HSV Lower: {self.app_config.hsv_lower}")
            logger.info(f"  HSV Upper: {self.app_config.hsv_upper}")
            logger.info(f"  Status: '{frame_result.status_text}'")
            logger.info(f"  Wave Results: {len(frame_result.frf_results)} regions detected")
            if frame_result.avg_energy_ratio is not None:
                logger.info(f"  Avg Energy Ratio: {frame_result.avg_energy_ratio:.3e}")
                logger.info(f"  Avg Exceedance: {frame_result.avg_exceedance_count}")
        
        # Save images for all wave regions regardless of detection
        if self.image_logging_enabled:
            for region_name, region in self.app_config.regions.items():
                if region.roi_type == 'frf' and region.enabled:
                    if region_name in all_rois:
                        roi = all_rois[region_name]
                        
                        # Always save ROI screenshot in continuous mode
                        if self.image_log_options.include_screenshot:
                            cv2.imwrite(f"image_logs/ROIs/{base_filename}_{region_name}_ROI.jpg", roi)
                        
                        # Create and save mask image
                        if self.image_log_options.include_color_filter:
                            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                            mask = cv2.inRange(hsv, np.array(self.app_config.hsv_lower), np.array(self.app_config.hsv_upper))
                            cv2.imwrite(f"image_logs/ColorMasks/{base_filename}_{region_name}_mask.jpg", mask)
                            
                            # Log mask statistics
                            total_px = mask.shape[0] * mask.shape[1]
                            white_px = np.count_nonzero(mask)
                            if self.verbose_logging_enabled:
                                logger.info(f"  [{region_name}] Mask: {white_px}/{total_px} ({100*white_px/total_px:.2f}%) white pixels")

    # =========================================================================
    # ENHANCED OCR METHODS
    # =========================================================================
    
    def _preprocess_for_ocr(self, roi: np.ndarray, scale_factor: int = 4, 
                            use_clahe: bool = True, use_sharpen: bool = True,
                            use_morphology: bool = False, invert: bool = True) -> np.ndarray:
        if roi.size == 0:
            return np.array([])
        
        width = int(roi.shape[1] * scale_factor)
        height = int(roi.shape[0] * scale_factor)
        resized = cv2.resize(roi, (width, height), interpolation=cv2.INTER_CUBIC)
        
        if len(resized.shape) == 3:
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        else:
            gray = resized
        
        if use_clahe:
            gray = self.clahe.apply(gray)
        
        if use_sharpen:
            gray = cv2.filter2D(gray, -1, self.sharpen_kernel)
        
        if invert:
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        else:
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        
        if use_morphology:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        return thresh

    def _run_ocr_with_config(self, image: np.ndarray, psm: int = 7, 
                              whitelist: Optional[str] = None, 
                              load_dawgs: bool = True) -> str:
        try:
            custom_config = f'--oem 3 --psm {psm}'
            if whitelist:
                custom_config += f' -c tessedit_char_whitelist={whitelist}'
            if not load_dawgs:
                custom_config += ' -c load_system_dawg=false -c load_freq_dawg=false'
            
            text = pytesseract.image_to_string(image, config=custom_config)
            return text.strip()
        except Exception as e:
            logger.debug(f"OCR failed: {e}")
            return ""

    def _analyze_status_robust(self, roi: np.ndarray) -> Tuple[str, Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True, 'invert': True},
            {'scale_factor': 3, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': False},
        ]
        
        best_text = ""
        best_img = None
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
                
            for psm in [7, 6]:
                text = self._run_ocr_with_config(preprocessed, psm=psm)
                text_lower = text.lower()
                
                if 'wait' in text_lower or 'trigger' in text_lower:
                    diag_imgs['status_preprocessed'] = preprocessed
                    return "Waiting for Trigger...", diag_imgs
                if 'measur' in text_lower:
                    diag_imgs['status_preprocessed'] = preprocessed
                    return "Measuring...", diag_imgs
                if 'ready' in text_lower:
                    diag_imgs['status_preprocessed'] = preprocessed
                    return "Ready", diag_imgs
                
                if len(text) > len(best_text):
                    best_text = text
                    best_img = preprocessed
        
        mean_color = np.mean(roi, axis=(0, 1))
        if best_img is not None:
            diag_imgs['status_preprocessed'] = best_img
        
        if mean_color[1] > 120 and mean_color[1] > mean_color[2]:
            return "Measuring... (color)", diag_imgs
        if mean_color[2] > 120 and mean_color[2] > mean_color[1]:
            return "Waiting for Trigger... (color)", diag_imgs
        if mean_color[0] > 120:
            return "Ready (color)", diag_imgs
        
        return f"Unknown (OCR: '{best_text[:20]}')" if best_text else "Unknown", diag_imgs

    def _analyze_overload_robust(self, roi: np.ndarray) -> Tuple[str, Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        mean_color = np.mean(roi, axis=(0, 1))
        is_red = mean_color[2] > 150 and mean_color[1] < 100 and mean_color[0] < 100
        
        if not is_red:
            return "No Overload", diag_imgs
        
        preprocessed = self._preprocess_for_ocr(roi, scale_factor=4)
        diag_imgs['overload_preprocessed'] = preprocessed
        
        whitelist = '0123456789ChannelinOverload '
        text = self._run_ocr_with_config(preprocessed, psm=7, whitelist=whitelist)
        
        match = re.search(r'(\d+)\s*Channel', text, re.IGNORECASE)
        if match:
            return f"{match.group(1)} Channel in Overload", diag_imgs
        
        return "Channel in Overload", diag_imgs

    def _analyze_run_robust(self, roi: np.ndarray) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True, 'invert': True},
            {'scale_factor': 6, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
        ]
        
        whitelist = 'Run0123456789 '
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
            
            diag_imgs[f'run_attempt_{i}'] = preprocessed
            
            for psm in [7, 8, 6]:
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                run_match = re.search(r'[Rr][Uu][Nn]\s*(\d+)', text)
                if run_match:
                    run_str = f"Run {run_match.group(1)}"
                    return run_str, diag_imgs
                
                num_match = re.search(r'^(\d+)$', text.strip())
                if num_match:
                    run_str = f"Run {num_match.group(1)}"
                    return run_str, diag_imgs
        
        return None, diag_imgs

    def _analyze_point_and_dir_robust(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        point, direction = None, None
        diag_imgs = {}
        
        try:
            width = roi.shape[1]
            split_ratios = [0.65, 0.70, 0.75, 0.60]
            
            for split_ratio in split_ratios:
                split_x = int(width * split_ratio)
                point_roi = roi[:, :split_x]
                dir_roi = roi[:, split_x:]
                
                point_result = self._analyze_point_only(point_roi, f"{name}_point")
                if point_result[0]:
                    point = point_result[0]
                    diag_imgs.update(point_result[1])
                    
                    dir_result = self._analyze_direction_only(dir_roi, f"{name}_dir")
                    if dir_result[0]:
                        direction = dir_result[0]
                    diag_imgs.update(dir_result[1])
                    break
            
            if not point:
                full_result = self._analyze_full_point_roi(roi, name)
                point = full_result[0]
                direction = full_result[1]
                diag_imgs.update(full_result[2])
                
        except Exception as e:
            logger.error(f"Failed to analyze point/dir for {name}: {e}")
        
        return point, direction, diag_imgs

    def _analyze_point_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False},
            {'scale_factor': 6, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True},
            {'scale_factor': 4, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False},
        ]
        
        whitelist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789 '
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
            
            diag_imgs[f'{name}_attempt_{i}'] = preprocessed
            
            for psm in [7, 8, 13]:
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                match = re.search(r'([A-Za-z])\s*[:\s]\s*(\d+)', text)
                if match:
                    point = f"{match.group(1).upper()}{match.group(2)}"
                    return point, diag_imgs
                
                match = re.search(r'([A-Za-z])(\d+)', text)
                if match:
                    point = f"{match.group(1).upper()}{match.group(2)}"
                    return point, diag_imgs
                
                match = re.search(r'^[\s:]*(\d+)\s*$', text)
                if match:
                    point = f"P{match.group(1)}"
                    return point, diag_imgs
        
        return None, diag_imgs

    def _analyze_direction_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        
        configs = [
            {'scale_factor': 6, 'use_clahe': True, 'use_sharpen': True},
            {'scale_factor': 5, 'use_clahe': False, 'use_sharpen': True},
        ]
        
        whitelist = '+-XYZxyz'
        
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(roi, **cfg)
            if preprocessed.size == 0:
                continue
            
            diag_imgs[f'{name}_attempt_{i}'] = preprocessed
            
            for psm in [10, 8, 13]:
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                match = re.search(r'([+\-])?\s*([XYZxyz])', text)
                if match:
                    sign = match.group(1) if match.group(1) else '+'
                    axis = match.group(2).upper()
                    direction = f"{sign}{axis}"
                    return direction, diag_imgs
        
        return None, diag_imgs

    def _analyze_full_point_roi(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
        point, direction = None, None
        
        preprocessed = self._preprocess_for_ocr(roi, scale_factor=5, use_clahe=True, use_sharpen=True)
        if preprocessed.size == 0:
            return None, None, diag_imgs
        
        diag_imgs[f'{name}_full'] = preprocessed
        
        whitelist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789+-XYZxyz '
        text = self._run_ocr_with_config(preprocessed, psm=7, whitelist=whitelist, load_dawgs=False)
        
        full_match = re.search(r'([A-Za-z])\s*[:\s]\s*(\d+)\s*([+\-]?\s*[XYZxyz])?', text)
        if full_match:
            point = f"{full_match.group(1).upper()}{full_match.group(2)}"
            if full_match.group(3):
                dir_text = full_match.group(3).replace(' ', '')
                if len(dir_text) == 1:
                    direction = f"+{dir_text.upper()}"
                else:
                    direction = f"{dir_text[0]}{dir_text[1].upper()}"
        
        return point, direction, diag_imgs

    # =========================================================================
    # WAVE ANALYSIS METHODS
    # =========================================================================

    def _validate_signal_quality(self, color_mask: np.ndarray) -> bool:
        height, width = color_mask.shape
        if height == 0 or width == 0:
            return False
        total_pixels = height * width
        if total_pixels == 0:
            return False
        signal_pixels = np.count_nonzero(color_mask)
        coverage_ratio = signal_pixels / total_pixels
        if not (0.0005 < coverage_ratio < 0.4):
            return False
        cols_with_signal = np.count_nonzero(np.sum(color_mask, axis=0) > 0)
        continuity_ratio = cols_with_signal / width
        if continuity_ratio < 0.15:
            return False
        return True

    def _apply_lowpass_filter(self, signal: np.ndarray) -> np.ndarray:
        if len(signal) < 15:
            return signal.copy()
        
        cutoff = min(self.app_config.lowpass_cutoff, 0.99)
        order = self.app_config.lowpass_filter_order
        
        try:
            b, a = butter(order, cutoff, btype='low')
            filtered = filtfilt(b, a, signal)
            return filtered
        except Exception as e:
            logger.warning(f"Lowpass filter failed: {e}. Returning original signal.")
            return signal.copy()

    def _calculate_exceedances(self, residual: np.ndarray, threshold: float) -> Tuple[int, float]:
        exceedance_mask = np.abs(residual) > threshold
        count = np.sum(exceedance_mask)
        ratio = count / len(residual) if len(residual) > 0 else 0.0
        return int(count), ratio

    def _analyze_wave_pattern(self, roi: np.ndarray, region: MonitoringRegion) -> Optional[FRFAnalysisResult]:
        if roi.size == 0:
            return None
        
        # Debug: Log HSV filter values being used (only when verbose logging enabled)
        if self.verbose_logging_enabled and self.verbose_log_options.log_config_values and self.frame_count % 20 == 0:  # Log every 20th frame to avoid spam
            logger.info(f"[HSV DEBUG] Lower: {self.app_config.hsv_lower}, Upper: {self.app_config.hsv_upper}")
            
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(self.app_config.hsv_lower), np.array(self.app_config.hsv_upper))
        
        # Debug: Log mask statistics (only when verbose logging AND mask debug enabled)
        if self.verbose_logging_enabled and self.verbose_log_options.log_mask_debug and self.frame_count % 20 == 0:
            total_px = mask.shape[0] * mask.shape[1]
            white_px = np.count_nonzero(mask)
            logger.info(f"[MASK DEBUG] White pixels: {white_px}/{total_px} ({100*white_px/total_px:.2f}%)")
        
        if not self._validate_signal_quality(mask):
            return None
        
        y_coords, x_coords = np.nonzero(mask)
        width = mask.shape[1]
        height = mask.shape[0]
        
        if len(x_coords) == 0:
            signal_pixels = np.full(width, height / 2)
        else:
            unique_x, anchor_y_idx = np.unique(x_coords, return_inverse=True)
            sum_y = np.bincount(anchor_y_idx, weights=y_coords)
            count_y = np.bincount(anchor_y_idx)
            anchor_y = sum_y / count_y
            if len(unique_x) < 2:
                signal_pixels = np.full(width, anchor_y[0] if anchor_y.size > 0 else height / 2)
            else:
                signal_pixels = np.interp(np.arange(width), unique_x, anchor_y)
        
        signal_pixels = height - signal_pixels
        
        if signal_pixels.size < 2:
            return None
        
        N = len(signal_pixels)
        detrended_pixels = signal_pixels - np.mean(signal_pixels)
        yf = rfft(detrended_pixels)
        xf = rfftfreq(N, 1)
        fft_mags = np.abs(yf)
        total_energy = np.sum(fft_mags**2)
        high_freq_energy, energy_ratio, is_hf = 0, 0, False
        
        if total_energy > 1e-9:
            cutoff_indices = np.where(xf >= self.app_config.fft_cutoff_frequency)[0]
            if cutoff_indices.size > 0:
                high_freq_energy = np.sum(fft_mags[cutoff_indices[0]:]**2)
                energy_ratio = high_freq_energy / total_energy
            is_hf = energy_ratio > self.app_config.fft_energy_ratio_threshold
        
        y_range = region.y_axis_max - region.y_axis_min
        signal_physical = region.y_axis_min + (signal_pixels / height) * y_range
        
        signal_mean = np.mean(signal_physical)
        signal_detrended = signal_physical - signal_mean
        
        filtered_detrended = self._apply_lowpass_filter(signal_detrended)
        residual_physical = signal_detrended - filtered_detrended
        filtered_physical = filtered_detrended + signal_mean
        
        exceedance_count, exceedance_ratio = self._calculate_exceedances(
            residual_physical, self.app_config.residual_threshold
        )
        lowpass_is_bad = exceedance_ratio > self.app_config.exceedance_ratio_threshold
        
        return FRFAnalysisResult(
            is_high_frequency=is_hf,
            energy_ratio=energy_ratio,
            high_freq_energy=high_freq_energy,
            signal_vector=signal_pixels,
            fft_freqs=xf,
            fft_mags=fft_mags,
            roi_image=roi.copy(),
            color_mask=mask.copy(),
            total_energy=total_energy,
            signal_physical=signal_physical,
            filtered_physical=filtered_physical,
            residual_physical=residual_physical,
            exceedance_count=exceedance_count,
            exceedance_ratio=exceedance_ratio,
            lowpass_is_bad_hit=lowpass_is_bad,
            y_axis_unit=region.y_axis_unit
        )

    # =========================================================================
    # VISUAL LOGGING - THREAD-SAFE (Using Figure directly, not pyplot)
    # =========================================================================

    def _create_visual_logs(self, frf_result: FRFAnalysisResult, frame_result: FrameAnalysisResult, 
                           frf_name: str, base_filename: str, all_rois: Dict[str, np.ndarray]):
        try:
            region = frame_result.active_regions[frf_name]
            
            title_info = (f"{frame_result.points_info.run} | H: {frame_result.points_info.hammer_point}"
                         f"{frame_result.points_info.hammer_dir} R: {frame_result.points_info.response_point}"
                         f"{frame_result.points_info.response_dir} | Overload: {frame_result.overload_text}")

            if self.image_log_options.include_screenshot:
                for r_name, r_img in all_rois.items():
                    try:
                        r_type = self.app_config.regions[r_name].roi_type
                        if r_type == 'frf':
                            cv2.imwrite(f"image_logs/ROIs/{base_filename}_{r_name}.jpg", r_img)
                    except KeyError:
                        pass
                    except Exception as e:
                        logger.error(f"Failed to save ROI image for '{r_name}': {e}")

            if self.image_log_options.include_color_filter: 
                cv2.imwrite(f"image_logs/ColorMasks/{base_filename}_mask.jpg", frf_result.color_mask)
            
            if self.image_log_options.include_signal_plot:
                self._create_signal_plot_safe(frf_result, region, title_info, 
                                             f"image_logs/Signals/{base_filename}_signal.png")
            
            if self.image_log_options.include_fft_plot:
                self._create_fft_plot_safe(frf_result, region, title_info, 
                                          f"image_logs/FFT/{base_filename}_fft.png")
            
            if self.image_log_options.include_lowpass_plot:
                self._create_lowpass_comparison_plot_safe(frf_result, region, title_info, 
                                                         f"image_logs/Lowpass/{base_filename}_lowpass.png")
            
            if self.image_log_options.include_residual_plot:
                self._create_residual_plot_safe(frf_result, region, title_info, 
                                               f"image_logs/Residual/{base_filename}_residual.png")
            
            if self.image_log_options.include_summary_chart and self.current_run in self.run_history:
                self._create_run_summary_chart_safe(self.run_history[self.current_run], 
                                                    f"image_logs/Summary/{base_filename}_summary.png")
            
            if self.image_log_options.include_ocr_images and frame_result.ocr_images:
                for ocr_name, ocr_img in frame_result.ocr_images.items():
                    if ocr_img is not None and ocr_img.size > 0:
                        cv2.imwrite(f"image_logs/OCRs/{base_filename}_{ocr_name}.jpg", ocr_img)
                
        except Exception as e:
            logger.error(f"Failed to create visual logs for {frf_name}: {e}")

    def _create_signal_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion, 
                                 title_info: str, filename: str):
        """Thread-safe signal plot creation using Figure directly."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            num_points = len(frf_result.signal_physical)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            
            ax.plot(freq_axis, frf_result.signal_physical, color='cyan', linewidth=1.5)
            ax.set_xlabel('Frequency (Hz)', color='white')
            ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
            ax.set_title(f'Reconstructed Signal\n{title_info}', color='white', fontsize=10)
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_fft_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion, 
                              title_info: str, filename: str):
        """Thread-safe FFT plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            ax.plot(frf_result.fft_freqs, frf_result.fft_mags, color='magenta', linewidth=1)
            ax.axvline(x=self.app_config.fft_cutoff_frequency, color='yellow', 
                      linestyle='--', linewidth=1, label=f'Cutoff: {self.app_config.fft_cutoff_frequency:.2f}')
            ax.set_xlim(0, 0.5)
            ax.set_xlabel('Normalized Frequency', color='white')
            ax.set_ylabel('Magnitude (A.U.)', color='white')
            
            fft_info = (f'Total E: {frf_result.total_energy:.2e} | '
                       f'HF E: {frf_result.high_freq_energy:.2e} | '
                       f'Ratio: {frf_result.energy_ratio:.3e}')
            classification = "HF (Bad)" if frf_result.is_high_frequency else "LF (Good)"
            ax.set_title(f'FFT Magnitude Spectrum - {classification}\n{title_info}\n{fft_info}', 
                        color='white', fontsize=9)
            
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.legend(facecolor='#2E2E2E', labelcolor='white')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_lowpass_comparison_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion,
                                             title_info: str, filename: str):
        """Thread-safe lowpass comparison plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            if frf_result.signal_physical is None or frf_result.filtered_physical is None:
                ax.text(0.5, 0.5, 'No lowpass data available', 
                       transform=ax.transAxes, ha='center', va='center', color='white', fontsize=14)
                fig.savefig(filename, facecolor=fig.get_facecolor())
                return
            
            num_points = len(frf_result.signal_physical)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            
            ax.plot(freq_axis, frf_result.signal_physical, 'w-', linewidth=2, label='Original', alpha=0.9)
            ax.plot(freq_axis, frf_result.filtered_physical, 'g--', linewidth=1.5, label='Lowpass Filtered')
            
            filter_info = f'Cutoff: {self.app_config.lowpass_cutoff:.3f} | Order: {self.app_config.lowpass_filter_order}'
            ax.set_title(f'Lowpass Filter Comparison\n{title_info}\n{filter_info}', 
                        color='white', fontsize=9)
            ax.set_xlabel('Frequency (Hz)', color='white')
            ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.legend(facecolor='#2E2E2E', labelcolor='white')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_residual_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion,
                                   title_info: str, filename: str):
        """Thread-safe residual plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            if frf_result.residual_physical is None:
                ax.text(0.5, 0.5, 'No residual data available', 
                       transform=ax.transAxes, ha='center', va='center', color='white', fontsize=14)
                fig.savefig(filename, facecolor=fig.get_facecolor())
                return
            
            num_points = len(frf_result.residual_physical)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            threshold = self.app_config.residual_threshold
            
            ax.plot(freq_axis, frf_result.residual_physical, 'c-', linewidth=1, label='Residual (HF Content)')
            ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
            ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.5, 
                      label=f'Threshold (±{threshold} {region.y_axis_unit})')
            ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.5)
            
            exceedances = np.abs(frf_result.residual_physical) > threshold
            if np.any(exceedances):
                ax.scatter(freq_axis[exceedances], frf_result.residual_physical[exceedances], 
                          c='red', s=15, zorder=5, alpha=0.7)
            
            classification = "BAD HIT" if frf_result.lowpass_is_bad_hit else "GOOD HIT"
            residual_info = (f'Exceedances: {frf_result.exceedance_count} ({frf_result.exceedance_ratio:.1%}) | '
                            f'Threshold: {self.app_config.exceedance_ratio_threshold:.1%} | {classification}')
            ax.set_title(f'Residual Analysis\n{title_info}\n{residual_info}', 
                        color='white', fontsize=9)
            ax.set_xlabel('Frequency (Hz)', color='white')
            ax.set_ylabel(f'Residual ({region.y_axis_unit})', color='white')
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.legend(facecolor='#2E2E2E', labelcolor='white', loc='upper right')
            ax.grid(True, linestyle='--', alpha=0.3)
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()

    def _create_run_summary_chart_safe(self, hit_data: Dict, filename: str):
        """Thread-safe run summary chart creation."""
        if not hit_data:
            return
        
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            import matplotlib.cm as cm
            
            fig = Figure(figsize=(12, 6), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            
            hits = list(hit_data.keys())
            values = [hit_data[h]['exceedance_count'] for h in hits]
            
            bars = ax.bar(range(len(hits)), values, edgecolor='white', linewidth=0.5)
            
            cmap = cm.jet
            vmin, vmax = min(values), max(values)
            if vmin == vmax:
                vmin, vmax = vmin - 1, vmax + 1
            
            for bar, val in zip(bars, values):
                normalized = (val - vmin) / (vmax - vmin) if vmax > vmin else 0.5
                bar.set_color(cmap(normalized))
            
            ax.set_xticks(range(len(hits)))
            ax.set_xticklabels(hits, rotation=45, ha='right', fontsize=8, color='white')
            ax.set_xlabel('Hit (Point Combination)', color='white')
            ax.set_ylabel('Exceedances', color='white')
            ax.set_title(f'Run Summary - Exceedance Counts\n{self.current_run}', color='white', fontsize=11)
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.grid(True, linestyle='--', alpha=0.3, axis='y')
            
            for spine in ax.spines.values():
                spine.set_color('white')
            
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                fig.clf()
                del fig
            gc.collect()
    
    # =========================================================================
    # DATA FILE LOGGING
    # =========================================================================
    

    def _save_unv_log(self, frf_result: FRFAnalysisResult, frame_result: FrameAnalysisResult, 
                      frf_name: str, base_filename: str):
        def parse_point(point_str: str) -> int: 
            return int(re.sub(r'\D', '', point_str)) if point_str and re.sub(r'\D', '', point_str) else 1
        
        def parse_dof(dir_str: str) -> int: 
            if not dir_str or len(dir_str) < 1:
                return 3
            last_char = dir_str.upper()[-1]
            return {'X': 1, 'Y': 2, 'Z': 3}.get(last_char, 3)
        
        try:
            filename = f"signal_logs/{base_filename}.unv"
            region = frame_result.active_regions[frf_name]
            points = frame_result.points_info
            num_points = len(frf_result.signal_physical)
            
            if num_points < 2:
                logger.warning(f"Skipping UNV log for {frf_name}: insufficient data points ({num_points})")
                return
            
            start_freq = region.x_axis_min
            freq_step = (region.x_axis_max - region.x_axis_min) / (num_points - 1) if num_points > 1 else 0
            
            resp_node = parse_point(points.response_point)
            resp_dof = parse_dof(points.response_dir)
            ref_node = parse_point(points.hammer_point)
            ref_dof = parse_dof(points.hammer_dir)
            timestamp = datetime.now().strftime('%d-%b-%y %H:%M:%S')
            
            with open(filename, 'w') as f:
                f.write(f"    -1{' ' * 74}\n")
                f.write(f"    58{' ' * 74}\n")
                
                id_line1 = f"FRF for {points.response_point}:{points.response_dir}/{points.hammer_point}:{points.hammer_dir}"
                f.write(f"{id_line1[:80]:<80}\n")
                
                id_line2 = "USMA v0.4.5 - Screen Reconstruction"
                f.write(f"{id_line2[:80]:<80}\n")
                
                f.write(f"{timestamp:<80}\n")
                
                id_line4 = f"Reconstructed from {points.run}, region \"{frf_name}\""
                f.write(f"{id_line4[:80]:<80}\n")
                
                dir_char = {1: 'X', 2: 'Y', 3: 'Z'}.get(resp_dof, 'Z')
                id_line5 = f"FRF\\\\{points.response_point}:+{dir_char}"
                f.write(f"{id_line5[:80]:<80}\n")
                
                func_type = 4
                func_id = 0
                version = 0
                load_case = 0
                resp_node_name = "C"
                ref_node_name = "C"
                
                dof_line = (
                    f"{func_type:5d}"
                    f"{func_id:10d}"
                    f"{version:5d}"
                    f"{load_case:10d}"
                    f"{resp_node_name:10s}"
                    f"{resp_node:10d}"
                    f"{resp_dof:5d}"
                    f"{ref_node_name:10s}"
                    f"{ref_node:10d}"
                    f"{ref_dof:5d}\n"
                )
                f.write(dof_line)
                
                data_type = 2
                spacing = 1
                z_value = 0.0
                
                f.write(
                    f"{data_type:10d}"
                    f"{num_points:10d}"
                    f"{spacing:10d}"
                    f"{start_freq:13.5E}"
                    f"{freq_step:13.5E}"
                    f"{z_value:13.5E}\n"
                )
                
                f.write(f"{18:10d}{0:5d}{0:5d}{0:5d}{'X-axis':20s}{'Hz':20s}\n")
                f.write(f"{12:10d}{0:5d}{0:5d}{0:5d}{'Y-axis':20s}{region.y_axis_unit:20s}\n")
                f.write(f"{13:10d}{0:5d}{0:5d}{0:5d}{'Z-axis':20s}{'NONE':20s}\n")
                f.write(f"{0:10d}{0:5d}{0:5d}{0:5d}{'NONE':20s}{'NONE':20s}\n")
                
                for val in frf_result.signal_physical:
                    real_part = val
                    imag_part = 0.0
                    f.write(f"  {real_part:13.6E}  {imag_part:13.6E}\n")
                
                f.write("    -1\n")
                
            logger.info(f"UNV file saved: {filename}")
            
        except Exception as e: 
            logger.error(f"Failed to save .unv file for {frf_name}: {e}")


# --- 4. VISUALIZATION & CONFIGURATION ---

# --- 4a. STARTUP DIALOG ---
class StartupDialog(tk.Toplevel):
    """
    Startup dialog for config selection or new calibration.

    Attributes:
        result: str or None - Selected config path, "NEW_CALIBRATION", or None if cancelled
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("USMA v0.5.0 - Startup")
        self.result = None

        # Don't use transient() with hidden parent - causes display issues on Windows
        # self.transient(parent)  # REMOVED

        # Scan for config files first to determine window size
        self.config_files = self._scan_configs()

        # Set window size based on content
        # Larger window when configs exist to show all buttons
        window_height = 320 if self.config_files else 250
        self.geometry(f"450x{window_height}")
        self.resizable(False, False)

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
        ttk.Label(title_frame, text="v0.5.0 - Calibration Release",
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
            self.config_var = tk.StringVar(value=self.config_files[0])
            config_combo = ttk.Combobox(combo_frame, textvariable=self.config_var,
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
            ttk.Label(info_frame, text="Let's create your first calibration!",
                      font=("Segoe UI", 9)).pack()

        # Create new calibration button
        new_cal_btn = ttk.Button(self, text="Create New Calibration",
                                 command=self._on_new_calibration, width=30)
        new_cal_btn.pack(pady=(5, 20))

        # Cancel button at bottom
        if self.config_files:
            ttk.Button(self, text="Cancel", command=self._on_cancel, width=10).pack(pady=(0, 10))

    def _on_load(self):
        """Set result to selected config path."""
        selected = self.config_var.get()
        if selected:
            self.result = os.path.join("configs", selected)
            self.destroy()

    def _on_new_calibration(self):
        """Set result to trigger new calibration."""
        self.result = "NEW_CALIBRATION"
        self.destroy()

    def _on_cancel(self):
        """User closed dialog without selection."""
        self.result = None
        self.destroy()


# --- 4b. HSV CALIBRATION WINDOW ---
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
        self.result_hsv_lower = None
        self.result_hsv_upper = None

        # Current values (copy to avoid modifying original until Apply)
        self.hsv_lower = list(current_hsv_lower)
        self.hsv_upper = list(current_hsv_upper)

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
        self.after(100, self._update_preview)

    def _setup_ui(self):
        # Top: Region selector (if multiple wave regions)
        if len(self.wave_regions) > 1:
            selector_frame = ttk.Frame(self)
            selector_frame.pack(fill=tk.X, padx=10, pady=5)
            ttk.Label(selector_frame, text="Preview Region:").pack(side=tk.LEFT)
            self.region_var = tk.StringVar(value=self.selected_region_name)
            region_combo = ttk.Combobox(selector_frame, textvariable=self.region_var,
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
class ROITypeDialog(tk.Toplevel):
    """
    Simple dialog to select ROI type after drawing a region.
    """

    ROI_TYPES = ['frf', 'status', 'overload', 'run', 'hammer', 'response']

    def __init__(self, parent, region_name: str):
        super().__init__(parent)
        self.title("Select Region Type")
        self.result = None
        self.region_name = region_name

        self.geometry("350x200")
        self.resizable(False, False)
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
            colors = {"frf": "#3498db", "status": "#2ecc71", "overload": "#e74c3c", 
                      "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
            for name, region_data in data.items():
                if not name.startswith('_') and region_data.get('enabled', True):
                    x, y, w, h = region_data['x'], region_data['y'], region_data['width'], region_data['height']
                    color = colors.get(region_data.get('roi_type', 'frf'), "#95a5a6")
                    canvas.create_rectangle(x-5, y-5, x+w+5, y+h+5, outline=color, width=2)
                    canvas.create_text(x-5, y-5, text=name, anchor="sw", font=("Arial", 10, "bold"), fill=color)
            canvas.create_text(self.winfo_screenwidth()-10, self.winfo_screenheight()-10, 
                              text=f"Config: {os.path.basename(self.config_path)}", anchor="se", fill="#333")
        except Exception as e:
            logger.error(f"Overlay Error: {e}")
            self.destroy()


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
            self.app_config = ScreenMonitor(preload_config_path).app_config
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
        self.right_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    
    def _build_right_panel_content(self, parent):
        capture_frame = ttk.LabelFrame(parent, text="Capture")
        capture_frame.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(capture_frame, text="Take Screenshot", command=self._take_screenshot).pack(pady=5, padx=5, fill=tk.X)

        # HSV Calibration button
        self.hsv_cal_btn = ttk.Button(capture_frame, text="Calibrate Color Filter",
                                       command=self._open_hsv_calibration)
        self.hsv_cal_btn.pack(fill=tk.X, pady=(0, 5), padx=5)
        self.hsv_cal_btn.config(state=tk.DISABLED)  # Disabled by default

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
            'resp_node': tk.IntVar(), 'resp_dof': tk.IntVar(), 'ref_node': tk.IntVar(), 'ref_dof': tk.IntVar()
        }
        
        f1 = ttk.Frame(editor_frame)
        f1.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f1, text="Name:").pack(side=tk.LEFT)
        ttk.Entry(f1, textvariable=self.editor_vars['name'], width=15).pack(side=tk.LEFT, padx=2)
        ttk.Label(f1, text="Type:").pack(side=tk.LEFT, padx=(10,0))
        ttk.Combobox(f1, textvariable=self.editor_vars['roi_type'], 
                     values=['frf', 'status', 'overload', 'run', 'hammer', 'response'], 
                     state='readonly', width=10).pack(side=tk.LEFT, padx=2)
        
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

        f_scale = ttk.LabelFrame(editor_frame, text="Physical Axis Scaling (wave)")
        f_scale.pack(fill=tk.X, pady=5, padx=5)
        
        g = ttk.Frame(f_scale)
        g.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(g, text="X-Min:").pack(side=tk.LEFT)
        ttk.Entry(g, textvariable=self.editor_vars['x_axis_min'], width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(g, text="X-Max:").pack(side=tk.LEFT)
        ttk.Entry(g, textvariable=self.editor_vars['x_axis_max'], width=8).pack(side=tk.LEFT, padx=2)
        
        g2 = ttk.Frame(f_scale)
        g2.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(g2, text="Y-Min:").pack(side=tk.LEFT)
        ttk.Entry(g2, textvariable=self.editor_vars['y_axis_min'], width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(g2, text="Y-Max:").pack(side=tk.LEFT)
        ttk.Entry(g2, textvariable=self.editor_vars['y_axis_max'], width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(g2, text="Unit:").pack(side=tk.LEFT, padx=(5,0))
        ttk.Entry(g2, textvariable=self.editor_vars['y_axis_unit'], width=6).pack(side=tk.LEFT, padx=2)
        
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

    def _update_hsv_button_state(self):
        """Enable HSV calibration button only if wave regions exist."""
        wave_regions = {name: r for name, r in self.app_config.regions.items()
                        if r.roi_type == 'frf'}
        state = tk.NORMAL if wave_regions else tk.DISABLED
        self.hsv_cal_btn.config(state=state)

    def _open_hsv_calibration(self):
        """Open HSV calibration window."""
        if self.screenshot is None:
            messagebox.showwarning("Warning", "Please take a screenshot first.", parent=self)
            return

        wave_regions = {name: r for name, r in self.app_config.regions.items()
                        if r.roi_type == 'frf'}

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
        if hsv_window.result_hsv_lower is not None:
            self.app_config.hsv_lower = hsv_window.result_hsv_lower
            self.app_config.hsv_upper = hsv_window.result_hsv_upper
            messagebox.showinfo("Success", "HSV color filter updated.", parent=self)

    def _apply_params(self):
        self.app_config.fft_cutoff_frequency = self.param_vars['fft_cutoff_frequency'].get()
        self.app_config.fft_energy_ratio_threshold = self.param_vars['fft_energy_ratio_threshold'].get()
        self.app_config.lowpass_cutoff = self.param_vars['lowpass_cutoff'].get()
        self.app_config.lowpass_filter_order = self.param_vars['lowpass_filter_order'].get()
        self.app_config.residual_threshold = self.param_vars['residual_threshold'].get()
        self.app_config.exceedance_ratio_threshold = self.param_vars['exceedance_ratio_threshold'].get()
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
        colors = {"frf": "#3498db", "status": "#2ecc71", "overload": "#e74c3c", 
                  "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
        if not hasattr(self, 'x_offset'):
            return 
        for name, r in self.app_config.regions.items():
            x1 = r.x * self.scale + self.x_offset
            y1 = r.y * self.scale + self.y_offset
            x2 = (r.x + r.width) * self.scale + self.x_offset
            y2 = (r.y + r.height) * self.scale + self.y_offset
            color = colors.get(r.roi_type, "white") if r.enabled else "gray"
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
                'exceedance_ratio_threshold': self.app_config.exceedance_ratio_threshold
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
            self.app_config = ScreenMonitor(path).app_config
            self._update_ui_from_data()
            if self.screenshot:
                self._redraw_regions_on_canvas()
            messagebox.showinfo("Success", f"Loaded {os.path.basename(path)}", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}", parent=self)


# --- 5. LIVE GRAPH VIEWER ---
class GraphViewerFrame(ttk.LabelFrame):
    """Embedded matplotlib graph viewer with hit navigation and console output."""
    
    PLOT_TYPES = ['Signal', 'FFT', 'Lowpass Comparison', 'Residual Analysis', 'Run Summary']
    MAX_HISTORY = 25  # Reduced from 50 to limit memory usage
    
    def __init__(self, parent):
        super().__init__(parent, text="Live Graph & Console")
        self.current_plot_index = 0
        self.current_hit_index = -1
        self.hit_history: List[LightweightHitData] = []
        self.run_history = {}
        self.app_config = None
        self.text_handler = None  # Will be set up after console is created
        
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
        
        self.hit_info_label = ttk.Label(nav_frame, text="No data", font=("Segoe UI", 9))
        self.hit_info_label.pack(side=tk.RIGHT, padx=10)
        
        # Horizontal PanedWindow for split view (Graph | Console)
        self.paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left side: Graph
        graph_frame = ttk.LabelFrame(self.paned, text="Live Graph")
        self.paned.add(graph_frame, weight=3)
        
        self.figure = Figure(figsize=(6, 3), dpi=100, facecolor='#1E1E1E')
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        self.ax.set_title('Waiting for data...', color='white')
        
        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Right side: Console
        console_frame = ttk.LabelFrame(self.paned, text="Console Output")
        self.paned.add(console_frame, weight=2)
        
        # Console text widget with scrollbar
        console_inner = ttk.Frame(console_frame)
        console_inner.pack(fill=tk.BOTH, expand=True)
        
        console_scroll = ttk.Scrollbar(console_inner, orient=tk.VERTICAL)
        console_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.console_text = tk.Text(console_inner, wrap=tk.WORD, bg='#1E1E1E', fg='#00FF00',
                                    font=('Consolas', 9), state='disabled',
                                    yscrollcommand=console_scroll.set)
        self.console_text.pack(fill=tk.BOTH, expand=True)
        console_scroll.config(command=self.console_text.yview)
        
        # Add text handler to logger
        self.text_handler = TextHandler(self.console_text)
        logger.addHandler(self.text_handler)
        
        # Clear console button
        clear_btn = ttk.Button(console_frame, text="Clear Console", command=self._clear_console)
        clear_btn.pack(pady=2)
        
        self._update_navigation_state()
    
    def _clear_console(self):
        """Clear the console text widget."""
        self.console_text.configure(state='normal')
        self.console_text.delete('1.0', tk.END)
        self.console_text.configure(state='disabled')
    
    def _prev_hit(self):
        if self.current_hit_index > 0:
            self.current_hit_index -= 1
            self._update_plot()
            self._update_navigation_state()
    
    def _next_hit(self):
        if self.current_hit_index < len(self.hit_history) - 1:
            self.current_hit_index += 1
            self._update_plot()
            self._update_navigation_state()
        
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
    
    def _update_navigation_state(self):
        total = len(self.hit_history)
        current = self.current_hit_index + 1 if total > 0 else 0
        
        self.hit_label.config(text=f"{current}/{total}")
        
        self.prev_hit_btn.config(state=tk.NORMAL if self.current_hit_index > 0 else tk.DISABLED)
        self.next_hit_btn.config(state=tk.NORMAL if self.current_hit_index < total - 1 else tk.DISABLED)
        
        if total > 0 and 0 <= self.current_hit_index < total:
            hit_data = self.hit_history[self.current_hit_index]
            self.hit_info_label.config(text=f"Hit: {hit_data.hit_key}")
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
        
        self.current_hit_index = len(self.hit_history) - 1
        
        self._update_plot()
        self._update_navigation_state()
        
    def _update_plot(self):
        if not self.hit_history or self.current_hit_index < 0:
            return
        
        if self.current_hit_index >= len(self.hit_history):
            self.current_hit_index = len(self.hit_history) - 1
            
        hit_data = self.hit_history[self.current_hit_index]
        
        self.ax.clear()
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        
        plot_type = self.plot_type_var.get()
        
        try:
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
            self.ax.text(0.5, 0.5, f'Error: {str(e)[:50]}', 
                        transform=self.ax.transAxes, ha='center', va='center', color='red')
            
        self.figure.tight_layout()
        self.canvas.draw_idle()  # Use draw_idle instead of draw for efficiency
        
    def _plot_signal(self, hit_data: LightweightHitData):
        if hit_data.signal_physical is None or len(hit_data.signal_physical) == 0:
            self.ax.text(0.5, 0.5, 'No signal data', transform=self.ax.transAxes, 
                        ha='center', va='center', color='white')
            return
            
        num_points = len(hit_data.signal_physical)
        freq_axis = np.linspace(hit_data.x_axis_min, hit_data.x_axis_max, num_points)
        
        self.ax.plot(freq_axis, hit_data.signal_physical, color='cyan', linewidth=1.5)
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Amplitude ({hit_data.y_axis_unit})', color='white')
        self.ax.set_title(f'Reconstructed Signal - {hit_data.hit_key}', color='white')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_fft(self, hit_data: LightweightHitData):
        self.ax.plot(hit_data.fft_freqs, hit_data.fft_mags, color='magenta', linewidth=1)
        
        if self.app_config:
            self.ax.axvline(x=self.app_config.fft_cutoff_frequency, color='yellow', 
                           linestyle='--', linewidth=1, label=f'Cutoff: {self.app_config.fft_cutoff_frequency:.2f}')
        
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
        self.ax.set_ylabel(f'Amplitude ({hit_data.y_axis_unit})', color='white')
        
        if self.app_config:
            title = f'Lowpass - {hit_data.hit_key} - Cutoff: {self.app_config.lowpass_cutoff:.3f}'
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
        
        if self.app_config:
            threshold = self.app_config.residual_threshold
        else:
            threshold = 0.005
        
        self.ax.plot(freq_axis, hit_data.residual_physical, 'c-', linewidth=1, label='Residual')
        self.ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
        self.ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.2, label=f'±{threshold}')
        self.ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.2)
        
        exceedances = np.abs(hit_data.residual_physical) > threshold
        if np.any(exceedances):
            self.ax.scatter(freq_axis[exceedances], hit_data.residual_physical[exceedances], 
                          c='red', s=12, zorder=5, alpha=0.7)
        
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Residual ({hit_data.y_axis_unit})', color='white')
        classification = "BAD" if hit_data.lowpass_is_bad_hit else "GOOD"
        self.ax.set_title(f'Residual - {hit_data.hit_key} - Exc: {hit_data.exceedance_count} ({hit_data.exceedance_ratio:.1%}) - {classification}', 
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
class MonitorControlGUI:
    def __init__(self, root, config_path=None):
        self.root = root
        self.root.title("USMA v0.5.0 - Calibration Release")
        self.root.geometry("1000x750")

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
        
        self.manual_points_vars = {
            'run': tk.StringVar(value='Run 1'),
            'hammer_point': tk.StringVar(value='P1'),
            'hammer_dir': tk.StringVar(value='-Z'),
            'response_point': tk.StringVar(value='P1'),
            'response_dir': tk.StringVar(value='-Z')
        }
        
        initial_freq = 1.0/self.monitor.app_config.screenshot_interval if self.monitor.app_config.screenshot_interval > 0 else 4.0
        self.sample_frequency = tk.DoubleVar(value=round(initial_freq, 2))
        
        self.overlay = None
        self._setup_main_gui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
    
    def _setup_main_gui(self):
        # Create main container with scrollbars
        main_container = ttk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # Canvas for scrolling
        self.main_canvas = tk.Canvas(main_container, highlightthickness=0)
        v_scrollbar = ttk.Scrollbar(main_container, orient=tk.VERTICAL, command=self.main_canvas.yview)
        h_scrollbar = ttk.Scrollbar(main_container, orient=tk.HORIZONTAL, command=self.main_canvas.xview)
        
        # Scrollable frame inside canvas
        self.scrollable_frame = ttk.Frame(self.main_canvas, padding=10)
        
        # Configure canvas scrolling
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))
        )
        
        self.canvas_window = self.main_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.main_canvas.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Bind mouse wheel scrolling
        self.main_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        # Layout scrollbars and canvas
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.main_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Make canvas resize with window
        main_container.bind("<Configure>", self._on_main_resize)
        
        # Now build content inside scrollable_frame
        frame = self.scrollable_frame
        
        config_frame = ttk.LabelFrame(frame, text="Configuration")
        config_frame.pack(fill=tk.X, pady=5)
        ttk.Entry(config_frame, textvariable=self.config_path, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        self.load_button = ttk.Button(config_frame, text="Load...", command=self._load_config)
        self.load_button.pack(side=tk.LEFT, padx=5)
        self.edit_button = ttk.Button(config_frame, text="Edit Config...", command=self._launch_config_tool)
        self.edit_button.pack(side=tk.LEFT, padx=5)
        
        feedback_frame = ttk.LabelFrame(frame, text="Live Analysis Feedback")
        feedback_frame.pack(fill=tk.X, pady=5)
        
        self.status_light = tk.Canvas(feedback_frame, width=40, height=40, bg="gray", highlightthickness=0)
        self.status_light.grid(row=0, column=0, rowspan=2, padx=10, pady=5)
        
        self.class_var = tk.StringVar(value="Overall: --")
        self.hf_ratio_var = tk.StringVar(value="FFT Ratio: --")
        self.exceedance_var = tk.StringVar(value="LP Exc: --")
        self.status_var = tk.StringVar(value="Status: --")
        self.overload_var = tk.StringVar(value="Overload: --")
        self.points_var = tk.StringVar(value="Points: --")
        
        ttk.Label(feedback_frame, textvariable=self.class_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.hf_ratio_var, font=("Segoe UI", 10)).grid(row=0, column=2, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.exceedance_var, font=("Segoe UI", 10)).grid(row=0, column=3, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.status_var, font=("Segoe UI", 10)).grid(row=1, column=1, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.overload_var, font=("Segoe UI", 10)).grid(row=1, column=2, sticky=tk.W, padx=5)
        ttk.Label(feedback_frame, textvariable=self.points_var, font=("Segoe UI", 10)).grid(row=1, column=3, columnspan=2, sticky=tk.W, padx=5)
        feedback_frame.columnconfigure(3, weight=1)
        
        # Graph viewer with reduced minimum height
        self.graph_viewer = GraphViewerFrame(frame)
        self.graph_viewer.pack(fill=tk.BOTH, expand=True, pady=5)
        self.graph_viewer.configure(height=200)  # Set minimum height

        bottom_panel = ttk.Frame(frame)
        bottom_panel.pack(fill=tk.X, pady=5)
        
        control_frame = ttk.LabelFrame(bottom_panel, text="Controls")
        control_frame.pack(fill=tk.Y, side=tk.LEFT, pady=5)
        
        self.start_stop_button = ttk.Button(control_frame, text="Start Monitoring", command=self._toggle_monitoring)
        self.start_stop_button.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)
        
        self.overlay_check = ttk.Checkbutton(control_frame, text="Show Overlay", variable=self.is_overlay_on, command=self._toggle_overlay)
        self.overlay_check.pack(padx=10, pady=(0,5))
        
        # Log on Events Only checkbox (new)
        self.events_only_check = ttk.Checkbutton(control_frame, text="Log on Events Only", 
                                                  variable=self.log_events_only,
                                                  command=self._toggle_events_only)
        self.events_only_check.pack(padx=10, pady=(0,5))
        
        params_frame = ttk.LabelFrame(control_frame, text="Parameters")
        params_frame.pack(padx=5, pady=5, fill=tk.Y)
        
        freq_frame = ttk.Frame(params_frame)
        ttk.Label(freq_frame, text="Sample Freq (Hz):").pack(side=tk.LEFT, padx=(5,2))
        self.freq_spinbox = ttk.Spinbox(freq_frame, from_=0.1, to=30.0, increment=0.1, textvariable=self.sample_frequency, width=6)
        self.freq_spinbox.pack(side=tk.LEFT, padx=(0,5))
        freq_frame.pack(pady=5)
        
        self.audio_check = ttk.Checkbutton(params_frame, text="Audio Feedback", variable=self.audio_feedback_on, command=self._toggle_audio_feedback)
        self.audio_check.pack(anchor=tk.W, padx=5, pady=(0, 5))
        if not SOUND_DEVICE_AVAILABLE:
            self.audio_check.config(state=tk.DISABLED)
            self.audio_feedback_on.set(False)
        
        right_panel = ttk.Frame(bottom_panel)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        self.manual_points_frame = ttk.LabelFrame(right_panel, text="Manual Points of Interest (POI) Entry")
        self.manual_points_frame.pack(fill=tk.X)
        pf = self.manual_points_frame
        
        # Store widget references for selective enabling/disabling
        self.manual_run_label = ttk.Label(pf, text="Run:")
        self.manual_run_label.grid(row=0, column=0, padx=5, pady=2)
        self.manual_run_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['run'], width=8)
        self.manual_run_entry.grid(row=0, column=1)
        
        self.manual_hammer_label = ttk.Label(pf, text="Hammer:")
        self.manual_hammer_label.grid(row=0, column=2, padx=5)
        self.manual_hammer_point_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_point'], width=6)
        self.manual_hammer_point_entry.grid(row=0, column=3)
        self.manual_hammer_dir_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_dir'], width=4)
        self.manual_hammer_dir_entry.grid(row=0, column=4)
        
        self.manual_response_label = ttk.Label(pf, text="Response:")
        self.manual_response_label.grid(row=1, column=2, padx=5)
        self.manual_response_point_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['response_point'], width=6)
        self.manual_response_point_entry.grid(row=1, column=3)
        self.manual_response_dir_entry = ttk.Entry(pf, textvariable=self.manual_points_vars['response_dir'], width=4)
        self.manual_response_dir_entry.grid(row=1, column=4)
        
        logging_controls_frame = ttk.LabelFrame(right_panel, text="Logging")
        logging_controls_frame.pack(fill=tk.X, pady=5)
        
        logging_main_frame = ttk.Frame(logging_controls_frame)
        logging_main_frame.pack(fill=tk.X, side=tk.LEFT, anchor=tk.N, padx=5)
        
        self.verbose_check = ttk.Checkbutton(logging_main_frame, text="Verbose Console Log", variable=self.verbose_logging_on, command=self._toggle_verbose_log_options_state)
        self.verbose_check.pack(anchor=tk.W, pady=2)
        
        # Verbose log options frame - horizontal 2x4 grid layout
        self.verbose_log_options_frame = ttk.Frame(logging_main_frame)
        self.verbose_log_options_frame.pack(fill=tk.X, pady=(2,5))
        
        # Row 1: Config, Mask, OCR, Classification
        vrow1 = ttk.Frame(self.verbose_log_options_frame)
        vrow1.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(vrow1, text="Config", variable=self.vlog_opt_config).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="Mask", variable=self.vlog_opt_mask).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="OCR", variable=self.vlog_opt_ocr).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow1, text="Classify", variable=self.vlog_opt_classification).pack(side=tk.LEFT, padx=2)
        
        # Row 2: FFT, Lowpass, File Saves
        vrow2 = ttk.Frame(self.verbose_log_options_frame)
        vrow2.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(vrow2, text="FFT", variable=self.vlog_opt_fft).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow2, text="Lowpass", variable=self.vlog_opt_lowpass).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(vrow2, text="FileSave", variable=self.vlog_opt_filesave).pack(side=tk.LEFT, padx=2)
        
        self.img_log_check = ttk.Checkbutton(logging_main_frame, text="Enable Image Logs", variable=self.image_logging_on, command=self._toggle_img_log_options_state)
        self.img_log_check.pack(anchor=tk.W, pady=2)
        
        # Image log options frame - horizontal 2x4 grid layout
        self.img_log_options_frame = ttk.Frame(logging_main_frame)
        self.img_log_options_frame.pack(fill=tk.X, pady=(5,0))
        
        # Row 1: ROI, Masks, OCR, Signal
        irow1 = ttk.Frame(self.img_log_options_frame)
        irow1.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(irow1, text="ROI", variable=self.log_opt_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="Masks", variable=self.log_opt_color_filter).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="OCR", variable=self.log_opt_ocr_images).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow1, text="Signal", variable=self.log_opt_signal_plot).pack(side=tk.LEFT, padx=2)
        
        # Row 2: FFT, Lowpass, Residual, Summary
        irow2 = ttk.Frame(self.img_log_options_frame)
        irow2.pack(fill=tk.X, anchor=tk.W)
        ttk.Checkbutton(irow2, text="FFT", variable=self.log_opt_fft_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Lowpass", variable=self.log_opt_lowpass_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Residual", variable=self.log_opt_residual_plot).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(irow2, text="Summary", variable=self.log_opt_summary_chart).pack(side=tk.LEFT, padx=2)
        
        data_logging_frame = ttk.Frame(logging_controls_frame)
        data_logging_frame.pack(fill=tk.X, side=tk.LEFT, padx=10, anchor=tk.N)
        ttk.Checkbutton(data_logging_frame, text="Log to .unv", variable=self.log_to_unv).pack(anchor=tk.W, pady=2)

        # Analysis Parameters Frame (Live Tuning)
        params_outer_frame = ttk.LabelFrame(right_panel, text="Analysis Parameters (Live)")
        params_outer_frame.pack(fill=tk.X, pady=(10, 0))

        # Initialize parameter variables from current config
        self.param_vars = {
            'fft_cutoff_frequency': tk.DoubleVar(value=self.monitor.app_config.fft_cutoff_frequency),
            'fft_energy_ratio_threshold': tk.DoubleVar(value=self.monitor.app_config.fft_energy_ratio_threshold),
            'lowpass_cutoff': tk.DoubleVar(value=self.monitor.app_config.lowpass_cutoff),
            'lowpass_filter_order': tk.IntVar(value=self.monitor.app_config.lowpass_filter_order),
            'residual_threshold': tk.DoubleVar(value=self.monitor.app_config.residual_threshold),
            'exceedance_ratio_threshold': tk.DoubleVar(value=self.monitor.app_config.exceedance_ratio_threshold)
        }

        # FFT Parameters row
        fft_frame = ttk.Frame(params_outer_frame)
        fft_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(fft_frame, text="FFT Cutoff:", width=12).pack(side=tk.LEFT)
        fft_cutoff_spin = ttk.Spinbox(fft_frame, from_=0.01, to=0.5, increment=0.01,
                                      textvariable=self.param_vars['fft_cutoff_frequency'],
                                      width=7, command=self._apply_params_live)
        fft_cutoff_spin.pack(side=tk.LEFT, padx=2)
        fft_cutoff_spin.bind('<Return>', lambda e: self._apply_params_live())

        ttk.Label(fft_frame, text="E.Ratio:", width=8).pack(side=tk.LEFT, padx=(10, 0))
        fft_ratio_spin = ttk.Spinbox(fft_frame, from_=0.001, to=0.5, increment=0.001,
                                     textvariable=self.param_vars['fft_energy_ratio_threshold'],
                                     width=7, command=self._apply_params_live)
        fft_ratio_spin.pack(side=tk.LEFT, padx=2)
        fft_ratio_spin.bind('<Return>', lambda e: self._apply_params_live())

        # Lowpass Parameters row
        lp_frame = ttk.Frame(params_outer_frame)
        lp_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(lp_frame, text="LP Cutoff:", width=12).pack(side=tk.LEFT)
        lp_cutoff_spin = ttk.Spinbox(lp_frame, from_=0.01, to=0.5, increment=0.01,
                                     textvariable=self.param_vars['lowpass_cutoff'],
                                     width=7, command=self._apply_params_live)
        lp_cutoff_spin.pack(side=tk.LEFT, padx=2)
        lp_cutoff_spin.bind('<Return>', lambda e: self._apply_params_live())

        ttk.Label(lp_frame, text="Order:", width=8).pack(side=tk.LEFT, padx=(10, 0))
        lp_order_spin = ttk.Spinbox(lp_frame, from_=1, to=10, increment=1,
                                    textvariable=self.param_vars['lowpass_filter_order'],
                                    width=4, command=self._apply_params_live)
        lp_order_spin.pack(side=tk.LEFT, padx=2)
        lp_order_spin.bind('<Return>', lambda e: self._apply_params_live())

        # Residual Parameters row
        res_frame = ttk.Frame(params_outer_frame)
        res_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(res_frame, text="Res.Thr:", width=12).pack(side=tk.LEFT)
        res_thr_spin = ttk.Spinbox(res_frame, from_=0.0001, to=0.1, increment=0.0005,
                                   textvariable=self.param_vars['residual_threshold'],
                                   width=8, format="%.4f", command=self._apply_params_live)
        res_thr_spin.pack(side=tk.LEFT, padx=2)
        res_thr_spin.bind('<Return>', lambda e: self._apply_params_live())

        ttk.Label(res_frame, text="Exc.Ratio:", width=8).pack(side=tk.LEFT, padx=(10, 0))
        exc_ratio_spin = ttk.Spinbox(res_frame, from_=0.01, to=0.5, increment=0.01,
                                     textvariable=self.param_vars['exceedance_ratio_threshold'],
                                     width=7, command=self._apply_params_live)
        exc_ratio_spin.pack(side=tk.LEFT, padx=2)
        exc_ratio_spin.bind('<Return>', lambda e: self._apply_params_live())

        self.status_label = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        self._toggle_img_log_options_state()
        self._toggle_verbose_log_options_state()
        self._update_manual_points_state()
    
    def _on_mousewheel(self, event):
        """Handle mouse wheel scrolling."""
        self.main_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    
    def _on_main_resize(self, event):
        """Resize canvas window when main window resizes."""
        # Update the width of the canvas window to match canvas width
        canvas_width = event.width
        self.main_canvas.itemconfig(self.canvas_window, width=canvas_width)
    
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
            self.monitor.app_config.fft_cutoff_frequency = self.param_vars['fft_cutoff_frequency'].get()
            self.monitor.app_config.fft_energy_ratio_threshold = self.param_vars['fft_energy_ratio_threshold'].get()
            self.monitor.app_config.lowpass_cutoff = self.param_vars['lowpass_cutoff'].get()
            self.monitor.app_config.lowpass_filter_order = self.param_vars['lowpass_filter_order'].get()
            self.monitor.app_config.residual_threshold = self.param_vars['residual_threshold'].get()
            self.monitor.app_config.exceedance_ratio_threshold = self.param_vars['exceedance_ratio_threshold'].get()

            # Also update the graph viewer's reference
            self.graph_viewer.app_config = self.monitor.app_config

            if self.verbose_logging_on.get():
                logger.info(f"Parameters updated live: FFT_cut={self.monitor.app_config.fft_cutoff_frequency:.3f}, "
                           f"FFT_thr={self.monitor.app_config.fft_energy_ratio_threshold:.4f}, "
                           f"LP_cut={self.monitor.app_config.lowpass_cutoff:.3f}")
        except tk.TclError as e:
            logger.warning(f"Invalid parameter value: {e}")

    def update_feedback_panel(self, result: FrameAnalysisResult):
        self.root.after(0, self._update_feedback_ui, result)
        
    def _update_feedback_ui(self, result: FrameAnalysisResult):
        if result.overall_is_hf is not None:
            fft_bad = result.overall_is_hf
            lp_bad = result.overall_lowpass_bad if result.overall_lowpass_bad is not None else False
            
            if fft_bad and lp_bad:
                self.class_var.set("Overall: BAD HIT (Both)")
                self.status_light.config(bg="red")
            elif fft_bad or lp_bad:
                method = "FFT" if fft_bad else "LP"
                self.class_var.set(f"Overall: SUSPECT ({method})")
                self.status_light.config(bg="orange")
            else:
                self.class_var.set("Overall: GOOD HIT")
                self.status_light.config(bg="green")
                
        if result.avg_energy_ratio is not None:
            self.hf_ratio_var.set(f"FFT Ratio: {result.avg_energy_ratio:.3e}")
            
        if result.avg_exceedance_count is not None:
            self.exceedance_var.set(f"LP Exc: {result.avg_exceedance_count:.0f} ({result.avg_exceedance_ratio:.1%})")
            
        self.status_var.set(f"Status: {result.status_text}")
        self.overload_var.set(f"Overload: {result.overload_text}")
        p = result.points_info
        self.points_var.set(f"Points: {p.run} | H: {p.hammer_point}{p.hammer_dir} | R: {p.response_point}{p.response_dir}")
        
    def _on_plot_data(self, lightweight_data: LightweightHitData, run_history: Dict):
        """Thread-safe callback to update graph viewer."""
        self.root.after(0, self._update_graph_viewer, lightweight_data, run_history)
            
    def _update_graph_viewer(self, lightweight_data: LightweightHitData, run_history: Dict):
        self.graph_viewer.update_data(lightweight_data, run_history, self.monitor.app_config)
        
    def _reset_feedback_ui(self):
        self.class_var.set("Overall: --")
        self.status_light.config(bg="gray")
        self.hf_ratio_var.set("FFT Ratio: --")
        self.exceedance_var.set("LP Exc: --")
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

    def _sync_param_ui_from_config(self):
        """Sync parameter UI variables from current monitor config."""
        cfg = self.monitor.app_config
        self.param_vars['fft_cutoff_frequency'].set(cfg.fft_cutoff_frequency)
        self.param_vars['fft_energy_ratio_threshold'].set(cfg.fft_energy_ratio_threshold)
        self.param_vars['lowpass_cutoff'].set(cfg.lowpass_cutoff)
        self.param_vars['lowpass_filter_order'].set(cfg.lowpass_filter_order)
        self.param_vars['residual_threshold'].set(cfg.residual_threshold)
        self.param_vars['exceedance_ratio_threshold'].set(cfg.exceedance_ratio_threshold)
    
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

        self.root.deiconify()
        
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
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.destroy()
        self.root.destroy()


# --- 7. APPLICATION ENTRY POINT ---
if __name__ == "__main__":
    import sys

    # Create hidden root for startup dialog
    temp_root = tk.Tk()
    temp_root.withdraw()

    # Ensure configs directory exists
    if not os.path.exists("configs"):
        os.makedirs("configs")

    # Show startup dialog
    startup = StartupDialog(temp_root)
    startup_result = startup.result  # Store result before destroying

    if startup_result is None:
        # User cancelled - exit application
        temp_root.destroy()
        sys.exit(0)
    elif startup_result == "NEW_CALIBRATION":
        # Destroy temp root, create new root for config tool
        temp_root.destroy()

        main_root = tk.Tk()
        main_root.withdraw()  # Hide main GUI initially

        # Open config tool for new calibration
        config_tool = ConfigToolWindow(main_root, main_root, is_new_calibration=True)
        config_tool.wait_window()

        # After config tool closes, check if a config was saved
        saved_path = config_tool.saved_config_path  # Store before any potential issues

        if saved_path and os.path.exists(saved_path):
            # Load the saved config into main GUI and show
            main_root.deiconify()
            app = MonitorControlGUI(main_root, config_path=saved_path)
            main_root.mainloop()
        else:
            # No config was saved - exit
            main_root.destroy()
            sys.exit(0)
    else:
        # Config file selected
        config_path = startup_result  # Store the path
        temp_root.destroy()

        main_root = tk.Tk()
        app = MonitorControlGUI(main_root, config_path=config_path)
        main_root.mainloop()