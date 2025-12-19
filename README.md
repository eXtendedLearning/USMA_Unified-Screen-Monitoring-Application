# USMA (Unified Screen Monitoring Application) - v0.5.2

A professional-grade GUI application for real-time screen monitoring, signal analysis, and Optical Character Recognition (OCR) designed for modal analysis workflows. USMA captures screen regions, reconstructs FRF signals, performs dual-method quality classification, and exports data in industry-standard UNV Dataset 58 format.

---

## Key Features

- **Startup Calibration Wizard** - Config selection or new calibration prompt at launch
- **HSV Color Filter Calibration** - Live preview window with sliders, text entry, and zoom
- **Real-time Screen Monitoring** - Fast capture using native OS APIs (mss library)
- **Dual Classification System** - FFT energy ratio + Lowpass residual analysis
- **Split-View Interface** - Live graph and console output displayed side-by-side
- **Verbose Logging Options** - Selective debug output (config values, mask debug, OCR, FFT, etc.)
- **OCR Integration** - Automatic extraction of test metadata (Run, Points, Direction)
- **Industry-Standard Export** - UNV Dataset 58 file format
- **Fully Portable** - No installation required, runs from USB drive

---

## Version History

### v0.5.2 (Current Release - UI Polish Release)

* **Mask Logging Bug Fix** - Mask debug logging now respects the checkbox state (only logs when enabled)
* **Logging Options Layout** - Reorganized from vertical (4x2) to horizontal (2x4) layout for better space usage
* **Selective Manual Points** - Run/Hammer/Response fields now individually enabled when their OCR region is missing
* **HSV Calibration Enhancements**:
  - Added text entry fields for precise HSV min/max value input
  - Preview images now stacked vertically (Original → Mask → Filtered)
  - Added zoom controls (+/- buttons and mouse wheel) for detailed inspection
  - Sliders and text entries stay synchronized

### v0.5.1 (FRF ROI Release)

* **FRF ROI Renamed** - "Wave" ROI type renamed to "FRF" for clarity (future versions will include other reconstructed signal types)
* **Removed .mat Export** - MATLAB .mat export removed; UNV Dataset 58 is now the sole export format
* **Split-View Interface** - Live graph and console now displayed side-by-side with resizable divider (replaces tabbed view)
* **Verbose Logging Options** - New expandable menu for selective verbose logging:
  - Config Values (HSV filter parameters)
  - Mask Debug (pixel statistics)
  - OCR Output (recognition results)
  - FFT Data (energy calculations)
  - Lowpass Data (filter results)
  - Classification (hit quality decisions)
  - File Saves (export confirmations)

### v0.5.0 (Color Calibration Release)

* **Startup Dialog** - Application now shows config selection or new calibration prompt at launch
* **HSV Color Filter Calibration** - New dedicated window with live preview for tuning HSV color filter parameters
* **Mandatory ROI Type Selection** - Drawing a new region now requires explicit type selection via dialog
* **Pre-load Config in Editor** - Opening Edit Config with a loaded config pre-loads that config for modification
* **Live Analysis Parameters** - Global analysis parameters moved to main GUI for real-time adjustment
* **Scrollable Main GUI** - Main window now has scrollbars for smaller screens
* **Embedded Console** - Graph Viewer includes console output (now split-view in v0.5.1)
* **Continuous Logging Mode** - "Log on Events Only" toggle allows continuous output for HSV debugging

### v0.4

* **Performance: Faster Screen Capture** - Switched from pyautogui to mss library for 2-5x faster capture
* **Compatibility: DPI Awareness** - Added explicit DPI awareness for Windows 10/11 with display scaling
* **Lowpass Residual Analysis** - Added alternative classification method using Butterworth lowpass filter
* **Dual Classification System** - Combined FFT and Lowpass methods for robust hit quality assessment
* **Live Graph Viewer** - Central panel with matplotlib canvas and hit navigation
* **Organized Image Logging** - Separate folders for each image type
* **Fixed Dataset 58 Format** - Corrected UNV file header to comply with universal file format specification

