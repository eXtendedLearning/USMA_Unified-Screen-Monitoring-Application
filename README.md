# USMA (Unified Screen Monitoring Application) - v.0.4.4 (Pre-Release)

This application provides a GUI for real-time screen monitoring, color analysis, and Optical Character Recognition (OCR) for specialized data logging.

---

## Version History

### v.0.4.4 (Current Release)

* **Lowpass Residual Analysis (AS's Method)**: Added alternative classification using time-domain residual analysis:
  - Butterworth lowpass filter to separate LF/HF content
  - Exceedance counting for quantitative HF measurement
  - All analysis performed in **PHYSICAL UNITS** (g/N) to enable direct comparison with TestLab signals for reconstruction validation
  - Complementary to existing FFT energy ratio method

* **Dual Classification System**:
  - Both methods flagging = **BAD HIT** (Red)
  - One method flagging = **SUSPECT** (Orange)
  - Neither flagging = **GOOD HIT** (Green)

* **Live Graph Viewer**: New central panel with matplotlib canvas:
  - Signal plot, FFT spectrum, Lowpass comparison, Residual analysis
  - **Hit navigation** (◀◀/▶▶) to browse through recorded hits (up to 50 stored)
  - Plot type selector to switch visualization modes
  - Auto-displays most recent hit, user can navigate to previous
  - Run Summary bar chart (color-coded like MATLAB's jet colormap)

* **Extended Logging Options**: 
  - New plot types: Lowpass Plot, Residual Plot, Summary Chart
  - Extended .mat export with lowpass analysis data

* **New Configuration Parameters**:
  - `lowpass_cutoff`: Normalized cutoff frequency (default: 0.05)
  - `lowpass_filter_order`: Butterworth filter order (default: 4)
  - `residual_threshold`: Physical unit threshold in g/N (default: 0.005)
  - `exceedance_ratio_threshold`: Classification threshold (default: 0.05 = 5%)

### v.0.4.3

* **Fixed Dataset 58 Format**: Corrected UNV file header to comply with universal file format specification.
* **Enhanced FFT Plots**: Added comprehensive analysis information to FFT plots.

### v.0.4.2

* **Production Logging**: Removed all OCR diagnostic images from logs.
* **Portable Setup**: Implemented logic to dynamically locate Tesseract OCR engine.

### v.0.4.1

* **OCR "Divide and Conquer"**: Replaced single 'points' region with three dedicated regions.
* **Advanced Preprocessing**: Implemented CLAHE and Sharpening pipeline.

### v.0.4.0

* **OCR Integration**: Added Optical Character Recognition using `pytesseract`.
* **New Region Types**: Added status, overload, and points region types.
* **Enhanced Logging**: OCR data embedded in logs, image titles, and data files.

---

## QUICK START

1. Ensure all files are extracted to the same folder:
   - RUN_USMA_PORTABLE.bat
   - monitor_app.py
   - requirements.txt
   - python\          (folder)
   - external\        (folder)

2. Double-click: RUN_USMA_PORTABLE.bat

3. The application will start automatically.


## WHAT'S INCLUDED

```
RUN_USMA_PORTABLE.bat   - Application launcher (START HERE!)
monitor_app.py          - Main application
requirements.txt        - Dependency list

python\                 - Portable Python 3.11.9 with all dependencies
external\tesseract\     - Portable Tesseract OCR engine

configs\                - Configuration files (created on first use)
logs\                   - Application logs (created on first use)
image_logs\             - Image logs (created when enabled)
signal_logs\            - Signal data logs (created when enabled)
```


## SYSTEM REQUIREMENTS

- Windows 10 or later (64-bit)
- ~500 MB disk space
- No Python installation required
- No administrator rights required


## NEW IN v0.4.4: DUAL CLASSIFICATION

The application now uses **two complementary methods** for hit classification:

### FFT Energy Ratio Method (Original)
- Analyzes frequency content via FFT
- Calculates ratio of high-frequency energy to total energy
- Parameters: `fft_cutoff_frequency`, `fft_energy_ratio_threshold`

### Lowpass Residual Method (Andrea's Method)
- Applies Butterworth lowpass filter to separate LF/HF content
- Counts how many samples exceed a threshold in the residual (HF content)
- Operates in **physical units** (g/N) for TestLab comparison
- Parameters: `lowpass_cutoff`, `residual_threshold`, `exceedance_ratio_threshold`

### Combined Classification
| FFT Result | Lowpass Result | Overall Classification | Color |
|------------|----------------|------------------------|-------|
| Bad        | Bad            | BAD HIT               | Red   |
| Bad        | Good           | SUSPECT (FFT)         | Orange|
| Good       | Bad            | SUSPECT (LP)          | Orange|
| Good       | Good           | GOOD HIT              | Green |


## TROUBLESHOOTING

If the application doesn't start, check run_log.txt for error details.

Common issues:

```
[ERROR] Python not found
  -> Ensure 'python' folder is present with python.exe

[ERROR] Tesseract not found
  -> Ensure 'external\tesseract' folder is present with tesseract.exe

[ERROR] monitor_app.py not found
  -> Keep all files in the same folder

Application exits immediately:
  -> Check run_log.txt for the full error message
```


## USING THE APPLICATION

1. **Launch**: Double-click RUN_USMA_PORTABLE.bat

2. **Load a configuration**:
   - Click "Load..." and select a .json config file
   - Or click "Edit Config..." to create a new one

3. **Configure Analysis Parameters** (in Edit Config):
   - FFT Method: Cutoff Frequency, Energy Ratio Threshold
   - Lowpass Method: LP Cutoff, Order, Residual Threshold, Exceedance Ratio

4. **Start monitoring**:
   - Click "Start Monitoring"
   - Watch the status light and live feedback panel
   - Use Graph Viewer to inspect individual hits

5. **Navigate Hits** (Graph Viewer):
   - Use ◀◀/▶▶ to move between recorded hits
   - Use ◀/▶ or dropdown to change plot type
   - Summary chart shows all hits in current run

6. **Stop monitoring**:
   - Click "Stop Monitoring"


## PORTABLE FEATURES

- Fully self-contained (no installation needed)
- Can run from USB drive
- Can be copied to any Windows computer
- No registry entries or system files
- All data stored in application folder


## VERSION INFORMATION

```
Version:       v0.4.4 (Release)
Python:        3.11.9 (Portable)
Tesseract:     5.x (Portable)
Package Size:  ~310 MB
```


## FUTURE UPDATES

1. Tutorial for Region Of Interest (ROI) definition and script usage
2. FRF self-calibration based on different datasets
3. Calibration Phase feature for intelligent threshold optimization