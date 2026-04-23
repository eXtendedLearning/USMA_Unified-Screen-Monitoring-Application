import gc
import logging
from typing import Dict, Optional

import numpy as np
import cv2
from matplotlib.figure import Figure

from usma.models import (
    FRFAnalysisResult, FrameAnalysisResult, MonitoringRegion, ImageLogOptions,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# IMAGE LOGGER
# ═══════════════════════════════════════════════════════════════
class ImageLogger:
    def __init__(self, image_log_options, app_config):
        self.image_log_options = image_log_options
        self.app_config = app_config
        self._figure_count = 0
        self._gc_every_n_figures = 5
        self.current_run = "Run 1"
        self.run_history = {}

    def _cleanup_figure(self, fig):
        """Clean up a matplotlib figure and periodically run garbage collection."""
        if fig is not None:
            fig.clf()
            del fig
        self._figure_count += 1
        if self._figure_count % self._gc_every_n_figures == 0:
            gc.collect()

    def log_hit(self, frf_result: FRFAnalysisResult, frame_result: FrameAnalysisResult,
                           frf_name: str, base_filename: str, all_rois: Dict[str, np.ndarray],
                           signal_type: Optional[str] = None):
        try:
            region = frame_result.active_regions[frf_name]

            # Resolve the correct AnalysisParams for plot annotations. If caller
            # didn't specify, infer from the ROI's own roi_type so PSD hits use
            # PSD thresholds instead of the FRF defaults.
            if signal_type is None:
                signal_type = region.roi_type if region.roi_type in ('frf', 'psd') else 'frf'
            try:
                params = self.app_config.analysis_params.get(signal_type) or \
                         self.app_config.analysis_params['frf']
            except (AttributeError, KeyError):
                params = None

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
                                          f"image_logs/FFT/{base_filename}_fft.png",
                                          params=params)

            if self.image_log_options.include_lowpass_plot:
                self._create_lowpass_comparison_plot_safe(frf_result, region, title_info,
                                                         f"image_logs/Lowpass/{base_filename}_lowpass.png",
                                                         params=params)
        
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
            from matplotlib.backends.backend_agg import FigureCanvasAgg # type: ignore
        
            phys = frf_result.signal_physical
            if phys is None: return
        
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
        
            num_points = len(phys)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        
            ax.plot(freq_axis, phys, color='cyan', linewidth=1.5)
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
            self._cleanup_figure(fig)

    def _create_fft_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion,
                              title_info: str, filename: str, params=None):
        """Thread-safe FFT plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg # type: ignore

            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)

            cutoff = params.fft_cutoff_frequency if params is not None else self.app_config.fft_cutoff_frequency
            ax.plot(frf_result.fft_freqs, frf_result.fft_mags, color='magenta', linewidth=1)
            ax.axvline(x=cutoff, color='yellow',
                      linestyle='--', linewidth=1, label=f'Cutoff: {cutoff:.2f}')
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
            self._cleanup_figure(fig)

    def _create_lowpass_comparison_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion,
                                             title_info: str, filename: str, params=None):
        """Thread-safe lowpass comparison plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg # type: ignore
        
            phys = frf_result.signal_physical
            filtered = frf_result.filtered_physical
            if phys is None or filtered is None:
                return
        
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
        
            num_points = len(phys)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
        
            ax.plot(freq_axis, phys, 'w-', linewidth=2, label='Original', alpha=0.9)
            ax.plot(freq_axis, filtered, 'g--', linewidth=1.5, label='Lowpass Filtered')
        
            lp_cutoff = params.lowpass_cutoff if params is not None else self.app_config.lowpass_cutoff
            lp_order = params.lowpass_filter_order if params is not None else self.app_config.lowpass_filter_order
            filter_info = f'Cutoff: {lp_cutoff:.3f} | Order: {lp_order}'
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
            self._cleanup_figure(fig)

    def _create_residual_plot_safe(self, frf_result: FRFAnalysisResult, region: MonitoringRegion,
                                   title_info: str, filename: str):
        """Thread-safe residual plot creation."""
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg # type: ignore
        
            resid = frf_result.residual_physical
            if resid is None:
                return
        
            fig = Figure(figsize=(10, 5), dpi=150, facecolor='#1E1E1E')
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
        
            num_points = len(resid)
            freq_axis = np.linspace(region.x_axis_min, region.x_axis_max, num_points)
            # Use the dynamic threshold computed at analysis time (pixel-space)
            threshold = frf_result.dynamic_threshold

            ax.plot(freq_axis, resid, 'c-', linewidth=1, label='Residual (px)')
            ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
            if threshold > 0:
                ax.axhline(y=threshold, color='r', linestyle='-.', linewidth=1.5,
                          label=f'±{threshold:.1f} px')
                ax.axhline(y=-threshold, color='r', linestyle='-.', linewidth=1.5)

            exceedances = np.abs(resid) > threshold
            if np.any(exceedances):
                ax.scatter(freq_axis[exceedances], resid[exceedances],
                          c='red', s=15, zorder=5, alpha=0.7)

            classification = "BAD HIT" if frf_result.lowpass_is_bad_hit else "GOOD HIT"
            ratio_pct = (frf_result.dynamic_threshold / frf_result.max_abs_residual * 100) if frf_result.max_abs_residual > 1e-9 else 0
            residual_info = (f'Exceedances: {frf_result.exceedance_count} ({frf_result.exceedance_ratio:.1%}) | '
                            f'Thr: {ratio_pct:.0f}% of max | {classification}')
            ax.set_title(f'Residual Analysis (px)\n{title_info}\n{residual_info}',
                        color='white', fontsize=9)
            ax.set_xlabel('Frequency (Hz)', color='white')
            ax.set_ylabel('Residual (pixels)', color='white')
            ax.set_facecolor('#2E2E2E')
            ax.tick_params(axis='both', colors='white')
            ax.legend(facecolor='#2E2E2E', labelcolor='white', loc='upper right')
            ax.grid(True, linestyle='--', alpha=0.3)
        
            for spine in ax.spines.values():
                spine.set_color('white')
        
            fig.tight_layout()
            fig.savefig(filename, facecolor=fig.get_facecolor())
        finally:
            self._cleanup_figure(fig)

    def _create_run_summary_chart_safe(self, hit_data: Dict, filename: str):
        """Thread-safe run summary chart creation."""
        if not hit_data:
            return
    
        fig = None
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg # type: ignore
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
            self._cleanup_figure(fig)

    # =========================================================================
    # DATA FILE LOGGING
    # =========================================================================

