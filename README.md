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

## Requirements

This is a **FULLY PORTABLE**, "ready-to-go" version of USMA.

There is **NO setup**. Everything is pre-installed.

It includes:
* A complete Python environment (no system installation needed)
* Tesseract-OCR (no system installation needed)
* All required Python libraries (pre-installed)

You can copy this entire folder to USB drives, network drives, or offline machines.

---

## How to Run

1.  Unzip the entire package to a folder.
2.  Double-click: **RUN_PORTABLE.bat**

The application will start immediately.

---

## Future Updates

1.  Tutorial for Region Of Interest (ROI) definition and script usage
2.  FRF self-calibration based on different datasets

---

## Troubleshooting

If the application window flashes and closes, please check the **`run_log.txt`** file that was created in this folder. It will contain the exact error message.

### Common errors:

* **Problem:** "Portable Python executable not found!"
    * **Solution:** This means the 'python' folder (which contains the 300MB+ Python environment) is missing or incomplete. Please re-unzip the original package.

* **Problem:** "Portable Tesseract executable not found!"
    * **Solution:** This means the `external\tesseract` folder is missing. Please re-unzip the original package.

* **Problem:** ModuleNotFoundError: No module named 'cv2' (or similar)
    * **Solution:** This indicates the 'python' folder is incomplete. Please re-unzip the original package.

* **Problem:** ImportError: DLL load failed while importing _tkinter
    * **Solution:** This indicates the 'python' folder is damaged or was created from an incompatible Python version. Please re-download the package.
