# USMA Classification Methods v2 — Design Specification

**Status:** Draft for review — no code changes yet.
**Scope:** Replaces the undifferentiated 4-vote FRF/PSD classifier with three independent, individually toggleable channels: PSD (existing methods, retested), FRF (new ripple method), Coherence (new area/pairwise method with OCR-free run tracking).
**Companion documents:** `THEORY.md` (current implementation), `usma/analysis/{signal,coherence,classifier}.py`.

---

## 1. Motivation

The current classifier (`HitClassifier.classify_hit`) applies the same two methods — FFT high-frequency energy ratio and lowpass residual exceedance (THEORY.md §2) — to both FRF and PSD ROIs, with fixed, expert-calibrated cutoffs. Field experience shows this cannot separate good from bad hits even after calibration. The root causes are physical, not algorithmic:

1. **The FRF is run-dependent.** In roving-hammer testing, the FRF magnitude H(f) at excitation point j is a weighted sum of modal contributions whose weights are the mode-shape coefficients at j. Moving the excitation point changes which modes participate and where resonances/antiresonances fall (Ewins 2000, ch. 2). A fixed spatial-frequency cutoff tuned on run r is therefore miscalibrated for run r+1: the "ripple scale" that distinguishes a noisy FRF from a clean one shifts with the underlying modal density visible in the plot window.
2. **The PSD is approximately run-invariant.** The monitored PSD is a dB-magnitude spectrum that decays smoothly with frequency; its shape is governed by the impact pulse (tip stiffness, hammer mass, contact duration), not by the excitation location. Fixed-parameter methods are therefore viable for PSD and must be evaluated on PSD in isolation.
3. **Coherence is excluded from the verdict.** The one quantity designed to measure measurement quality — the ordinary coherence function γ²(f) — is currently a separate display channel and contributes no vote.

Consequence: split the channels. PSD keeps the existing methods with independent parameters (already separated since v0.7.0); FRF gets a new scale-adaptive ripple method (§3); coherence enters the verdict with an area-based method and hit-pair logic (§4); every method is individually toggleable (§5).

---

## 2. Architecture

Each ROI type maps to a set of **methods**; each method produces at most one binary vote per hit, or abstains (`None`) when its preconditions are unmet:

| Channel | Method key | Basis | Availability |
|---|---|---|---|
| PSD | `psd_fft` | existing FFT energy ratio (THEORY.md §2.1) | always |
| PSD | `psd_lp` | existing LP residual exceedance (THEORY.md §2.2) | always |
| PSD | `psd_comb` | comb/ripple periodicity detector (§3.4) | always |
| FRF | `frf_ripple` | scale-adaptive ripple score (§3) | always |
| FRF | `frf_fft`, `frf_lp` | legacy methods, kept for comparison | default **off** |
| COH | `coh_area` | absolute area criterion (§4.3) | hit_index ≥ 2 |
| COH | `coh_pair` | pairwise area-drop criterion (§4.4) | hit_index ≥ 2 and previous hit area known |

Rules:

- A hit is classifiable if **at least one** enabled method produced a vote. If only coherence ROIs exist and `hit_index = 1`, the hit is reported as `UNCLASSIFIED (first hit)` — by design, since γ² carries no information on the first average (§4.2).
- Votes merge exactly as today (any-bad → SUSPECT, majority/all-bad → BAD; §5), preserving GUI semantics.
- `enabled_methods` (already a parameter of `classify_hit`) is extended with the new keys and exposed in the config GUI as per-method checkboxes.

All new metrics operate on the **pixel-space signal** `s[i], i = 0…N−1` (mean trace row per ROI column, THEORY.md §1.1), keeping the established property that classification is immune to y-axis miscalibration. Note the monitored FRF/PSD plots are dB-magnitude: the pixel signal is an affine image of log|H(f)|, which is the natural domain for ripple analysis (multiplicative ripple in |H| becomes additive in log-magnitude).

---

## 3. FRF channel — scale-adaptive ripple method (`frf_ripple`)

### 3.1 Definition of ripple

