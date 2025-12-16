# USMA (Unified Screen Monitoring Application) - v0.5.0 (Calibration Release)

A professional-grade GUI application for real-time screen monitoring, signal analysis, and Optical Character Recognition (OCR) designed for modal analysis workflows. USMA captures screen regions, reconstructs FRF signals, performs dual-method quality classification, and exports data in industry-standard formats.

---

## Key Features

- **Startup Calibration Wizard** - Config selection or new calibration prompt at launch
- **HSV Color Filter Calibration** - Live preview window for tuning color detection
- **Real-time Screen Monitoring** - Fast capture using native OS APIs (mss library)
- **Dual Classification System** - FFT energy ratio + Lowpass residual analysis
- **Live Graph Viewer** - Interactive signal visualization with hit navigation and console output
- **OCR Integration** - Automatic extraction of test metadata (Run, Points, Direction)
- **Industry-Standard Export** - UNV Dataset 58 and MATLAB .mat file formats
- **Fully Portable** - No installation required, runs from USB drive

---

## Version History

### v0.5.0 (Current Release - Calibration Release)

* **Startup Dialog** - Application now shows config selection or new calibration prompt at launch for improved first-time user experience.
* **HSV Color Filter Calibration** - New dedicated window with live preview for tuning HSV color filter parameters. Accessible from Config Tool when wave regions are defined.
* **Mandatory ROI Type Selection** - Drawing a new region now requires explicit type selection via dialog instead of defaulting to 'wave'.
* **Pre-load Config in Editor** - Opening Edit Config from main GUI with a loaded config now pre-loads that config for modification.
* **Live Analysis Parameters** - Global analysis parameters (FFT cutoff, thresholds, lowpass settings) moved to main GUI for real-time adjustment during monitoring.
* **Scrollable Main GUI** - Main window now has scrollbars to ensure all controls are accessible on smaller screens.
* **Embedded Console** - Graph Viewer now includes a Console tab showing live verbose logging output.
* **Continuous Logging Mode** - New "Log on Events Only" toggle allows continuous image/log output every ~1 second for HSV debugging.
* **Enhanced Config Loading** - Detailed debug logging when loading configs to diagnose HSV calibration issues.

### v0.4.5

* **Performance: Faster Screen Capture** - Switched from pyautogui to mss library for 2-5x faster screen capture using native OS APIs (BitBlt on Windows). Falls back to pyautogui automatically if mss is unavailable.
* **Compatibility: DPI Awareness** - Added explicit DPI awareness declaration for correct operation on Windows 10/11 with display scaling (125%, 150%, etc.). Fixes coordinate offset issues and blurry GUI rendering on high-DPI displays.
* **Robustness: Tesseract Path Fallback** - Application now checks system PATH for Tesseract if the bundled version is not found, enabling graceful degradation.
* **Code Quality** - Refactored plotting functions to reduce code duplication.

### v0.4.4

* **Lowpass Residual Analysis** - Added alternative classification method using time-domain residual analysis with Butterworth lowpass filter. All analysis performed in physical units (g/N) for direct comparison with TestLab signals.
* **Dual Classification System**:
  - Both methods flagging = **BAD HIT** (Red)
  - One method flagging = **SUSPECT** (Orange)
  - Neither flagging = **GOOD HIT** (Green)
* **Live Graph Viewer** - New central panel with matplotlib canvas featuring:
  - Signal plot, FFT spectrum, Lowpass comparison, Residual analysis
  - Hit navigation to browse through recorded hits
  - Plot type selector to switch visualization modes
  - Run Summary bar chart (updated after each hit)
* **Improved OCR Robustness** - Enhanced preprocessing with multiple attempts, morphological operations, and better regex patterns.
* **Organized Image Logging** - Separate folders for each image type (ROIs, ColorMasks, Signals, FFT, Lowpass, Residual, Summary, OCRs).
* **Enhanced Verbose Logging** - Includes OCR values and classification reasoning.
* **BUGFIX** - Fixed memory leak from matplotlib figures in background thread.
* **BUGFIX** - Fixed GDI resource exhaustion from hit_history accumulation.

### v0.4.3

* **Fixed Dataset 58 Format** - Corrected UNV file header to comply with universal file format specification.
* **Enhanced FFT Plots** - Added comprehensive analysis information including total energy, high-frequency energy, energy ratio, and cutoff frequency.

### v0.4.2

* **Production Logging** - Removed OCR diagnostic images from logs. Image logging for 'ROI Screenshot' and 'Color Filter Mask' now only saves images related to 'wave' regions.
* **Portable Setup** - Implemented logic to dynamically locate the Tesseract OCR engine in the relative `external/tesseract` directory.

