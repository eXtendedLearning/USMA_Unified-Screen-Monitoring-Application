# USMA — Changelog

All notable changes to this project will be documented in this file.

---

## v0.12.3 — Calibration Diagnostics Persistence, Overlay Geometry & View Scaling *(current)*

- **Runtime version aligned to v0.12.3** — `APP_VERSION` now reports 0.12.3 across the package and portable launcher.
- **DPI-aware startup on Windows** — the package entry point sets process DPI awareness before Tk starts, keeping Tk geometry and `mss` screen-capture coordinates in the same physical pixel space.
- **Multi-monitor overlay geometry fixed** — ROI overlays now use the virtual desktop bounds and apply per-monitor offsets, so configured regions appear on the selected monitor even with non-primary or multi-monitor layouts.
- **ROI selection wording clarified** — configuration dialogs now describe ROI setup in clearer, mode-specific language.
- **Calibration diagnostics preserved after finish** — completed calibration sessions keep their stored signals available for diagnostics instead of clearing them immediately after thresholds are applied.
- **Calibration export persistence tightened** — live calibration additions are saved immediately, signal plot filenames use stable enumeration, and clearing calibration no longer creates empty `calibration_data` folders.
- **GUI view controls added** — the main window sizes itself to the active monitor and includes a `View...` dialog for window size and Tk scaling presets.

## v0.12.2 — Config Hot-Swap, Queue-Only GUI Bridge & Export Fixes

Follow-up based on an external Codex review of the launcher → monitor → analysis → export → GUI pipeline (see `TASKS.md`). All P0/P1/P2 code issues flagged in that review are addressed here; the optional test-suite task is deferred.

- **Config hot-swap now fully rebinds runtime state** — `ScreenMonitor.update_config()` previously only replaced `self.app_config`, leaving `SignalAnalyzer`, `CoherenceAnalyzer`, and `ImageLogger` pointing at the old object. It now also:
  - re-assigns `app_config` on all three collaborators so analysis thresholds, HSV values, monitor selection and image-log annotations are all picked up;
  - clears `_ocr_cache`, `_ocr_roi_hash`, `hit_counters`, `run_history`, `coherence_tracking`, `last_logged_*` and `last_known_*` so stale per-run state doesn't leak across configs;
  - drains the `event_queue` of any frames queued against the previous config.
- **Queue-only Tk bridge** — the monitor previously drove the GUI through BOTH a thread-safe `event_queue` AND direct `update_callback` / `plot_callback` invocations from the worker thread. The direct callbacks are removed from `_monitoring_loop()` and `_handle_logging()`; `MonitorEvent`s are now the single handoff. `ScreenMonitor._monitoring_loop()` also emits a terminal `"stopped"` event when it exits (either via `stop()` or the consecutive-error guard).
- **Poll loop no longer drops final events** — `MonitorControlGUI._poll_monitor_queue()` used to terminate as soon as `monitor.running` flipped to `False`, which could discard queued `"error"` messages. It now keeps rescheduling until BOTH the worker thread has finished AND the queue is empty, and handles the new `"stopped"` event.
- **PSD-only and coherence-only sessions now emit hits** — `_handle_logging()` previously returned immediately when `frame_result.frf_results` was empty, making the downstream PSD/coherence code unreachable. The entry guard now checks FRF ∪ PSD ∪ coherence, and hit-change detection is computed per signal family (`last_logged_ratio` for FRF, new `last_logged_psd_ratio` for PSD, new `last_logged_coh_badness` for coherence).
- **UNV exporter preserves signed DOFs** — `parse_dof()` used to collapse `+X/-X`, `+Y/-Y`, `+Z/-Z` into unsigned 1/2/3, and identifier line 5 always wrote `+{dir}`. It now returns signed DOFs (`±1`, `±2`, `±3`) and writes the actual sign in both the DOF record and the `FRF\\{point}:±{axis}` identifier — modal-analysis datasets no longer alias opposing excitation/response orientations.
- **Calibration state reloaded on config switch** — new `MonitorControlGUI._reload_calibration_from_current_config()` helper rebuilds `HybridCalibrationEngine` from the loaded config's `_calibration` block, syncs `graph_viewer.calibration_engine`, refreshes the status bar and diagnostic plots, and re-applies calibrated params. Called from both `_load_config()` (Load… dialog) and `_launch_config_tool()` (post-save).
- **ConfigToolWindow state desync on load fixed** — `_load_config()` now updates `current_config_path` (so "Save As" defaults correctly) and mirrors `monitor_index_var` from the loaded config (so subsequent screenshots capture the right monitor). `_update_ui_from_data()` also re-syncs `monitor_index_var` so programmatic config loads stay consistent.
- **PSD image logs use PSD thresholds** — `ImageLogger._create_fft_plot_safe()` and `_create_lowpass_comparison_plot_safe()` were annotating every plot with `app_config.fft_cutoff_frequency` / `lowpass_cutoff` (FRF values), even for PSD hits. `log_hit()` now accepts a `signal_type` hint (defaulting to inference from `region.roi_type`) and passes the resolved `AnalysisParams` into the plot helpers, so saved PSD plots show the actual PSD cutoff/order used at runtime.

