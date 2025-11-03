# USMA (Unified Screen Monitoring Application) - v.0.4.1

This application provides a GUI for real-time screen monitoring, color analysis, and Optical Character Recognition (OCR) for specialized data logging.

---

## Version History

### v.0.4.1 (Current Release)

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

## Requirements

You must have these two programs installed before running the application:

1.  **Python 3.x:**
    * Download and install from python.org.
    * **IMPORTANT:** During installation, make sure to check the box that says "Add Python to PATH".

2.  **Tesseract-OCR Engine:**
    * This is an external program required for all OCR features (reading text from the screen).
    * Download the installer from here: [https://github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki)
    * **IMPORTANT:** During installation, you MUST check the box to "Add Tesseract to system PATH" (it might be under "Additional language data" or a similar component).
    * If you do not do this, the application will not be able to find it.

---

## How to Run

1.  Download all files from this repository (or clone it).
2.  Double-click the `RUN_monitor.bat` file.
    * This batch file is a "wizard" that will automatically:
        * Check if Python is installed.
        * Check if Tesseract-OCR is installed and in your `PATH`.
        * Create a local Python virtual environment (in a folder named `sm_venv`).
        * Install all required Python libraries (like `opencv`, `pytesseract`, `sounddevice`, etc.) into that environment.
        * Launch the main application (`monitor_app.py`).

---

## Troubleshooting

### "ERROR: Tesseract-OCR not found in PATH..."

You did not install Tesseract correctly.

1.  Re-run the Tesseract installer (from the link above).
2.  Find the step where it asks which components to install.
3.  Make sure the checkbox for "Add Tesseract to system `PATH`" is selected.
4.  Finish the installation and run `RUN_monitor.bat` again.

### "Warning: sounddevice library not found..."

This means the `sounddevice` Python package couldn't load, and audio feedback will be disabled. This can happen if your system doesn't have a recognized audio output device. The core monitoring features will still work.
