# USMA — Changelog

All notable changes to this project will be documented in this file.

---

## v0.10.0 — Package Architecture *(current)*

- **Modular package** — monolithic `monitor_app.py` (6000+ lines) refactored into `usma/` package with 15 focused modules
- **Clean module boundaries** — `models.py`, `monitor.py`, `audio.py`, `calibration.py`, `utils.py`, `analysis/` (signal, ocr, coherence, classifier), `export/` (unv, image_logger), `gui/` (main_window, config_tool, graph_viewer, dialogs, hsv_calibration, overlay)
- **Backward-compatible launcher** — `monitor_app.py` remains as thin entry point; `RUN_USMA_PORTABLE.bat` unchanged
- **All v0.9.x features preserved** — calibration engine, dual FRF/PSD analysis, classification lights, resizable panels, OCR caching, exponential backoff

## v0.9.2 — UI Polish & Classification Controls

- Coherence decoupled from calibration & classification (informational only)
- Dual FRF/PSD analysis parameter panels with independent manual tuning
- Classification method indicator lights with per-method enable/disable toggle
- Resizable graph/console panels via draggable sash
- Clear Calibration button, live Good/Bad buttons in Analysis Parameters
- Monitoring loop exponential backoff, batched GC, `slots=True` dataclasses
- Configurable monitor index, improved OCR change detection, standalone config loader

## v0.9.1 — Calibration Engine Release

- **HybridCalibrationEngine** — three-tier statistical estimation (Percentile Boundary → Bayesian → ROC/Youden)
- Automatic threshold computation after 6+ classified signals (3 Good + 3 Bad minimum)
- 5-level confidence scale: Not Calibrated (red) → Robust (green)
- Calibration persistence in config JSON under `_calibration` key

## v0.9.0 — Calibration Wizard Release

- **CalibrationChoiceDialog** — post-config dialog: "Use Default Parameters" or "Calibrate with Expert Feedback"
- **Calibration Mode UI** — Good/Bad/Ignore buttons activate on signal detection; signal counter with 5-level status bar
- **Signal Source selector** — dropdown in GraphViewer to filter by FRF, PSD, or Coherence signals
- **Coherence plot callback** — coherence signals now sent to graph viewer alongside FRF and PSD
- **Y-axis scale type** — new metadata field (linear/dB/log/ln) in region editor for display context
- **Multi-signal console output** — single-line summary per hit: `[HIT #n] CLASSIFICATION | FRF | PSD | Coh | Avg`
- **Dedicated feedback labels** — PSD and Coherence now have their own rows in Live Analysis Feedback

## v0.8.0 — Coherence & OCR Release

Implementation of Coherence ROI analysis, tracking, and Averages OCR readout tracking.

## v0.7.0 — PSD Release

Implementation of PSD ROI analysis, logging, UNV output, and combined FRF+PSD hit classification.

## v0.6.0 — Foundation Release

Infrastructure update introducing shared signal processing and data structures required by upcoming PSD, Coherence, and Calibration features:

- **5 new dataclasses:** `CalibrationSignal`, `CalibrationSession`, `CoherenceAnalysisResult`, `CoherenceTrackingState`, `LightweightCoherenceData`
- **`FrameAnalysisResult` extended** with PSD result fields, coherence results dict, and current averages
- **`AppConfig` extended** with 6 independent PSD analysis parameters (`psd_fft_*`, `psd_lowpass_*`, `psd_residual_*`, `psd_exceedance_*`) and 3 coherence parameters (`coherence_threshold`, `coherence_degradation_pct`, `hits_per_run`)
- **`_reconstruct_signal_from_roi()`** extracted as shared helper — HSV→mask→signal pipeline reused by FRF, PSD, and future Coherence analysis
- **`_analyze_wave_pattern()` parameterized** with `param_prefix` — enables PSD to run with independent parameters via `_analyze_wave_pattern(roi, region, param_prefix='psd_')`
- **`classify_hit()` extracted** — centralises FRF classification logic; returns `(text, color)` tuple
- **Config backward compatible** — existing v0.5.x configs load without error; new fields default gracefully
- **3 new ROI types** in type dialog and region editor: `psd`, `coherence`, `averages`

## v0.5.x — UI & FRF Polish *(2025)*

- v0.5.2: Mask logging fix, horizontal logging layout, selective manual points, HSV calibration text entry + zoom
- v0.5.1: FRF ROI rename, UNV-only export, split-view interface, verbose logging menu
- v0.5.0: Startup wizard, HSV calibration window, mandatory ROI type selection, live analysis parameters, scrollable GUI

## v0.4 — Performance & Analysis *(2025)*

mss screen capture (2-5× faster), DPI awareness, lowpass residual analysis, dual classification, live graph viewer, UNV Dataset 58 format fix

## v0.1–v0.3 — Prototype

Initial screen monitoring, pyautogui capture, basic FRF reconstruction, early UNV export