A clean FRF magnitude in dB is piecewise smooth: a small number of resonance peaks and antiresonance notches connected by slowly varying segments. A degraded FRF (double hit, poor contact, overload, noise) superimposes **diffuse small-scale oscillation** ("ripple") along the whole curve. The discriminating property is not the presence of high spatial frequencies per se — a sharp resonance also contains them — but how *broadly distributed* the signal energy is across spatial frequencies, and how *rough* the curve is relative to its own scale.

Let ŝ = s − mean(s), S(k) = |rfft(ŝ)|², k = 0…N/2 the meta-spectrum (spectrum of the plotted curve, in cycles/pixel; distinct from the physical frequency axis of the FRF itself).

### 3.2 Metrics (all dimensionless, self-normalizing)

**(a) Spectral rolloff R_q** — the smallest normalized spatial frequency f_q ∈ [0, 0.5] such that

    Σ_{k: x_k ≤ f_q} S(k) ≥ q · Σ_k S(k),    default q = 0.95.

A clean FRF concentrates energy at low meta-frequencies (few smooth features) → small R_q. Diffuse ripple pushes R_q up. This is the formalization of "how broad the spectrum is": no fixed cutoff, the statistic adapts to each signal.

**(b) Roughness ratio ρ** — normalized second-difference energy:

    ρ = Var(Δ²s) / Var(s),    Δ²s[i] = s[i+1] − 2 s[i] + s[i−1].

ρ is invariant to affine amplitude scaling and to the physical y-range of the plot. For a smooth curve sampled at N ≫ number of features, ρ ≪ 1; broadband ripple drives ρ up by orders of magnitude. (For white noise ρ → 6; for a pure sinusoid of normalized frequency f, ρ = 16 sin⁴(πf), so ρ directly encodes the dominant ripple scale.)

**(c) Spectral flatness (Wiener entropy)**

    SFM = exp( mean_k log S(k) ) / mean_k S(k),   computed for k ≥ 1 (DC excluded).

SFM → 1 for flat (noise-like) meta-spectra, → 0 for spectra dominated by a few low-frequency components. Complements R_q: R_q measures *where* the energy sits, SFM measures *how concentrated* it is.

### 3.3 Antiresonance valley masking

At antiresonances the response falls to the noise floor of the acquisition chain, so local roughness there is physical and expected even for perfect hits (coherence also drops there; Ewins 2000 §3; Bendat & Piersol 2010 §9). Without masking, good hits at low-response points get flagged. Procedure:

1. Smooth s with a wide moving median (window w_med = N/20, odd) → baseline b.
2. Mark column i as *valley* if s[i] < percentile_10(b) + δ_v, default δ_v = 0.05·(max b − min b).
3. Compute ρ on the unmasked columns only; compute S(k) after replacing masked segments by linear interpolation of b (prevents mask edges from injecting spurious high frequencies).

If a coherence ROI is present and axis-valid, an optional refinement weights columns by the resampled γ²(f) instead of the binary mask (`frf_ripple_coh_weighting`, default off — requires x-axis alignment between the FRF and coherence ROIs, which the user must guarantee).

### 3.4 Double-hit comb detector (`psd_comb`, also reported for FRF)

Two impacts separated by Δt multiply the force spectrum by |1 + α e^{−i2πfΔt}|, producing quasi-periodic ripple with spacing 1/Δt in physical frequency (standard impact-testing result; Ewins 2000 §3.6, ISO 7626-5). In the meta-spectrum this appears as a **distinct isolated peak** at the corresponding spatial frequency, unlike broadband noise. Detector:

    comb_score = max_{k ∈ K} S(k) / median_{k ∈ K} S(k),   K = {k : x_k > R_50},

flag if comb_score > θ_comb (default 30, calibratable) **and** the peak is isolated (prominence over ±3 bins). Applied to the PSD signal as a third PSD vote and reported (not voted) on FRF for diagnostics. This targets exactly the "decreasing dB function with ripples on double hits" morphology of the monitored PSD.

### 3.5 Decision rule and auto-fit

