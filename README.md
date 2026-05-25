# USMA (Unified Screen Monitoring Application) — v0.12.3

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
├── monitor_app.py           ← Thin launcher (delegates to usma/)
├── usma/                    ← Package (modular architecture introduced in v0.10.0)
│   ├── models.py            ← Data classes, config, calibration engine
│   ├── monitor.py           ← Screen capture & analysis orchestration
│   ├── audio.py             ← Audio feedback
│   ├── calibration.py       ← Calibration re-exports
│   ├── utils.py             ← Environment setup, logging, helpers
│   ├── analysis/            ← Signal, OCR, coherence, classifier
│   ├── export/              ← UNV exporter, image logger
│   └── gui/                 ← Main window, config tool, dialogs, etc.
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

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

**Current version:** v0.12.3 — Calibration diagnostics persistence, overlay geometry, and view scaling

---

## License

Internal tool for modal analysis workflow optimization.
