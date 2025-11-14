#!/usr/bin/env python3
"""
USMA (Unified Screen Monitoring Application) - v.0.4.3 (Release)

A single, GUI-driven application that combines a professional-grade region 
configuration tool, real-time screen monitoring, visual overlay, and clear 
image logging.

--- Version Evolution (v.0.4.x) ---

v.0.4.0:
- OCR Integration: Added pytesseract for text extraction.
- New Region Types: 'status', 'overload', and a single 'points' region.
- Enhanced Logging: OCR data embedded in logs and signal headers (UFF Type 18).
- Dynamic Filenaming: Logs named with hit counters (e.g., FRF_P1P1_1).
- Manual POI Entry: Added manual input on main GUI as fallback.

v.0.4.1:
- OCR "Divide and Conquer": Replaced single 'points' region with three
  dedicated regions ('run', 'hammer', 'response') for vastly improved
  accuracy using single-line OCR (psm 7).
- Advanced Preprocessing: Implemented CLAHE (contrast) and Sharpening
  pipeline to read low-contrast text.
- Bugfixes: Corrected 'points' regex parser, fixed GUI state-loss bug in
  ConfigToolWindow, and re-enabled manual geometry entry (x,y,w,h).
- Added Diagnostic Logs: Temporarily added OCR preprocessed images to
  logs to debug parser failures.

v.0.4.2:
- Production Logging: Removed all OCR diagnostic images from logs. Image
  logging for 'ROI Screenshot' and 'Color Filter Mask' now *only* saves
  images related to 'wave' regions, cleaning up the output.
- Portable Setup: Implemented logic to dynamically locate the Tesseract
  OCR engine in the relative 'external/tesseract' directory, allowing
  the application to be fully portable.

v.0.4.3 (This release):
- **Fixed Dataset 58 Format**: Corrected UNV file header to comply with 
  universal file format specification:
  * Added all 5 required identification lines (function ID, program info, 
    date/time, record info, response entity name)
  * Fixed DOF identification line with proper field widths (I5, I10 format)
  * Padded separator and dataset type lines to 80 characters
  * Files now compatible with pyuff, MATLAB, and commercial modal software
- **Enhanced FFT Plots**: Added comprehensive analysis information to FFT 
  plots including total energy, high-frequency energy, energy ratio, and 
  cutoff frequency for better diagnostic capability.
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
import scipy.io
import matplotlib
matplotlib.use('Agg') # Use the 'Agg' backend for non-interactive plotting in a thread.
import matplotlib.pyplot as plt
from datetime import datetime

# --- Import sounddevice with fallback ---
try:
    import sounddevice as sd
    SOUND_DEVICE_AVAILABLE = True
except (ImportError, OSError) as e:
    SOUND_DEVICE_AVAILABLE = False
    print(f"Warning: sounddevice library not found or audio device error: {e}. Audio feedback disabled.")

# --- OCR Configuration ---
# NOTE: This version is configured to use the portable Tesseract OCR engine
# located in the 'external/tesseract' directory.
try:
    import pytesseract
    
    # --- Portable Tesseract Path ---
    # Get the directory where the script is running
    script_dir = os.path.dirname(os.path.realpath(__file__))
    # Construct the relative path to the portable Tesseract executable
    tesseract_path = os.path.join(script_dir, 'external', 'tesseract', 'tesseract.exe')
    
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    
    # Check if the portable Tesseract executable actually exists
    if not os.path.exists(tesseract_path):
        raise FileNotFoundError(f"Portable Tesseract not found at: {tesseract_path}")
        
    OCR_AVAILABLE = True
    
except (ImportError, FileNotFoundError) as e:
    OCR_AVAILABLE = False
    print(f"Warning: Portable OCR features disabled. Error: {e}")
    print("Please ensure 'pytesseract' is installed and Tesseract is in the 'external/tesseract' folder.")


# --- 1. SETUP: DIRECTORY AND LOGGING CONFIGURATION ---
def setup_environment():
    """Create necessary directories for logs, configs, and image logs."""
    for folder in ['logs', 'configs', 'image_logs', 'signal_logs']:
        if not os.path.exists(folder): os.makedirs(folder)

setup_environment()

# Configure logging
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) 
file_handler = logging.FileHandler('logs/monitor_app.log'); file_handler.setFormatter(log_formatter); logger.addHandler(file_handler)
stream_handler = logging.StreamHandler(); stream_handler.setFormatter(log_formatter); logger.addHandler(stream_handler)


# --- 2. DATA CLASSES: CORE DATA STRUCTURES ---
@dataclass
class ImageLogOptions:
    include_screenshot: bool = False; include_color_filter: bool = False
    include_signal_plot: bool = False; include_fft_plot: bool = False

@dataclass
class DataLogOptions:
    log_mat: bool = False; log_unv: bool = False

@dataclass
class PointsInfo:
    """Stores parsed measurement point metadata."""
    run: str = "Run 1"; hammer_point: str = "P1"; hammer_dir: str = "-Z"
    response_point: str = "P1"; response_dir: str = "-Z"

@dataclass
class MonitoringRegion:
    name: str; x: int; y: int; width: int; height: int; roi_type: str
    enabled: bool = field(default=True); x_axis_min: float = field(default=0.0)
    x_axis_max: float = field(default=1024.0); y_axis_min: float = field(default=0.0)
    y_axis_max: float = field(default=1.0); y_axis_unit: str = field(default="g/N")
    resp_node: int = field(default=1); resp_dof: int = field(default=3)
    ref_node: int = field(default=1); ref_dof: int = field(default=3)

@dataclass
class WaveAnalysisResult:
    is_high_frequency: bool; energy_ratio: float; high_freq_energy: float
    signal_vector: np.ndarray; fft_freqs: np.ndarray; fft_mags: np.ndarray
    roi_image: np.ndarray; color_mask: np.ndarray
    total_energy: float = 0.0  # Added for FFT plot information

@dataclass
class FrameAnalysisResult:
    """Holds all analysis results from a single captured frame."""
    wave_results: Dict[str, WaveAnalysisResult] = field(default_factory=dict)
    active_regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    status_text: str = "Unknown"; overload_text: str = "Unknown"
    points_info: PointsInfo = field(default_factory=PointsInfo)
    overall_is_hf: Optional[bool] = None
    avg_energy_ratio: Optional[float] = None
    avg_high_freq_energy: Optional[float] = None

@dataclass
class AppConfig:
    regions: Dict[str, MonitoringRegion] = field(default_factory=dict)
    hsv_lower: List[int] = field(default_factory=lambda: [0, 0, 0])
    hsv_upper: List[int] = field(default_factory=lambda: [179, 255, 240])
    screenshot_interval: float = 0.25; fft_cutoff_frequency: float = 0.09
    fft_energy_ratio_threshold: float = 0.013


# --- 3. CORE LOGIC: THE SCREEN MONITOR ENGINE ---
class ScreenMonitor:
    """Handles the core task of capturing and analyzing the screen."""
    def __init__(self, config_path, update_callback=None):
        self.running = False; self.thread = None; self.config_path = config_path
        self.app_config = self._load_config(self.config_path); self.update_callback = update_callback
        self.frame_count = 0; self.verbose_logging_enabled = True; self.image_logging_enabled = True
        self.image_log_options = ImageLogOptions(); self.data_log_options = DataLogOptions()
        self.last_logged_ratio: Optional[float] = None; self.last_logged_energy: Optional[float] = None
        self.audio_feedback_enabled = False; self.audio_stream = None; self.audio_phase = 0
        self.audio_frequency = 400; self.audio_lock = threading.Lock(); self.sample_rate = 44100
        self.hit_counters: Dict[str, int] = {}
        self.manual_points_info: Optional[PointsInfo] = None
        self.last_known_status: str = "Unknown"
        self.last_known_overload: str = "Unknown"
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        self.sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])


    def start(self, verbose_logging=True, image_logging=True, image_log_options=None, data_log_options=None, manual_points=None):
        if not self.app_config.regions:
            messagebox.showerror("Error", "Cannot start. Please load a valid configuration."); return False
        if not OCR_AVAILABLE and any(r.roi_type in ['status', 'overload', 'run', 'hammer', 'response'] for r in self.app_config.regions.values()):
            logger.warning("Config uses OCR regions, but pytesseract is not available. These regions will be ignored.")
        self.verbose_logging_enabled, self.image_logging_enabled = verbose_logging, image_logging
        self.image_log_options = image_log_options if image_log_options else ImageLogOptions()
        self.data_log_options = data_log_options if data_log_options else DataLogOptions()
        self.manual_points_info = manual_points
        self.frame_count, self.last_logged_ratio, self.last_logged_energy = 0, None, None
        self.hit_counters.clear(); self.running = True
        self.last_known_status = "Unknown"; self.last_known_overload = "Unknown"
        self.thread = threading.Thread(target=self._monitoring_loop, daemon=True); self.thread.start()
        logger.info(f"Screen monitoring thread started for USMA v.0.4.3"); return True

    def stop(self):
        self.running = False; self._stop_audio_feedback()
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=1.5)
        logger.info("Screen monitoring stopped.")

    def update_config(self, new_config_path):
        self.config_path = new_config_path; self.app_config = self._load_config(new_config_path)
        logger.info(f"Configuration updated to {new_config_path}")

    def set_audio_feedback(self, enabled: bool):
        self.audio_feedback_enabled = enabled
        if not enabled: self._stop_audio_feedback()

    def _load_config(self, path: str) -> AppConfig:
        try:
            with open(path, 'r') as f: config_data = json.load(f)
            config = AppConfig()
            metadata = config_data.get('_metadata', {})
            config.hsv_lower = metadata.get('hsv_lower', config.hsv_lower)
            config.hsv_upper = metadata.get('hsv_upper', config.hsv_upper)
            config.screenshot_interval = metadata.get('screenshot_interval', config.screenshot_interval)
            config.fft_cutoff_frequency = metadata.get('fft_cutoff_frequency', config.fft_cutoff_frequency)
            config.fft_energy_ratio_threshold = metadata.get('fft_energy_ratio_threshold', config.fft_energy_ratio_threshold)
            region_fields = MonitoringRegion.__annotations__.keys()
            for name, data in config_data.items():
                if not name.startswith('_') and isinstance(data, dict):
                    filtered_data = {k: v for k, v in data.items() if k in region_fields}
                    if 'name' in filtered_data: config.regions[name] = MonitoringRegion(**filtered_data)
            return config
        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}"); return AppConfig()

    def _audio_callback(self, outdata, frames, time, status):
        try:
            if status: logger.warning(f"Audio stream status: {status}")
            t = (self.audio_phase + np.arange(frames)) / self.sample_rate; t = t.reshape(-1, 1)
            amplitude = np.iinfo(np.int16).max * 0.3
            outdata[:] = amplitude * np.sin(2 * np.pi * self.audio_frequency * t)
            self.audio_phase += frames
        except Exception as e: logger.error(f"Audio callback error: {e}"); outdata.fill(0)

    def _start_audio_feedback(self):
        if not SOUND_DEVICE_AVAILABLE or self.audio_stream is not None: return
        try:
            self.audio_phase = 0
            self.audio_stream = sd.OutputStream(samplerate=self.sample_rate, channels=1, callback=self._audio_callback, dtype='int16')
            self.audio_stream.start(); logger.info("Continuous audio feedback started.")
        except Exception as e: logger.error(f"Failed to start audio stream: {e}"); self.audio_stream = None

    def _stop_audio_feedback(self):
        if self.audio_stream is not None:
            try: self.audio_stream.stop(); self.audio_stream.close(); logger.info("Continuous audio feedback stopped.")
            except Exception as e: logger.error(f"Error stopping audio stream: {e}")
            finally: self.audio_stream = None
            
    def _monitoring_loop(self):
        while self.running:
            start_time = time.time()
            try:
                screenshot = pyautogui.screenshot()
                image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                frame_result, all_rois, ocr_diagnostics = self._process_frame(image)
                if self.update_callback: self.update_callback(frame_result)
                
                if self.audio_feedback_enabled:
                    with self.audio_lock:
                        is_hf = frame_result.overall_is_hf if frame_result.overall_is_hf is not None else False
                        if is_hf and self.audio_stream is None: self._start_audio_feedback()
                        elif not is_hf and self.audio_stream is not None: self._stop_audio_feedback()
                
                self._handle_logging(frame_result, all_rois, ocr_diagnostics)
                
                elapsed_time = time.time() - start_time
                sleep_duration = self.app_config.screenshot_interval - elapsed_time
                if sleep_duration > 0: time.sleep(sleep_duration)
            except Exception as e: logger.error(f"Error in monitoring loop: {e}"); time.sleep(1)

    def _process_frame(self, image: np.ndarray) -> Tuple[FrameAnalysisResult, Dict[str, np.ndarray], Dict[str, np.ndarray]]:
        frame_result = FrameAnalysisResult()
        ocr_points_active = False
        all_rois: Dict[str, np.ndarray] = {}
        ocr_diagnostics: Dict[str, np.ndarray] = {}
        
        frame_result.points_info = PointsInfo()
        
        for name, region in self.app_config.regions.items():
            if not region.enabled: continue
            roi = image[region.y:region.y+region.height, region.x:region.x+region.width]
            if roi.size == 0: continue
            
            all_rois[name] = roi.copy()
            diag_img = None
            
            if region.roi_type == 'wave':
                analysis_result = self._analyze_wave_pattern(roi)
                if analysis_result:
                    frame_result.wave_results[name] = analysis_result
                    frame_result.active_regions[name] = region
            elif OCR_AVAILABLE:
                if region.roi_type == 'status':
                    frame_result.status_text, diag_img = self._analyze_status(roi)
                elif region.roi_type == 'overload':
                    frame_result.overload_text, diag_img = self._analyze_overload(roi)
                elif region.roi_type == 'run':
                    run_str, diag_img = self._analyze_run(roi)
                    if run_str: frame_result.points_info.run = run_str
                    ocr_points_active = True
                elif region.roi_type == 'hammer':
                    point, dir, diag_imgs = self._analyze_point_and_dir(roi, "hammer")
                    if point: frame_result.points_info.hammer_point = point
                    if dir: frame_result.points_info.hammer_dir = dir
                    ocr_diagnostics.update(diag_imgs)
                    ocr_points_active = True
                elif region.roi_type == 'response':
                    point, dir, diag_imgs = self._analyze_point_and_dir(roi, "response")
                    if point: frame_result.points_info.response_point = point
                    if dir: frame_result.points_info.response_dir = dir
                    ocr_diagnostics.update(diag_imgs)
                    ocr_points_active = True
                
                if diag_img is not None:
                    ocr_diagnostics[name] = diag_img

        current_status = frame_result.status_text
        if current_status != "Unknown" and current_status != self.last_known_status:
            logger.info(f"STATUS UPDATE: '{self.last_known_status}' -> '{current_status}'")
            self.last_known_status = current_status
            
        current_overload = frame_result.overload_text
        if current_overload != "Unknown" and current_overload != self.last_known_overload:
            logger.info(f"OVERLOAD UPDATE: '{self.last_known_overload}' -> '{current_overload}'")
            self.last_known_overload = current_overload

        if not ocr_points_active and self.manual_points_info:
            frame_result.points_info = self.manual_points_info

        if frame_result.wave_results:
            classifications = [res.is_high_frequency for res in frame_result.wave_results.values()]
            frame_result.overall_is_hf = sum(classifications) > len(classifications) / 2 if classifications else False
            frame_result.avg_energy_ratio = np.mean([res.energy_ratio for res in frame_result.wave_results.values()]) if classifications else 0.0
            frame_result.avg_high_freq_energy = np.mean([res.high_freq_energy for res in frame_result.wave_results.values()]) if classifications else 0.0
        
        return frame_result, all_rois, ocr_diagnostics

    def _handle_logging(self, frame_result: FrameAnalysisResult, all_rois: Dict[str, np.ndarray], ocr_diagnostics: Dict[str, np.ndarray]):
        if not frame_result.wave_results:
            return

        has_changed = (frame_result.avg_energy_ratio is not None and (self.last_logged_ratio is None or 
                       not np.isclose(frame_result.avg_energy_ratio, self.last_logged_ratio, atol=1e-9) or
                       not np.isclose(frame_result.avg_high_freq_energy, self.last_logged_energy, atol=1e-9)))

        if has_changed:
            if self.verbose_logging_enabled: 
                logger.info(f"WAVE EVENT: R: {frame_result.avg_energy_ratio:.3e}, S: {frame_result.status_text}, O: {frame_result.overload_text}, P: {frame_result.points_info.hammer_point}/{frame_result.points_info.response_point}")
            self.last_logged_ratio, self.last_logged_energy = frame_result.avg_energy_ratio, frame_result.avg_high_freq_energy
            
            points = frame_result.points_info
            counter_key = f"{points.hammer_point}{points.response_point}"
            current_hit = self.hit_counters.get(counter_key, 0) + 1
            self.hit_counters[counter_key] = current_hit
            
            for wave_name, wave_result in frame_result.wave_results.items():
                base_filename = f"{wave_name}_{counter_key}_{current_hit}"
                if self.image_logging_enabled: 
                    self._create_visual_logs(wave_result, frame_result, wave_name, base_filename, all_rois, ocr_diagnostics)
                if self.data_log_options.log_mat: 
                    self._save_mat_log(wave_result, frame_result, wave_name, base_filename)
                if self.data_log_options.log_unv: 
                    self._save_unv_log(wave_result, frame_result, wave_name, base_filename)

    def _run_ocr(self, roi: np.ndarray, psm: int = 7, whitelist: Optional[str] = None, load_dawgs: bool = True) -> Tuple[str, np.ndarray]:
        """Runs OCR with advanced preprocessing and returns text + diagnostic image."""
        try:
            if roi.size == 0: return "", np.array([])
            scale_factor = 3
            width = int(roi.shape[1] * scale_factor)
            height = int(roi.shape[0] * scale_factor)
            resized = cv2.resize(roi, (width, height), interpolation=cv2.INTER_CUBIC)

            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            
            contrast_enhanced = self.clahe.apply(gray)
            sharpened = cv2.filter2D(contrast_enhanced, -1, self.sharpen_kernel)
            thresh = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            
            custom_config = f'--oem 3 --psm {psm}'
            if whitelist:
                custom_config += f' -c tessedit_char_whitelist={whitelist}'
            if not load_dawgs:
                custom_config += ' -c load_system_dawg=false -c load_freq_dawg=false'
                
            text = pytesseract.image_to_string(thresh, config=custom_config)
            return text.strip(), thresh
        except Exception as e:
            logger.error(f"OCR failed: {e}"); return "", np.array([])

    def _analyze_status(self, roi: np.ndarray) -> Tuple[str, np.ndarray]:
        text, diag_img = self._run_ocr(roi, psm=7)
        if "Waiting" in text: return "Waiting for Trigger...", diag_img
        if "Measuring" in text: return "Measuring...", diag_img
        if "Ready" in text: return "Ready", diag_img
        mean_color = np.mean(roi, axis=(0, 1))
        if mean_color[1] > 100: return "Measuring... (color)", diag_img
        if mean_color[2] > 100 and mean_color[1] < 100: return "Measuring... (orange-ish, color)", diag_img
        return "Ready (color)", diag_img
        
    def _analyze_overload(self, roi: np.ndarray) -> Tuple[str, np.ndarray]:
        whitelist = '0123456789ChannelinOverd '
        text, diag_img = self._run_ocr(roi, psm=7, whitelist=whitelist)
        mean_color = np.mean(roi, axis=(0, 1))
        if mean_color[2] > 150 and mean_color[1] < 100:
            match = re.search(r'(\d+)\s+Channel', text, re.IGNORECASE)
            return (f"{match.group(1)} Channel in Overload" if match else "Channel in Overload"), diag_img
        return "No Overload", diag_img

    def _analyze_run(self, roi: np.ndarray) -> Tuple[Optional[str], np.ndarray]:
        whitelist = 'Run 0123456789'
        text, diag_img = self._run_ocr(roi, psm=7, whitelist=whitelist, load_dawgs=False)
        run_match = re.search(r'Run\s*(\d+)', text, re.IGNORECASE)
        run_str = f"Run {run_match.group(1)}" if run_match else None
        return run_str, diag_img

    def _analyze_point_and_dir(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        """Analyzes a 'hammer' or 'response' ROI by splitting it."""
        point, dir = None, None
        diag_imgs = {}
        try:
            width = roi.shape[1]
            point_roi = roi[:, :int(width * 0.7)]
            dir_roi = roi[:, int(width * 0.7):]

            point_whitelist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789 '
            point_text, point_diag = self._run_ocr(point_roi, psm=7, whitelist=point_whitelist, load_dawgs=False)
            point_regex = r'([A-Z])\s*:\s*(\d+)'
            point_match = re.search(point_regex, point_text, re.IGNORECASE)
            if point_match:
                point = f"{point_match.group(1).upper()}{point_match.group(2)}"
            diag_imgs[f"{name}_point"] = point_diag

            dir_whitelist = '+-XYZ'
            dir_text, dir_diag = self._run_ocr(dir_roi, psm=7, whitelist=dir_whitelist, load_dawgs=False)
            dir_regex = r'([+\-][XYZ])'
            dir_match = re.search(dir_regex, dir_text, re.IGNORECASE)
            if dir_match:
                dir = dir_match.group(1).upper()
            diag_imgs[f"{name}_dir"] = dir_diag
            
        except Exception as e:
            logger.error(f"Failed to parse point/dir for {name}: {e}")
            
        return point, dir, diag_imgs

    def _validate_signal_quality(self, color_mask: np.ndarray) -> bool:
        height, width = color_mask.shape
        if height == 0 or width == 0: return False
        total_pixels = height * width
        if total_pixels == 0: return False
        signal_pixels = np.count_nonzero(color_mask)
        coverage_ratio = signal_pixels / total_pixels
        if not (0.0005 < coverage_ratio < 0.4):
            if self.verbose_logging_enabled: logger.debug(f"Skip: Signal coverage {coverage_ratio:.3e} out of range.")
            return False
        cols_with_signal = np.count_nonzero(np.sum(color_mask, axis=0) > 0)
        continuity_ratio = cols_with_signal / width
        if continuity_ratio < 0.15:
            if self.verbose_logging_enabled: logger.debug(f"Skip: Signal continuity {continuity_ratio:.3e} too low.")
            return False
        return True

    def _analyze_wave_pattern(self, roi: np.ndarray) -> Optional[WaveAnalysisResult]:
        if roi.size == 0: return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(self.app_config.hsv_lower), np.array(self.app_config.hsv_upper))
        if not self._validate_signal_quality(mask): return None
        y_coords, x_coords = np.nonzero(mask); width = mask.shape[1]
        if len(x_coords) == 0: signal_vector = np.full(width, roi.shape[0] / 2)
        else:
            unique_x, anchor_y_idx = np.unique(x_coords, return_inverse=True)
            sum_y = np.bincount(anchor_y_idx, weights=y_coords); count_y = np.bincount(anchor_y_idx)
            anchor_y = sum_y / count_y
            if len(unique_x) < 2: signal_vector = np.full(width, anchor_y[0] if anchor_y.size > 0 else roi.shape[0] / 2)
            else: signal_vector = np.interp(np.arange(width), unique_x, anchor_y)
        signal_vector = roi.shape[0] - signal_vector
        if signal_vector.size < 2: return None
        N = len(signal_vector); detrended_signal = signal_vector - np.mean(signal_vector)
        yf, xf = rfft(detrended_signal), rfftfreq(N, 1); fft_mags = np.abs(yf)
        total_energy = np.sum(fft_mags**2)
        high_freq_energy, energy_ratio, is_hf = 0, 0, False
        if total_energy > 1e-9:
            cutoff_indices = np.where(xf >= self.app_config.fft_cutoff_frequency)[0]
            if cutoff_indices.size > 0:
                high_freq_energy = np.sum(fft_mags[cutoff_indices[0]:]**2)
                energy_ratio = high_freq_energy / total_energy
            is_hf = energy_ratio > self.app_config.fft_energy_ratio_threshold
        return WaveAnalysisResult(is_hf, energy_ratio, high_freq_energy, signal_vector, xf, fft_mags, roi.copy(), mask.copy(), total_energy)

    def _create_visual_logs(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, wave_name: str, base_filename: str, all_rois: Dict[str, np.ndarray], ocr_diagnostics: Dict[str, np.ndarray]):
        try:
            region = frame_result.active_regions[wave_name]
            full_base_filename = f"image_logs/{base_filename}"
            
            title_info = (f"{frame_result.points_info.run} | H: {frame_result.points_info.hammer_point}{frame_result.points_info.hammer_dir} "
                          f"R: {frame_result.points_info.response_point}{frame_result.points_info.response_dir} | "
                          f"Overload: {frame_result.overload_text}")

            if self.image_log_options.include_screenshot:
                for r_name, r_img in all_rois.items():
                    try:
                        r_type = self.app_config.regions[r_name].roi_type
                        if r_type == 'wave':
                            cv2.imwrite(f"{full_base_filename}_01_ROI_{r_name}_{r_type}.jpg", r_img)
                    except KeyError:
                        logger.warning(f"Could not find region config for '{r_name}' during image logging.")
                    except Exception as e:
                        logger.error(f"Failed to save ROI image for '{r_name}': {e}")

            if self.image_log_options.include_color_filter: 
                cv2.imwrite(f"{full_base_filename}_02_Mask.jpg", wave_result.color_mask)
            
            if self.image_log_options.include_signal_plot:
                num_points = len(wave_result.signal_vector)
                freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
                amp_axis = region.y_axis_min + (wave_result.signal_vector / region.height) * (region.y_axis_max - region.y_axis_min)
                fig, ax = plt.subplots(figsize=(10, 5), dpi=150); fig.patch.set_facecolor('#1E1E1E')
                ax.plot(freq_axis, amp_axis, color='cyan')
                ax.set_title(f'Reconstructed Signal - {wave_name}\n{title_info}', color='white', fontsize=10)
                ax.set_xlabel('Frequency (Hz)', color='white'); ax.set_ylabel(f'Amplitude ({region.y_axis_unit})', color='white')
                ax.set_facecolor('#2E2E2E'); ax.tick_params(axis='both', colors='white'); ax.grid(True, linestyle='--', alpha=0.3)
                fig.tight_layout(rect=[0, 0, 1, 0.95]); fig.savefig(f"{full_base_filename}_03_Signal.png", facecolor=fig.get_facecolor()); plt.close(fig)
            
            if self.image_log_options.include_fft_plot:
                fig, ax = plt.subplots(figsize=(10, 5), dpi=150); fig.patch.set_facecolor('#1E1E1E')
                ax.plot(wave_result.fft_freqs, wave_result.fft_mags, color='magenta')
                
                # Enhanced title with FFT analysis information
                fft_info = (f'Total Energy: {wave_result.total_energy:.3e} | '
                           f'HF Energy: {wave_result.high_freq_energy:.3e} | '
                           f'HF Ratio: {wave_result.energy_ratio:.3e} | '
                           f'Cutoff: {self.app_config.fft_cutoff_frequency:.3f}')
                ax.set_title(f'FFT Magnitude Spectrum - {wave_name}\n{title_info}\n{fft_info}', 
                            color='white', fontsize=9)
                
                ax.axvline(x=self.app_config.fft_cutoff_frequency, color='yellow', linestyle='--', 
                          linewidth=1, label=f'Cutoff: {self.app_config.fft_cutoff_frequency:.2f}')
                ax.set_xlim(left=0, right=0.5); ax.set_xlabel('Normalized Frequency', color='white')
                ax.set_ylabel('Magnitude (A.U.)', color='white')
                ax.set_facecolor('#2E2E2E'); ax.tick_params(axis='both', colors='white')
                ax.grid(True, linestyle='--', alpha=0.3); ax.legend(labelcolor='white')
                
                # Add text box with analysis parameters
                textstr = (f'Threshold: {self.app_config.fft_energy_ratio_threshold:.4f}\n'
                          f'Classification: {"HF" if wave_result.is_high_frequency else "LF"}')
                props = dict(boxstyle='round', facecolor='#2E2E2E', alpha=0.8, edgecolor='white')
                ax.text(0.98, 0.97, textstr, transform=ax.transAxes, fontsize=9,
                       verticalalignment='top', horizontalalignment='right', 
                       bbox=props, color='white')
                
                fig.tight_layout(rect=[0, 0, 1, 0.95])
                fig.savefig(f"{full_base_filename}_04_FFT.png", facecolor=fig.get_facecolor())
                plt.close(fig)
        except Exception as e: logger.error(f"Failed to create visual logs for {wave_name}: {e}")
    
    def _save_mat_log(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, wave_name: str, base_filename: str):
        try:
            filename = f"signal_logs/{base_filename}.mat"
            region = frame_result.active_regions[wave_name]; num_points = len(wave_result.signal_vector)
            frequency_hz = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            amplitude_scaled = region.y_axis_min + (wave_result.signal_vector / region.height) * (region.y_axis_max - region.y_axis_min)
            points = frame_result.points_info
            mat_data = {
                'frequency_hz': frequency_hz, 'amplitude': amplitude_scaled, 'amplitude_units': region.y_axis_unit,
                'info_region_name': wave_name, 'info_hf_ratio': wave_result.energy_ratio, 'raw_amplitude_pixels': wave_result.signal_vector,
                'meta_run': points.run, 'meta_hammer_point': points.hammer_point, 'meta_hammer_dir': points.hammer_dir,
                'meta_response_point': points.response_point, 'meta_response_dir': points.response_dir,
                'meta_overload_status': frame_result.overload_text,
                'fft_total_energy': wave_result.total_energy, 'fft_high_freq_energy': wave_result.high_freq_energy,
                'fft_cutoff_frequency': self.app_config.fft_cutoff_frequency
            }
            scipy.io.savemat(filename, mat_data)
        except Exception as e: logger.error(f"Failed to save .mat file for {wave_name}: {e}")

    def _save_unv_log(self, wave_result: WaveAnalysisResult, frame_result: FrameAnalysisResult, wave_name: str, base_filename: str):
        """Save data in proper Dataset 58 format with complete header structure."""
        def parse_point(point_str: str) -> int: 
            """Extract numeric node number from point string like 'P1' or 'A3'."""
            return int(re.sub(r'\D', '', point_str)) if point_str and re.sub(r'\D', '', point_str) else 1
        
        def parse_dof(dir_str: str) -> int: 
            """Convert direction string like '+Z' or '-X' to DOF number (1=X, 2=Y, 3=Z)."""
            if not dir_str or len(dir_str) < 1:
                return 3
            last_char = dir_str.upper()[-1]
            return {'X': 1, 'Y': 2, 'Z': 3}.get(last_char, 3)
        
        try:
            filename = f"signal_logs/{base_filename}.unv"
            region = frame_result.active_regions[wave_name]
            points = frame_result.points_info
            num_points = len(wave_result.signal_vector)
            
            if num_points < 2:
                logger.warning(f"Skipping UNV log for {wave_name}: insufficient data points ({num_points})")
                return
            
            # Calculate frequency parameters
            start_freq = region.x_axis_min
            freq_step = (region.x_axis_max - region.x_axis_min) / (num_points - 1) if num_points > 1 else 0
            
            # Scale amplitude to physical units
            amplitude_scaled = region.y_axis_min + (wave_result.signal_vector / region.height) * (region.y_axis_max - region.y_axis_min)
            
            # Parse node and DOF information
            resp_node = parse_point(points.response_point)
            resp_dof = parse_dof(points.response_dir)
            ref_node = parse_point(points.hammer_point)
            ref_dof = parse_dof(points.hammer_dir)
            
            # Get current timestamp
            timestamp = datetime.now().strftime('%d-%b-%y %H:%M:%S')
            
            with open(filename, 'w') as f:
                # Line 1: Record separator (80 characters)
                f.write(f"    -1{' ' * 74}\n")
                
                # Line 2: Dataset type (80 characters)
                f.write(f"    58{' ' * 74}\n")
                
                # Lines 3-7: Identification lines (5 lines, each 80 characters max)
                # ID Line 1: Function type identification
                id_line1 = f"FRF for {points.response_point}:{points.response_dir}/{points.hammer_point}:{points.hammer_dir}"
                f.write(f"{id_line1[:80]:<80}\n")
                
                # ID Line 2: Program identification
                id_line2 = "USMA v0.4.3 - Screen Reconstruction"
                f.write(f"{id_line2[:80]:<80}\n")
                
                # ID Line 3: Date/Time
                f.write(f"{timestamp:<80}\n")
                
                # ID Line 4: Load case identification
                id_line4 = f"Reconstructed from {points.run}, region \"{wave_name}\""
                f.write(f"{id_line4[:80]:<80}\n")
                
                # ID Line 5: Response entity name
                dir_char = {1: 'X', 2: 'Y', 3: 'Z'}.get(resp_dof, 'Z')
                id_line5 = f"FRF\\\\{points.response_point}:+{dir_char}"
                f.write(f"{id_line5[:80]:<80}\n")
                
                # Header Record 1: DOF Identification (CRITICAL - Fixed width format!)
                # FORMAT: 2(I5,I10,I5,I10),3A10,3I5
                func_type = 4      # Frequency response function
                func_id = 0        # Function ID number
                version = 0        # Version number
                load_case = 0      # Load case ID
                resp_node_name = "C"    # Response node name
                ref_node_name = "C"     # Reference node name
                
                dof_line = (
                    f"{func_type:5d}"           # Function type (I5)
                    f"{func_id:10d}"            # Function ID (I10)
                    f"{version:5d}"             # Version (I5)
                    f"{load_case:10d}"          # Load case (I10)
                    f"{resp_node_name:10s}"     # Response node name (A10)
                    f"{resp_node:10d}"          # Response node number (I10)
                    f"{resp_dof:5d}"            # Response direction (I5)
                    f"{ref_node_name:10s}"      # Reference node name (A10)
                    f"{ref_node:10d}"           # Reference node number (I10)
                    f"{ref_dof:5d}\n"           # Reference direction (I5)
                )
                f.write(dof_line)
                
                # Header Record 2: Data Form
                # FORMAT: 3I10,3E13.5
                data_type = 2      # Real and Imaginary
                spacing = 1        # Even spacing
                z_value = 0.0      # Z-axis value (for 3D plots)
                
                f.write(
                    f"{data_type:10d}"          # Ordinate data type (I10)
                    f"{num_points:10d}"         # Number of data pairs (I10)
                    f"{spacing:10d}"            # Abscissa spacing (I10)
                    f"{start_freq:13.5E}"       # Abscissa minimum (E13.5)
                    f"{freq_step:13.5E}"        # Abscissa increment (E13.5)
                    f"{z_value:13.5E}\n"        # Z-axis value (E13.5)
                )
                
                # Header Records 3-6: Axis Labels
                # FORMAT: I10,3I5,2A20
                f.write(f"{18:10d}{0:5d}{0:5d}{0:5d}{'X-axis':20s}{'Hz':20s}\n")
                f.write(f"{12:10d}{0:5d}{0:5d}{0:5d}{'Y-axis':20s}{region.y_axis_unit:20s}\n")
                f.write(f"{13:10d}{0:5d}{0:5d}{0:5d}{'Z-axis':20s}{'NONE':20s}\n")
                f.write(f"{0:10d}{0:5d}{0:5d}{0:5d}{'NONE':20s}{'NONE':20s}\n")
                
                # Data Section: Real and Imaginary pairs
                # FORMAT: 2E13.5 per line
                # NOTE: For real-only data (from screen reconstruction), imaginary part is zero
                for val in amplitude_scaled:
                    real_part = val
                    imag_part = 0.0
                    f.write(f"  {real_part:13.6E}  {imag_part:13.6E}\n")
                
                # Record terminator
                f.write("    -1\n")
                
            logger.info(f"UNV file saved: {filename} (Dataset 58 format compliant)")
            
        except Exception as e: 
            logger.error(f"Failed to save .unv file for {wave_name}: {e}")

# --- 4. VISUALIZATION & CONFIGURATION ---
class RegionOverlay(tk.Toplevel):
    def __init__(self, parent, config_path):
        super().__init__(parent); self.config_path = config_path
        self.attributes("-transparentcolor", "white", "-topmost", True); self.overrideredirect(True)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        try:
            with open(self.config_path, 'r') as f: data = json.load(f)
            canvas = tk.Canvas(self, bg="white", highlightthickness=0); canvas.pack(fill=tk.BOTH, expand=True)
            colors = {"wave": "#3498db", "status": "#2ecc71", "overload": "#e74c3c", 
                      "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
            for name, region_data in data.items():
                if not name.startswith('_') and region_data.get('enabled', True):
                    x,y,w,h = region_data['x'], region_data['y'], region_data['width'], region_data['height']
                    color = colors.get(region_data.get('roi_type', 'wave'), "#95a5a6")
                    canvas.create_rectangle(x-5, y-5, x+w+5, y+h+5, outline=color, width=2)
                    canvas.create_text(x-5, y-5, text=name, anchor="sw", font=("Arial", 10, "bold"), fill=color)
            canvas.create_text(self.winfo_screenwidth()-10, self.winfo_screenheight()-10, text=f"Config: {os.path.basename(self.config_path)}", anchor="se", fill="#333")
        except Exception as e: logger.error(f"Overlay Error: {e}"); self.destroy()

class ConfigToolWindow(tk.Toplevel):
    def __init__(self, parent, main_root):
        super().__init__(parent); self.title("Advanced Region & Color Configuration Tool"); self.main_root = main_root
        self.app_config = AppConfig(); self.screenshot = None; self.photo = None; self.scale = 1.0
        self.drawing, self.start_x, self.start_y, self.selected_region_name = False, 0, 0, None
        self.resize_timer = None; self.x_offset, self.y_offset = 0, 0; self.state('zoomed') 
        self._setup_gui(); self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.after(200, self._take_screenshot)
    def _on_closing(self): self.main_root.deiconify(); self.destroy()
    def _setup_gui(self):
        toolbar = ttk.Frame(self, padding=5); toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(toolbar, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Load Config", command=self._load_config).pack(side=tk.LEFT, padx=2)
        main_frame = ttk.Frame(self, padding=5); main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas_frame = ttk.LabelFrame(main_frame, text="Screenshot Preview"); canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.canvas = tk.Canvas(canvas_frame, bg="black"); self.canvas.pack(fill=tk.BOTH, expand=True)
        right_panel = ttk.Frame(main_frame, width=450); right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5); right_panel.pack_propagate(False)
        ttk.Button(ttk.LabelFrame(right_panel, text="Capture"), text="Take Screenshot", command=self._take_screenshot).pack(pady=5, padx=5, fill=tk.X)
        list_frame = ttk.LabelFrame(right_panel, text="Defined Regions"); list_frame.pack(fill=tk.X, pady=5)
        self.region_listbox = tk.Listbox(list_frame, height=6, exportselection=False); self.region_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.region_listbox.yview); list_scroll.pack(side=tk.RIGHT, fill=tk.Y); self.region_listbox.config(yscrollcommand=list_scroll.set)
        editor_frame = ttk.LabelFrame(right_panel, text="Region Editor"); editor_frame.pack(fill=tk.X, pady=5)
        self.editor_vars = {'name': tk.StringVar(),'x': tk.IntVar(),'y': tk.IntVar(),'width': tk.IntVar(),'height': tk.IntVar(),'roi_type': tk.StringVar(),'enabled': tk.BooleanVar(), 'x_axis_min': tk.DoubleVar(), 'x_axis_max': tk.DoubleVar(),'y_axis_min': tk.DoubleVar(), 'y_axis_max': tk.DoubleVar(), 'y_axis_unit': tk.StringVar(),'resp_node': tk.IntVar(), 'resp_dof': tk.IntVar(), 'ref_node': tk.IntVar(), 'ref_dof': tk.IntVar()}
        
        f1 = ttk.Frame(editor_frame); f1.pack(fill=tk.X, pady=2)
        ttk.Label(f1, text="Name:", width=12).pack(side=tk.LEFT, padx=5); ttk.Entry(f1, textvariable=self.editor_vars['name']).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Label(f1, text="Type:").pack(side=tk.LEFT, padx=5); ttk.Combobox(f1, textvariable=self.editor_vars['roi_type'], values=['wave', 'status', 'overload', 'run', 'hammer', 'response'], state='readonly', width=8).pack(side=tk.LEFT, padx=5)
        
        f_geom = ttk.Frame(editor_frame); f_geom.pack(fill=tk.X, pady=2)
        ttk.Label(f_geom, text="x:").grid(row=0, column=0, padx=5); ttk.Entry(f_geom, textvariable=self.editor_vars['x'], width=6).grid(row=0, column=1)
        ttk.Label(f_geom, text="y:").grid(row=0, column=2, padx=5); ttk.Entry(f_geom, textvariable=self.editor_vars['y'], width=6).grid(row=0, column=3)
        ttk.Label(f_geom, text="w:").grid(row=0, column=4, padx=5); ttk.Entry(f_geom, textvariable=self.editor_vars['width'], width=6).grid(row=0, column=5)
        ttk.Label(f_geom, text="h:").grid(row=0, column=6, padx=5); ttk.Entry(f_geom, textvariable=self.editor_vars['height'], width=6).grid(row=0, column=7)

        f_scale = ttk.LabelFrame(editor_frame, text="Physical Axis Scaling (for 'wave' regions)"); f_scale.pack(fill=tk.X, pady=5, padx=5)
        g = ttk.Frame(f_scale); g.pack(fill=tk.X); ttk.Label(g, text="X-Min (Hz):").grid(row=0, column=0, sticky=tk.W); ttk.Entry(g, textvariable=self.editor_vars['x_axis_min'], width=10).grid(row=0, column=1, padx=5); ttk.Label(g, text="X-Max (Hz):").grid(row=0, column=2, sticky=tk.W, padx=5); ttk.Entry(g, textvariable=self.editor_vars['x_axis_max'], width=10).grid(row=0, column=3, padx=5)
        g2 = ttk.Frame(f_scale); g2.pack(fill=tk.X); ttk.Label(g2, text="Y-Min:").grid(row=0, column=0, sticky=tk.W); ttk.Entry(g2, textvariable=self.editor_vars['y_axis_min'], width=10).grid(row=0, column=1, padx=5); ttk.Label(g2, text="Y-Max:").grid(row=0, column=2, sticky=tk.W, padx=5); ttk.Entry(g2, textvariable=self.editor_vars['y_axis_max'], width=10).grid(row=0, column=3, padx=5)
        f_unv = ttk.LabelFrame(editor_frame, text="UNV/.mat Metadata (for 'wave' regions)"); f_unv.pack(fill=tk.X, pady=5, padx=5)
        g3 = ttk.Frame(f_unv); g3.pack(fill=tk.X); ttk.Label(g3, text="Y-Axis Unit:").grid(row=0, column=0); ttk.Entry(g3, textvariable=self.editor_vars['y_axis_unit'], width=10).grid(row=0, column=1, padx=5);
        f_buttons = ttk.Frame(editor_frame); f_buttons.pack(fill=tk.X, pady=5)
        ttk.Checkbutton(f_buttons, text="Enabled", variable=self.editor_vars['enabled']).pack(side=tk.LEFT, padx=10)
        ttk.Button(f_buttons, text="Update Region", command=self._update_region_from_editor).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(f_buttons, text="Delete Region", command=self._delete_selected_region).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        params_frame = ttk.LabelFrame(right_panel, text="Global FFT Analysis Parameters"); params_frame.pack(fill=tk.X, pady=5)
        self.param_vars = {'fft_cutoff_frequency': tk.DoubleVar(value=self.app_config.fft_cutoff_frequency), 'fft_energy_ratio_threshold': tk.DoubleVar(value=self.app_config.fft_energy_ratio_threshold)}
        g_fft = ttk.Frame(params_frame); g_fft.pack(fill=tk.X, pady=2)
        ttk.Label(g_fft, text="Cutoff Freq:").grid(row=0, column=0); ttk.Spinbox(g_fft, from_=0.0, to=0.5, increment=0.01, textvariable=self.param_vars['fft_cutoff_frequency'], width=8).grid(row=0, column=1, padx=5)
        ttk.Label(g_fft, text="Energy Ratio:").grid(row=0, column=2); ttk.Spinbox(g_fft, from_=0.0, to=1.0, increment=0.001, textvariable=self.param_vars['fft_energy_ratio_threshold'], width=8).grid(row=0, column=3, padx=5)
        ttk.Button(params_frame, text="Apply Global Parameters", command=self._apply_params).pack(fill=tk.X, pady=5)
        self.canvas.bind("<Button-1>", self._on_canvas_click); self.canvas.bind("<B1-Motion>", self._update_selection); self.canvas.bind("<ButtonRelease-1>", self._end_selection); self.region_listbox.bind("<<ListboxSelect>>", self._on_listbox_select); self.canvas.bind("<Configure>", self._on_canvas_resize)
    def _on_canvas_resize(self, event):
        if self.resize_timer: self.after_cancel(self.resize_timer)
        self.resize_timer = self.after(150, self._redraw_canvas_content)
    def _take_screenshot(self):
        self.withdraw(); self.main_root.iconify(); time.sleep(0.5)
        self.screenshot = cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2BGR)
        self.deiconify(); self.lift(); self.focus_force(); self._redraw_canvas_content()
    def _redraw_canvas_content(self):
        if self.screenshot is None: return
        canvas_w, canvas_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if canvas_w < 2 or canvas_h < 2: return
        self.canvas.delete("all"); img_h, img_w = self.screenshot.shape[:2]
        self.scale = min(canvas_w / img_w, canvas_h / img_h)
        disp_w, disp_h = int(img_w * self.scale), int(img_h * self.scale)
        img_resized = Image.fromarray(cv2.cvtColor(self.screenshot, cv2.COLOR_BGR2RGB)).resize((disp_w, disp_h), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image=img_resized)
        self.x_offset, self.y_offset = (canvas_w - disp_w) // 2, (canvas_h - disp_h) // 2
        self.canvas.create_image(self.x_offset, self.y_offset, image=self.photo, anchor=tk.NW, tags="screenshot")
        self._redraw_regions_on_canvas()
    def _on_canvas_click(self, event): self.drawing, self.start_x, self.start_y = True, event.x, event.y; self.canvas.delete("selection_rect")
    def _update_selection(self, event):
        if self.drawing: self.canvas.delete("selection_rect"); self.canvas.create_rectangle(self.start_x, self.start_y, event.x, event.y, outline="red", width=2, tags="selection_rect")
    def _end_selection(self, event):
        if not self.drawing: return
        self.drawing = False; x1_c, y1_c = min(self.start_x, event.x), min(self.start_y, event.y)
        x2_c, y2_c = max(self.start_x, event.x), max(self.start_y, event.y)
        x1, y1 = int((x1_c - self.x_offset) / self.scale), int((y1_c - self.y_offset) / self.scale)
        x2, y2 = int((x2_c - self.x_offset) / self.scale), int((y2_c - self.y_offset) / self.scale)
        name = f"region_{len(self.app_config.regions)+1}"
        new_region = MonitoringRegion(name=name, x=x1, y=y1, width=x2-x1, height=y2-y1, roi_type='wave')
        self.app_config.regions[name] = new_region; self.canvas.delete("selection_rect"); self._update_ui_from_data()
        new_idx = sorted(self.app_config.regions.keys()).index(name)
        self.region_listbox.selection_clear(0, tk.END); self.region_listbox.selection_set(new_idx); self.region_listbox.activate(new_idx)
        self._on_listbox_select(None)
    def _apply_params(self): self.app_config.fft_cutoff_frequency=self.param_vars['fft_cutoff_frequency'].get(); self.app_config.fft_energy_ratio_threshold=self.param_vars['fft_energy_ratio_threshold'].get(); messagebox.showinfo("Success", "Analysis parameters updated.", parent=self)
    def _update_ui_from_data(self):
        sel_name = self.selected_region_name
        sel_idx = -1
        if sel_name:
            try: sel_idx = sorted(self.app_config.regions.keys()).index(sel_name)
            except ValueError: sel_name = None

        self.region_listbox.delete(0, tk.END)
        for i, name in enumerate(sorted(self.app_config.regions.keys())):
            disp = f"{name}" if self.app_config.regions[name].enabled else f"{name} (Disabled)"
            self.region_listbox.insert(tk.END, disp)
        
        if sel_idx != -1: self.region_listbox.selection_set(sel_idx)

        self.param_vars['fft_cutoff_frequency'].set(self.app_config.fft_cutoff_frequency); self.param_vars['fft_energy_ratio_threshold'].set(self.app_config.fft_energy_ratio_threshold); self._redraw_regions_on_canvas()
    def _redraw_regions_on_canvas(self):
        self.canvas.delete("region"); colors = {"wave": "#3498db", "status": "#2ecc71", "overload": "#e74c3c", "run": "#f1c40f", "hammer": "#f39c12", "response": "#e67e22"}
        if not hasattr(self, 'x_offset'): return 
        for name, r in self.app_config.regions.items():
            x1, y1 = r.x * self.scale + self.x_offset, r.y * self.scale + self.y_offset
            x2, y2 = (r.x + r.width) * self.scale + self.x_offset, (r.y + r.height) * self.scale + self.y_offset
            color = colors.get(r.roi_type,"white") if r.enabled else "gray"
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2, tags=("region", name)); self.canvas.create_text(x1+5, y1+5, text=name, fill=color, anchor="nw", tags=("region", name))
    
    def _on_listbox_select(self, _):
        if not self.region_listbox.curselection(): return
        self.selected_region_name = self.region_listbox.get(self.region_listbox.curselection()).replace(" (Disabled)", "")
        region_data = self.app_config.regions[self.selected_region_name]
        for key, var in self.editor_vars.items(): 
            if hasattr(region_data, key): var.set(getattr(region_data, key))

    def _update_region_from_editor(self):
        if not self.selected_region_name: return messagebox.showerror("Error", "No region selected.", parent=self)
        old_name, new_name = self.selected_region_name, self.editor_vars['name'].get()
        if new_name != old_name and new_name in self.app_config.regions: return messagebox.showerror("Error", "Region name must be unique.", parent=self)
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
        if not self.selected_region_name: return messagebox.showerror("Error", "No region selected.", parent=self)
        if messagebox.askyesno("Confirm Delete", f"Delete '{self.selected_region_name}'?", parent=self):
            del self.app_config.regions[self.selected_region_name]; self.selected_region_name = None
            for key, var in self.editor_vars.items():
                if isinstance(var, (tk.IntVar, tk.DoubleVar)): var.set(0)
                elif isinstance(var, tk.BooleanVar): var.set(False)
                else: var.set("")
            self._update_ui_from_data()
    def _save_config(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialdir="configs", parent=self)
        if not path: return
        try:
            self._apply_params(); data = {n: asdict(r) for n, r in self.app_config.regions.items()}
            data['_metadata'] = {'hsv_lower': self.app_config.hsv_lower, 'hsv_upper': self.app_config.hsv_upper, 'screenshot_interval': self.app_config.screenshot_interval, 'fft_cutoff_frequency': self.app_config.fft_cutoff_frequency, 'fft_energy_ratio_threshold': self.app_config.fft_energy_ratio_threshold}
            with open(path, 'w') as f: json.dump(data, f, indent=2)
            messagebox.showinfo("Success", f"Saved to {os.path.basename(path)}", parent=self)
        except Exception as e: messagebox.showerror("Error", f"Failed to save: {e}", parent=self)
    def _load_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], initialdir="configs", parent=self)
        if not path: return
        try:
            self.app_config = ScreenMonitor(path).app_config; self._update_ui_from_data()
            if self.screenshot: self._redraw_regions_on_canvas()
            messagebox.showinfo("Success", f"Loaded {os.path.basename(path)}", parent=self)
        except Exception as e: messagebox.showerror("Error", f"Failed to load: {e}", parent=self)

# --- 5. MAIN GUI: THE CENTRAL CONTROL APPLICATION ---
class MonitorControlGUI:
    def __init__(self, root):
        self.root = root; self.root.title("USMA v.0.4.3 (Release)"); self.root.geometry("850x550")
        self.config_path = tk.StringVar(value="configs/default_config.json")
        self.is_monitoring, self.is_overlay_on = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.verbose_logging_on, self.image_logging_on = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self.log_opt_screenshot, self.log_opt_color_filter = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.log_opt_signal_plot, self.log_opt_fft_plot = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.audio_feedback_on = tk.BooleanVar(value=False)
        self.log_to_mat, self.log_to_unv = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.monitor = ScreenMonitor(self.config_path.get(), self.update_feedback_panel)
        self.manual_points_vars = {'run': tk.StringVar(value='Run 1'), 'hammer_point': tk.StringVar(value='P1'), 'hammer_dir': tk.StringVar(value='-Z'),'response_point': tk.StringVar(value='P1'), 'response_dir': tk.StringVar(value='-Z')}
        initial_freq = 1.0/self.monitor.app_config.screenshot_interval if self.monitor.app_config.screenshot_interval>0 else 4.0
        self.sample_frequency = tk.DoubleVar(value=round(initial_freq, 2))
        self.overlay = None; self._setup_main_gui(); self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
    
    def _setup_main_gui(self):
        frame = ttk.Frame(self.root, padding=10); frame.pack(fill=tk.BOTH, expand=True)
        config_frame = ttk.LabelFrame(frame, text="Configuration"); config_frame.pack(fill=tk.X, pady=5)
        ttk.Entry(config_frame, textvariable=self.config_path, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        self.load_button = ttk.Button(config_frame, text="Load...", command=self._load_config); self.load_button.pack(side=tk.LEFT, padx=5)
        self.edit_button = ttk.Button(config_frame, text="Edit Config...", command=self._launch_config_tool); self.edit_button.pack(side=tk.LEFT, padx=5)
        
        feedback_frame = ttk.LabelFrame(frame, text="Live Analysis Feedback"); feedback_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.status_light = tk.Canvas(feedback_frame, width=30, height=30, bg="gray", highlightthickness=0); self.status_light.grid(row=0, column=0, rowspan=4, padx=15, pady=5)
        self.class_var, self.hf_ratio_var, self.hf_energy_var = tk.StringVar(value="Overall: --"), tk.StringVar(value="Avg HF Ratio: --"), tk.StringVar(value="Avg HF Energy: --")
        self.status_var, self.overload_var, self.points_var = tk.StringVar(value="Status: --"), tk.StringVar(value="Overload: --"), tk.StringVar(value="Points: --")
        ttk.Label(feedback_frame, textvariable=self.class_var, font=("Segoe UI", 14)).grid(row=0, column=1, sticky=tk.W, padx=10)
        ttk.Label(feedback_frame, textvariable=self.hf_ratio_var, font=("Segoe UI", 10)).grid(row=1, column=1, sticky=tk.W, padx=10)
        ttk.Label(feedback_frame, textvariable=self.status_var, font=("Segoe UI", 10)).grid(row=2, column=1, sticky=tk.W, padx=10)
        ttk.Label(feedback_frame, textvariable=self.overload_var, font=("Segoe UI", 10)).grid(row=2, column=2, sticky=tk.W, padx=10)
        ttk.Label(feedback_frame, textvariable=self.points_var, font=("Segoe UI", 10)).grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=10)
        feedback_frame.columnconfigure(1, weight=1)

        bottom_panel = ttk.Frame(frame); bottom_panel.pack(fill=tk.X, pady=5)
        control_frame = ttk.LabelFrame(bottom_panel, text="Controls"); control_frame.pack(fill=tk.Y, side=tk.LEFT, pady=5)
        self.start_stop_button = ttk.Button(control_frame, text="Start Monitoring", command=self._toggle_monitoring); self.start_stop_button.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)
        self.overlay_check = ttk.Checkbutton(control_frame, text="Show Overlay", variable=self.is_overlay_on, command=self._toggle_overlay); self.overlay_check.pack(padx=10, pady=(0,10))
        params_frame = ttk.LabelFrame(control_frame, text="Parameters"); params_frame.pack(padx=5, pady=5, fill=tk.Y)
        freq_frame = ttk.Frame(params_frame); ttk.Label(freq_frame, text="Sample Freq (Hz):").pack(side=tk.LEFT, padx=(5,2)); self.freq_spinbox = ttk.Spinbox(freq_frame, from_=0.1, to=30.0, increment=0.1, textvariable=self.sample_frequency, width=6); self.freq_spinbox.pack(side=tk.LEFT, padx=(0,5)); freq_frame.pack(pady=5)
        self.audio_check = ttk.Checkbutton(params_frame, text="Audio Feedback", variable=self.audio_feedback_on, command=self._toggle_audio_feedback); self.audio_check.pack(anchor=tk.W, padx=5, pady=(0, 5))
        if not SOUND_DEVICE_AVAILABLE: self.audio_check.config(state=tk.DISABLED); self.audio_feedback_on.set(False)
        
        right_panel = ttk.Frame(bottom_panel); right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        self.manual_points_frame = ttk.LabelFrame(right_panel, text="Manual Points of Interest (POI) Entry"); self.manual_points_frame.pack(fill=tk.X)
        pf = self.manual_points_frame; ttk.Label(pf, text="Run:").grid(row=0, column=0, padx=5, pady=2); ttk.Entry(pf, textvariable=self.manual_points_vars['run'], width=8).grid(row=0, column=1)
        ttk.Label(pf, text="Hammer:").grid(row=0, column=2, padx=5); ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_point'], width=6).grid(row=0, column=3); ttk.Entry(pf, textvariable=self.manual_points_vars['hammer_dir'], width=4).grid(row=0, column=4)
        ttk.Label(pf, text="Response:").grid(row=1, column=2, padx=5); ttk.Entry(pf, textvariable=self.manual_points_vars['response_point'], width=6).grid(row=1, column=3); ttk.Entry(pf, textvariable=self.manual_points_vars['response_dir'], width=4).grid(row=1, column=4)
        
        logging_controls_frame = ttk.LabelFrame(right_panel, text="Logging"); logging_controls_frame.pack(fill=tk.X, pady=5)
        logging_main_frame = ttk.Frame(logging_controls_frame); logging_main_frame.pack(fill=tk.X, side=tk.LEFT, anchor=tk.N, padx=5)
        self.verbose_check = ttk.Checkbutton(logging_main_frame, text="Verbose Console Log", variable=self.verbose_logging_on); self.verbose_check.pack(anchor=tk.W, pady=2)
        self.img_log_check = ttk.Checkbutton(logging_main_frame, text="Enable Image Logs", variable=self.image_logging_on, command=self._toggle_img_log_options_state); self.img_log_check.pack(anchor=tk.W, pady=2)
        self.img_log_options_frame = ttk.Frame(logging_main_frame); self.img_log_options_frame.pack(fill=tk.X, pady=(5,0))
        for txt, var in [("ROI Screenshot",self.log_opt_screenshot), ("Color Filter Mask",self.log_opt_color_filter), ("Signal Plot",self.log_opt_signal_plot), ("FFT Plot",self.log_opt_fft_plot)]: 
            ttk.Checkbutton(self.img_log_options_frame, text=txt, variable=var).pack(anchor=tk.W, padx=15)
        data_logging_frame = ttk.Frame(logging_controls_frame); data_logging_frame.pack(fill=tk.X, side=tk.LEFT, padx=10, anchor=tk.N)
        ttk.Checkbutton(data_logging_frame, text="Log to .mat file", variable=self.log_to_mat).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(data_logging_frame, text="Log to .unv file", variable=self.log_to_unv).pack(anchor=tk.W, pady=2)

        self.status_label = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W); self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        self._toggle_img_log_options_state(); self._update_manual_points_state()

    def _toggle_audio_feedback(self): self.monitor.set_audio_feedback(self.audio_feedback_on.get())
    def _toggle_img_log_options_state(self):
        state = tk.NORMAL if self.image_logging_on.get() else tk.DISABLED
        for child in self.img_log_options_frame.winfo_children(): child.configure(state=state)

    def update_feedback_panel(self, result: FrameAnalysisResult): self.root.after(0, self._update_feedback_ui, result)
    def _update_feedback_ui(self, result: FrameAnalysisResult):
        if result.overall_is_hf is not None:
            self.class_var.set(f"Overall: {'HF' if result.overall_is_hf else 'LF'}")
            self.status_light.config(bg="red" if result.overall_is_hf else "green")
        if result.avg_energy_ratio is not None:
            self.hf_ratio_var.set(f"Avg HF Ratio: {result.avg_energy_ratio:.3e}")
        self.status_var.set(f"Status: {result.status_text}"); self.overload_var.set(f"Overload: {result.overload_text}")
        p = result.points_info; self.points_var.set(f"Points: {p.run} | Hammer: {p.hammer_point} {p.hammer_dir} | Response: {p.response_point} {p.response_dir}")
        
    def _reset_feedback_ui(self):
        self.class_var.set("Overall: --"); self.status_light.config(bg="gray"); self.hf_ratio_var.set("Avg HF Ratio: --")
        self.status_var.set("Status: --"); self.overload_var.set("Overload: --"); self.points_var.set("Points: --")
    
    def _update_manual_points_state(self):
        has_points_region = any(r.roi_type in ['run', 'hammer', 'response'] for r in self.monitor.app_config.regions.values() if r.enabled)
        state = tk.DISABLED if has_points_region else tk.NORMAL
        for child in self.manual_points_frame.winfo_children():
            if isinstance(child, (ttk.Entry, ttk.Label)): child.configure(state=state)

    def _load_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], initialdir="configs", title="Select Config")
        if not path: return
        self.config_path.set(path); self.monitor.update_config(path) 
        try: self.sample_frequency.set(round(1.0 / self.monitor.app_config.screenshot_interval, 2))
        except (ZeroDivisionError, TypeError, AttributeError): self.sample_frequency.set(4.0)
        if self.is_overlay_on.get(): self._toggle_overlay(); self._toggle_overlay()
        self.status_label.config(text=f"Loaded: {os.path.basename(path)}"); self._update_manual_points_state()
    
    def _launch_config_tool(self): self.root.iconify(); ConfigToolWindow(self.root, self.root).grab_set()
    def _toggle_monitoring(self):
        if self.is_monitoring.get():
            self.monitor.stop(); self.is_monitoring.set(False); self.start_stop_button.config(text="Start Monitoring")
            self.status_label.config(text="Stopped."); self._reset_feedback_ui()
            self.load_button.config(state=tk.NORMAL); self.edit_button.config(state=tk.NORMAL)
        else:
            if not os.path.exists(self.config_path.get()): return messagebox.showerror("Error", "Config file not found.")
            try:
                freq = self.sample_frequency.get()
                if freq <= 0: return messagebox.showerror("Error", "Sample frequency must be positive.")
            except tk.TclError: return messagebox.showerror("Error", "Invalid sample frequency.")
            self.monitor.app_config.screenshot_interval = 1.0 / self.sample_frequency.get()
            self.monitor.set_audio_feedback(self.audio_feedback_on.get())
            img_log_opts = ImageLogOptions(self.log_opt_screenshot.get(),self.log_opt_color_filter.get(),self.log_opt_signal_plot.get(),self.log_opt_fft_plot.get())
            data_log_opts = DataLogOptions(self.log_to_mat.get(), self.log_to_unv.get())
            manual_points = PointsInfo(**{k: v.get() for k, v in self.manual_points_vars.items()})
            if self.monitor.start(self.verbose_logging_on.get(), self.image_logging_on.get(), img_log_opts, data_log_opts, manual_points):
                self.is_monitoring.set(True); self.start_stop_button.config(text="Stop Monitoring"); self.status_label.config(text="Monitoring active...")
                self.load_button.config(state=tk.DISABLED); self.edit_button.config(state=tk.DISABLED)

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
        if self.is_monitoring.get(): self.monitor.stop()
        if self.overlay and self.overlay.winfo_exists(): self.overlay.destroy()
        self.root.destroy()

# --- 6. APPLICATION ENTRY POINT ---
if __name__ == "__main__":
    main_root = tk.Tk()
    app = MonitorControlGUI(main_root)
    main_root.mainloop()