### v0.4.1

* **Portability and Installation Wizard** - Removed hardcoded path to `tesseract.exe`. App now finds Tesseract from system PATH.
* `RUN_monitor.bat` is now an installation wizard that automatically sets up the virtual environment.

### v0.4.0

* **OCR Integration** - Added pytesseract for text extraction from screen regions.
* **New Region Types** - Added status, overload, run, hammer, and response regions.
* **Standard-Compliant UNV Headers** - Added UFF Type 18 and mapped points to UFF Type 58.
* **Dynamic Filenaming** - Log files named using parsed points info (e.g., `FRF_P1P3_1.mot`).
* **Manual POI Entry** - Added GUI option for manual entry of Hammer/Response points.

---

## Quick Start

1. **Extract all files** to the same folder:
   ```
   USMA/
   ├── RUN_PORTABLE.bat      <- Double-click to start
   ├── monitor_app.py
   ├── requirements.txt
   ├── python/               <- Portable Python 3.11.9
   └── external/tesseract/   <- Portable Tesseract OCR
   ```

2. **Double-click:** `RUN_PORTABLE.bat`

3. **First Launch:** Choose to load existing config or create new calibration

4. **New Calibration Workflow:**
   - Take screenshot in Config Tool
   - Draw regions and select their types
   - Click "Calibrate Color Filter" to tune HSV values with live preview
   - Save configuration

5. **Start monitoring** and begin your impact test sequence

---

## Folder Structure

```
USMA/
├── RUN_PORTABLE.bat        - Portable launcher (START HERE)
├── RUN_monitor.bat         - Development launcher (requires Python installed)
├── monitor_app.py          - Main application
├── requirements.txt        - Python dependencies
│
├── python/                 - Portable Python 3.11.9 with all dependencies
│   └── Lib/site-packages/  - Includes mss, numpy, opencv, matplotlib, etc.
│
├── external/
│   └── tesseract/          - Portable Tesseract OCR engine
│
├── configs/                - Configuration files (JSON)
├── logs/                   - Application logs
├── image_logs/             - Visual logs (organized by type)
│   ├── ROIs/
│   ├── ColorMasks/
│   ├── Signals/
│   ├── FFT/
│   ├── Lowpass/
│   ├── Residual/
│   ├── Summary/
│   └── OCRs/
└── signal_logs/            - Data files (.mat, .unv)
```

---

## System Requirements

| Requirement | Specification |
|-------------|---------------|
| OS | Windows 10 or later (64-bit) |
| Display | Any resolution, supports HiDPI scaling |
| Disk Space | ~500 MB |
| Python | Not required (included in portable bundle) |
| Admin Rights | Not required |

---

## Classification System

USMA uses a dual-method classification approach for robust hit quality assessment:

### Method 1: FFT Energy Ratio
- Computes normalized frequency spectrum
- Calculates ratio of high-frequency to total energy
- Configurable cutoff frequency and threshold

### Method 2: Lowpass Residual Analysis
- Applies Butterworth lowpass filter to signal
- Measures residual (high-frequency content)
- Counts exceedances above threshold
- All calculations in physical units (g/N)

### Combined Classification

| FFT Result | Lowpass Result | Classification | Color |
|------------|----------------|----------------|-------|
| OK | OK | **GOOD HIT** | Green |
| BAD | OK | **SUSPECT** | Orange |
| OK | BAD | **SUSPECT** | Orange |
| BAD | BAD | **BAD HIT** | Red |

---

## Configuration

### Region Types

| Type | Purpose |
|------|---------|
| `wave` | Signal capture region (FRF plot area) |
| `status` | System status text (Waiting/Measuring/Ready) |
| `overload` | Overload indicator region |
| `run` | Run number text |
| `hammer` | Hammer point and direction |
| `response` | Response point and direction |

### HSV Color Filter Calibration

The HSV calibration window (accessible via "Calibrate Color Filter" in Config Tool) allows you to:
- See live preview of Original | Mask | Filtered views
- Adjust Hue, Saturation, and Value ranges with sliders
- Immediately see which pixels will be detected as the signal line
- Apply changes to save to your configuration

**Tip:** For best results, the mask should show only the signal line as white pixels, with everything else black.

### Analysis Parameters

**FFT Method:**
- `fft_cutoff_frequency` - Normalized frequency threshold (default: 0.09)
- `fft_energy_ratio_threshold` - Energy ratio limit (default: 0.013)