## v0.12.1 — Calibration Diagnostics Rework, Theory Additions & Bugfixes

- **Dedicated Calibration Diagnostics window** — `Cal. Diagnostics` is no longer a spurious entry in the Signal-source dropdown. It now opens in its own non-modal `tk.Toplevel` (`usma/gui/cal_diagnostics.py`) via a new **"Cal. Diagnostics"** button on the Graph Viewer nav bar:
  - `SIGNAL_SOURCES` cleaned up to `['All', 'FRF', 'PSD', 'Coherence']`; the old `_plot_calibration_diagnostics` method, `cal_info_frame` row, and related early-return guards removed from `GraphViewerFrame`.
  - Button is greyed out until the engine has ≥ 6 stored signals; `update_calibration_diagnostics()` now manages that state and forwards live updates to the open window.
  - Singleton behaviour — re-clicking the button raises the existing window via `deiconify()` + `lift()` rather than duplicating it.
  - Window layout: header (Good/Bad/Total/Confidence summary + ⓘ help + Refresh), `ttk.PanedWindow` with a left signal table + per-signal detail panel and a right 12×8 matplotlib figure, toolbar with **Export PNG** and **Close**.
  - **Signal table** — sortable `ttk.Treeview` (click column headers to toggle asc/desc) listing every calibration signal with columns `#, Hit Key, Type, Judgment, Energy Ratio, Exc. Ratio, Max Residual`; rows colour-coded green (GOOD) / red (BAD). A filter combobox (`All / FRF only / PSD only`) filters both the table and which signals contribute to the histograms.
  - **Per-signal detail panel** — selecting a row draws that signal's `signal_physical` as a line plot and shows `"Energy Ratio … | Exc. Ratio … | Max |Residual| … | Dynamic Thr …"` underneath, so the user can eyeball whether each Good/Bad call was correctly labelled.
  - **Diagnostic plots** — FRF vs. PSD distinguished via `hatch='//'` on PSD histograms; selecting a table row overlays that signal's metric value as a dashed gold vertical line across the corresponding distribution subplot; ROC dots gain a `"thr = <value>"` annotation in the parameter's native units; convergence subplot adds translucent 95 % credible-interval bands from a new `get_threshold_history(include_ci=True)` extension.
  - `HybridCalibrationEngine` gains `get_signal_table_data()` (minimal rows for the table) and an extended `get_threshold_history(include_ci: bool = False)` (replays Bayesian CI at each step).
