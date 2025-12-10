#!/usr/bin/env python3
"""
USMA (Unified Screen Monitoring Application) - v.0.4.4 (Pre-Release)

A single, GUI-driven application that combines a professional-grade region 
configuration tool, real-time screen monitoring, visual overlay, and clear 
image logging.

v.0.4.4 (This release):
- **Lowpass Residual Analysis (AS's Method)**: Added alternative 
  classification using time-domain residual analysis:
  * Butterworth lowpass filter to separate LF/HF content
  * Exceedance counting for quantitative HF measurement
  * All analysis performed in PHYSICAL UNITS (g/N) to enable direct
    comparison with TestLab signals for reconstruction validation
  * Complementary to existing FFT energy ratio method
- **Dual Classification System**: 
  * Both methods flagging = BAD HIT (Red)
  * One method flagging = SUSPECT (Orange)  
  * Neither flagging = GOOD HIT (Green)
- **Live Graph Viewer**: New central panel with matplotlib canvas:
  * Signal plot, FFT spectrum, Lowpass comparison, Residual analysis
  * Hit navigation (◀◀/▶▶) to browse through recorded hits
  * Plot type selector to switch visualization modes
  * Auto-displays most recent hit, user can navigate to previous
  * Run Summary bar chart (updated after each hit)
- **Improved OCR Robustness**: Enhanced preprocessing with multiple
  attempts, morphological operations, and better regex patterns
- **Organized Image Logging**: Separate folders for each image type
  (ROIs, ColorMasks, Signals, FFT, Lowpass, Residual, Summary, OCRs)
- **Enhanced Verbose Logging**: Includes OCR values and classification
  reasoning (BAD/SUSPECT/GOOD with method details)
- **Scrollable Config Tool**: Right panel now scrollable for smaller screens
- **Extended Logging Options**: Separate checkbox for OCR diagnostic images
"""

import cv2
import numpy as np
import pyautogui
import time
import threading
import json
import os
import logging
import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Tuple
from PIL import Image, ImageTk
from scipy.fft import rfft, rfftfreq
from scipy.signal import butter, filtfilt
import scipy.io
import matplotlib
matplotlib.use('TkAgg')
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
    script_dir = os.path.dirname(os.path.realpath(__file__))
    tesseract_path = os.path.join(script_dir, 'external', 'tesseract', 'tesseract.exe')
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    if not os.path.exists(tesseract_path):
        raise FileNotFoundError(f"Portable Tesseract not found at: {tesseract_path}")
    OCR_AVAILABLE = True
except (ImportError, FileNotFoundError) as e:
    OCR_AVAILABLE = False
    print(f"Warning: Portable OCR features disabled. Error: {e}")


# --- 1. SETUP: DIRECTORY AND LOGGING CONFIGURATION ---
def setup_environment():
    """Create necessary directories for logs, configs, and organized image logs."""
    base_folders = ['logs', 'configs', 'signal_logs']
    # Organized image log subfolders
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


# --- 2. DATA CLASSES: CORE DATA STRUCTURES ---
@dataclass
class ImageLogOptions:
    include_screenshot: bool = False      # Wave ROI screenshots
    include_color_filter: bool = False    # Color masks
    include_signal_plot: bool = False     # Reconstructed signal
    include_fft_plot: bool = False        # FFT spectrum
    include_lowpass_plot: bool = False    # Lowpass comparison
    include_residual_plot: bool = False   # Residual analysis
    include_summary_chart: bool = False   # Run summary bar chart
    include_ocr_images: bool = False      # OCR diagnostic images (NEW)

@dataclass
class DataLogOptions:
    log_mat: bool = False
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
class WaveAnalysisResult:
    """Extended to include lowpass residual analysis results in physical units."""
    # Existing FFT fields
    is_high_frequency: bool
    energy_ratio: float
    high_freq_energy: float
    signal_vector: np.ndarray
    fft_freqs: np.ndarray
    fft_mags: np.ndarray
    roi_image: np.ndarray
    color_mask: np.ndarray
    total_energy: float = 0.0
    
    # Physical-unit signal and lowpass analysis
    signal_physical: Optional[np.ndarray] = None
    filtered_physical: Optional[np.ndarray] = None
    residual_physical: Optional[np.ndarray] = None
    exceedance_count: int = 0
    exceedance_ratio: float = 0.0
    lowpass_is_bad_hit: bool = False
    y_axis_unit: str = "g/N"

@dataclass
class FrameAnalysisResult:
    """Holds all analysis results from a single captured frame."""
    wave_results: Dict[str, WaveAnalysisResult] = field(default_factory=dict)
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
    # OCR diagnostic images for logging
    ocr_images: Dict[str, np.ndarray] = field(default_factory=dict)

