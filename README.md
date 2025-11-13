# USMA (Unified Screen Monitoring Application) - v.0.4.2 (Portable Edition)

This application provides a GUI for real-time screen monitoring, color analysis, and Optical Character Recognition (OCR) for specialized data logging.

---

## Version History

### v.0.4.2 (Current Release)

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

QUICK START
-----------
1. Ensure all files are extracted to the same folder:
   - RUN_USMA_PORTABLE.bat
   - monitor_app.py
   - requirements.txt
   - python\          (folder) --> See "Pre-Release"
   - external\        (folder) --> See "Pre-Release"

2. Double-click: RUN_USMA_PORTABLE.bat

3. The application will start automatically.


WHAT'S INCLUDED
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


SYSTEM REQUIREMENTS
-------------------
- Windows 10 or later (64-bit)
- ~500 MB disk space
- No Python installation required
- No administrator rights required


TROUBLESHOOTING
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


USING THE APPLICATION
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


PORTABLE FEATURES
-----------------
- Fully self-contained (no installation needed)
- Can run from USB drive
- Can be copied to any Windows computer
- No registry entries or system files
- All data stored in application folder


SUPPORT
-------
If you encounter issues:
1. Check run_log.txt (created each time you run the launcher)
2. Verify all folders are present (python\, external\)
3. Ensure Windows 10/11 64-bit

For false antivirus warnings:
- Windows SmartScreen: Click "More info" → "Run anyway"
- This is a known false positive with portable Python


VERSION INFORMATION
-------------------
Version:       v0.4.2 (Pre-Release)
Python:        3.11.9 (Portable)
Tesseract:     5.x (Portable)
Package Size:  ~310 MB

## Future Updates

1.  Tutorial for Region Of Interest (ROI) definition and script usage
2.  FRF self-calibration based on different datasets