- **Signal-plot hit-navigation bugfix** — `_plot_all_signals_stacked()` now respects `self.current_hit_index` instead of always using the latest hit of each type; the currently selected hit keeps its slot in the stack (highlighted with a gold ★ title and thicker line), while the other signal types show their most recent entry for context. `_update_plot()` also snaps `current_hit_index` onto a filter-matching hit when it drifts out of range.
- **THEORY.md — three new reading guides in §7.2** (calibration diagnostic plots):
  - §7.2.1 explains what `density=True` means on the distribution Y-axis — how `bar_height` is computed, why density is preferred over raw counts when GOOD/BAD sample sizes differ, and what the relative position of the two humps actually tells you.
  - A dedicated subsection explains the 95 % **credible interval** band, including the Bayesian-vs-frequentist distinction and how to read "wide band", "narrow band" and "white line outside the band" cases. §5.3 gets a matching interpretation paragraph on the Bayesian estimator itself.
  - §7.2.2 is a full ROC-reading guide — axes, diagonal, AUC rule-of-thumb, the Youden dot, the three per-metric curves, and what to do when all AUCs are below 0.75.
- **THEORY.md — Practical Tuning Guide reformatted** — the narrow-viewer-hostile markdown table replaced with a vertical definition-list: one short paragraph per goal (FFT too sensitive / not sensitive enough, LP not firing / firing on clean hits, LP Order). Renders cleanly in the in-app Theory Wiki viewer.

## v0.12.0 — Calibration Cleanup, Diagnostic Labels & Theory Wiki

- **Calibration signal pool now purged correctly** — the "Similar Signal Detected" popup no longer fires against stale session data:
  - New `HybridCalibrationEngine.clear_signals()` method empties `_all_signals` and resets `good_count` / `bad_count` while preserving the fitted estimator state (thresholds stay applied)
  - Called at the end of `_finish_calibration()` so subsequent live-calibration hits are compared against an empty pool, not the finished session's population
  - `_clear_calibration()` now syncs `graph_viewer.calibration_engine` and re-renders the diagnostic plots, so the "Cal. Diagnostics" view correctly shows the empty state after a Clear Cal.
- **Axis labels on calibration distribution plots** — `_plot_calibration_diagnostics()` now sets `xlabel` (FFT Energy Ratio / Exceedance Ratio / Relative Residual Ratio) and `ylabel` ("Density") on each of the three histogram subplots, matching the existing labels on ROC and convergence plots. A `tight_layout(pad=1.0)` call at the end of the method prevents clipping at smaller layout sizes.
- **Theory Wiki — in-app ⓘ help pages** — a contextual help system linking the GUI to the mathematical reference:
  - New `usma/theory/` package with `TheoryWikiViewer` (dark-themed `tk.Toplevel` that renders pseudo-markdown `.txt` files), `show_theory_page()` public API with one-instance-per-page registry, and a `make_info_button()` DRY helper
  - **15 theory pages** under `usma/theory/pages/`: `signal_reconstruction`, `fft_method`, `fft_cutoff_frequency`, `fft_energy_ratio_threshold`, `lowpass_method`, `lowpass_cutoff`, `lowpass_filter_order`, `residual_threshold`, `exceedance_ratio_threshold`, `coherence_analysis`, `hit_classification`, `calibration_engine`, `hsv_calibration`, `practical_tuning`, `default_parameters` — all adapted from `THEORY.md`
  - ⓘ buttons wired throughout: all 6 parameter spinboxes in both the FRF and PSD sections, the Classification Methods lights row, the classification result banner, the coherence readout, the calibration panel, the live-calibration section (links to both `calibration_engine` and `practical_tuning`), the calibration status bar, the HSV calibration window, and the Config Tool's Y-axis fields
  - Pop-ups are non-modal, resizable, one-instance-per-page (re-clicking raises the existing window instead of duplicating), render `#` headings, triple-backtick code blocks, `|`-delimited tables, `**bold**` inline markers, `-` bullets, and `See also:` footers
  - Cal. Diagnostics view gains an ⓘ header row (shown only when that signal source is active) linking to the `calibration_engine` page
  - Pure Tkinter — no new dependencies

## v0.11.0 — Pixel-Space LP with Relative Residual Threshold