@dataclass
class AppConfig:
    regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    hsv_lower: List[int] = field(default_factory=lambda: [0, 0, 0])
    hsv_upper: List[int] = field(default_factory=lambda: [179, 255, 240])
    screenshot_interval: float = 0.25
    fft_cutoff_frequency: float = 0.09
    fft_energy_ratio_threshold: float = 0.013
    # Lowpass analysis parameters
    lowpass_cutoff: float = 0.05
    lowpass_filter_order: int = 4
    residual_threshold: float = 0.005
    exceedance_ratio_threshold: float = 0.05


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
        
        # Enhanced OCR preprocessing tools
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self.sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        
        # Run history for summary chart
        self.run_history: Dict[str, Dict] = {}
        self.current_run: str = "Run 1"

    def start(self, verbose_logging=True, image_logging=True, image_log_options=None, 
              data_log_options=None, manual_points=None):
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
        logger.info("Screen monitoring thread started for USMA v.0.4.4")
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
            with open(path, 'r') as f:
                config_data = json.load(f)
            config = AppConfig()
            metadata = config_data.get('_metadata', {})
            config.hsv_lower = metadata.get('hsv_lower', config.hsv_lower)
            config.hsv_upper = metadata.get('hsv_upper', config.hsv_upper)
            config.screenshot_interval = metadata.get('screenshot_interval', config.screenshot_interval)
            config.fft_cutoff_frequency = metadata.get('fft_cutoff_frequency', config.fft_cutoff_frequency)
            config.fft_energy_ratio_threshold = metadata.get('fft_energy_ratio_threshold', config.fft_energy_ratio_threshold)
            config.lowpass_cutoff = metadata.get('lowpass_cutoff', config.lowpass_cutoff)
            config.lowpass_filter_order = metadata.get('lowpass_filter_order', config.lowpass_filter_order)
            config.residual_threshold = metadata.get('residual_threshold', config.residual_threshold)
            config.exceedance_ratio_threshold = metadata.get('exceedance_ratio_threshold', config.exceedance_ratio_threshold)
            
            region_fields = MonitoringRegion.__annotations__.keys()
            for name, data in config_data.items():
                if not name.startswith('_') and isinstance(data, dict):
                    filtered_data = {k: v for k, v in data.items() if k in region_fields}
                    if 'name' in filtered_data:
                        config.regions[name] = MonitoringRegion(**filtered_data)
            return config
        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}")
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
                screenshot = pyautogui.screenshot()
                image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
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
                
                self._handle_logging(frame_result, all_rois)
                
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
            
            if region.roi_type == 'wave':
                analysis_result = self._analyze_wave_pattern(roi, region)
                if analysis_result:
                    frame_result.wave_results[name] = analysis_result
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

        if frame_result.wave_results:
            # FFT classification aggregation
            classifications = [res.is_high_frequency for res in frame_result.wave_results.values()]
            frame_result.overall_is_hf = sum(classifications) > len(classifications) / 2 if classifications else False
            frame_result.avg_energy_ratio = np.mean([res.energy_ratio for res in frame_result.wave_results.values()])
            frame_result.avg_high_freq_energy = np.mean([res.high_freq_energy for res in frame_result.wave_results.values()])
            # Lowpass aggregation
            frame_result.avg_exceedance_count = np.mean([res.exceedance_count for res in frame_result.wave_results.values()])
            frame_result.avg_exceedance_ratio = np.mean([res.exceedance_ratio for res in frame_result.wave_results.values()])
            lp_classifications = [res.lowpass_is_bad_hit for res in frame_result.wave_results.values()]
            frame_result.overall_lowpass_bad = sum(lp_classifications) > len(lp_classifications) / 2 if lp_classifications else False
        
        return frame_result, all_rois

    def _handle_logging(self, frame_result: FrameAnalysisResult, all_rois: Dict[str, np.ndarray]):
        if not frame_result.wave_results:
            return

        has_changed = (frame_result.avg_energy_ratio is not None and 
                       (self.last_logged_ratio is None or 
                        not np.isclose(frame_result.avg_energy_ratio, self.last_logged_ratio, atol=1e-9) or
                        not np.isclose(frame_result.avg_high_freq_energy, self.last_logged_energy, atol=1e-9)))

        if has_changed:
            points = frame_result.points_info
            
            # Determine classification for verbose logging
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
                # Log OCR values
                logger.info(f"--- WAVE EVENT DETECTED ---")
                logger.info(f"  OCR Status: '{frame_result.status_text}'")
                logger.info(f"  OCR Overload: '{frame_result.overload_text}'")
                logger.info(f"  OCR Run: '{points.run}'")
                logger.info(f"  OCR Hammer: '{points.hammer_point}' Dir: '{points.hammer_dir}'")
                logger.info(f"  OCR Response: '{points.response_point}' Dir: '{points.response_dir}'")
                # Log analysis values
                logger.info(f"  FFT Energy Ratio: {frame_result.avg_energy_ratio:.3e} (threshold: {self.app_config.fft_energy_ratio_threshold:.3e}) -> {'BAD' if fft_bad else 'OK'}")
                logger.info(f"  LP Exceedances: {frame_result.avg_exceedance_count:.0f} ({frame_result.avg_exceedance_ratio:.1%}) (threshold: {self.app_config.exceedance_ratio_threshold:.1%}) -> {'BAD' if lp_bad else 'OK'}")
                logger.info(f"  CLASSIFICATION: {classification}")
                logger.info(f"----------------------------")
            
            self.last_logged_ratio = frame_result.avg_energy_ratio
            self.last_logged_energy = frame_result.avg_high_freq_energy
            
            counter_key = f"{points.hammer_point}{points.response_point}"
            current_hit = self.hit_counters.get(counter_key, 0) + 1
            self.hit_counters[counter_key] = current_hit
            
            # Store in run history for summary chart
            hit_key = f"{counter_key}_{current_hit}"
            self.current_run = points.run
            if self.current_run not in self.run_history:
                self.run_history[self.current_run] = {}
            
            for wave_name, wave_result in frame_result.wave_results.items():
                base_filename = f"{wave_name}_{counter_key}_{current_hit}"
                
                # Store hit data for summary
                self.run_history[self.current_run][hit_key] = {
                    'exceedance_count': wave_result.exceedance_count,
                    'exceedance_ratio': wave_result.exceedance_ratio,
                    'energy_ratio': wave_result.energy_ratio,
                    'is_hf': wave_result.is_high_frequency,
                    'lowpass_bad': wave_result.lowpass_is_bad_hit
                }
                
                if self.image_logging_enabled: 
                    self._create_visual_logs(wave_result, frame_result, wave_name, base_filename, all_rois)
                if self.data_log_options.log_mat: 
                    self._save_mat_log(wave_result, frame_result, wave_name, base_filename)
                if self.data_log_options.log_unv: 
                    self._save_unv_log(wave_result, frame_result, wave_name, base_filename)
                
                # Notify plot callback
                if self.plot_callback:
                    self.plot_callback(wave_result, frame_result, wave_name, hit_key)

    # =========================================================================
    # ENHANCED OCR METHODS (Improved Robustness)
    # =========================================================================
    
    def _preprocess_for_ocr(self, roi: np.ndarray, scale_factor: int = 4, 
                            use_clahe: bool = True, use_sharpen: bool = True,
                            use_morphology: bool = False, invert: bool = True) -> np.ndarray:
        """
        Enhanced preprocessing pipeline for OCR with multiple options.
        Returns the preprocessed binary image.
        """
        if roi.size == 0:
            return np.array([])
        
        # Scale up for better OCR
        width = int(roi.shape[1] * scale_factor)
        height = int(roi.shape[0] * scale_factor)
        resized = cv2.resize(roi, (width, height), interpolation=cv2.INTER_CUBIC)
        
        # Convert to grayscale
        if len(resized.shape) == 3:
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        else:
            gray = resized
        
        # CLAHE for contrast enhancement
        if use_clahe:
            gray = self.clahe.apply(gray)
        
        # Sharpening
        if use_sharpen:
            gray = cv2.filter2D(gray, -1, self.sharpen_kernel)
        
        # Thresholding
        if invert:
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        else:
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        
        # Morphological operations to clean up
        if use_morphology:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        return thresh

    def _run_ocr_with_config(self, image: np.ndarray, psm: int = 7, 
                              whitelist: Optional[str] = None, 
                              load_dawgs: bool = True) -> str:
        """Run OCR with specific configuration."""
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
        """
        Robust status analysis with multiple preprocessing attempts.
        Returns (status_text, diagnostic_images_dict)
        """
        diag_imgs = {}
        
        # Try multiple preprocessing configurations
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
                
            # Try PSM 7 (single line) and PSM 6 (block)
            for psm in [7, 6]:
                text = self._run_ocr_with_config(preprocessed, psm=psm)
                text_lower = text.lower()
                
                # Check for status keywords
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
        
        # Fallback to color analysis
        mean_color = np.mean(roi, axis=(0, 1))
        if best_img is not None:
            diag_imgs['status_preprocessed'] = best_img
        
        # Green = Measuring, Red/Orange = Waiting, Blue = Ready (typical TestLab colors)
        if mean_color[1] > 120 and mean_color[1] > mean_color[2]:  # Green dominant
            return "Measuring... (color)", diag_imgs
        if mean_color[2] > 120 and mean_color[2] > mean_color[1]:  # Red/Orange dominant
            return "Waiting for Trigger... (color)", diag_imgs
        if mean_color[0] > 120:  # Blue dominant
            return "Ready (color)", diag_imgs
        
        return f"Unknown (OCR: '{best_text[:20]}')" if best_text else "Unknown", diag_imgs

    def _analyze_overload_robust(self, roi: np.ndarray) -> Tuple[str, Dict[str, np.ndarray]]:
        """Robust overload analysis."""
        diag_imgs = {}
        
        # Check color first - overload is typically red
        mean_color = np.mean(roi, axis=(0, 1))
        is_red = mean_color[2] > 150 and mean_color[1] < 100 and mean_color[0] < 100
        
        if not is_red:
            return "No Overload", diag_imgs
        
        # Try OCR if red detected
        preprocessed = self._preprocess_for_ocr(roi, scale_factor=4)
        diag_imgs['overload_preprocessed'] = preprocessed
        
        whitelist = '0123456789ChannelinOverload '
        text = self._run_ocr_with_config(preprocessed, psm=7, whitelist=whitelist)
        
        # Extract channel count
        match = re.search(r'(\d+)\s*Channel', text, re.IGNORECASE)
        if match:
            return f"{match.group(1)} Channel in Overload", diag_imgs
        
        return "Channel in Overload", diag_imgs

    def _analyze_run_robust(self, roi: np.ndarray) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        """
        Robust run number analysis with multiple attempts.
        Returns (run_string, diagnostic_images_dict)
        """
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
            
            for psm in [7, 8, 6]:  # Single line, single word, block
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                # Try to find "Run" followed by number
                run_match = re.search(r'[Rr][Uu][Nn]\s*(\d+)', text)
                if run_match:
                    run_str = f"Run {run_match.group(1)}"
                    if self.verbose_logging_enabled:
                        logger.debug(f"Run OCR success: '{run_str}' from '{text}'")
                    return run_str, diag_imgs
                
                # Try just finding a number if "Run" label is nearby but not in ROI
                num_match = re.search(r'^(\d+)$', text.strip())
                if num_match:
                    run_str = f"Run {num_match.group(1)}"
                    return run_str, diag_imgs
        
        if self.verbose_logging_enabled:
            logger.debug(f"Run OCR failed, no match found")
        return None, diag_imgs

    def _analyze_point_and_dir_robust(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        """
        Robust point and direction analysis with split ROI approach.
        Returns (point_string, direction_string, diagnostic_images_dict)
        """
        point, direction = None, None
        diag_imgs = {}
        
        try:
            # Determine split ratio based on ROI width
            width = roi.shape[1]
            height = roi.shape[0]
            
            # Try different split ratios
            split_ratios = [0.65, 0.70, 0.75, 0.60]
            
            for split_ratio in split_ratios:
                split_x = int(width * split_ratio)
                point_roi = roi[:, :split_x]
                dir_roi = roi[:, split_x:]
                
                # Analyze point (e.g., "P: 1" or "P1" or "A: 3")
                point_result = self._analyze_point_only(point_roi, f"{name}_point")
                if point_result[0]:
                    point = point_result[0]
                    diag_imgs.update(point_result[1])
                    
                    # Analyze direction
                    dir_result = self._analyze_direction_only(dir_roi, f"{name}_dir")
                    if dir_result[0]:
                        direction = dir_result[0]
                    diag_imgs.update(dir_result[1])
                    break
            
            # If split approach failed, try full ROI
            if not point:
                full_result = self._analyze_full_point_roi(roi, name)
                point = full_result[0]
                direction = full_result[1]
                diag_imgs.update(full_result[2])
                
        except Exception as e:
            logger.error(f"Failed to analyze point/dir for {name}: {e}")
        
        return point, direction, diag_imgs

    def _analyze_point_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        """Analyze just the point part of the ROI."""
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
            
            for psm in [7, 8, 13]:  # Single line, single word, raw line
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                # Pattern: Letter followed by colon and number (e.g., "P: 1", "A: 3")
                match = re.search(r'([A-Za-z])\s*[:\s]\s*(\d+)', text)
                if match:
                    point = f"{match.group(1).upper()}{match.group(2)}"
                    if self.verbose_logging_enabled:
                        logger.debug(f"Point OCR success: '{point}' from '{text}'")
                    return point, diag_imgs
                
                # Pattern: Just letter and number (e.g., "P1")
                match = re.search(r'([A-Za-z])(\d+)', text)
                if match:
                    point = f"{match.group(1).upper()}{match.group(2)}"
                    return point, diag_imgs
                
                # Pattern: Just a number (point letter might be label outside ROI)
                match = re.search(r'^[\s:]*(\d+)\s*$', text)
                if match:
                    point = f"P{match.group(1)}"  # Assume P if no letter
                    return point, diag_imgs
        
        return None, diag_imgs

    def _analyze_direction_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        """Analyze just the direction part of the ROI."""
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
            
            for psm in [10, 8, 13]:  # Single char, single word, raw line
                text = self._run_ocr_with_config(preprocessed, psm=psm, whitelist=whitelist, load_dawgs=False)
                
                # Pattern: +/- followed by X/Y/Z
                match = re.search(r'([+\-])?\s*([XYZxyz])', text)
                if match:
                    sign = match.group(1) if match.group(1) else '+'
                    axis = match.group(2).upper()
                    direction = f"{sign}{axis}"
                    if self.verbose_logging_enabled:
                        logger.debug(f"Direction OCR success: '{direction}' from '{text}'")
                    return direction, diag_imgs
        
        return None, diag_imgs

    def _analyze_full_point_roi(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        """Analyze the full ROI for both point and direction when split fails."""
        diag_imgs = {}
        point, direction = None, None
        
        preprocessed = self._preprocess_for_ocr(roi, scale_factor=5, use_clahe=True, use_sharpen=True)
        if preprocessed.size == 0:
            return None, None, diag_imgs
        
        diag_imgs[f'{name}_full'] = preprocessed
        
        whitelist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789+-XYZxyz '
        text = self._run_ocr_with_config(preprocessed, psm=7, whitelist=whitelist, load_dawgs=False)
        
        # Try to parse full string like "P: 1 +Z" or "A:3-X"
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
        """Apply zero-phase Butterworth lowpass filter."""
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
        """Count samples where |residual| exceeds threshold."""
        exceedance_mask = np.abs(residual) > threshold
        count = np.sum(exceedance_mask)
        ratio = count / len(residual) if len(residual) > 0 else 0.0
        return int(count), ratio

    def _analyze_wave_pattern(self, roi: np.ndarray, region: MonitoringRegion) -> Optional[WaveAnalysisResult]:
        """Analyze wave pattern with both FFT and Lowpass methods."""
        if roi.size == 0:
            return None
            
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(self.app_config.hsv_lower), np.array(self.app_config.hsv_upper))
        
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
        
        # FFT Analysis
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
        
        # Convert to physical units
        y_range = region.y_axis_max - region.y_axis_min
        signal_physical = region.y_axis_min + (signal_pixels / height) * y_range
        
        signal_mean = np.mean(signal_physical)
        signal_detrended = signal_physical - signal_mean
        
        # Lowpass Analysis
        filtered_detrended = self._apply_lowpass_filter(signal_detrended)
        residual_physical = signal_detrended - filtered_detrended
        filtered_physical = filtered_detrended + signal_mean
        
        exceedance_count, exceedance_ratio = self._calculate_exceedances(
            residual_physical, self.app_config.residual_threshold
        )
        lowpass_is_bad = exceedance_ratio > self.app_config.exceedance_ratio_threshold
        
        return WaveAnalysisResult(
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
    # VISUAL LOGGING (Organized into folders)
    # =========================================================================

    def _create_visual_logs(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, 
                           wave_name: str, base_filename: str, all_rois: Dict[str, np.ndarray]):
        try:
            region = frame_result.active_regions[wave_name]
            
            title_info = (f"{frame_result.points_info.run} | H: {frame_result.points_info.hammer_point}"
                         f"{frame_result.points_info.hammer_dir} R: {frame_result.points_info.response_point}"
                         f"{frame_result.points_info.response_dir} | Overload: {frame_result.overload_text}")

            # Wave ROI Screenshots -> image_logs/ROIs/
            if self.image_log_options.include_screenshot:
                for r_name, r_img in all_rois.items():
                    try:
                        r_type = self.app_config.regions[r_name].roi_type
                        if r_type == 'wave':
                            cv2.imwrite(f"image_logs/ROIs/{base_filename}_{r_name}.jpg", r_img)
                    except KeyError:
                        pass
                    except Exception as e:
                        logger.error(f"Failed to save ROI image for '{r_name}': {e}")

            # Color Masks -> image_logs/ColorMasks/
            if self.image_log_options.include_color_filter: 
                cv2.imwrite(f"image_logs/ColorMasks/{base_filename}_mask.jpg", wave_result.color_mask)
            
            # Signal Plots -> image_logs/Signals/
            if self.image_log_options.include_signal_plot:
                self._create_signal_plot(wave_result, region, title_info, 
                                        f"image_logs/Signals/{base_filename}_signal.png")
            
            # FFT Plots -> image_logs/FFT/
            if self.image_log_options.include_fft_plot:
                self._create_fft_plot(wave_result, region, title_info, 
                                     f"image_logs/FFT/{base_filename}_fft.png")
            
            # Lowpass Plots -> image_logs/Lowpass/
            if self.image_log_options.include_lowpass_plot:
                self._create_lowpass_comparison_plot(wave_result, region, title_info, 
                                                     f"image_logs/Lowpass/{base_filename}_lowpass.png")
            
            # Residual Plots -> image_logs/Residual/
            if self.image_log_options.include_residual_plot:
                self._create_residual_plot(wave_result, region, title_info, 
                                          f"image_logs/Residual/{base_filename}_residual.png")
            
            # Summary Charts -> image_logs/Summary/
            if self.image_log_options.include_summary_chart and self.current_run in self.run_history:
                self._create_run_summary_chart(self.run_history[self.current_run], 
                                               f"image_logs/Summary/{base_filename}_summary.png")
            
            # OCR Diagnostic Images -> image_logs/OCRs/
            if self.image_log_options.include_ocr_images and frame_result.ocr_images:
                for ocr_name, ocr_img in frame_result.ocr_images.items():
                    if ocr_img is not None and ocr_img.size > 0:
                        cv2.imwrite(f"image_logs/OCRs/{base_filename}_{ocr_name}.jpg", ocr_img)
                
        except Exception as e:
            logger.error(f"Failed to create visual logs for {wave_name}: {e}")

    def _create_signal_plot(self, wave_result: WaveAnalysisResult, region: MonitoringRegion, 
                           title_info: str, filename: str):
        fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
        fig.patch.set_facecolor('#1E1E1E')
        
        num_points = len(wave_result.signal_physical)
        freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        
        ax.plot(freq_axis, wave_result.signal_physical, color='cyan', linewidth=1.5)
        ax.set_xlabel('Frequency (Hz)', color='white')
        ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
        ax.set_title(f'Reconstructed Signal\n{title_info}', color='white', fontsize=10)
        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white')
        ax.grid(True, linestyle='--', alpha=0.3)
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        fig.savefig(filename, facecolor=fig.get_facecolor())
        plt.close(fig)

    def _create_fft_plot(self, wave_result: WaveAnalysisResult, region: MonitoringRegion, 
                        title_info: str, filename: str):
        fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
        fig.patch.set_facecolor('#1E1E1E')
        
        ax.plot(wave_result.fft_freqs, wave_result.fft_mags, color='magenta', linewidth=1)
        ax.axvline(x=self.app_config.fft_cutoff_frequency, color='yellow', 
                  linestyle='--', linewidth=1, label=f'Cutoff: {self.app_config.fft_cutoff_frequency:.2f}')
        ax.set_xlim(0, 0.5)
        ax.set_xlabel('Normalized Frequency', color='white')
        ax.set_ylabel('Magnitude (A.U.)', color='white')
        
        fft_info = (f'Total E: {wave_result.total_energy:.2e} | '
                   f'HF E: {wave_result.high_freq_energy:.2e} | '
                   f'Ratio: {wave_result.energy_ratio:.3e}')
        classification = "HF (Bad)" if wave_result.is_high_frequency else "LF (Good)"
        ax.set_title(f'FFT Magnitude Spectrum - {classification}\n{title_info}\n{fft_info}', 
                    color='white', fontsize=9)
        
        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white')
        ax.legend(facecolor='#2E2E2E', labelcolor='white')
        ax.grid(True, linestyle='--', alpha=0.3)
        
        textstr = f'Threshold: {self.app_config.fft_energy_ratio_threshold:.4f}'
        props = dict(boxstyle='round', facecolor='#2E2E2E', alpha=0.8, edgecolor='white')
        ax.text(0.98, 0.97, textstr, transform=ax.transAxes, fontsize=9,
               verticalalignment='top', horizontalalignment='right', 
               bbox=props, color='white')
        
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        fig.savefig(filename, facecolor=fig.get_facecolor())
        plt.close(fig)

    def _create_lowpass_comparison_plot(self, wave_result: WaveAnalysisResult, region: MonitoringRegion,
                                        title_info: str, filename: str):
        fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
        fig.patch.set_facecolor('#1E1E1E')
        
        if wave_result.signal_physical is None or wave_result.filtered_physical is None:
            ax.text(0.5, 0.5, 'No lowpass data available', 
                   transform=ax.transAxes, ha='center', va='center', color='white', fontsize=14)
            fig.savefig(filename, facecolor=fig.get_facecolor())
            plt.close(fig)
            return
        
        num_points = len(wave_result.signal_physical)
        freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        
        ax.plot(freq_axis, wave_result.signal_physical, 'w-', linewidth=2, label='Original', alpha=0.9)
        ax.plot(freq_axis, wave_result.filtered_physical, 'g--', linewidth=1.5, label='Lowpass Filtered')
        
        filter_info = f'Cutoff: {self.app_config.lowpass_cutoff:.3f} | Order: {self.app_config.lowpass_filter_order}'
        ax.set_title(f'Lowpass Filter Comparison (Physical Units)\n{title_info}\n{filter_info}', 
                    color='white', fontsize=9)
        ax.set_xlabel('Frequency (Hz)', color='white')
        ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white')
        ax.legend(facecolor='#2E2E2E', labelcolor='white')
        ax.grid(True, linestyle='--', alpha=0.3)
        
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        fig.savefig(filename, facecolor=fig.get_facecolor())
        plt.close(fig)

    def _create_residual_plot(self, wave_result: WaveAnalysisResult, region: MonitoringRegion,
                              title_info: str, filename: str):
        fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
        fig.patch.set_facecolor('#1E1E1E')
        
        if wave_result.residual_physical is None:
            ax.text(0.5, 0.5, 'No residual data available', 
                   transform=ax.transAxes, ha='center', va='center', color='white', fontsize=14)
            fig.savefig(filename, facecolor=fig.get_facecolor())
            plt.close(fig)
            return
        
        num_points = len(wave_result.residual_physical)
        freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        threshold = self.app_config.residual_threshold
        
        ax.plot(freq_axis, wave_result.residual_physical, 'c-', linewidth=1, label='Residual (HF Content)')
        ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
        ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.5, 
                  label=f'Threshold (±{threshold} {region.y_axis_unit})')
        ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.5)
        
        exceedances = np.abs(wave_result.residual_physical) > threshold
        if np.any(exceedances):
            ax.scatter(freq_axis[exceedances], wave_result.residual_physical[exceedances], 
                      c='red', s=15, zorder=5, alpha=0.7)
        
        classification = "BAD HIT" if wave_result.lowpass_is_bad_hit else "GOOD HIT"
        residual_info = (f'Exceedances: {wave_result.exceedance_count} ({wave_result.exceedance_ratio:.1%}) | '
                        f'Threshold: {self.app_config.exceedance_ratio_threshold:.1%} | {classification}')
        ax.set_title(f'Residual Analysis (Physical Units)\n{title_info}\n{residual_info}', 
                    color='white', fontsize=9)
        ax.set_xlabel('Frequency (Hz)', color='white')
        ax.set_ylabel(f'Residual ({region.y_axis_unit})', color='white')
        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white')
        ax.legend(facecolor='#2E2E2E', labelcolor='white', loc='upper right')
        ax.grid(True, linestyle='--', alpha=0.3)
        
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        fig.savefig(filename, facecolor=fig.get_facecolor())
        plt.close(fig)

    def _create_run_summary_chart(self, hit_data: Dict, filename: str):
        if not hit_data:
            return
            
        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        fig.patch.set_facecolor('#1E1E1E')
        
        hits = list(hit_data.keys())
        values = [hit_data[h]['exceedance_count'] for h in hits]
        
        bars = ax.bar(range(len(hits)), values, edgecolor='white', linewidth=0.5)
        
        cmap = plt.cm.jet
        vmin, vmax = min(values), max(values)
        if vmin == vmax:
            vmin, vmax = vmin - 1, vmax + 1
        
        for bar, val in zip(bars, values):
            normalized = (val - vmin) / (vmax - vmin) if vmax > vmin else 0.5
            bar.set_color(cmap(normalized))
        
        ax.set_xticks(range(len(hits)))
        ax.set_xticklabels(hits, rotation=45, ha='right', fontsize=8, color='white')
        ax.set_xlabel('Hit (Point Combination)', color='white')
        ax.set_ylabel('Exceedances (Values Outside Range)', color='white')
        ax.set_title(f'Run Summary - Exceedance Counts\n{self.current_run}', color='white', fontsize=11)
        ax.set_facecolor('#2E2E2E')
        ax.tick_params(axis='both', colors='white')
        ax.grid(True, linestyle='--', alpha=0.3, axis='y')
        
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(vmin=vmin, vmax=vmax))
        sm.set_array([])
        cbar = plt.colorbar(sm, ax=ax)
        cbar.set_label('Exceedance Count', color='white')
        cbar.ax.yaxis.set_tick_params(color='white')
        plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color='white')
        
        fig.tight_layout()
        fig.savefig(filename, facecolor=fig.get_facecolor())
        plt.close(fig)
    
    # =========================================================================
    # DATA FILE LOGGING
    # =========================================================================
    
    def _save_mat_log(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, 
                      wave_name: str, base_filename: str):
        try:
            filename = f"signal_logs/{base_filename}.mat"
            region = frame_result.active_regions[wave_name]
            num_points = len(wave_result.signal_physical)
            frequency_hz = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            points = frame_result.points_info
            
            mat_data = {
                'frequency_hz': frequency_hz,
                'amplitude': wave_result.signal_physical,
                'amplitude_units': region.y_axis_unit,
                'raw_amplitude_pixels': wave_result.signal_vector,
                'info_region_name': wave_name,
                'meta_run': points.run,
                'meta_hammer_point': points.hammer_point,
                'meta_hammer_dir': points.hammer_dir,
                'meta_response_point': points.response_point,
                'meta_response_dir': points.response_dir,
                'meta_overload_status': frame_result.overload_text,
                'fft_total_energy': wave_result.total_energy,
                'fft_high_freq_energy': wave_result.high_freq_energy,
                'fft_energy_ratio': wave_result.energy_ratio,
                'fft_cutoff_frequency': self.app_config.fft_cutoff_frequency,
                'fft_is_hf': wave_result.is_high_frequency,
                'lowpass_filtered_signal': wave_result.filtered_physical,
                'lowpass_residual_signal': wave_result.residual_physical,
                'lowpass_exceedance_count': wave_result.exceedance_count,
                'lowpass_exceedance_ratio': wave_result.exceedance_ratio,
                'lowpass_cutoff': self.app_config.lowpass_cutoff,
                'lowpass_filter_order': self.app_config.lowpass_filter_order,
                'lowpass_residual_threshold': self.app_config.residual_threshold,
                'lowpass_exceedance_threshold': self.app_config.exceedance_ratio_threshold,
                'lowpass_is_bad_hit': wave_result.lowpass_is_bad_hit
            }
            scipy.io.savemat(filename, mat_data)
        except Exception as e:
            logger.error(f"Failed to save .mat file for {wave_name}: {e}")

    def _save_unv_log(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, 
                      wave_name: str, base_filename: str):
        def parse_point(point_str: str) -> int: 
            return int(re.sub(r'\D', '', point_str)) if point_str and re.sub(r'\D', '', point_str) else 1
        
        def parse_dof(dir_str: str) -> int: 
            if not dir_str or len(dir_str) < 1:
                return 3
            last_char = dir_str.upper()[-1]
            return {'X': 1, 'Y': 2, 'Z': 3}.get(last_char, 3)
        
        try:
            filename = f"signal_logs/{base_filename}.unv"
            region = frame_result.active_regions[wave_name]
            points = frame_result.points_info
            num_points = len(wave_result.signal_physical)
            
            if num_points < 2:
                logger.warning(f"Skipping UNV log for {wave_name}: insufficient data points ({num_points})")
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
                
                id_line2 = "USMA v0.4.4 - Screen Reconstruction"
                f.write(f"{id_line2[:80]:<80}\n")
                
                f.write(f"{timestamp:<80}\n")
                
                id_line4 = f"Reconstructed from {points.run}, region \"{wave_name}\""
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
                
                for val in wave_result.signal_physical:
                    real_part = val
                    imag_part = 0.0
                    f.write(f"  {real_part:13.6E}  {imag_part:13.6E}\n")
                
                f.write("    -1\n")
                
            logger.info(f"UNV file saved: {filename}")
            
        except Exception as e: 
            logger.error(f"Failed to save .unv file for {wave_name}: {e}")


