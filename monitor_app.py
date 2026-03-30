#!/usr/bin/env python3
"""Backward-compatible launcher. Delegates to usma package."""
# DPI awareness must be set before any GUI imports
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except AttributeError:
        pass

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from usma.__main__ import main
if __name__ == '__main__':
    main()