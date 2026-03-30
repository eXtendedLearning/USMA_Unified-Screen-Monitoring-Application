import re
import logging
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import cv2

try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# OCR ENGINE
# ═══════════════════════════════════════════════════════════════
class OCREngine:
    def __init__(self):
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self.sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        self._ocr_cache = {}  # region_name -> last OCR result
        self._ocr_roi_hash = {}  # region_name -> (mean, std, spatial_signature)
        self._ocr_change_threshold = 4.0

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

    def analyze_status(self, roi: np.ndarray) -> Tuple[str, Dict[str, np.ndarray]]:
        diag_imgs = {}
    
        configs: List[Dict[str, Any]] = [
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True, 'invert': True},
            {'scale_factor': 3, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': False},
        ]
    
        best_text = ""
        best_img = None
    
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(
                roi,
                scale_factor=int(cfg.get('scale_factor', 4)),
                use_clahe=bool(cfg.get('use_clahe', True)),
                use_sharpen=bool(cfg.get('use_sharpen', True)),
                use_morphology=bool(cfg.get('use_morphology', False)),
                invert=bool(cfg.get('invert', True))
            )
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
    
        mean_b, mean_g, mean_r = cv2.mean(roi)[:3]
        if best_img is not None:
            diag_imgs['status_preprocessed'] = best_img
    
        if mean_g > 120 and mean_g > mean_r:
            return "Measuring... (color)", diag_imgs
        if mean_r > 120 and mean_r > mean_g:
            return "Waiting for Trigger... (color)", diag_imgs
        if mean_b > 120:
            return "Ready (color)", diag_imgs
    
        return f"Unknown (OCR: '{best_text[:20]}')" if best_text else "Unknown", diag_imgs

    def analyze_overload(self, roi: np.ndarray) -> Tuple[str, Dict[str, np.ndarray]]:
        diag_imgs = {}
    
        mean_b, mean_g, mean_r = cv2.mean(roi)[:3]
        is_red = mean_r > 150 and mean_g < 100 and mean_b < 100
    
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

    def analyze_run(self, roi: np.ndarray) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
    
        configs: List[Dict[str, Any]] = [
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
            {'scale_factor': 4, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True, 'invert': True},
            {'scale_factor': 6, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False, 'invert': True},
        ]
    
        whitelist = 'Run0123456789 '
    
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(
                roi,
                scale_factor=int(cfg.get('scale_factor', 4)),
                use_clahe=bool(cfg.get('use_clahe', True)),
                use_sharpen=bool(cfg.get('use_sharpen', True)),
                use_morphology=bool(cfg.get('use_morphology', False)),
                invert=bool(cfg.get('invert', True))
            )
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

    def analyze_point_and_dir(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
        point, direction = None, None
        diag_imgs = {}
    
        try:
            width = roi.shape[1]
            split_ratios = [0.65, 0.70, 0.75, 0.60]
        
            for split_ratio in split_ratios:
                split_x = int(width * split_ratio)
                point_roi = roi[:, :split_x]
                dir_roi = roi[:, split_x:]
            
                point_result = self.analyze_point_only(point_roi, f"{name}_point")
                if point_result[0]:
                    point = point_result[0]
                    diag_imgs.update(point_result[1])
                
                    dir_result = self.analyze_direction_only(dir_roi, f"{name}_dir")
                    if dir_result[0]:
                        direction = dir_result[0]
                    diag_imgs.update(dir_result[1])
                    break
        
            if not point:
                full_result = self.analyze_full_point_roi(roi, name)
                point = full_result[0]
                direction = full_result[1]
                diag_imgs.update(full_result[2])
            
        except Exception as e:
            logger.error(f"Failed to analyze point/dir for {name}: {e}")
    
        return point, direction, diag_imgs

    def analyze_point_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
    
        configs: List[Dict[str, Any]] = [
            {'scale_factor': 5, 'use_clahe': True, 'use_sharpen': True, 'use_morphology': False},
            {'scale_factor': 6, 'use_clahe': True, 'use_sharpen': False, 'use_morphology': True},
            {'scale_factor': 4, 'use_clahe': False, 'use_sharpen': True, 'use_morphology': False},
        ]
    
        whitelist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789 '
    
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(
                roi,
                scale_factor=int(cfg.get('scale_factor', 4)),
                use_clahe=bool(cfg.get('use_clahe', True)),
                use_sharpen=bool(cfg.get('use_sharpen', True)),
                use_morphology=bool(cfg.get('use_morphology', False)),
                invert=bool(cfg.get('invert', True))
            )
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

    def analyze_direction_only(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Dict[str, np.ndarray]]:
        diag_imgs = {}
    
        configs: List[Dict[str, Any]] = [
            {'scale_factor': 6, 'use_clahe': True, 'use_sharpen': True},
            {'scale_factor': 5, 'use_clahe': False, 'use_sharpen': True},
        ]
    
        whitelist = '+-XYZxyz'
    
        for i, cfg in enumerate(configs):
            preprocessed = self._preprocess_for_ocr(
                roi,
                scale_factor=int(cfg.get('scale_factor', 4)),
                use_clahe=bool(cfg.get('use_clahe', True)),
                use_sharpen=bool(cfg.get('use_sharpen', True)),
                use_morphology=bool(cfg.get('use_morphology', False)),
                invert=bool(cfg.get('invert', True))
            )
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

    def analyze_full_point_roi(self, roi: np.ndarray, name: str) -> Tuple[Optional[str], Optional[str], Dict[str, np.ndarray]]:
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
    # COHERENCE ANALYSIS METHODS (Phase 3)
    # =========================================================================

    def analyze_averages(self, roi: np.ndarray) -> int:
        """OCR the 'averages' ROI to extract current hit/average count."""
        if not OCR_AVAILABLE:
            return 0
        try:
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            text = self._run_ocr_with_config(thresh, psm=7, whitelist='0123456789', load_dawgs=False)
            digits = ''.join(c for c in text if c.isdigit())
            return int(digits) if digits else 0
        except Exception:
            return 0

    def has_changed(self, name: str, roi: np.ndarray) -> bool:
        """Check if an OCR ROI has changed enough to warrant re-running OCR.
        Uses mean + std + downsampled pixel signature for robust change detection."""
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi

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

        mean_diff = abs(mean_val - prev_mean)
        std_diff = abs(std_val - prev_std)
        spatial_diff = float(np.mean(np.abs(spatial_sig - prev_spatial)))

        return (mean_diff > self._ocr_change_threshold or
                std_diff > self._ocr_change_threshold * 0.5 or
                spatial_diff > self._ocr_change_threshold * 0.8)