- **LP analysis reworked** — operates on `signal_pixels` (pixel-space) instead of `signal_physical`, making it immune to Y-axis calibration errors
- **Relative residual threshold** — new `relative_residual_ratio` parameter (default 0.10) replaces the absolute `residual_threshold`; the threshold is computed as `ratio × max(|residual|)` per signal, making it dimensionless and scale-invariant
- **`sosfiltfilt` filter** — Butterworth filter switched from `b,a` + `filtfilt` to second-order sections + `sosfiltfilt`, fixing numerical instability at high filter orders (7+)
- **New result fields** — `FRFAnalysisResult`, `LightweightHitData` gain `max_abs_residual` and `dynamic_threshold` for per-hit display and calibration
- **Calibration engines updated** — `PercentileBoundaryEstimator` sweeps candidate ratios for best Good/Bad separation; `ROCYoudenEstimator` sweeps ratios with Youden's J; `BayesianThresholdEstimator` grid updated to [0.02, 0.50]
- **Graph viewer** — residual plot shows pixel-space values with dynamic threshold; lowpass comparison Y-axis now labelled "pixels"; calibration diagnostics renamed to "Relative Residual Ratio"
- **Config UI** — spinbox renamed "Res.Ratio:" with range 0.01–0.50 (fraction, not percent)
- **Config migration** — old configs with `residual_threshold` automatically migrate to `relative_residual_ratio = 0.10` on load; backward-compat property aliases kept in `AppConfig`

## v0.10.1 — Calibration Robustness, Diagnostics & Layout Overhaul

- **Stricter calibration similarity detection** — the "too similar" check in `_check_signal_similarity()` is no longer tripped by every new hit:
  - NCC and cosine-similarity thresholds raised from 0.95 → 0.99
  - New peak-amplitude ratio gate (must be > 0.90 to count as duplicate)
  - Cross-correlation uses a ±10-sample lag search instead of zero-lag only
  - FFT magnitude vectors unit-normalised before cosine similarity (shape vs. magnitude decoupled)
  - Separate FFT magnitude ratio gate (> 0.90)
  - All four metrics (NCC, CosSim, AmpRatio, FFTMagRatio) logged when a signal is rejected
- **Calibration Diagnostic Visualization** — new `Cal. Diagnostics` entry in the Graph Viewer's Signal-Source selector:
  - 2×2 matplotlib layout with **Good/Bad feature-distribution histograms** for FFT energy ratio, exceedance ratio, and relative residual ratio
  - Vertical threshold lines from the merged estimator; shaded 95% Bayesian credible interval
  - **ROC curves** with Youden's J optimal-point marker and AUC (once Level 3+ is reached)
  - **Threshold convergence** line plot showing how estimates stabilise as signals accumulate
  - New helpers on `HybridCalibrationEngine`: `get_distribution_data()`, `get_roc_data()`, `get_threshold_history()`, `get_bayesian_ci()`
  - Plots refresh live after each `_cal_classify()` / `_live_cal_classify()` via `update_calibration_diagnostics()`
- **Main GUI layout overhaul** — `_setup_main_gui()` redesigned for a data-first layout:
  - Horizontal `ttk.PanedWindow` split with a draggable sash — **40 % left controls / 60 % right visualization**
  - Left column is now a scrollable `Canvas` hosting Configuration, Controls, Manual POI, Logging, Classification Methods, and Calibration/Parameters panels
  - Right column promotes the `GraphViewerFrame` to the dominant widget; prominent classification result banner (large colored status light + overall label + compact metric rows) sits above it
  - **Collapsible console** — console is now hidden by default with a "Show Console" toggle; mounted via the new `GraphViewerFrame.setup_console()` entry point
  - All existing controls preserved — no feature removed

## v0.10.0 — Package Architecture

- **Modular package** — monolithic `monitor_app.py` (6000+ lines) refactored into `usma/` package with 15 focused modules
- **Clean module boundaries** — `models.py`, `monitor.py`, `audio.py`, `calibration.py`, `utils.py`, `analysis/` (signal, ocr, coherence, classifier), `export/` (unv, image_logger), `gui/` (main_window, config_tool, graph_viewer, dialogs, hsv_calibration, overlay)
- **Backward-compatible launcher** — `monitor_app.py` remains as thin entry point; `RUN_USMA_PORTABLE.bat` unchanged
- **All v0.9.x features preserved** — calibration engine, dual FRF/PSD analysis, classification lights, resizable panels, OCR caching, exponential backoff

