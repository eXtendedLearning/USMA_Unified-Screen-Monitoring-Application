import os
import sys
import logging
from typing import Dict, Tuple, Optional
from usma.models import FrameAnalysisResult

# ═══════════════════════════════════════════════════════════════
# HIT CLASSIFIER
# ═══════════════════════════════════════════════════════════════
class HitClassifier:
    @staticmethod
    def classify_hit(frame_result: FrameAnalysisResult, enabled_methods: Optional[Dict[str, bool]] = None) -> Tuple[str, str]:
        """
        Determine hit quality classification from a FrameAnalysisResult.
    
        Considers FRF dual-method results and PSD (Phase 2). Coherence
        contribution added in Phase 3.
    
        Returns:
            (classification_text, severity_color) where severity_color is one of
            "green", "orange", or "red".
        """
        if enabled_methods is None:
            enabled_methods = {'frf_fft': True, 'frf_lp': True, 
                              'psd_fft': True, 'psd_lp': True}
    
        frf_fft_bad = (frame_result.overall_is_hf or False) and enabled_methods.get('frf_fft', True)
        frf_lp_bad = (frame_result.overall_lowpass_bad or False) and enabled_methods.get('frf_lp', True)
        psd_fft_bad = (frame_result.psd_overall_is_hf or False) and enabled_methods.get('psd_fft', True)
        psd_lp_bad = (frame_result.psd_overall_lowpass_bad or False) and enabled_methods.get('psd_lp', True)
    
        any_bad = frf_fft_bad or frf_lp_bad or psd_fft_bad or psd_lp_bad
        all_bad = (frf_fft_bad or frf_lp_bad) and (psd_fft_bad or psd_lp_bad) if (
            frame_result.frf_results and frame_result.psd_results
        ) else (frf_fft_bad and frf_lp_bad) or (psd_fft_bad and psd_lp_bad)
    
        if not any_bad:
            return "GOOD HIT", "green"
    
        # Build detail string
        reasons = []
        if frf_fft_bad:
            reasons.append("FRF-FFT")
        if frf_lp_bad:
            reasons.append("FRF-LP")
        if psd_fft_bad:
            reasons.append("PSD-FFT")
        if psd_lp_bad:
            reasons.append("PSD-LP")
        detail = "+".join(reasons)
    
        if all_bad:
            return f"BAD HIT ({detail})", "red"
        else:
            return f"SUSPECT ({detail})", "orange"
