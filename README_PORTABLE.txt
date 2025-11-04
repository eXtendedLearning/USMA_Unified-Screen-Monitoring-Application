================================================================================
USMA - Unified Screen Monitoring Application (Portable Edition)
Version 0.4.2

This is a FULLY PORTABLE, "ready-to-go" version of USMA. It includes:
✓ A complete Python environment (no system installation needed)
✓ Tesseract-OCR (no system installation needed)
✓ All required Python libraries (pre-installed)

You can copy this entire folder to:

USB drives

Network drives

Other computers

Offline/air-gapped machines

================================================================================
INSTRUCTIONS

There is NO setup. Everything is pre-installed.

Unzip the entire package to a folder.

Double-click: RUN_PORTABLE.bat

The application will start immediately.

================================================================================
MOVING TO ANOTHER COMPUTER

Copy the entire USMA_Portable folder.

Paste it onto the new computer.

Run RUN_PORTABLE.bat

No additional setup is needed. The application is 100% offline.

================================================================================
TROUBLESHOOTING

If the application window flashes and closes, please check the 'run_log.txt'
file that was created in this folder. It will contain the exact error message.

Common errors:

Problem: "Portable Python executable not found!"
Solution:
This means the 'python' folder (which contains the 300MB+ Python
environment) is missing or incomplete. Please re-unzip the
original package.

Problem: "Portable Tesseract executable not found!"
Solution:
This means the 'external\tesseract' folder is missing. Please
re-unzip the original package.

Problem: ModuleNotFoundError: No module named 'cv2' (or similar)
Solution:
This indicates the 'python' folder is incomplete. Please re-unzip
the original package.

Problem: ImportError: DLL load failed while importing _tkinter
Solution:
This indicates the 'python' folder is damaged or was created from
an incompatible Python version. Please re-download the package.

================================================================================
FOLDER CONTENTS

python/              : The complete, self-contained Python environment.

external/tesseract/  : The complete, self-contained Tesseract OCR engine.

configs/             : User configuration files.

monitor_app.py       : The main application.

RUN_PORTABLE.bat     : The script you double-click to start.

run_log.txt          : (Created after you run) Contains app output/errors.

================================================================================
SUPPORT

For issues, updates, and documentation:
GitHub: https://github.com/eXtendedLearning/USMA

Report bugs: https://github.com/eXtendedLearning/USMA/issues

================================================================================