## v0.9.2 — UI Polish & Classification Controls

- Coherence decoupled from calibration & classification (informational only)
- Dual FRF/PSD analysis parameter panels with independent manual tuning
- Classification method indicator lights with per-method enable/disable toggle
- Resizable graph/console panels via draggable sash
- Clear Calibration button, live Good/Bad buttons in Analysis Parameters
- Monitoring loop exponential backoff, batched GC, `slots=True` dataclasses
- Configurable monitor index, improved OCR change detection, standalone config loader

## v0.9.1 — Calibration Engine Release

- **HybridCalibrationEngine** — three-tier statistical estimation (Percentile Boundary → Bayesian → ROC/Youden)
- Automatic threshold computation after 6+ classified signals (3 Good + 3 Bad minimum)
- 5-level confidence scale: Not Calibrated (red) → Robust (green)
- Calibration persistence in config JSON under `_calibration` key

## v0.9.0 — Calibration Wizard Release

- **CalibrationChoiceDialog** — post-config dialog: "Use Default Parameters" or "Calibrate with Expert Feedback"
- **Calibration Mode UI** — Good/Bad/Ignore buttons activate on signal detection; signal counter with 5-level status bar
- **Signal Source selector** — dropdown in GraphViewer to filter by FRF, PSD, or Coherence signals
- **Coherence plot callback** — coherence signals now sent to graph viewer alongside FRF and PSD
- **Y-axis scale type** — new metadata field (linear/dB/log/ln) in region editor for display context
- **Multi-signal console output** — single-line summary per hit: `[HIT #n] CLASSIFICATION | FRF | PSD | Coh | Avg`
- **Dedicated feedback labels** — PSD and Coherence now have their own rows in Live Analysis Feedback

## v0.8.0 — Coherence & OCR Release

Implementation of Coherence ROI analysis, tracking, and Averages OCR readout tracking.

## v0.7.0 — PSD Release

Implementation of PSD ROI analysis, logging, UNV output, and combined FRF+PSD hit classification.

## v0.6.0 — Foundation Release

Infrastructure update introducing shared signal processing and data structures required by upcoming PSD, Coherence, and Calibration features:

- **5 new dataclasses:** `CalibrationSignal`, `CalibrationSession`, `CoherenceAnalysisResult`, `CoherenceTrackingState`, `LightweightCoherenceData`
- **`FrameAnalysisResult` extended** with PSD result fields, coherence results dict, and current averages
- **`AppConfig` extended** with 6 independent PSD analysis parameters (`psd_fft_*`, `psd_lowpass_*`, `psd_residual_*`, `psd_exceedance_*`) and 3 coherence parameters (`coherence_threshold`, `coherence_degradation_pct`, `hits_per_run`)
- **`_reconstruct_signal_from_roi()`** extracted as shared helper — HSV→mask→signal pipeline reused by FRF, PSD, and future Coherence analysis
- **`_analyze_wave_pattern()` parameterized** with `param_prefix` — enables PSD to run with independent parameters via `_analyze_wave_pattern(roi, region, param_prefix='psd_')`
- **`classify_hit()` extracted** — centralises FRF classification logic; returns `(text, color)` tuple
- **Config backward compatible** — existing v0.5.x configs load without error; new fields default gracefully
- **3 new ROI types** in type dialog and region editor: `psd`, `coherence`, `averages`

## v0.5.x — UI & FRF Polish *(2025)*

- v0.5.2: Mask logging fix, horizontal logging layout, selective manual points, HSV calibration text entry + zoom
- v0.5.1: FRF ROI rename, UNV-only export, split-view interface, verbose logging menu
- v0.5.0: Startup wizard, HSV calibration window, mandatory ROI type selection, live analysis parameters, scrollable GUI

## v0.4 — Performance & Analysis *(2025)*

mss screen capture (2-5× faster), DPI awareness, lowpass residual analysis, dual classification, live graph viewer, UNV Dataset 58 format fix

## v0.1–v0.3 — Prototype

Initial screen monitoring, pyautogui capture, basic FRF reconstruction, early UNV export
