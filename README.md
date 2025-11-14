# USMA (Unified Screen Monitoring Application) - v.0.4.3 (Pre-Release)

This application provides a GUI for real-time screen monitoring, color analysis, and Optical Character Recognition (OCR) for specialized data logging for Impact Experimental Modal Analysis.

---

## Version History

### v.0.4.3 (Current Release)

* **Fixed Dataset 58 Format:** Corrected UNV file header to comply with universal file format specification:
  - Added all 5 required identification lines (function ID, program info, date/time, record info, response entity name)
  - Fixed DOF identification line with proper field widths (I5, I10 format as per FORTRAN specification)
  - Padded separator and dataset type lines to 80 characters
  - Files now compatible with pyuff, MATLAB, and commercial modal software (Siemens Testlab, LMS, etc.)
  - Eliminates parsing errors in standard UFF readers
* **Enhanced FFT Plots:** Added comprehensive analysis information to FFT plots:
  - Total energy display
  - High-frequency energy display
  - Energy ratio display
  - Cutoff frequency visualization
  - Classification result (HF/LF) with threshold value
  - Better diagnostic capability for troubleshooting and analysis

### v.0.4.2

* **Production Logging:** Removed all OCR diagnostic images from logs. Image logging for 'ROI Screenshot' and 'Color Filter Mask' now *only* saves images related to 'wave' regions, cleaning up the output.
* **Portable Setup:** Implemented logic to dynamically locate the Tesseract OCR engine in the relative `external/tesseract` directory, allowing the application to be fully portable.

### v.0.4.1

* **Portability & Installation Wizard:** Removed the hardcoded path to `tesseract.exe` from `monitor_app.py`. The app now correctly finds Tesseract from the system's `PATH`.
* `RUN_monitor.bat` is now an installation "wizard" that automatically checks for Python, Tesseract, and installs all required Python packages in a virtual environment.

### v.0.4.0

* **OCR Integration:** Added Optical Character Recognition (OCR) using `pytesseract` to extract text from designated screen regions.
* **New Region Types:** Added status, overload, and points region types for OCR.
* **Enhanced Logging:** OCR data is now embedded in logs, image titles, and data files.
* **Standard-Compliant UNV Headers:** Added UFF Type 18 (Overload status) and mapped points to UFF Type 58 (Node/DOF IDs).
* **Dynamic Filenaming:** Log files are now named using parsed points info (e.g., `FRF_P1P3_1.mot`).
* **Manual POI Entry:** Added a GUI option for manual entry of Hammer/Response points.

---

## QUICK START
-----------
1. Ensure all files are extracted to the same folder:
   - RUN_USMA_PORTABLE.bat
   - monitor_app.py
   - requirements.txt
   - python\          (folder) --> See "Pre-Release"
   - external\        (folder) --> See "Pre-Release"

2. Double-click: RUN_USMA_PORTABLE.bat

3. The application will start automatically.


## WHAT'S INCLUDED
---------------
RUN_USMA_PORTABLE.bat   - Application launcher (START HERE!)
monitor_app.py          - Main application
requirements.txt        - Dependency list (for reference)

python\                 - Portable Python 3.11.9 with all dependencies
external\tesseract\     - Portable Tesseract OCR engine

configs\                - Configuration files (created on first use)
logs\                   - Application logs (created on first use)
image_logs\             - Image logs (created when enabled)
signal_logs\            - Signal data logs (created when enabled)


## SYSTEM REQUIREMENTS
-------------------
- Windows 10 or later (64-bit)
- ~500 MB disk space
- No Python installation required
- No administrator rights required


## TROUBLESHOOTING
---------------
If the application doesn't start, check run_log.txt for error details.

Common issues:

[ERROR] Python not found
  -> Ensure 'python' folder is present with python.exe

[ERROR] Tesseract not found
  -> Ensure 'external\tesseract' folder is present with tesseract.exe

[ERROR] monitor_app.py not found
  -> Keep all files in the same folder

Application exits immediately:
  -> Check run_log.txt for the full error message


## USING THE APPLICATION
---------------------
1. Launch: Double-click RUN_USMA_PORTABLE.bat

2. Load a configuration:
   - Click "Load..." and select a .json config file
   - Or click "Edit Config..." to create a new one

