import gc
import time
import logging
import threading
import queue
from datetime import datetime
from typing import Dict, Tuple, Optional, Any

import numpy as np
import cv2
from tkinter import messagebox

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

try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

from usma.models import (
    APP_VERSION, ImageLogOptions, DataLogOptions, VerboseLogOptions,
    PointsInfo, MonitorEvent, FrameAnalysisResult, FRFAnalysisResult,
    LightweightHitData, CoherenceTrackingState, AnalysisParams,
)
from usma.utils import load_app_config
from usma.audio import AudioFeedback, SOUND_DEVICE_AVAILABLE
from usma.export.unv import UNVExporter
from usma.export.image_logger import ImageLogger
from usma.analysis.signal import SignalAnalyzer
from usma.analysis.ocr import OCREngine
from usma.analysis.coherence import CoherenceAnalyzer
from usma.analysis.classifier import HitClassifier

logger = logging.getLogger(__name__)


class ScreenMonitor:
    """Handles the core task of capturing and analyzing the screen."""
    
    def __init__(self, config_path, update_callback=None, plot_callback=None):
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.config_path = config_path
        self.app_config = self._load_config(self.config_path)
        self.signal_analyzer = SignalAnalyzer(self.app_config)
        self.coherence_analyzer = CoherenceAnalyzer(self.app_config, self.signal_analyzer)
        # Callbacks are deprecated, use event_queue instead
        self.update_callback = update_callback
        self.plot_callback = plot_callback
        self.event_queue = queue.Queue(maxsize=100)
        self.unv_exporter = UNVExporter()
        self.frame_count = 0
        self.verbose_logging_enabled = True
        self.image_logging_enabled = True
        self.image_log_options = ImageLogOptions()
        self.image_logger = ImageLogger(self.image_log_options, self.app_config)
        self.data_log_options = DataLogOptions()
        self.verbose_log_options = VerboseLogOptions()
        self.last_logged_ratio: Optional[float] = None
        self.last_logged_energy: Optional[float] = None
        self.audio = AudioFeedback()
        self.audio_feedback_enabled = False
        self.audio_stream = None
        self.audio_lock = threading.Lock()
        self.ocr_engine = OCREngine()
        self.classifier = HitClassifier()
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
        # Phase 3: coherence tracking per run
        self.coherence_tracking: Dict[str, CoherenceTrackingState] = {}

        # OCR caching: only re-run OCR when ROI content changes significantly
        self._ocr_cache: Dict[str, Any] = {}  # region_name -> last OCR result
        self._ocr_roi_hash: Dict[str, Any] = {}  # region_name -> (mean, std, spatial_signature)
        self._ocr_change_threshold: float = 3.0  # pixel difference to trigger re-OCR

        # Figure cleanup: batch gc.collect instead of per-figure
        self._figure_count: int = 0
        self._gc_every_n_figures: int = 5  # Run GC every N figures instead of every figure

        # Error tracking for exponential backoff
        self._consecutive_errors: int = 0
        self._max_consecutive_errors: int = 20  # Auto-stop after this many

    def _capture_screen(self) -> np.ndarray:
        """
        Capture the screen using the fastest available method.
        Returns BGR numpy array compatible with OpenCV.
        """
        if MSS_AVAILABLE:
            with mss.mss() as sct:
                idx = self.app_config.monitor_index
                if idx >= len(sct.monitors):
                    idx = 1  # Fall back to primary monitor
                    logger.warning(f"Monitor index {self.app_config.monitor_index} not available, "
                                 f"falling back to primary (index 1). "
                                 f"Available monitors: {len(sct.monitors) - 1}")
                monitor = sct.monitors[idx]
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
        self.image_logger.image_log_options = self.image_log_options
        self.data_log_options = data_log_options if data_log_options else DataLogOptions()
        self.verbose_log_options = verbose_log_options if verbose_log_options else VerboseLogOptions()
        # Sync verbose settings to signal analyzer
        self.signal_analyzer.verbose_logging_enabled = verbose_logging
        self.signal_analyzer.verbose_log_options = self.verbose_log_options
        self.manual_points_info = manual_points
        self.frame_count = 0
        self.last_logged_ratio = None
        self.last_logged_energy = None
        self.hit_counters.clear()
        self.run_history.clear()
        self.running = True
        self.last_known_status = "Unknown"
        self.last_known_overload = "Unknown"
        t = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.thread = t
        t.start()
        logger.info(f"Screen monitoring thread started for USMA v{APP_VERSION}")
        return True

    def stop(self):
        self.running = False
        self.audio.stop()
        t = self.thread
        if t is not None and t.is_alive():
            t.join(timeout=1.5)
        gc.collect()
        logger.info("Screen monitoring stopped.")

    def update_config(self, new_config_path):
        self.config_path = new_config_path
        self.app_config = self._load_config(new_config_path)
        logger.info(f"Configuration updated to {new_config_path}")


    def _load_config(self, path: str):
        """Load config using the standalone loader."""
        return load_app_config(path)

    def classify_hit(self, frame_result, enabled_methods=None):
        """Delegate to HitClassifier."""
        return self.classifier.classify_hit(frame_result, enabled_methods)

    def set_audio_feedback(self, enabled: bool):
        """Enable or disable audio feedback."""
        self.audio_feedback_enabled = enabled
        if not enabled:
            with self.audio_lock:
                if self.audio.audio_stream is not None:
                    self.audio.stop()

    def _monitoring_loop(self):
        while self.running:
            start_time = time.time()
            try:
                image = self._capture_screen()
                frame_result, all_rois = self._process_frame(image)

                # Reset error counter on successful frame
                self._consecutive_errors = 0

                try:
                    self.event_queue.put_nowait(MonitorEvent(
                        event_type="frame_update",
                        frame_result=frame_result
                    ))
                except queue.Full:
                    pass

                if self.update_callback:
                    self.update_callback(frame_result)

                if self.audio_feedback_enabled:
                    with self.audio_lock:
                        is_hf = frame_result.overall_is_hf if frame_result.overall_is_hf is not None else False
                        if is_hf and self.audio.audio_stream is None:
                            self.audio.start()
                        elif not is_hf and self.audio.audio_stream is not None:
                            self.audio.stop()

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
                self._consecutive_errors += 1
                # Exponential backoff: 1s, 2s, 4s, 8s... capped at 30s
                backoff = min(30.0, 2 ** (self._consecutive_errors - 1))
                logger.error(f"Error in monitoring loop (attempt {self._consecutive_errors}): {e}")

                if self._consecutive_errors >= self._max_consecutive_errors:
                    logger.critical(
                        f"Monitoring stopped after {self._consecutive_errors} consecutive errors. "
                        f"Last error: {e}"
                    )
                    self.running = False
                    self.audio.stop()
                    # Notify GUI if callback is available
                    try:
                        self.event_queue.put_nowait(MonitorEvent(
                            event_type="error",
                            error_message=f"Stopped after {self._consecutive_errors} failures"
                        ))
                    except queue.Full:
                        pass
                    if self.update_callback:
                        try:
                            error_result = FrameAnalysisResult()
                            error_result.status_text = f"ERROR: Stopped after {self._consecutive_errors} failures"
                            self.update_callback(error_result)
                        except Exception:
                            pass  # GUI may already be dead
                    return

                time.sleep(backoff)

    def _process_frame(self, image: np.ndarray) -> Tuple[FrameAnalysisResult, Dict[str, np.ndarray]]:
        self.frame_count += 1
        self.signal_analyzer.frame_count = self.frame_count
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
                analysis_result = self.signal_analyzer.analyze(roi, region, self.app_config.analysis_params['frf'])
                if analysis_result:
                    frame_result.frf_results[name] = analysis_result
                    frame_result.active_regions[name] = region
            elif region.roi_type == 'psd':
                psd_analysis = self.signal_analyzer.analyze(roi, region, self.app_config.analysis_params['psd'])
                if psd_analysis:
                    frame_result.psd_results[name] = psd_analysis
                    frame_result.active_regions[name] = region
            elif region.roi_type == 'coherence':
                coh_result = self.coherence_analyzer.analyze(roi, region)
                if coh_result:
                    frame_result.coherence_results[name] = coh_result
                    frame_result.active_regions[name] = region
            elif OCR_AVAILABLE:
                # Only re-run OCR if the ROI content has visually changed
                ocr_changed = self.ocr_engine.has_changed(name, roi)
                cache_key = f"{name}_{region.roi_type}"

                if region.roi_type == 'averages':
                    if ocr_changed:
                        avg = self.ocr_engine.analyze_averages(roi)
                        self._ocr_cache[cache_key] = avg
                    else:
                        avg = self._ocr_cache.get(cache_key)
                    if avg:
                        frame_result.current_averages = avg
                elif region.roi_type == 'status':
                    if ocr_changed:
                        text, diag_imgs = self.ocr_engine.analyze_status(roi)
                        self._ocr_cache[cache_key] = (text, diag_imgs)
                    else:
                        text, diag_imgs = self._ocr_cache.get(cache_key, ("Unknown", {}))
                    frame_result.status_text = text
                    frame_result.ocr_images.update(diag_imgs)
                elif region.roi_type == 'overload':
                    if ocr_changed:
                        text, diag_imgs = self.ocr_engine.analyze_overload(roi)
                        self._ocr_cache[cache_key] = (text, diag_imgs)
                    else:
                        text, diag_imgs = self._ocr_cache.get(cache_key, ("Unknown", {}))
                    frame_result.overload_text = text
                    frame_result.ocr_images.update(diag_imgs)
                elif region.roi_type == 'run':
                    if ocr_changed:
                        run_str, diag_imgs = self.ocr_engine.analyze_run(roi)
                        self._ocr_cache[cache_key] = (run_str, diag_imgs)
                    else:
                        run_str, diag_imgs = self._ocr_cache.get(cache_key, (None, {}))
                    if run_str:
                        frame_result.points_info.run = run_str
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True
                elif region.roi_type == 'hammer':
                    if ocr_changed:
                        point, direction, diag_imgs = self.ocr_engine.analyze_point_and_dir(roi, "hammer")
                        self._ocr_cache[cache_key] = (point, direction, diag_imgs)
                    else:
                        point, direction, diag_imgs = self._ocr_cache.get(cache_key, (None, None, {}))
                    if point:
                        frame_result.points_info.hammer_point = point
                    if direction:
                        frame_result.points_info.hammer_dir = direction
                    frame_result.ocr_images.update(diag_imgs)
                    ocr_points_active = True
                elif region.roi_type == 'response':
                    if ocr_changed:
                        point, direction, diag_imgs = self.ocr_engine.analyze_point_and_dir(roi, "response")
                        self._ocr_cache[cache_key] = (point, direction, diag_imgs)
                    else:
                        point, direction, diag_imgs = self._ocr_cache.get(cache_key, (None, None, {}))
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

        mpi = self.manual_points_info
        if not ocr_points_active and mpi is not None:
            frame_result.points_info = mpi

        if frame_result.frf_results:
            classifications = [res.is_high_frequency for res in frame_result.frf_results.values()]
            frame_result.overall_is_hf = sum(classifications) > len(classifications) / 2 if classifications else False
            frame_result.avg_energy_ratio = np.mean([res.energy_ratio for res in frame_result.frf_results.values()])
            frame_result.avg_high_freq_energy = np.mean([res.high_freq_energy for res in frame_result.frf_results.values()])
            frame_result.avg_exceedance_count = np.mean([res.exceedance_count for res in frame_result.frf_results.values()])
            frame_result.avg_exceedance_ratio = np.mean([res.exceedance_ratio for res in frame_result.frf_results.values()])
            lp_classifications = [res.lowpass_is_bad_hit for res in frame_result.frf_results.values()]
            frame_result.overall_lowpass_bad = sum(lp_classifications) > len(lp_classifications) / 2 if lp_classifications else False

        if frame_result.psd_results:
            psd_class = [res.is_high_frequency for res in frame_result.psd_results.values()]
            frame_result.psd_overall_is_hf = sum(psd_class) > len(psd_class) / 2
            frame_result.psd_avg_energy_ratio = float(np.mean([res.energy_ratio for res in frame_result.psd_results.values()]))
            frame_result.psd_avg_exceedance_count = float(np.mean([res.exceedance_count for res in frame_result.psd_results.values()]))
            frame_result.psd_avg_exceedance_ratio = float(np.mean([res.exceedance_ratio for res in frame_result.psd_results.values()]))
            psd_lp_class = [res.lowpass_is_bad_hit for res in frame_result.psd_results.values()]
            frame_result.psd_overall_lowpass_bad = sum(psd_lp_class) > len(psd_lp_class) / 2
        
        return frame_result, all_rois

    def _handle_logging(self, frame_result: FrameAnalysisResult, all_rois: Dict[str, np.ndarray]):
        if not frame_result.frf_results:
            return

        has_changed = (frame_result.avg_energy_ratio is not None and 
                       (self.last_logged_ratio is None or 
                        not np.isclose(frame_result.avg_energy_ratio, self.last_logged_ratio, atol=1e-5) or
                        not np.isclose(frame_result.avg_high_freq_energy, self.last_logged_energy, atol=1e-5)))

        if has_changed:
            points = frame_result.points_info
            
            classification, _color = self.classify_hit(frame_result)

            # --- Build multi-signal summary line ---
            counter_key_tmp = f"{points.hammer_point}{points.response_point}"
            hit_num = self.hit_counters.get(counter_key_tmp, 0) + 1

            summary_parts = [f"[HIT #{hit_num}] {classification}"]

            # FRF status
            if frame_result.frf_results:
                frf_fft_bad = frame_result.overall_is_hf or False
                frf_lp_bad = frame_result.overall_lowpass_bad or False
                if frf_fft_bad and frf_lp_bad:
                    frf_status = "BAD"
                elif frf_fft_bad or frf_lp_bad:
                    detail = "FFT" if frf_fft_bad else "LP"
                    frf_status = f"SUSPECT({detail})"
                else:
                    frf_status = "GOOD"
                summary_parts.append(f"FRF: {frf_status}")

            # PSD status
            if frame_result.psd_results:
                psd_fft_bad = frame_result.psd_overall_is_hf or False
                psd_lp_bad = frame_result.psd_overall_lowpass_bad or False
                if psd_fft_bad and psd_lp_bad:
                    psd_status = "BAD"
                elif psd_fft_bad or psd_lp_bad:
                    detail = "FFT" if psd_fft_bad else "LP"
                    psd_status = f"SUSPECT({detail})"
                else:
                    psd_status = "GOOD"
                summary_parts.append(f"PSD: {psd_status}")

            # Coherence status
            if frame_result.coherence_results:
                coh_values = list(frame_result.coherence_results.values())
                avg_coh = sum(c.mean_coherence for c in coh_values) / len(coh_values)
                # Determine trend from tracking state
                trend = "UNKNOWN"
                for _cname, cstate in self.coherence_tracking.items():
                    trend = cstate.trend
                    break
                summary_parts.append(f"Coh: {avg_coh:.2f} ({trend})")

            # Averages count
            if frame_result.current_averages is not None:
                avg_target = self.app_config.hits_per_run
                summary_parts.append(f"Avg: {frame_result.current_averages}/{avg_target}")

            summary_line = " | ".join(summary_parts)
            logger.info(summary_line)

            if self.verbose_logging_enabled:
                logger.info(f"--- WAVE EVENT DETAILS ---")
                logger.info(f"  OCR Status: '{frame_result.status_text}'")
                logger.info(f"  OCR Overload: '{frame_result.overload_text}'")
                logger.info(f"  OCR Run: '{points.run}'")
                logger.info(f"  OCR Hammer: '{points.hammer_point}' Dir: '{points.hammer_dir}'")
                logger.info(f"  OCR Response: '{points.response_point}' Dir: '{points.response_dir}'")
                if frame_result.frf_results:
                    logger.info(f"  [FRF] FFT Energy Ratio: {frame_result.avg_energy_ratio:.3e} (thr: {self.app_config.fft_energy_ratio_threshold:.3e}) -> {'BAD' if frame_result.overall_is_hf else 'OK'}")
                    logger.info(f"  [FRF] LP Exceedances: {frame_result.avg_exceedance_count:.0f} ({frame_result.avg_exceedance_ratio:.1%}) -> {'BAD' if frame_result.overall_lowpass_bad else 'OK'}")
                if frame_result.psd_results:
                    logger.info(f"  [PSD] FFT Energy Ratio: {frame_result.psd_avg_energy_ratio:.3e} (thr: {self.app_config.psd_fft_energy_ratio_threshold:.3e}) -> {'BAD' if frame_result.psd_overall_is_hf else 'OK'}")
                    logger.info(f"  [PSD] LP Exceedances: {frame_result.psd_avg_exceedance_count:.0f} ({frame_result.psd_avg_exceedance_ratio:.1%}) -> {'BAD' if frame_result.psd_overall_lowpass_bad else 'OK'}")
                if frame_result.coherence_results:
                    coh_values = list(frame_result.coherence_results.values())
                    for cname, cres in frame_result.coherence_results.items():
                        logger.info(f"  [COH:{cname}] Mean: {cres.mean_coherence:.3f} | Badness: {cres.normalized_badness:.4f}")
                    if self.coherence_tracking:
                        for cname, cstate in self.coherence_tracking.items():
                            logger.info(f"  [COH:{cname}] Trend: {cstate.trend} | Hits in run: {cstate.hit_count}")
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
                    self.image_logger.current_run = self.current_run
                    self.image_logger.run_history = self.run_history
                    self.image_logger.log_hit(frf_result, frame_result, frf_name, base_filename, all_rois)
                if self.data_log_options.log_unv:
                    self.unv_exporter.export(frf_result, frame_result, frf_name, base_filename)

                if self.plot_callback:
                    # Create lightweight copy for plot callback
                    lightweight_data = LightweightHitData(
                        signal_physical=np.copy(frf_result.signal_physical) if frf_result.signal_physical is not None else np.array([]),
                        filtered_physical=np.copy(frf_result.filtered_physical) if frf_result.filtered_physical is not None else None,
                        residual_physical=np.copy(frf_result.residual_physical) if frf_result.residual_physical is not None else None,
                        fft_freqs=np.copy(frf_result.fft_freqs),
                        fft_mags=np.copy(frf_result.fft_mags),
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
                        run=points.run,
                        signal_type="frf"
                    )
                    if self.plot_callback:
                        self.plot_callback(lightweight_data, self.run_history.copy())
                    try:
                        self.event_queue.put_nowait(MonitorEvent(
                            event_type="hit_detected",
                            lightweight_data=lightweight_data,
                            run_history=self.run_history.copy()
                        ))
                    except queue.Full:
                        pass

            # --- PSD results: fire separate plot_callback with psd_ prefix ---
            for psd_name, psd_result in frame_result.psd_results.items():
                region = frame_result.active_regions[psd_name]
                psd_base = f"psd_{psd_name}_{counter_key}_{current_hit}"
                hit_key_psd = f"psd_{counter_key}_{current_hit}"

                self.run_history[self.current_run][hit_key_psd] = {
                    'exceedance_count': psd_result.exceedance_count,
                    'exceedance_ratio': psd_result.exceedance_ratio,
                    'energy_ratio': psd_result.energy_ratio,
                    'is_hf': psd_result.is_high_frequency,
                    'lowpass_bad': psd_result.lowpass_is_bad_hit,
                    'signal_type': 'psd',
                }

                if self.image_logging_enabled:
                    self.image_logger.current_run = self.current_run
                    self.image_logger.run_history = self.run_history
                    self.image_logger.log_hit(psd_result, frame_result, psd_name, psd_base, all_rois)

                if self.plot_callback:
                    psd_lightweight = LightweightHitData(
                        signal_physical=np.copy(psd_result.signal_physical) if psd_result.signal_physical is not None else np.array([]),
                        filtered_physical=np.copy(psd_result.filtered_physical) if psd_result.filtered_physical is not None else None,
                        residual_physical=np.copy(psd_result.residual_physical) if psd_result.residual_physical is not None else None,
                        fft_freqs=np.copy(psd_result.fft_freqs),
                        fft_mags=np.copy(psd_result.fft_mags),
                        energy_ratio=psd_result.energy_ratio,
                        is_high_frequency=psd_result.is_high_frequency,
                        exceedance_count=psd_result.exceedance_count,
                        exceedance_ratio=psd_result.exceedance_ratio,
                        lowpass_is_bad_hit=psd_result.lowpass_is_bad_hit,
                        total_energy=psd_result.total_energy,
                        high_freq_energy=psd_result.high_freq_energy,
                        y_axis_unit=region.y_axis_unit,
                        x_axis_min=region.x_axis_min,
                        x_axis_max=region.x_axis_max,
                        hit_key=hit_key_psd,
                        run=points.run,
                        signal_type="psd"
                    )
                    if self.plot_callback:
                        self.plot_callback(psd_lightweight, self.run_history.copy())
                    try:
                        self.event_queue.put_nowait(MonitorEvent(
                            event_type="hit_detected",
                            lightweight_data=psd_lightweight,
                            run_history=self.run_history.copy()
                        ))
                    except queue.Full:
                        pass

            # --- Coherence results: fire plot_callback with coherence signal ---
            for coh_name, coh_result in frame_result.coherence_results.items():
                region = frame_result.active_regions.get(coh_name)
                if region is None:
                    continue
                hit_key_coh = f"coh_{counter_key}_{current_hit}"

                if self.plot_callback:
                    coh_lightweight = LightweightHitData(
                        signal_physical=np.copy(coh_result.signal_physical),
                        filtered_physical=None,
                        residual_physical=np.copy(coh_result.inverted_signal),
                        fft_freqs=np.copy(coh_result.freq_axis) if coh_result.freq_axis is not None else np.array([]),
                        fft_mags=np.array([]),
                        energy_ratio=coh_result.normalized_badness,
                        is_high_frequency=False,
                        exceedance_count=0,
                        exceedance_ratio=0.0,
                        lowpass_is_bad_hit=False,
                        total_energy=coh_result.mean_coherence,
                        high_freq_energy=coh_result.min_coherence,
                        y_axis_unit="Coherence",
                        x_axis_min=region.x_axis_min,
                        x_axis_max=region.x_axis_max,
                        hit_key=hit_key_coh,
                        run=points.run,
                        signal_type="coherence"
                    )
                    if self.plot_callback:
                        self.plot_callback(coh_lightweight, self.run_history.copy())
                    try:
                        self.event_queue.put_nowait(MonitorEvent(
                            event_type="hit_detected",
                            lightweight_data=coh_lightweight,
                            run_history=self.run_history.copy()
                        ))
                    except queue.Full:
                        pass

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
                if region.roi_type in ('frf', 'psd') and region.enabled:
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

    def _ocr_roi_changed(self, name: str, roi: np.ndarray) -> bool:
        """Check if an OCR ROI has changed enough to warrant re-running OCR.
        Uses mean + std + downsampled pixel signature for robust change detection."""
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi

        # Build a compact feature vector: mean, std, and 4x4 downsampled grid
        mean_val = float(np.mean(gray))
        std_val = float(np.std(gray))
        small = cv2.resize(gray, (4, 4), interpolation=cv2.INTER_AREA)
        spatial_sig = small.flatten().astype(float)

        current_features = (mean_val, std_val, spatial_sig)
        prev_features = self._ocr_roi_hash.get(name)
        self._ocr_roi_hash[name] = current_features

        if prev_features is None:
            return True  # First frame — must run OCR

        prev_mean, prev_std, prev_spatial = prev_features

        # Check if any feature has changed significantly
        mean_diff = abs(mean_val - prev_mean)
        std_diff = abs(std_val - prev_std)
        spatial_diff = float(np.mean(np.abs(spatial_sig - prev_spatial)))

        # Trigger re-OCR if any metric exceeds threshold
        return (mean_diff > self._ocr_change_threshold or
                std_diff > self._ocr_change_threshold * 0.5 or
                spatial_diff > self._ocr_change_threshold * 0.8)

