# USMA (Unified Screen Monitoring Application) — v0.9.2

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

## Calibration System (v0.9.1+)

USMA includes an expert-guided calibration system that automatically tunes
classification thresholds to match your specific test setup. Instead of relying
on hardcoded defaults, the system learns from your Good/Bad judgments during
an initial calibration phase.

### How It Works

1. **Start calibration:** When loading a config, choose "Calibrate with Expert Feedback"
2. **Classify signals:** As hits are detected, click Good, Bad, or Skip for each one
3. **Automatic threshold computation:** After 6+ signals (3 Good + 3 Bad minimum),
   the system computes optimised thresholds using a hybrid statistical approach
4. **Finish & monitor:** Click "Finish Calibration" to switch to normal monitoring
   with your calibrated parameters

### Statistical Methods

The calibration uses three complementary estimation methods that activate
progressively as more data is collected:

- **Level 1 (6-7 signals):** Percentile Boundary estimation — places thresholds
  at the midpoint between Good and Bad distributions, or at the 95th percentile
  of the Good distribution when distributions overlap
- **Level 2 (8-11 signals):** Adds Bayesian grid-based posterior estimation with
  sigmoid likelihoods, providing credible intervals for each parameter
- **Level 3+ (12+ signals):** Adds ROC/Youden J-statistic optimisation and
  cross-validates across all three methods, dropping outlier estimates

### Confidence Scale

The calibration status bar shows your current confidence level:

| Level | Color | Signals | Meaning |
|-------|-------|---------|---------|
| 0 | Red | <6 | Not calibrated — using defaults |
| 1 | Orange | 6-7 | Preliminary — rough estimates |
| 2 | Yellow | 8-11 | Basic — Bayesian converging |
| 3 | Blue | 12-15 | Solid — cross-validated |
| 4 | Green | 16+ | Robust — high confidence |

### Parameters Tuned

The calibration system optimises these parameters for both FRF and PSD analysis:

- **FFT cutoff frequency** — boundary between low and high frequency energy
- **FFT energy ratio threshold** — maximum acceptable high-frequency energy proportion
- **Lowpass cutoff frequency** — Butterworth filter cutoff for residual analysis
- **Residual threshold** — amplitude threshold for exceedance counting
- **Exceedance ratio threshold** — maximum fraction of samples exceeding residual threshold

### Calibration Persistence

Calibration data is saved inside the config JSON file under the `_calibration` key.
This means:
- Calibration persists across sessions — load a calibrated config to use its thresholds
- Additional signals can be added later to improve confidence
- If the estimation algorithm improves in future versions, old data can be re-processed

### Tips for Good Calibration

- Use signals from different impact points — the similarity detector will warn
  if signals are too alike
- Include a mix of clearly good and clearly bad hits — borderline signals are
  less informative
- Aim for at least 12 signals (Level 3) for reliable results
- After calibration, you can fine-tune parameters manually in the Analysis
  Parameters panel

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

### v0.9.2 — UI Polish & Classification Controls *(current)*

- **Coherence Decoupled** — coherence signals informational only, excluded from calibration & classification
- **Dual Parameter Panels** — independent manual tuning for FRF and PSD analysis
- **UI Fixes** — robust spinbox constraints and appropriate sizing
- **Classification Lights** — Live feedback lights for sub-methods (FRF-FFT, FRF-LP, PSD-FFT, PSD-LP)
- **Method Toggling** — click lights to dynamically enable/disable classification sub-methods
- **Resizable Panels** — adjustable sash between graph viewer and console output
- **Version strings unified** — all window titles, log messages, and UNV exports now reference `APP_VERSION`
- **Configurable monitor index** — screen capture target is now a config field (`monitor_index`) with UI selector and bounds-check fallback; defaults to primary monitor (index 1)
- **Improved OCR change detection** — replaced single-mean hash with mean + std + 4×4 spatial signature; catches subtle text changes like "Run 1" → "Run 2"
- **Standalone config loader** — `load_app_config()` function replaces heavyweight `ScreenMonitor` instantiation for config loading in the Config Tool
- **Monitoring loop backoff** — exponential backoff (1→30 s) on persistent errors with auto-stop after 20 consecutive failures
- **`AppConfig.from_json()` classmethod** — config loading as a classmethod on `AppConfig`; `load_app_config()` delegates to it
- **`slots=True` on hot-path dataclasses** — `FRFAnalysisResult`, `LightweightHitData`, `CoherenceAnalysisResult`, `LightweightCoherenceData`, `FrameAnalysisResult` now use `__slots__` for lower memory and faster attribute access
- **Batched `gc.collect()`** — matplotlib figure cleanup runs GC every 5 figures instead of every figure; final GC on `stop()`
- **Clear Calibration** — button in both calibration and live analysis to reset calibration data while keeping ROI config
- **Live Calibration buttons** — Good/Bad buttons in the Analysis Parameters panel for continued calibration during normal monitoring
- **Portable launcher fix** — `.bat` version extraction now reads from line 20 of `monitor_app.py`

### v0.9.1 — Calibration Engine Release

### v0.9.0 — Calibration Wizard Release

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