---

## Quick Start

1. **Extract all files** to the same folder:
   ```
   USMA/
   ├── RUN_USMA_PORTABLE.bat   <- Double-click to start
   ├── monitor_app.py
   ├── requirements.txt
   ├── python/                  <- Portable Python 3.11.9
   └── external/tesseract/      <- Portable Tesseract OCR
   ```

2. **Double-click:** `RUN_USMA_PORTABLE.bat`

3. **First Launch:** Choose to load existing config or create new calibration

4. **New Calibration Workflow:**
   - Take screenshot in Config Tool
   - Draw regions and select their types (FRF for signal regions)
   - Click "Calibrate Color Filter" to tune HSV values with live preview
   - Save configuration

5. **Start monitoring** and begin your impact test sequence

---

## Folder Structure

```
USMA/
├── RUN_USMA_PORTABLE.bat   - Portable launcher (START HERE)
├── monitor_app.py          - Main application
├── requirements.txt        - Python dependencies
│
├── python/                 - Portable Python 3.11.9 with all dependencies
├── external/tesseract/     - Portable Tesseract OCR engine
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
└── signal_logs/            - Data files (.unv)
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
| `frf` | Signal capture region (FRF plot area) |
| `status` | System status text (Waiting/Measuring/Ready) |
| `overload` | Overload indicator region |
| `run` | Run number text |
| `hammer` | Hammer point and direction |
| `response` | Response point and direction |

### HSV Color Filter Calibration

The HSV calibration window (accessible via "Calibrate Color Filter" in Config Tool) provides:
- **Live preview** of Original, Mask, and Filtered views (stacked vertically)
- **Dual input methods** - Sliders for quick adjustment, text entry for precise values
- **Zoom controls** - Use +/- buttons or mouse wheel to inspect details
- **Real-time updates** - See mask changes immediately as you adjust values

**Tip:** For best results, the mask should show only the signal line as white pixels, with everything else black.

### Manual Points Entry

When OCR regions are not defined in your configuration, you can manually enter values:
- **Run Number** - Current test run (enabled when Run OCR region is missing)
- **Hammer Point/Direction** - Impact location and direction (enabled when Hammer OCR region is missing)
- **Response Point/Direction** - Measurement location and direction (enabled when Response OCR region is missing)

Each field is independently enabled based on which OCR regions are configured.

### Verbose Logging Options

Enable/disable specific logging categories (horizontal layout for compact display):
- **Config Values** - HSV filter parameters logged periodically
- **Mask Debug** - Pixel statistics for color mask analysis
- **OCR Output** - Recognition results from text regions
- **FFT Data** - Energy calculations and frequency analysis
- **Lowpass Data** - Filter coefficients and residual statistics
- **Classification** - Hit quality decision reasoning
- **File Saves** - Confirmation of exported files

---

## Output Format

### UNV Dataset 58
- Industry-standard universal file format
- Compatible with: LMS TestLab, Siemens Simcenter, pyuff, MATLAB
- Contains complete header with DOF identification
- Real + Imaginary data pairs (imaginary = 0 for reconstructed signals)

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
- Check Console output for "Config loaded - HSV Lower/Upper" messages

### Memory usage grows over time
- Hit history is limited to 25 entries in the graph viewer
- Restart application for very long sessions (500+ hits)

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

## Version Information

| Component | Version |
|-----------|---------|
| USMA | 0.5.2 |
| Python | 3.11.9 (Portable) |
| Tesseract | 5.x (Portable) |
| Package Size | ~350 MB |

---

## License

Internal tool for modal analysis workflow optimization.

## Support

For issues:
1. Check `logs/monitor_app.log` for errors
2. Check Console panel (right side of Live Graph) for live debug info
3. Enable specific verbose log options to diagnose issues
4. Test with a minimal configuration first