Each metric is converted to a z-like score against **within-run running statistics**: after each classified-good hit, update run medians m and MAD of (R_q, ρ, SFM). Vote BAD if at least two of three metrics exceed m + κ·MAD (default κ = 4) **or** any single metric exceeds its absolute ceiling (defaults: R_q > 0.25, ρ > 0.5, SFM > 0.3 — calibratable via the existing hybrid engine, THEORY.md §5). The absolute ceilings make the first hit of a run classifiable (no running stats yet); the adaptive part delivers the required per-run self-fitting.

---

## 4. Coherence channel

### 4.1 Metric

`CoherenceAnalyzer.analyze` already computes the required quantity. Define the **normalized coherence area**

    A = (1/Δf) ∫_ROI γ²(f) df = 1 − normalized_badness,

with Δf the ROI frequency span. A ∈ [0, 1]; A ≈ 1 for an ideal measurement; localized coherence valleys (mispositioned hit, double hit, irregular contact) reduce A. Expert experience places the good/bad boundary near A ≈ 0.7, consistent with the existing default `coherence_threshold = 0.30` on badness.

### 4.2 First-hit degeneracy — theoretical basis

The ordinary coherence estimated from n_d averaged records satisfies γ̂² ≡ 1 identically when n_d = 1, regardless of the data (Bendat & Piersol 2010, ch. 9; the estimator's bias term collapses the statistic to unity for a single average). Therefore the first hit of a run **cannot** be classified by coherence — the vote must abstain, and if coherence is the only ROI, the hit is `UNCLASSIFIED (first hit)`.

### 4.3 Absolute criterion (`coh_area`)

For hit_index k ≥ 2: vote BAD if A_k < θ_A (default 0.70). Under cumulative averaging this also catches slow degradation across the run. Optional band refinement: reuse the existing 4-band `band_badness` and require the violation in ≥ 2 bands to reduce sensitivity to a single physical antiresonance dip.

### 4.4 Pairwise criterion (`coh_pair`)

Analyzers recompute γ² cumulatively over averages 1…k, so a single bad hit at index k produces a **drop** A_k < A_{k−1} that the absolute threshold may miss (and cannot attribute). Vote BAD on hit k if

    A_{k−1} − A_k > δ_pair,    default δ_pair = 0.05 (calibratable).

This isolates *which* hit degraded the run — information the absolute criterion cannot provide. Both criteria are independent toggles; the existing run-start degradation tracker (`coherence_degradation_pct`) remains as a third, non-voting warning channel.

### 4.5 OCR-free run/hit tracking

Run and hit indices are currently unavailable in practice (OCR unreliable). The coherence display itself provides a robust run-start signature: after hit 1, γ̂² ≡ 1 (§4.2), the plotting software autoscales the y-axis to a span of order 10⁻¹²…10⁻⁹, and the rendered trace degenerates into full-height numerical artifacts. Two independent detectors, either of which declares **run start**:

**(a) Axis-strip fingerprint.** At calibration time, capture a reference image T_ref of the y-axis tick-label strip (a user-drawn sub-ROI left of the coherence plot) while a normal 0–1 axis is displayed. Per frame, compute the normalized cross-correlation NCC(T_ref, T_now) on the grayscale strips. The degenerate axis renders long labels ("1.0000000001"-style), visually unrelated to "0.2 0.4 0.6 0.8 1.0" → NCC drops far below the matched case. Declare degenerate if NCC < θ_ncc (default 0.5). No text recognition involved — pure template similarity, robust where OCR fails.

**(b) Trace-statistics fallback.** Physical coherence curves after ≥ 2 averages are piecewise smooth with bounded roughness; the autoscale artifact is dense, full-height noise. Declare degenerate if ρ_coh = Var(Δ²p)/Var(p) > θ_ρ (default 2.0, vs ≲ 0.1 for genuine curves; p = coherence pixel signal) **and** column coverage of the mask exceeds 0.9.

State machine: degenerate detected on a *new* stable frame → `run_index += 1, hit_index = 1`, coherence votes abstain. Each subsequent stable frame change (existing frame-change detection) → `hit_index += 1`, store A_k. Non-monotonic safety: if a degenerate frame appears mid-run, treat as new run (analyzer was reset). OCR-derived run/hit numbers, when available, are logged alongside for cross-validation but never gate the state machine ("backdoor" for a future OCR fix).

---

## 5. Classifier integration

`classify_hit` gains: (i) the new method keys in `enabled_methods` (defaults: `psd_fft`, `psd_lp`, `psd_comb`, `frf_ripple`, `coh_area`, `coh_pair` = on; `frf_fft`, `frf_lp` = off); (ii) abstention — methods return `True`/`False`/`None`, and only non-None votes enter the verdict; (iii) verdict text carries the abstention reason for the first-hit case. Severity mapping unchanged: no bad votes → GOOD (green); some → SUSPECT (orange); all channels with votes bad → BAD (red).

---

## 6. Parameters (new; all exposed in config + calibration engine)

| Parameter | Default | Range | Meaning |
|---|---|---|---|
| `frf_rolloff_q` | 0.95 | 0.8–0.99 | Energy quantile for rolloff R_q |
| `frf_rolloff_max` | 0.25 | 0–0.5 | Absolute ceiling on R_q |
| `frf_roughness_max` | 0.5 | 0–6 | Absolute ceiling on ρ |
| `frf_sfm_max` | 0.3 | 0–1 | Absolute ceiling on SFM |
| `frf_adaptive_kappa` | 4 | 2–8 | MAD multiplier for within-run adaptation |
| `frf_valley_delta` | 0.05 | 0–0.2 | Valley-mask depth fraction (§3.3) |
| `psd_comb_threshold` | 30 | 5–100 | Comb peak-to-median ratio |
| `coh_area_threshold` | 0.70 | 0–1 | Min area A for GOOD (= 1 − `coherence_threshold`) |
| `coh_pair_drop` | 0.05 | 0–0.5 | Max allowed A_{k−1} − A_k |
| `coh_axis_ncc_threshold` | 0.5 | 0–1 | NCC below which axis is degenerate |
| `coh_degenerate_roughness` | 2.0 | 0.5–6 | ρ_coh above which trace is degenerate |

Defaults are engineering estimates; all thresholds enter the existing hybrid calibration engine (THEORY.md §5) so labelled sessions refit them.

---

## 7. Validation plan

1. **Retrospective:** replay `calibration_data/` sessions (incl. `PSD_FRF_ONLY`); report per-method confusion matrices against expert labels; verify legacy methods on PSD-only and quantify the claimed FRF failure.
2. **Metric separation:** distributions of R_q, ρ, SFM for labelled good vs bad FRFs; ROC per metric and for the 2-of-3 rule.
3. **Run-start detector:** measure NCC and ρ_coh on logged first-hit and steady-state frames; target zero missed run starts (a miss corrupts every subsequent pairwise comparison).
4. **Ablation:** classifier accuracy with each method toggled off, to confirm each channel contributes.

## 8. Known limitations

- Valley masking assumes antiresonance notches are narrow relative to N/20; very dense modal spacing violates this.
- The pairwise criterion assumes cumulative averaging in the monitored analyzer; if the analyzer uses block-wise restarts, δ_pair must be recalibrated.
- Axis-strip fingerprint requires one extra calibration capture and assumes the plot theme (font, colors) does not change mid-session.
- ρ thresholds depend weakly on ROI width N (second-difference operator is scale-local); ROIs narrower than ~150 px need rescaled defaults.

## 9. References

- J. S. Bendat, A. G. Piersol, *Random Data: Analysis and Measurement Procedures*, 4th ed., Wiley, 2010 — coherence estimation, single-average degeneracy (ch. 9), random error of γ̂².
- D. J. Ewins, *Modal Testing: Theory, Practice and Application*, 2nd ed., Research Studies Press, 2000 — FRF spatial dependence, impact testing practice, double-hit spectra.
- ISO 7626-5:2019, *Mechanical vibration and shock — Experimental determination of mechanical mobility — Part 5: Measurements using impact excitation with an exciter which is not attached to the structure* — impact quality requirements.
- A. Brandt, *Noise and Vibration Analysis: Signal Analysis and Experimental Procedures*, Wiley, 2011 — coherence interpretation, impact-testing quality checks.