# --- 4. VISUALIZATION & CONFIGURATION ---
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
            colors = {"wave": "#3498db", "status": "#2ecc71", "overload": "#e74c3c", 
                      "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
            for name, region_data in data.items():
                if not name.startswith('_') and region_data.get('enabled', True):
                    x, y, w, h = region_data['x'], region_data['y'], region_data['width'], region_data['height']
                    color = colors.get(region_data.get('roi_type', 'wave'), "#95a5a6")
                    canvas.create_rectangle(x-5, y-5, x+w+5, y+h+5, outline=color, width=2)
                    canvas.create_text(x-5, y-5, text=name, anchor="sw", font=("Arial", 10, "bold"), fill=color)
            canvas.create_text(self.winfo_screenwidth()-10, self.winfo_screenheight()-10, 
                              text=f"Config: {os.path.basename(self.config_path)}", anchor="se", fill="#333")
        except Exception as e:
            logger.error(f"Overlay Error: {e}")
            self.destroy()


class ConfigToolWindow(tk.Toplevel):
    """Advanced Region & Color Configuration Tool with scrollable right panel."""
    
    def __init__(self, parent, main_root):
        super().__init__(parent)
        self.title("Advanced Region & Color Configuration Tool")
        self.main_root = main_root
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

    def _on_closing(self):
        self.main_root.deiconify()
        self.destroy()

    def _setup_gui(self):
        # Toolbar
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(toolbar, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Load Config", command=self._load_config).pack(side=tk.LEFT, padx=2)
        
        # Main frame
        main_frame = ttk.Frame(self, padding=5)
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        # Canvas frame (left)
        canvas_frame = ttk.LabelFrame(main_frame, text="Screenshot Preview")
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.canvas = tk.Canvas(canvas_frame, bg="black")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Right panel with fixed width and both scrollbars
        right_outer_frame = ttk.Frame(main_frame, width=550)
        right_outer_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        right_outer_frame.pack_propagate(False)
        right_outer_frame.grid_propagate(False)
        
        # Create canvas for scrolling (both vertical and horizontal)
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
        
        # Enable mouse wheel scrolling
        self.right_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.right_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Build right panel content inside scrollable frame
        self._build_right_panel_content(self.right_scrollable_frame)
        
        # Canvas bindings
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<B1-Motion>", self._update_selection)
        self.canvas.bind("<ButtonRelease-1>", self._end_selection)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
    
    def _on_mousewheel(self, event):
        """Handle mouse wheel scrolling for the right panel."""
        self.right_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    
    def _build_right_panel_content(self, parent):
        """Build all the controls in the scrollable right panel."""
        
        # Capture button
        capture_frame = ttk.LabelFrame(parent, text="Capture")
        capture_frame.pack(fill=tk.X, pady=5, padx=5)
        ttk.Button(capture_frame, text="Take Screenshot", command=self._take_screenshot).pack(pady=5, padx=5, fill=tk.X)
        
        # Region list
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
        
        # Region Editor
        editor_frame = ttk.LabelFrame(parent, text="Region Editor")
        editor_frame.pack(fill=tk.X, pady=5, padx=5)
        
        self.editor_vars = {
            'name': tk.StringVar(), 'x': tk.IntVar(), 'y': tk.IntVar(),
            'width': tk.IntVar(), 'height': tk.IntVar(), 'roi_type': tk.StringVar(),
            'enabled': tk.BooleanVar(), 'x_axis_min': tk.DoubleVar(), 'x_axis_max': tk.DoubleVar(),
            'y_axis_min': tk.DoubleVar(), 'y_axis_max': tk.DoubleVar(), 'y_axis_unit': tk.StringVar(),
            'resp_node': tk.IntVar(), 'resp_dof': tk.IntVar(), 'ref_node': tk.IntVar(), 'ref_dof': tk.IntVar()
        }
        
        # Name and type row
        f1 = ttk.Frame(editor_frame)
        f1.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f1, text="Name:").pack(side=tk.LEFT)
        ttk.Entry(f1, textvariable=self.editor_vars['name'], width=15).pack(side=tk.LEFT, padx=2)
        ttk.Label(f1, text="Type:").pack(side=tk.LEFT, padx=(10,0))
        ttk.Combobox(f1, textvariable=self.editor_vars['roi_type'], 
                     values=['wave', 'status', 'overload', 'run', 'hammer', 'response'], 
                     state='readonly', width=10).pack(side=tk.LEFT, padx=2)
        
        # Geometry row
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

        # Physical scaling
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
        
        # Buttons
        f_buttons = ttk.Frame(editor_frame)
        f_buttons.pack(fill=tk.X, pady=5, padx=5)
        ttk.Checkbutton(f_buttons, text="Enabled", variable=self.editor_vars['enabled']).pack(side=tk.LEFT)
        ttk.Button(f_buttons, text="Update", command=self._update_region_from_editor).pack(side=tk.LEFT, padx=5)
        ttk.Button(f_buttons, text="Delete", command=self._delete_selected_region).pack(side=tk.LEFT)
        
        # Analysis Parameters
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
        
        # FFT parameters
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
        
        # Lowpass parameters
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
        ttk.Spinbox(lp_row2, from_=0.01, to=0.5, increment=0.01, 
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
        x1 = int((x1_c - self.x_offset) / self.scale)
        y1 = int((y1_c - self.y_offset) / self.scale)
        x2 = int((x2_c - self.x_offset) / self.scale)
        y2 = int((y2_c - self.y_offset) / self.scale)
        name = f"region_{len(self.app_config.regions)+1}"
        new_region = MonitoringRegion(name=name, x=x1, y=y1, width=x2-x1, height=y2-y1, roi_type='wave')
        self.app_config.regions[name] = new_region
        self.canvas.delete("selection_rect")
        self._update_ui_from_data()
        new_idx = sorted(self.app_config.regions.keys()).index(name)
        self.region_listbox.selection_clear(0, tk.END)
        self.region_listbox.selection_set(new_idx)
        self.region_listbox.activate(new_idx)
        self._on_listbox_select(None)

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

    def _redraw_regions_on_canvas(self):
        self.canvas.delete("region")
        colors = {"wave": "#3498db", "status": "#2ecc71", "overload": "#e74c3c", 
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
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialdir="configs", parent=self)
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
    """Embedded matplotlib graph viewer with hit and plot type navigation."""
    
    PLOT_TYPES = ['Signal', 'FFT', 'Lowpass Comparison', 'Residual Analysis', 'Run Summary']
    MAX_HISTORY = 50
    
    def __init__(self, parent):
        super().__init__(parent, text="Graph Viewer")
        self.current_plot_index = 0
        self.current_hit_index = -1
        self.hit_history: List[Tuple] = []
        self.run_history = {}
        self.app_config = None
        
        self._setup_ui()
        
    def _setup_ui(self):
        nav_frame = ttk.Frame(self)
        nav_frame.pack(fill=tk.X, padx=5, pady=5)
        
        hit_nav_frame = ttk.LabelFrame(nav_frame, text="Hit")
        hit_nav_frame.pack(side=tk.LEFT, padx=5)
        
        self.prev_hit_btn = ttk.Button(hit_nav_frame, text="◀◀", command=self._prev_hit, width=4)
        self.prev_hit_btn.pack(side=tk.LEFT, padx=2)
        
        self.hit_label = ttk.Label(hit_nav_frame, text="--/--", width=12, anchor=tk.CENTER)
        self.hit_label.pack(side=tk.LEFT, padx=5)
        
        self.next_hit_btn = ttk.Button(hit_nav_frame, text="▶▶", command=self._next_hit, width=4)
        self.next_hit_btn.pack(side=tk.LEFT, padx=2)
        
        plot_nav_frame = ttk.LabelFrame(nav_frame, text="Plot Type")
        plot_nav_frame.pack(side=tk.LEFT, padx=10)
        
        self.prev_plot_btn = ttk.Button(plot_nav_frame, text="◀", command=self._prev_plot, width=3)
        self.prev_plot_btn.pack(side=tk.LEFT, padx=2)
        
        self.plot_type_var = tk.StringVar(value=self.PLOT_TYPES[0])
        self.plot_selector = ttk.Combobox(plot_nav_frame, textvariable=self.plot_type_var, 
                                          values=self.PLOT_TYPES, state='readonly', width=18)
        self.plot_selector.pack(side=tk.LEFT, padx=2)
        self.plot_selector.bind("<<ComboboxSelected>>", self._on_plot_selected)
        
        self.next_plot_btn = ttk.Button(plot_nav_frame, text="▶", command=self._next_plot, width=3)
        self.next_plot_btn.pack(side=tk.LEFT, padx=2)
        
        self.hit_info_label = ttk.Label(nav_frame, text="No data", font=("Segoe UI", 9))
        self.hit_info_label.pack(side=tk.RIGHT, padx=10)
        
        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.figure = Figure(figsize=(8, 4), dpi=100, facecolor='#1E1E1E')
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        self.ax.set_title('Waiting for data...', color='white')
        
        self.canvas = FigureCanvasTkAgg(self.figure, master=canvas_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        self._update_navigation_state()
    
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
            _, frame_result, _, hit_key = self.hit_history[self.current_hit_index]
            self.hit_info_label.config(text=f"Hit: {hit_key}")
        else:
            self.hit_info_label.config(text="No data")
        
    def update_data(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, 
                    region: MonitoringRegion, hit_key: str, run_history: Dict, app_config: AppConfig):
        self.hit_history.append((wave_result, frame_result, region, hit_key))
        
        if len(self.hit_history) > self.MAX_HISTORY:
            self.hit_history = self.hit_history[-self.MAX_HISTORY:]
        
        self.run_history = run_history
        self.app_config = app_config
        
        self.current_hit_index = len(self.hit_history) - 1
        
        self._update_plot()
        self._update_navigation_state()
        
    def _update_plot(self):
        if not self.hit_history or self.current_hit_index < 0:
            return
        
        if self.current_hit_index >= len(self.hit_history):
            self.current_hit_index = len(self.hit_history) - 1
            
        wave_result, frame_result, region, hit_key = self.hit_history[self.current_hit_index]
        
        self.ax.clear()
        self.ax.set_facecolor('#2E2E2E')
        self.ax.tick_params(axis='both', colors='white')
        
        plot_type = self.plot_type_var.get()
        
        try:
            if plot_type == 'Signal':
                self._plot_signal(wave_result, region, hit_key)
            elif plot_type == 'FFT':
                self._plot_fft(wave_result, hit_key)
            elif plot_type == 'Lowpass Comparison':
                self._plot_lowpass_comparison(wave_result, region, hit_key)
            elif plot_type == 'Residual Analysis':
                self._plot_residual(wave_result, region, hit_key)
            elif plot_type == 'Run Summary':
                self._plot_run_summary(frame_result)
        except Exception as e:
            logger.error(f"Error plotting {plot_type}: {e}")
            self.ax.text(0.5, 0.5, f'Error: {str(e)[:50]}', 
                        transform=self.ax.transAxes, ha='center', va='center', color='red')
            
        self.figure.tight_layout()
        self.canvas.draw()
        
    def _plot_signal(self, wave_result, region, hit_key):
        if wave_result.signal_physical is None:
            self.ax.text(0.5, 0.5, 'No signal data', transform=self.ax.transAxes, 
                        ha='center', va='center', color='white')
            return
            
        num_points = len(wave_result.signal_physical)
        freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        
        self.ax.plot(freq_axis, wave_result.signal_physical, color='cyan', linewidth=1.5)
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
        self.ax.set_title(f'Reconstructed Signal - {hit_key}', color='white')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_fft(self, wave_result, hit_key):
        self.ax.plot(wave_result.fft_freqs, wave_result.fft_mags, color='magenta', linewidth=1)
        
        if self.app_config:
            self.ax.axvline(x=self.app_config.fft_cutoff_frequency, color='yellow', 
                           linestyle='--', linewidth=1, label=f'Cutoff: {self.app_config.fft_cutoff_frequency:.2f}')
        
        self.ax.set_xlim(0, 0.5)
        self.ax.set_xlabel('Normalized Frequency', color='white')
        self.ax.set_ylabel('Magnitude', color='white')
        
        classification = "HF (Bad)" if wave_result.is_high_frequency else "LF (Good)"
        self.ax.set_title(f'FFT - {hit_key} - Ratio: {wave_result.energy_ratio:.3e} - {classification}', 
                         color='white', fontsize=10)
        self.ax.legend(facecolor='#2E2E2E', labelcolor='white')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_lowpass_comparison(self, wave_result, region, hit_key):
        if wave_result.signal_physical is None or wave_result.filtered_physical is None:
            self.ax.text(0.5, 0.5, 'No lowpass data available', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        num_points = len(wave_result.signal_physical)
        freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        
        self.ax.plot(freq_axis, wave_result.signal_physical, 'w-', linewidth=1.5, label='Original', alpha=0.9)
        self.ax.plot(freq_axis, wave_result.filtered_physical, 'g--', linewidth=1.2, label='Lowpass Filtered')
        
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
        
        if self.app_config:
            title = f'Lowpass - {hit_key} - Cutoff: {self.app_config.lowpass_cutoff:.3f}'
        else:
            title = f'Lowpass Comparison - {hit_key}'
        self.ax.set_title(title, color='white', fontsize=10)
        self.ax.legend(facecolor='#2E2E2E', labelcolor='white')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_residual(self, wave_result, region, hit_key):
        if wave_result.residual_physical is None:
            self.ax.text(0.5, 0.5, 'No residual data available', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        num_points = len(wave_result.residual_physical)
        freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        
        if self.app_config:
            threshold = self.app_config.residual_threshold
        else:
            threshold = 0.005
        
        self.ax.plot(freq_axis, wave_result.residual_physical, 'c-', linewidth=1, label='Residual')
        self.ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
        self.ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.2, label=f'±{threshold}')
        self.ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.2)
        
        exceedances = np.abs(wave_result.residual_physical) > threshold
        if np.any(exceedances):
            self.ax.scatter(freq_axis[exceedances], wave_result.residual_physical[exceedances], 
                          c='red', s=12, zorder=5, alpha=0.7)
        
        self.ax.set_xlabel('Frequency (Hz)', color='white')
        self.ax.set_ylabel(f'Residual ({region.y_axis_unit})', color='white')
        classification = "BAD" if wave_result.lowpass_is_bad_hit else "GOOD"
        self.ax.set_title(f'Residual - {hit_key} - Exc: {wave_result.exceedance_count} ({wave_result.exceedance_ratio:.1%}) - {classification}', 
                         color='white', fontsize=10)
        self.ax.legend(facecolor='#2E2E2E', labelcolor='white', loc='upper right')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        
    def _plot_run_summary(self, frame_result):
        if not self.run_history:
            self.ax.text(0.5, 0.5, 'No run history available', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        current_run = frame_result.points_info.run if frame_result else "Run 1"
        if current_run in self.run_history:
            hit_data = self.run_history[current_run]
        else:
            hit_data = {}
            for run_data in self.run_history.values():
                hit_data.update(run_data)
        
        if not hit_data:
            self.ax.text(0.5, 0.5, 'No hits recorded yet', 
                        transform=self.ax.transAxes, ha='center', va='center', color='white')
            return
            
        hits = list(hit_data.keys())
        values = [hit_data[h]['exceedance_count'] for h in hits]
        
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
        self.canvas.draw()
        
        self._update_navigation_state()


# --- 6. MAIN GUI ---
class MonitorControlGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("USMA v.0.4.4 (Release)")
        self.root.geometry("1000x750")
        
        self.config_path = tk.StringVar(value="configs/default_config.json")
        self.is_monitoring = tk.BooleanVar(value=False)
        self.is_overlay_on = tk.BooleanVar(value=False)
        self.verbose_logging_on = tk.BooleanVar(value=True)
        self.image_logging_on = tk.BooleanVar(value=False)
        self.log_opt_screenshot = tk.BooleanVar(value=False)
        self.log_opt_color_filter = tk.BooleanVar(value=False)
        self.log_opt_signal_plot = tk.BooleanVar(value=False)
        self.log_opt_fft_plot = tk.BooleanVar(value=False)
        self.log_opt_lowpass_plot = tk.BooleanVar(value=False)
        self.log_opt_residual_plot = tk.BooleanVar(value=False)
        self.log_opt_summary_chart = tk.BooleanVar(value=False)
        self.log_opt_ocr_images = tk.BooleanVar(value=False)
        
        self.audio_feedback_on = tk.BooleanVar(value=False)
        self.log_to_mat = tk.BooleanVar(value=False)
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
        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Configuration frame
        config_frame = ttk.LabelFrame(frame, text="Configuration")
        config_frame.pack(fill=tk.X, pady=5)
        ttk.Entry(config_frame, textvariable=self.config_path, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        self.load_button = ttk.Button(config_frame, text="Load...", command=self._load_config)
        self.load_button.pack(side=tk.LEFT, padx=5)
        self.edit_button = ttk.Button(config_frame, text="Edit Config...", command=self._launch_config_tool)
        self.edit_button.pack(side=tk.LEFT, padx=5)
        
        # Live feedback frame
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
        
        # Graph Viewer
        self.graph_viewer = GraphViewerFrame(frame)
        self.graph_viewer.pack(fill=tk.BOTH, expand=True, pady=5)

        # Bottom panel
        bottom_panel = ttk.Frame(frame)
        bottom_panel.pack(fill=tk.X, pady=5)
        
        # Controls
        control_frame = ttk.LabelFrame(bottom_panel, text="Controls")
        control_frame.pack(fill=tk.Y, side=tk.LEFT, pady=5)
        
        self.start_stop_button = ttk.Button(control_frame, text="Start Monitoring", command=self._toggle_monitoring)
        self.start_stop_button.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)
        
        self.overlay_check = ttk.Checkbutton(control_frame, text="Show Overlay", variable=self.is_overlay_on, command=self._toggle_overlay)
        self.overlay_check.pack(padx=10, pady=(0,5))
        
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
        
        # Right panel
        right_panel = ttk.Frame(bottom_panel)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        # Manual points frame
        self.manual_points_frame = ttk.LabelFrame(right_panel, text="Manual Points of Interest (POI) Entry")
        self.manual_points_frame.pack(fill=tk.X)
        pf = self.manual_points_frame
        ttk.Label(pf, text="Run:").grid(row=0, column=0, padx=5, pady=2)
        ttk.Entry(pf, textvariable=self.manual_points_vars['run'], width=8).grid(row=0, column=1)
        ttk.Label(pf, text="Hammer:").grid(row=0, column=2, padx=5)
        ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_point'], width=6).grid(row=0, column=3)
        ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_dir'], width=4).grid(row=0, column=4)
        ttk.Label(pf, text="Response:").grid(row=1, column=2, padx=5)
        ttk.Entry(pf, textvariable=self.manual_points_vars['response_point'], width=6).grid(row=1, column=3)
        ttk.Entry(pf, textvariable=self.manual_points_vars['response_dir'], width=4).grid(row=1, column=4)
        
        # Logging controls
        logging_controls_frame = ttk.LabelFrame(right_panel, text="Logging")
        logging_controls_frame.pack(fill=tk.X, pady=5)
        
        # Main logging options
        logging_main_frame = ttk.Frame(logging_controls_frame)
        logging_main_frame.pack(fill=tk.X, side=tk.LEFT, anchor=tk.N, padx=5)
        
        self.verbose_check = ttk.Checkbutton(logging_main_frame, text="Verbose Console Log", variable=self.verbose_logging_on)
        self.verbose_check.pack(anchor=tk.W, pady=2)
        
        self.img_log_check = ttk.Checkbutton(logging_main_frame, text="Enable Image Logs", variable=self.image_logging_on, command=self._toggle_img_log_options_state)
        self.img_log_check.pack(anchor=tk.W, pady=2)
        
        # Image log options in three columns
        self.img_log_options_frame = ttk.Frame(logging_main_frame)
        self.img_log_options_frame.pack(fill=tk.X, pady=(5,0))
        
        col1 = ttk.Frame(self.img_log_options_frame)
        col1.pack(side=tk.LEFT, anchor=tk.N)
        col2 = ttk.Frame(self.img_log_options_frame)
        col2.pack(side=tk.LEFT, anchor=tk.N, padx=5)
        col3 = ttk.Frame(self.img_log_options_frame)
        col3.pack(side=tk.LEFT, anchor=tk.N)
        
        # Column 1: Basic images
        ttk.Checkbutton(col1, text="ROI Screenshots", variable=self.log_opt_screenshot).pack(anchor=tk.W)
        ttk.Checkbutton(col1, text="Color Masks", variable=self.log_opt_color_filter).pack(anchor=tk.W)
        ttk.Checkbutton(col1, text="OCR Images", variable=self.log_opt_ocr_images).pack(anchor=tk.W)
        
        # Column 2: Analysis plots
        ttk.Checkbutton(col2, text="Signal Plot", variable=self.log_opt_signal_plot).pack(anchor=tk.W)
        ttk.Checkbutton(col2, text="FFT Plot", variable=self.log_opt_fft_plot).pack(anchor=tk.W)
        ttk.Checkbutton(col2, text="Summary Chart", variable=self.log_opt_summary_chart).pack(anchor=tk.W)
        
        # Column 3: Lowpass method
        ttk.Checkbutton(col3, text="Lowpass Plot", variable=self.log_opt_lowpass_plot).pack(anchor=tk.W)
        ttk.Checkbutton(col3, text="Residual Plot", variable=self.log_opt_residual_plot).pack(anchor=tk.W)
        
        # Data logging
        data_logging_frame = ttk.Frame(logging_controls_frame)
        data_logging_frame.pack(fill=tk.X, side=tk.LEFT, padx=10, anchor=tk.N)
        ttk.Checkbutton(data_logging_frame, text="Log to .mat", variable=self.log_to_mat).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(data_logging_frame, text="Log to .unv", variable=self.log_to_unv).pack(anchor=tk.W, pady=2)

        self.status_label = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        self._toggle_img_log_options_state()
        self._update_manual_points_state()

    def _toggle_audio_feedback(self):
        self.monitor.set_audio_feedback(self.audio_feedback_on.get())
        
    def _toggle_img_log_options_state(self):
        state = tk.NORMAL if self.image_logging_on.get() else tk.DISABLED
        for col in self.img_log_options_frame.winfo_children():
            for child in col.winfo_children():
                child.configure(state=state)

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
        
    def _on_plot_data(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, 
                      wave_name: str, hit_key: str):
        region = frame_result.active_regions.get(wave_name)
        if region:
            self.root.after(0, self._update_graph_viewer, wave_result, frame_result, region, hit_key)
            
    def _update_graph_viewer(self, wave_result, frame_result, region, hit_key):
        self.graph_viewer.update_data(
            wave_result, frame_result, region, hit_key,
            self.monitor.run_history, self.monitor.app_config
        )
        
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
        has_points_region = any(r.roi_type in ['run', 'hammer', 'response'] 
                                for r in self.monitor.app_config.regions.values() if r.enabled)
        state = tk.DISABLED if has_points_region else tk.NORMAL
        for child in self.manual_points_frame.winfo_children():
            if isinstance(child, (ttk.Entry, ttk.Label)):
                child.configure(state=state)

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
    
    def _launch_config_tool(self):
        self.root.iconify()
        ConfigToolWindow(self.root, self.root).grab_set()
        
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
            data_log_opts = DataLogOptions(self.log_to_mat.get(), self.log_to_unv.get())
            manual_points = PointsInfo(**{k: v.get() for k, v in self.manual_points_vars.items()})
            
            if self.monitor.start(self.verbose_logging_on.get(), self.image_logging_on.get(), 
                                  img_log_opts, data_log_opts, manual_points):
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
    main_root = tk.Tk()
    app = MonitorControlGUI(main_root)
    main_root.mainloop()