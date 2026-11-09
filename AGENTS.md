# AGENTS.md — working instructions for USMA

Repository instructions for any AI coding agent (and for humans skimming before a
change). Model-agnostic: no prompt scaffolding, no role-play, no tool-specific
syntax. Keep it short enough to stay read.

---

## 1. What this project is

USMA (Unified Screen Monitoring Application) reconstructs FRF, PSD and coherence
curves **from screen pixels** of a running Siemens Simcenter TestLab session,
classifies each impact-hammer hit, and exports UNV Dataset 58. It is a research
instrument used in published work at DIISM / Università Politecnica delle Marche.

Two consequences follow, and they govern everything below:

- **Numerical claims must be true.** Results from this code appear in conference
  and journal submissions. A plausible-looking number is worse than no number.
- **`THEORY.md` is normative, not descriptive.** It is the canonical reference for
  every mathematical operation in the codebase and it is cited externally.

## 2. Non-negotiables

1. **Any change to a mathematical operation must be reflected in `THEORY.md` in
   the same change.** New operation → new section. Changed formula, default, or
   normalisation → update the text, the default table in §8, and Appendix A. A
   maths change that leaves `THEORY.md` stale is an incomplete change.
2. **Do not invent empirical claims.** Statements of the form "X separates good
   from bad better than Y" require a measured number on the labelled data in
   `configs/*.json` → `_calibration.signals`. If you have not measured it, say so
   explicitly instead of asserting it. This rule exists because such claims were
   found in `THEORY.md` and were contradicted by the project's own data
   (`docs/AUDIT_2026-09.md`, findings T-3 and T-4).
3. **Verify before reporting.** Run the code against the real stored calibration
   data rather than reasoning about what it should do. Several long-standing
   defects were invisible to reading and obvious on execution.
4. **Never silently swallow a numerical failure.** The codebase already has ~39
   broad `except` blocks; several converted a crash into a wrong scientific
   result (finding C-5). If a computation cannot be performed, log it at WARNING
   and mark the result invalid — do not fall through to a default that reads as
   a passing verdict.
5. **Do not commit.** Leave changes in the working tree for review unless
   explicitly asked. See §6 on line endings before running any `git` command.

## 3. Layout

```
monitor_app.py          Thin launcher (sets Windows DPI awareness, calls usma.__main__)
usma/
  models.py             Dataclasses, AppConfig + JSON loader, calibration engine,
                        trapezoid() NumPy-compat helper
  monitor.py            ScreenMonitor: capture loop, per-frame orchestration, logging
  utils.py, audio.py, calibration.py
  analysis/
    signal.py           SignalAnalyzer.analyze() — FFT + lowpass methods
    coherence.py        CoherenceAnalyzer — badness integral
    classifier.py       HitClassifier.classify_hit() — combines the 4 votes
    ocr.py              Tesseract wrapper for run/point/status ROIs
  export/               unv.py (Dataset 58), image_logger.py
  gui/                  main_window.py, config_tool.py, graph_viewer.py,
                        cal_diagnostics.py, hsv_calibration.py, dialogs.py, overlay.py
  theory/               In-app wiki viewer + pages/ adapted from THEORY.md
docs/
  SPEC_CLASSIFICATION_V2.md   Approved design for replacing the FRF/PSD methods
  AUDIT_2026-09.md            Findings and evidence from the last full audit
THEORY.md               Canonical maths reference — see §2.1
CHANGELOG.md, TASKS.md, README.md
```

Not in git, present on disk: `python/` (portable CPython 3.11.9 + deps),
`external/tesseract/`, `configs/`, `logs/`, `image_logs/`, `signal_logs/`,
`calibration_data/`.

## 4. Running it

Windows, portable runtime, no installation:

```
RUN_USMA_PORTABLE.bat            # normal start
python\python.exe monitor_app.py # equivalent
python\python.exe -m usma        # same entry point
```

There is **no test suite**. Until one exists, verify a change by (a) byte-compiling
the touched modules and (b) exercising the real data:

```
python\python.exe -m py_compile usma\models.py usma\analysis\signal.py
python\python.exe -c "import json; from usma.models import HybridCalibrationEngine as E; \
  c=json.load(open('configs/PSD_FRF_ONLY_SMNS.json'))['_calibration']; \
  e=E.from_dict(c); print(e.confidence_level, e.get_estimates())"
```

`PSD_FRF_ONLY_SMNS.json` (9 GOOD / 8 BAD) is the useful regression fixture: it is
the only stored set balanced enough to make `ROCYoudenEstimator` eligible, which
is where the Level 3/4 code path lives.

Adding a real `tests/` directory is the outstanding P2 item in `TASKS.md`.

## 5. Conventions that are easy to get wrong

- **Two different frequency normalisations.** `fft_cutoff_frequency` is in
  cycles/pixel on a `[0, 0.5]` axis (`rfftfreq(N, 1)`). `lowpass_cutoff` is the
  `Wn` argument of `scipy.signal.butter`, where `1.0` is Nyquist — a `[0, 1]`
  axis. The same number means two frequencies a factor of 2 apart. Both default
  to `0.07`; that is a digit coincidence, not a coupling. `THEORY.md` §2.1.
- **Pixel-space vs physical-space.** Classification runs entirely on
  `signal_pixels`; `signal_physical` exists only for display and UNV export. Do
  not route a classification metric through the physical signal — it reintroduces
  the y-axis calibration dependency that v0.10.2 removed.
- **Field names lie in one place.** `FRFAnalysisResult.filtered_physical` and
  `.residual_physical` hold **pixel-space** data. Renaming them is desirable but
  breaks stored `_calibration` blocks; do it with a migration, not in passing.
- **`y_scale_type` on `MonitoringRegion` is unused metadata.** Reconstruction is
  always affine. `dB` axes are fine (dB is linear in the plotted quantity);
  `log`/`ln` are silently wrong.
- **The exceedance ratio is scale-invariant by construction**
  (`threshold = ratio × max|residual|`). It measures residual distribution shape,
  never residual magnitude. Do not describe it as measuring "how rough" a signal
  is.
- **NumPy trapezoidal integration**: always `from usma.models import trapezoid`.
  `np.trapz` was removed in NumPy 2.0; `np.trapezoid` does not exist before it.
- **GUI threading**: `ScreenMonitor` runs on a worker thread and communicates with
  Tk **only** through `event_queue` (`MonitorEvent`). The legacy
  `update_callback` / `plot_callback` arguments are kept for API compatibility and
  must not be called from the worker thread.

## 6. Line endings

The repository had no `.gitattributes` until 2026-09 and source files carried
**mixed CRLF and LF within the same file**, so `git diff` reported every tracked
file as fully rewritten (6458 insertions against 6458 deletions) and review was
impossible. `.gitattributes` now normalises to LF (CRLF for `.bat`/`.cmd`), which
fixed it outright — `git diff` immediately dropped to real changes only.

**No renormalisation commit is needed.** This was verified rather than assumed: a
`git add --renormalize .` run against a scratch `GIT_INDEX_FILE` stages nothing
beyond the files actually edited. The index already held LF for every text file;
only the *comparison* was broken, and `.gitattributes` repairs that at read time.

Working-tree files still physically carry mixed terminators. Git normalises on
`add`, so this converges on its own and a fresh clone is clean. When editing a file
in place, preserve each line's existing terminator rather than rewriting the whole
file — that keeps diffs minimal until the files turn over naturally.

## 7. Current state

- `docs/SPEC_CLASSIFICATION_V2.md` is the approved direction: the FRF FFT/LP
  methods are to be replaced by a scale-adaptive ripple method, PSD gains a comb
  detector, and coherence enters the verdict. Do not invest in tuning the legacy
  FRF methods.
- `docs/AUDIT_2026-09.md` lists what was fixed, what was deliberately left alone,
  and why. Read its "Open items" section before proposing work.
