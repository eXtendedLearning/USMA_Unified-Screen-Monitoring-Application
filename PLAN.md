# Phase 4 Implementation Plan: Calibration Phase (v0.9)

## Prerequisites
1. Copy `monitor_app.py` from main branch (3954 lines, with Phases 1-3) into worktree
2. Update version strings in copied file to v0.9

## Implementation Steps

### Step 1: New Dataclasses (~30 lines)
Add after existing dataclasses (after `AppConfig`, around line 390):
- `CalibrationSignal` dataclass — stores a single signal with judgment, metrics, timestamp, source
- `CalibrationSession` dataclass — collection of signals + estimated params + metadata

### Step 2: Calibration Estimators (~250 lines)
Add after new dataclasses. Four classes per UPDATE.md §2.6:
- `PercentileBoundaryEstimator` (Method A) — direct boundary between Good/Bad populations
- `BayesianCalibrationEstimator` (Method C) — grid-based posterior updating with credible intervals
- `ROCCalibrationEstimator` (Method D) — Youden's J for optimal threshold
- `HybridCalibrationEstimator` — manages all sub-estimators, confidence levels 0-4, `add_signal()`, `get_estimates()`

Also add `_check_signal_similarity()` function for diversity checking.

### Step 3: `CalibrationChoiceDialog` (~60 lines)
New class after `StartupDialog` (around line 1700):
- Two buttons: "Use Default Parameters" → result="DEFAULT", "Calibrate with Expert Feedback" → result="CALIBRATE"
- Shows config name for context

### Step 4: `LiveCalibrationSaveDialog` (~80 lines)
New class after `CalibrationChoiceDialog`:
- Shows count of live signals (Good vs Bad breakdown)
- Comparison table: old params → new proposed params
- Confidence level change display
- Three buttons: "Save & Update", "Discard", "Cancel"

### Step 5: Modify `ScreenMonitor._handle_logging` (~30 lines changed)
When `calibration_mode=True`:
- Store raw analysis data from `FRFAnalysisResult` (and PSD results)
- Send callback to GUI: "new signal detected, waiting for judgment"
- Pause logging (don't update `last_logged_ratio`/`last_logged_energy`) until judgment received
- Add `calibration_callback` parameter to `ScreenMonitor.__init__`
- Add `calibration_mode` flag and `_pending_calibration_signal` storage

### Step 6: Modify `MonitorControlGUI.__init__` (~20 lines)
- Add `calibration_mode: bool = False` parameter
- Add instance variables: `calibration_estimator`, `live_calibration_buffer`, `live_cal_good_count`, `live_cal_bad_count`, `_pending_signal`, `auto_update_cal`

### Step 7: Modify `MonitorControlGUI._setup_main_gui` (~120 lines)
Conditionally build either:
- **Calibration mode**: "Calibration Estimates" LabelFrame with read-only parameter displays + "Signal Judgment" frame (Good/Bad/Ignore buttons) + "Finish Calibration" button
- **Normal mode (post-calibration)**: Standard "Analysis Parameters (Live)" section + calibration status bar + Live Calibration buttons (Good/Bad)

Both modes get the calibration status bar (`_update_calibration_status` method with 5-level color coding).

### Step 8: Calibration UI Methods (~150 lines)
New methods on `MonitorControlGUI`:
- `_setup_calibration_panel()` — build calibration-mode UI
- `_setup_normal_params_panel()` — build normal-mode UI (extended with live cal buttons)
- `_update_calibration_status(level, total_signals)` — color-coded status bar
- `_on_cal_good_click()`, `_on_cal_bad_click()`, `_on_cal_ignore_click()` — calibration judgment handlers
- `_on_live_good_click()`, `_on_live_bad_click()` — live monitoring feedback handlers
- `_apply_estimated_params()` — push estimator results to AppConfig
- `_finish_calibration()` — show summary dialog, transition to normal mode
- `_on_calibration_signal(signal_data)` — callback from ScreenMonitor
- `_show_live_calibration_save_dialog()` — show post-monitoring prompt
- `_update_calibration_estimates_display()` — refresh read-only param displays

### Step 9: Modify `_toggle_monitoring` (~15 lines)
On stop: check `live_calibration_buffer`, show `LiveCalibrationSaveDialog` if non-empty.

### Step 10: Calibration Persistence (~50 lines)
- Modify `ScreenMonitor._load_config` to load `_calibration` section, reconstruct estimator
- Modify `ConfigToolWindow._save_config` to save `_calibration` section
- Add `_save_calibration_to_config()` method

### Step 11: Modify Entry Point (~20 lines)
After config selection, insert `CalibrationChoiceDialog`. Pass `calibration_mode=True/False` to `MonitorControlGUI`.

### Step 12: Version strings
Update all version references to v0.9 (already partially done in worktree, need to re-apply after copying main branch file).

## Total estimated new code: ~800-900 lines
## Files modified: `monitor_app.py`, `RUN_USMA_PORTABLE.bat`