**Lowpass Method:**
- `lowpass_cutoff` - Filter cutoff frequency (default: 0.05)
- `lowpass_filter_order` - Butterworth filter order (default: 4)
- `residual_threshold` - Amplitude threshold in physical units (default: 0.005)
- `exceedance_ratio_threshold` - Allowable exceedance ratio (default: 0.05)

---

## Debugging HSV Issues

If signal detection is not working correctly:

1. **Enable continuous logging:**
   - Check "Enable Image Logs" and select "ROI Screenshots" + "Color Masks"
   - Uncheck "Log on Events Only" (under Controls)
   - Start monitoring

2. **Check the output:**
   - Images will save every ~1 second to `image_logs/`
   - Look at `ColorMasks/` folder to see what the HSV filter is detecting
   - If mask is mostly white, HSV range is too broad
   - If mask is mostly black, HSV range is too narrow or wrong color

3. **Check the Console tab:**
   - Shows live logging including HSV values being used
   - Will warn if `hsv_lower`/`hsv_upper` are missing from config

4. **Re-calibrate:**
   - Open Config Tool and click "Calibrate Color Filter"
   - Adjust sliders until only the signal line appears in the mask
   - Save configuration

---

## Output Formats

### UNV Dataset 58
- Industry-standard universal file format
- Compatible with: LMS TestLab, Siemens Simcenter, pyuff, MATLAB
- Contains complete header with DOF identification
- Real + Imaginary data pairs (imaginary = 0 for reconstructed signals)

### MATLAB .mat
- Contains all signal data and metadata
- Includes both raw (pixel) and scaled (physical) amplitudes
- FFT and Lowpass analysis results embedded

---

## Troubleshooting

### Application does not start
Check `run_log.txt` for detailed error messages.

| Error | Solution |
|-------|----------|
| Python not found | Ensure `python/` folder exists with `python.exe` |
| Tesseract not found | Ensure `external/tesseract/` folder exists |
| monitor_app.py not found | Keep all files in the same folder |
| DLL load failed | Reinstall Visual C++ Redistributable 2015-2022 |

### HSV calibration not saving
- Ensure you click "Apply" in the HSV Calibration window
- Save the config after applying HSV changes
- Check Console tab for "Config loaded - HSV Lower/Upper" messages

### GUI appears blurry or coordinates are offset
This was fixed in v0.4.5 with DPI awareness. If still occurring:
1. Right-click `RUN_PORTABLE.bat` then Properties then Compatibility
2. Click "Change high DPI settings"
3. Check "Override high DPI scaling behavior"
4. Select "Application" from dropdown

### OCR not reading text correctly
1. Ensure region boundaries tightly crop the text
2. Try different region sizes (OCR works best with clear, high-contrast text)
3. Check `image_logs/OCRs/` for preprocessing diagnostic images
4. Consider using Manual POI Entry as fallback

### Memory usage grows over time
- Hit history is limited to 25 entries in the graph viewer
- Restart application for very long sessions (500+ hits)

---

## Development Setup

For development without the portable bundle:

1. Install Python 3.11+
2. Install Tesseract OCR and add to PATH
3. Create virtual environment:
   ```batch
   python -m venv sm_venv
   sm_venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Run: `python monitor_app.py`

---

## Dependencies

| Package | Purpose |
|---------|---------|
| mss | Fast screen capture (primary) |
| pyautogui | Screen capture (fallback) |
| opencv-python | Image processing |
| numpy | Numerical operations |
| scipy | Signal processing (FFT, filtering) |
| matplotlib | Plotting and visualization |
| Pillow | Image handling |
| pytesseract | OCR wrapper |
| sounddevice | Audio feedback (optional) |

---

## Known Limitations

- OCR accuracy depends on screen resolution and text clarity
- Audio feedback requires working audio device
- Very high capture rates (>10 Hz) may stress CPU on older hardware
- UNV export contains real-only data (imaginary part is zero)

---

## Future Updates

1. Intelligent threshold suggestion based on labeled training hits
2. Batch reprocessing of saved signals
3. Network streaming for remote monitoring
4. Integration with TestLab API (if available)

---

## Version Information

| Component | Version |
|-----------|---------|
| USMA | 0.5.0 |
| Python | 3.11.9 (Portable) |
| Tesseract | 5.x (Portable) |
| Package Size | ~350 MB |

---

## License

Internal tool for modal analysis workflow optimization.

## Support

For issues:
1. Check `run_log.txt` and `logs/monitor_app.log`
2. Check Console tab in Graph Viewer for live debug info
3. Verify all folders are present
4. Test with a minimal configuration first
5. Report issues with log files attached