3. Start monitoring:
   - Click "Start Monitoring"
   - Application captures and analyzes screen regions

4. Stop monitoring:
   - Click "Stop Monitoring"


## DATA OUTPUT FORMATS
--------------------

### UNV Files (.unv)
USMA v0.4.3 generates UNV files in proper Dataset 58 format, ensuring compatibility with:
- **Python**: pyuff library
- **MATLAB**: Universal File Format readers
- **Commercial Software**: Siemens Testlab, LMS Test.Lab, STAR Modal, etc.
- **Custom Tools**: EMAV and other modal analysis applications

**Dataset 58 Format Features:**
- Complete 5-line identification header (function info, program version, timestamp, record info, entity name)
- Proper field-width formatting (I5, I10, E13.5 as per FORTRAN specification)
- Node/DOF information correctly encoded
- Real/Imaginary data pairs for frequency response functions
- 80-character line padding for legacy system compatibility

### MAT Files (.mat)
Standard MATLAB format containing:
- Frequency array (Hz)
- Amplitude data (physical units)
- FFT analysis parameters (total energy, HF energy, energy ratio, cutoff frequency)
- Metadata (run, hammer point, response point, overload status)

### Image Logs
Optional visual diagnostics:
- ROI screenshots (original captured regions)
- Color filter masks (signal extraction visualization)
- Signal plots (reconstructed time/frequency domain)
- FFT plots (with comprehensive analysis information)


## PORTABLE FEATURES
-----------------
- Fully self-contained (no installation needed)
- Can run from USB drive
- Can be copied to any Windows computer
- No registry entries or system files
- All data stored in application folder


## SUPPORT
-------
If you encounter issues:
1. Check run_log.txt (created each time you run the launcher)
2. Verify all folders are present (python\, external\)
3. Ensure Windows 10/11 64-bit

For false antivirus warnings:
- Windows SmartScreen: Click "More info" → "Run anyway"
- This is a known false positive with portable Python


## VERSION INFORMATION
-------------------
Version:       v0.4.3 (Release)
Python:        3.11.9 (Portable)
Tesseract:     5.x (Portable)
Package Size:  ~310 MB

## DATA FORMAT COMPLIANCE
-----------------------
USMA v0.4.3 outputs are compliant with:
- Universal File Format (I-DEAS) Dataset 58 specification
- SDRC/Siemens UFF standards for frequency response functions
- ISO 18431-4:2007 (Mechanical vibration and shock — Signal processing)

Files generated by USMA have been validated with:
- pyuff 2.x+ (Python)
- MATLAB R2020a+ UFF readers
- EMAV v0.3.0+ (custom modal analysis tool)

## KNOWN LIMITATIONS
-----------------
1. **Screen Resolution**: Works best with 1920x1080 or higher resolution displays
2. **Color Detection**: Requires sufficient contrast between signal and background
3. **OCR Accuracy**: Dependent on font size and clarity of on-screen text
4. **Data Format**: UNV files contain real-only data (imaginary part set to zero) as reconstructed from screen captures
5. **Sampling Rate**: Limited by screen capture speed (typically 0.25-4 Hz)

## Future Updates

1. Tutorial for Region Of Interest (ROI) definition and script usage
2. FRF self-calibration based on different datasets
3. Phase information extraction (if available from source application)
4. Enhanced OCR accuracy with machine learning models
5. Multi-monitor support

## CHANGELOG SUMMARY

**v0.4.3 - Dataset 58 Compliance & Enhanced FFT Diagnostics**
- Fixed UNV file format to comply with universal file specification
- Added comprehensive FFT plot information (energy metrics, classification)
- Improved compatibility with industry-standard modal analysis software

**v0.4.2 - Production-Ready Logging**
- Cleaned up image logging (removed OCR diagnostics)
- Portable Tesseract OCR integration

**v0.4.1 - OCR Accuracy Improvements**
- Split points region into three regions for better OCR accuracy
- Advanced preprocessing (CLAHE, sharpening)

**v0.4.0 - OCR Integration**
- Initial OCR support for status, overload, and measurement points
- Dynamic file naming based on parsed information
- Manual POI entry fallback

---

**For technical support, bug reports, or feature requests, please contact the development team.**