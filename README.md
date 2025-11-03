# USMA (Unified Screen Monitoring Application) - v.0.4.1

[cite_start]This application provides a GUI for real-time screen monitoring, color analysis, and Optical Character Recognition (OCR) for specialized data logging. [cite: 1, 2]

---

## Version History

### [cite_start]v.0.4.1 (Current Release) [cite: 4]

* [cite_start]**Portability & Installation Wizard:** Removed the hardcoded path to `tesseract.exe` from `monitor_app.py`. [cite: 6] [cite_start]The app now correctly finds Tesseract from the system's `PATH`. [cite: 6]
* [cite_start]`RUN_monitor.bat` is now an installation "wizard" that automatically checks for Python, Tesseract, and installs all required Python packages in a virtual environment. [cite: 7]

### [cite_start]v.0.4.0 [cite: 8]

* [cite_start]**OCR Integration:** Added Optical Character Recognition (OCR) using `pytesseract` to extract text from designated screen regions. [cite: 9]
* [cite_start]**New Region Types:** Added status, overload, and points region types for OCR. [cite: 10]
* [cite_start]**Enhanced Logging:** OCR data is now embedded in logs, image titles, and data files. [cite: 11]
* [cite_start]**Standard-Compliant UNV Headers:** Added UFF Type 18 (Overload status) and mapped points to UFF Type 58 (Node/DOF IDs). [cite: 12]
* [cite_start]**Dynamic Filenaming:** Log files are now named using parsed points info (e.g., `FRF_P1P3_1.mot`). [cite: 13]
* [cite_start]**Manual POI Entry:** Added a GUI option for manual entry of Hammer/Response points. [cite: 14]

---

## Requirements

[cite_start]You must have these two programs installed before running the application: [cite: 16]

1.  [cite_start]**Python 3.x:** [cite: 17]
    * [cite_start]Download and install from python.org. [cite: 18]
    * [cite_start]**IMPORTANT:** During installation, make sure to check the box that says "Add Python to PATH". [cite: 19]

2.  [cite_start]**Tesseract-OCR Engine:** [cite: 20]
    * [cite_start]This is an external program required for all OCR features (reading text from the screen). [cite: 22]
    * [cite_start]Download the installer from here: [https://github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki) [cite: 23]
    * [cite_start]**IMPORTANT:** During installation, you MUST check the box to "Add Tesseract to system PATH" (it might be under "Additional language data" or a similar component). [cite: 24]
    * [cite_start]If you do not do this, the application will not be able to find it. [cite: 25]

---

## How to Run

1.  [cite_start]Download all files from this repository (or clone it). [cite: 27]
2.  [cite_start]Double-click the `RUN_monitor.bat` file. [cite: 28]
    * [cite_start]This batch file is a "wizard" that will automatically: [cite: 29]
        * [cite_start]Check if Python is installed. [cite: 30]
        * [cite_start]Check if Tesseract-OCR is installed and in your `PATH`. [cite: 31]
* [cite_start]Create a local Python virtual environment (in a folder named `sm_venv`). [cite: 32]
        * [cite_start]Install all required Python libraries (like `opencv`, `pytesseract`, `sounddevice`, etc.) into that environment. [cite: 33]
        * [cite_start]Launch the main application (`monitor_app.py`). [cite: 34]

---

## Troubleshooting

### [cite_start]"ERROR: Tesseract-OCR not found in PATH..." [cite: 36]

[cite_start]You did not install Tesseract correctly. [cite: 36]

1.  [cite_start]Re-run the Tesseract installer (from the link above). [cite: 37]
2.  [cite_start]Find the step where it asks which components to install. [cite: 38]
3.  [cite_start]Make sure the checkbox for "Add Tesseract to system `PATH`" is selected. [cite: 39]
4.  [cite_start]Finish the installation and run `RUN_monitor.bat` again. [cite: 40]

### [cite_start]"Warning: sounddevice library not found..." [cite: 41]

[cite_start]This means the `sounddevice` Python package couldn't load, and audio feedback will be disabled. [cite: 41] [cite_start]This can happen if your system doesn't have a recognized audio output device. [cite: 42] [cite_start]The core monitoring features will still work. [cite: 42]
