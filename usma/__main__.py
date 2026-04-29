"""Entry point for `python -m usma`."""
import sys
import os

# DPI awareness must be set before Tk is imported so screen-capture coordinates
# from mss and Tk window geometry use the same physical pixel space.
if os.name == "nt":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass

import tkinter as tk

# Ensure working directory is the package root (for configs/, logs/, etc.)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from usma.models import APP_VERSION
from usma.gui.dialogs import StartupDialog, CalibrationChoiceDialog
from usma.gui.config_tool import ConfigToolWindow
from usma.gui.main_window import MonitorControlGUI

def main():
    # Create hidden root for startup dialog
    temp_root = tk.Tk()
    temp_root.withdraw()

    # Ensure configs directory exists
    if not os.path.exists("configs"):
        os.makedirs("configs")

    # Show startup dialog
    startup = StartupDialog(temp_root)
    startup_result = startup.result  # Store result before destroying

    if startup_result is None:
        # User cancelled - exit application
        temp_root.destroy()
        sys.exit(0)
    elif startup_result == "NEW_CALIBRATION":
        # Destroy temp root, create new root for config tool
        temp_root.destroy()

        main_root = tk.Tk()
        main_root.withdraw()  # Hide main GUI initially

        # Open config tool for new ROI definition
        config_tool = ConfigToolWindow(main_root, main_root, is_new_calibration=True)
        config_tool.wait_window()

        # After config tool closes, check if a config was saved
        saved_path = config_tool.saved_config_path  # Store before any potential issues

        if saved_path and os.path.exists(saved_path):
            # Show calibration choice dialog
            cal_dialog = CalibrationChoiceDialog(main_root, config_name=saved_path)
            calibration_mode = (cal_dialog.result == "CALIBRATE")
            if cal_dialog.result is None:
                main_root.destroy()
                sys.exit(0)

            # Load the saved config into main GUI and show
            main_root.deiconify()
            app = MonitorControlGUI(main_root, config_path=saved_path, calibration_mode=calibration_mode)
            main_root.mainloop()
        else:
            # No config was saved - exit
            main_root.destroy()
            sys.exit(0)
    else:
        # Config file selected
        config_path = startup_result  # Store the path

        # Show calibration choice dialog
        cal_dialog = CalibrationChoiceDialog(temp_root, config_name=config_path)
        calibration_mode = (cal_dialog.result == "CALIBRATE")
        if cal_dialog.result is None:
            temp_root.destroy()
            sys.exit(0)

        temp_root.destroy()

        main_root = tk.Tk()
        app = MonitorControlGUI(main_root, config_path=config_path, calibration_mode=calibration_mode)
        main_root.mainloop()

if __name__ == "__main__":
    main()
