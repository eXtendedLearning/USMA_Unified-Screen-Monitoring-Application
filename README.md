# USMA (Unified Screen Monitoring Application) — v0.9.0

Real-time screen monitoring, FRF signal reconstruction, OCR-based metadata extraction, and UNV Dataset 58 export for modal analysis workflows. Fully portable — no installation required.

---

## Quick Start

1. Extract all files to the same folder
2. Double-click `RUN_USMA_PORTABLE.bat`
3. Choose an existing config or create a new one via the calibration wizard
4. Draw ROI regions, set types, tune HSV color filter, save config
5. Start monitoring and run your impact test sequence

---

## Folder Structure

```
USMA/
├── RUN_USMA_PORTABLE.bat   ← Start here
├── monitor_app.py
├── requirements.txt
├── python/                  ← Portable Python 3.11.9
├── external/tesseract/      ← Portable Tesseract OCR
├── configs/                 ← JSON configuration files
├── logs/                    ← Application logs
├── image_logs/              ← Visual logs (ROIs, masks, signals, FFT…)
└── signal_logs/             ← UNV export files (.unv)
```

---

## System Requirements

| Requirement | Specification |
|-------------|---------------|
| OS | Windows 10+ (64-bit) |
| Disk Space | ~500 MB |
| Python | Included (portable) |
| Admin Rights | Not required |

---

## ROI Types

| Type | Purpose |
|------|---------|
| `frf` | FRF signal capture — FFT + Lowpass classification |
| `psd` | PSD signal capture — independent parameter set *(v0.7.0)* |
| `coherence` | Coherence plot monitoring *(v0.8.0)* |
| `averages` | OCR read of average count *(v0.8.0)* |
| `status` | System status text (Waiting / Measuring / Ready) |
| `overload` | Overload indicator region |
| `run` | Run number OCR |
| `hammer` | Hammer point + direction OCR |
| `response` | Response point + direction OCR |

---

## Classification System

Dual-method classification (FRF / PSD):

| FFT | Lowpass | Result | Color |
|-----|---------|--------|-------|
| OK | OK | **GOOD HIT** | Green |
| BAD | OK | **SUSPECT (FFT only)** | Orange |
| OK | BAD | **SUSPECT (Lowpass only)** | Orange |
| BAD | BAD | **BAD HIT** | Red |

---

## Output

- **UNV Dataset 58** — compatible with LMS TestLab, Siemens Simcenter, pyuff, MATLAB
- Real data only (imaginary part zero for reconstructed signals)

---

## Dependencies

`mss`, `pyautogui`, `opencv-python`, `numpy`, `scipy`, `matplotlib`, `Pillow`, `pytesseract`, `sounddevice`

---

## Troubleshooting

| Error | Solution |
|-------|---------|
| App doesn't start | Check `run_log.txt` |
| Tesseract not found | Ensure `external/tesseract/` exists |
| HSV not saving | Click "Apply" then save config |
| Memory grows | History limited to 25 hits; restart for 500+ sessions |

---

## Version History

### v0.9.0 — Calibration Wizard Release *(current)*

- **CalibrationChoiceDialog** — post-config dialog: "Use Default Parameters" or "Calibrate with Expert Feedback"
- **Calibration Mode UI** — Good/Bad/Ignore buttons activate on signal detection; signal counter with 5-level status bar
- **5-level calibration status bar** — Not Calibrated (red) → Robust (green), updates with each classification
- **Signal Source selector** — dropdown in GraphViewer to filter by FRF, PSD, or Coherence signals
- **Coherence plot callback** — coherence signals now sent to graph viewer alongside FRF and PSD
- **Y-axis scale type** — new metadata field (linear/dB/log/ln) in region editor for display context
- **Multi-signal console output** — single-line summary per hit: `[HIT #n] CLASSIFICATION | FRF | PSD | Coh | Avg`
- **Dedicated feedback labels** — PSD and Coherence now have their own rows in Live Analysis Feedback

### v0.8.0 — Coherence & OCR Release

Implementation of Coherence ROI analysis, tracking, and Averages OCR readout tracking.

### v0.7.0 — PSD Release

Implementation of PSD ROI analysis, logging, UNV output, and combined FRF+PSD hit classification.

### v0.6.0 — Foundation Release

Infrastructure update introducing shared signal processing and data structures required by upcoming PSD, Coherence, and Calibration features:

- **5 new dataclasses:** `CalibrationSignal`, `CalibrationSession`, `CoherenceAnalysisResult`, `CoherenceTrackingState`, `LightweightCoherenceData`
- **`FrameAnalysisResult` extended** with PSD result fields, coherence results dict, and current averages
- **`AppConfig` extended** with 6 independent PSD analysis parameters (`psd_fft_*`, `psd_lowpass_*`, `psd_residual_*`, `psd_exceedance_*`) and 3 coherence parameters (`coherence_threshold`, `coherence_degradation_pct`, `hits_per_run`)
- **`_reconstruct_signal_from_roi()`** extracted as a shared helper — HSV→mask→signal pipeline reused by FRF, PSD, and future Coherence analysis
- **`_analyze_wave_pattern()` parameterized** with `param_prefix` — enables PSD to run with independent parameters via `_analyze_wave_pattern(roi, region, param_prefix='psd_')`
- **`classify_hit()` extracted** — centralises FRF classification logic; returns `(text, color)` tuple used by both `_handle_logging` and the GUI
- **Config backward compatible** — existing v0.5.x configs load without error; new fields default gracefully
- **3 new ROI types** in type dialog and region editor: `psd`, `coherence`, `averages`

### v0.5.x — UI & FRF Polish *(2025)*

- v0.5.2: Mask logging fix, horizontal logging layout, selective manual points, HSV calibration text entry + zoom
- v0.5.1: FRF ROI rename, UNV-only export, split-view interface, verbose logging menu
- v0.5.0: Startup wizard, HSV calibration window, mandatory ROI type selection, live analysis parameters, scrollable GUI

### v0.4 — Performance & Analysis *(2025)*

mss screen capture (2-5× faster), DPI awareness, lowpass residual analysis, dual classification, live graph viewer, UNV Dataset 58 format fix

### v0.1–v0.3 — Prototype

Initial screen monitoring, pyautogui capture, basic FRF reconstruction, early UNV export

---

## License

Internal tool for modal analysis workflow optimization.
