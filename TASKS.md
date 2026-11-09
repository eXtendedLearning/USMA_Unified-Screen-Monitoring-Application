# USMA Project Review Tasks

## Codex (OpenAI) external review — resolved in v0.12.2

| Priority | Task | Status |
|----------|------|--------|
| P0 | Fix config hot-swap propagation in `ScreenMonitor.update_config()` | ✅ Done |
| P0 | Remove mixed callback + queue threading model for Tk updates | ✅ Done |
| P1 | Allow PSD-only / coherence-only sessions to log and emit hits | ✅ Done |
| P1 | Preserve signed directions in UNV export | ✅ Done |
| P1 | Reload calibration state when user switches configs at runtime | ✅ Done |
| P2 | Fix `ConfigToolWindow` state desync after loading an existing config | ✅ Done |
| P2 | Make PSD image logs use PSD thresholds instead of FRF thresholds | ✅ Done |
| P2 | Add automated regression suite (tests/) | ⏳ Deferred — see below |

## September 2026 audit — `docs/AUDIT_2026-09.md`

Blockers found and fixed (details in `CHANGELOG.md` → Unreleased):

| ID | Task | Status |
|----|------|--------|
| C-1 | `np.trapz` removed in NumPy 2.0; Level 3/4 calibration raised `AttributeError` | ✅ Fixed |
| D-2 | Mixed CRLF/LF; `git diff` showed all 18 files as rewritten | ✅ Fixed by `.gitattributes` — no renormalise commit needed (verified) |
| C-2 | Calibration lowpass sweep filtered the residual instead of the signal | ✅ Fixed |
| C-3 | Same sweep hardcoded filter order 7 and ratio 0.10 | ✅ Fixed |
| C-4 | Bayesian `relative_residual_ratio` was fed a constant | ✅ Fixed |
| C-5 | Swallowed filter exception produced a silent GOOD verdict | ✅ Fixed |
| T-1…T-11 | Documentation errors in `THEORY.md` and `README.md` | ✅ Fixed |
| D-1 | `THEORY.md` excluded from version control | ✅ Fixed |
| D-3 | `requirements.txt` unpinned | ✅ Fixed |
| D-4 | No agent/context instructions in the repository | ✅ `AGENTS.md` |

Open, in recommended order:

| # | Priority | Task |
|---|----------|------|
| 1 | P0 | Add `tests/`, seeded with a regression fixture over `configs/PSD_FRF_ONLY_SMNS.json` — the only stored session that exercises the Level 3/4 path where C-1 lived (C-9) |
| 2 | P0 | Label more calibration data: ≥ 30 signals per family, GOOD/BAD split no worse than 2:1. Gates everything in the method (S-1) and the V2 spec's §7 validation |
| 3 | P1 | Add a separability guard: warn when a threshold falls outside the observed metric range, when a metric's AUC is within ~0.15 of 0.5, or when an estimate saturates at a sweep-grid boundary (S-2, S-5) |
| 4 | P1 | Implement `docs/SPEC_CLASSIFICATION_V2.md`, specifying the FFT window explicitly (S-4) |
| 5 | P2 | Housekeeping: rename the mislabelled `get_distribution_data()` key (C-6); remove dead `clear_signals()` and correct the v0.12.0 CHANGELOG claim (C-7); unused/star imports (C-8) |
| 6 | P2 | Capture-path performance: hoist `mss.mss()` out of the frame loop, grab only the ROI bounding box, stop copying every ROI per frame, keep images off the `frame_update` event (P-1…P-4) |
| 7 | P2 | Cache `get_threshold_history()` and `to_dict()` refits (P-5, P-6) |
