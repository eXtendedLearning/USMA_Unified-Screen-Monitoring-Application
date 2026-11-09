# USMA Theory and Mathematical Reference

This document is the canonical reference for **every numerical, statistical and signal-processing operation** performed by USMA. It is intended as the go-to reading whenever there is doubt about *what* a piece of code is doing to your data, *why*, and *what knob to turn* to influence it.

The document is organised top-down following the data flow:

1. Pixel extraction and signal reconstruction (raw → physical units)
2. The two analysis methods on FRF/PSD signals (FFT, Lowpass)
3. Coherence analysis
4. Hit classification (how the four method votes become a verdict)
5. The calibration engine (three estimators + hybrid merger)
6. HSV colour calibration
7. Graphical representations of every diagnostic plot
8. Default parameter table

---

# 1. Signal reconstruction and Y-axis calibration

**Source:** `usma/analysis/signal.py` (`SignalAnalyzer._reconstruct_signal_from_roi`, lines ~63–127).

Before any analysis runs, USMA must turn an HSV-masked ROI image into a one-dimensional signal in physical units (g/N, m/s², dimensionless coherence, etc.). This step is silent but it is the *foundation* on which everything else stands — if it is wrong, every threshold that follows is wrong.

## 1.1 From mask to pixel signal

The HSV mask (see §6) marks pixels belonging to the trace as white. For each column `x` of the ROI, USMA computes the *mean row* of all white pixels in that column. Columns with no white pixels are filled by linear interpolation from neighbouring columns.

```python
y_coords, x_coords = np.nonzero(mask)
unique_x, idx     = np.unique(x_coords, return_inverse=True)
sum_y             = np.bincount(idx, weights=y_coords)
count_y           = np.bincount(idx)
anchor_y          = sum_y / count_y                       # mean row per column
signal_pixels     = np.interp(np.arange(width), unique_x, anchor_y)
signal_pixels     = height - signal_pixels                # invert Y (image rows grow downward)
```

The result, `signal_pixels`, has length equal to the ROI width. Each sample is a Y position measured in pixels from the bottom of the ROI.

## 1.2 From pixel signal to physical signal

USMA assumes the ROI's vertical axis is **linearly calibrated** between two user-supplied bounds, `region.y_axis_min` and `region.y_axis_max`. The conversion is a single affine map:

```
signal_physical[i] = y_axis_min + (signal_pixels[i] / height) * (y_axis_max - y_axis_min)
```

Equivalently, if `pix_row[i]` is the original image row index:

```
signal_physical[i] = y_axis_min + ((height - pix_row[i]) / height) * (y_axis_max - y_axis_min)
```

Why this matters:

- **Pixel-space and physical-space are NOT the same signal.** They differ by a constant scale and offset.
- **Spatial frequency in pixels is invariant** under the affine map, which is why the FFT method (§2) works on `signal_pixels` directly. It cares about the *shape* of the signal, not its amplitude.
- **The Lowpass method also works on `signal_pixels`** (see §2.3). Both analysis methods operate in pixel-space, making them immune to Y-axis miscalibration. The physical signal is retained for display and export purposes only.

The physical signal remains useful for the signal plot in the graph viewer and for UNV Dataset 58 export, but it no longer participates in any classification decision.

## 1.3 Linear-axis assumption

USMA does **not** support log-Y axes. If the source plot uses a log scale, the reconstructed `signal_physical` is wrong by a non-linear factor. Since both analysis methods now operate on pixel-space, the log-scale issue is limited to display and export. However, the FFT energy ratios will still be biased towards low frequencies on log-scale signals, and the LP residual shape will differ from what a linear-scale signal would produce. There is no autodetection — the user must ensure the source plot is linear.

> **Trap (2026-09 audit, finding T-5).** `MonitoringRegion` carries a `y_scale_type` field with
> options `linear` / `dB` / `log` / `ln`, and the Config Tool presents it as a combobox. **Nothing
> reads it.** The affine map above is applied unconditionally. Selecting `log` in the GUI does not
> change the reconstruction and does not warn; it only records an intention. Note that `dB` is not
> a problem — a dB axis *is* linear in the plotted quantity, so the affine map is correct there,
> which is the case that matters for the monitored TestLab plots. `log`/`ln` are silently wrong.

---

## The two analysis methods

Before diving into parameters, the architecture: every `frf` and `psd` ROI runs through `SignalAnalyzer.analyze()`, which performs two completely independent analyses on the same reconstructed pixel signal. Each produces a binary pass/fail result that feeds one of the four indicator lights.

**Both methods operate on the pixel-space signal.** This is a deliberate design choice: the pixel signal comes directly from the HSV mask and requires no user-supplied calibration. It is always trustworthy. Physical-unit conversion is an affine map that preserves shape but introduces a dependency on correct `y_axis_min/max` — a dependency that adds no analytical value and creates a common source of error, especially for students.

---

## 2.1 FFT method

This operates on the **pixel-space signal** (raw column positions of the detected colour trace), not the physical-unit signal. The reason is that the frequency content of interest is a structural property of the *shape* of the signal, independent of what physical units it's in.

**Steps:**
1. Detrend: subtract the mean from `signal_pixels`
2. Apply `rfft()` → get complex spectrum
3. Compute magnitude spectrum `fft_mags = abs(yf)`
4. Build normalised frequency axis with `rfftfreq(N, 1)` → values from 0 to 0.5 (Nyquist)

### FFT Cutoff Frequency (`fft_cutoff_frequency` / `psd_fft_cutoff_frequency`)

**What it is:** A normalised spatial frequency threshold, ranging from 0 to 0.5 (Nyquist = 0.5). The signal is *not* sampled in time — it's sampled in pixels horizontally across the ROI. So this is a **spatial frequency** in units of cycles/pixel, not Hz.

**What it does:** Splits the FFT spectrum into "low-frequency" (≤ cutoff) and "high-frequency" (> cutoff) halves. The code:

```python
cutoff_indices = np.where(xf >= fft_cutoff)[0]
high_freq_energy = np.sum(fft_mags[cutoff_indices[0]:] ** 2)
energy_ratio = high_freq_energy / total_energy
```

**Intuition:** A clean, smooth FRF/PSD signal changes slowly across the ROI → most energy at low spatial frequencies. A noisy or double-hit signal has rapid oscillations → elevated high-frequency energy. The cutoff defines the boundary between "slowly varying" and "rapidly oscillating."

Default is `0.07` **cycles/pixel**. Since `rfftfreq(N, 1)` returns an axis that ends at 0.5 cycles/pixel (= Nyquist), 0.07 is **14 % of Nyquist**, not 7 %. Equivalently, the cutoff selects features whose period is `1 / 0.07 ≈ 14 pixels` or shorter — a figure that is independent of ROI width, not tied to the 500-pixel example.

> **Normalisation warning.** This parameter and `lowpass_cutoff` (§2.2) use *different*
> normalisations even though both default to `0.07`. `fft_cutoff_frequency` is in
> cycles/pixel on a `[0, 0.5]` axis. `lowpass_cutoff` is the `Wn` argument of
> `scipy.signal.butter`, where **`Wn = 1.0` is Nyquist**, i.e. a `[0, 1]` axis.
> The same numeric value therefore means **two different frequencies, a factor of 2
> apart**: `fft_cutoff_frequency = 0.07` → 0.07 cycles/pixel, while
> `lowpass_cutoff = 0.07` → 0.035 cycles/pixel (verified: the −3 dB point of
> `butter(7, 0.07)` is at 0.0350 cycles/pixel). Do not reason about the two knobs
> as if they were on a common axis.

**Effect of changing it:** Lower cutoff → more of the spectrum is labelled "high frequency" → higher energy ratios → easier to flag as BAD. Higher cutoff → stricter definition of "high frequency" → lower energy ratios → harder to flag.

### FFT Energy Ratio Threshold (`fft_energy_ratio_threshold`)

**What it is:** The minimum `energy_ratio` to flag a signal as high-frequency. Dimensionless, ranges roughly 0.001–0.1 in practice.

**What it does:** `is_hf = energy_ratio > fft_threshold`. If the fraction of total spectral power above the cutoff exceeds this threshold, the FFT method votes BAD.

Default `0.006` = if more than 0.6% of the signal's total power is in high spatial frequencies, it's flagged. This is intentionally sensitive.

---

## 2.2 Lowpass method

This operates on the **pixel-space signal**, the same input as the FFT method. It uses a Butterworth filter to separate smooth from rough components, then applies a **relative threshold** to classify the residuals.

**Steps:**
1. Detrend: subtract `mean(signal_pixels)`
2. Design a Butterworth lowpass filter: `butter(lp_order, lp_cutoff, btype='low', output='sos')`
3. Apply zero-phase filtering: `sosfiltfilt(sos, signal_detrended)`
4. Compute residual: `residual = signal_detrended - filtered`
5. Compute `max_abs_residual = max(|residual|)`
6. Compute dynamic threshold: `dynamic_threshold = relative_residual_ratio × max_abs_residual`
7. Count how many residual samples exceed `dynamic_threshold`
8. Compute `exceedance_ratio = exceedance_count / N`
9. Flag: `lowpass_is_bad = exceedance_ratio > exceedance_ratio_threshold`

### Why pixel-space instead of physical-space

The LP method's core operation — smooth, subtract, measure residuals — detects a **shape property** of the signal. A double-hit produces rapid oscillations regardless of what units the signal is in. The Butterworth filter separates slow-varying from fast-varying components based on normalised spatial frequency, which is unit-independent.

Operating on `signal_pixels` eliminates the dependency on correct `y_axis_min/max`, which was the single most common source of "calibration drift" complaints in earlier versions. Students no longer need to set Y-axis bounds correctly for the analysis to work — they only need correct HSV colour calibration.

### Why a relative threshold instead of an absolute one

The earlier design used an absolute `residual_threshold` in physical units (e.g. 0.005 g/N). This had two problems:

1. **Scale dependency.** The correct threshold value depended entirely on the signal's amplitude range. A 1.0 g/N FRF had residuals in a completely different range than a 0.01 g/N FRF, so the same threshold could not work for both.
2. **Y-axis coupling.** Since the threshold was in physical units, miscalibrated `y_axis_min/max` silently shifted the meaning of the threshold. Halving the Y-axis range halved all residuals, causing the threshold to flag everything.

The relative threshold `relative_residual_ratio × max(|residual|)` is dimensionless. It asks: "what fraction of samples have residuals comparable in magnitude to the peak residual?" This question has the same answer regardless of signal scale, units, or Y-axis calibration.

### LP Cutoff (`lowpass_cutoff` / `psd_lowpass_cutoff`)

**What it is:** The `Wn` argument of `scipy.signal.butter`, normalised so that **1.0 = Nyquist** (a `[0, 1]` axis). This is **not** the same normalisation as `fft_cutoff_frequency`, which lives on a `[0, 0.5]` cycles/pixel axis — see the normalisation warning in §2.1. To convert: `cycles_per_pixel = 0.5 × lowpass_cutoff`. In practice `lowpass_cutoff` should stay below ~0.4 to remain meaningful.

**What it does:** Everything *below* this frequency passes through the filter unchanged. Everything *above* it is attenuated. The filtered signal represents the "smooth envelope" of the signal. The residual = what's left over after removing the smooth component.

**Intuition:** Think of the lowpass filter as drawing a smooth curve through the signal. The residual is the jagged deviation between the actual signal and that smooth curve. A good hit has tiny, uniformly distributed residuals (the signal *is* smooth). A bad hit — rattling, double-hit, overload — has large, non-uniform residuals.

**Effect of changing it:** Lower cutoff → tighter smoothing → more of the signal ends up in the residual → more exceedances → easier to flag BAD. Higher cutoff → looser smoothing → only very rapid oscillations end up in the residual → fewer exceedances.

Default is `0.07`, numerically the same as the FFT cutoff default but **not the same frequency**: `lowpass_cutoff = 0.07` is 0.035 cycles/pixel, half of `fft_cutoff_frequency = 0.07`. The coincidence is in the digits only, and it has repeatedly been misread as a coupling.

### LP Order (`lowpass_filter_order` / `psd_lowpass_filter_order`)

**What it is:** The order of the Butterworth filter passed to `scipy.signal.butter(order, cutoff, btype='low', output='sos')`.

**Implementation note:** USMA uses `sosfiltfilt()` (second-order sections with zero-phase filtering), which applies the filter **twice** — forward and backward — giving an effective order of `2 × lp_order`. At order 7 (the default), the effective roll-off is equivalent to a 14th-order filter.

The `sosfiltfilt` implementation is numerically stable at any order, unlike the older `filtfilt(b, a, ...)` approach which suffers from ill-conditioned polynomial coefficients at high orders and low cutoffs. This means the Order spinbox is now functional across its full range, though in practice it remains a low-impact tuning knob because the filter is already very steep at order ≥ 5.

| Order | Effective order (sosfiltfilt) | Attenuation at 2× cutoff |
|-------|------------------------------|--------------------------|
| 3 | 6 | ~−37 dB |
| 5 | 10 | ~−61 dB |
| 7 | 14 | ~−86 dB |
| 9 | 18 | ~−110 dB |

*(Corrected in the 2026-09 audit, finding T-2. The previous table listed −72/−120/−168/−216 dB, exactly 2× too large in dB: the `filtfilt` doubling had been applied twice. For a Butterworth filter of order n, `|H(2ω_c)| = (1 + 4ⁿ)^(−1/2)`, and zero-phase filtering squares the magnitude response, so the attenuation is `−20·log₁₀(1 + 4ⁿ)` dB. Values verified numerically with `scipy.signal.sosfreqz` on `butter(n, 0.07)`.)*

At the default `lp_cutoff = 0.07`, doubling to `0.14` is already in the deep stopband regardless of order. The residual is dominated by the *cutoff frequency* choice, not the order.

**Practical guidance:** Order is not a useful tuning knob at these settings. Leave it at 7. The effective controls for LP behaviour are `lp_cutoff`, `relative_residual_ratio`, and `exceedance_ratio_threshold`.

### Relative Residual Ratio (`relative_residual_ratio`)

**What it is:** A dimensionless fraction in `[0, 1]` (displayed as a percentage in the UI). It defines the exceedance threshold as a proportion of the peak residual amplitude.

**What it does:**
```python
max_abs_residual = max(|residual|)
dynamic_threshold = relative_residual_ratio × max_abs_residual
exceedance = |residual[i]| > dynamic_threshold
```

A residual sample is counted as an "exceedance" if its absolute value exceeds this fraction of the peak residual.

Default `0.10` (10%) means: a sample is an exceedance if it exceeds 10% of the largest residual in the signal.

**What the metric actually measures.** Because the threshold is `ratio × max(|residual|)`, the
exceedance ratio is *invariant under any rescaling of the residual*. It therefore carries **no
information about how large the residual is** — only about the **shape of the residual amplitude
distribution**, i.e. how peaked the residual is relative to its own maximum. A signal whose
residual is multiplied by 1000 produces exactly the same exceedance ratio. Any earlier statement
in this document that appealed to "`max` itself is tiny, so this is benign" was wrong: the scale of
`max` cancels out of the metric by construction.

The consequences, in order of importance:

- **Impulsive contamination lowers the metric.** One large spike (trace dropout, clipping artefact,
  a rendering glitch) sets a large `max`; the rest of the residual then falls below `ratio × max`,
  so the exceedance ratio *falls*. The metric is **anti-correlated** with isolated-spike defects.
- **Broadband roughness raises it.** A pervasively noisy residual has `max ≈ a few × RMS`, so a
  large fraction of samples clear the threshold and the ratio rises. This is the only defect class
  the metric responds to in the intended direction.
- **Quasi-periodic ripple is nearly invisible.** The comb ripple produced by a double impact is a
  near-sinusoidal residual, for which the exceedance ratio is ≈ 0.94 essentially regardless of
  amplitude — indistinguishable from a clean quantisation-noise residual in practice.

**Measured behaviour (2026-09 audit, finding T-3).** On the stored `_calibration` populations in
`configs/`, with expert GOOD/BAD labels, the separation achieved by `exceedance_ratio` is:

| Dataset | Family | n (GOOD/BAD) | AUC = P(metric_BAD > metric_GOOD) |
|---|---|---|---|
| `PSD_FRF_ONLY` | FRF | 8 / 4 | 0.656 |
| `PSD_FRF_ONLY_SMNS` | PSD | 9 / 8 | **0.368 (inverted)** |

At these sample sizes neither value is distinguishable from 0.5 (no information). **The lowpass
method has no demonstrated discriminative power on USMA's own labelled data, and on the balanced
dataset its sign is reversed.** `docs/SPEC_CLASSIFICATION_V2.md` §1 reaches the same conclusion
from field experience and proposes replacing this method; the numbers above are the quantitative
form of that argument. Treat the LP vote as provisional until that work lands.

**The relative ratio and the exceedance ratio threshold work as a pair.** Lowering `relative_residual_ratio` (e.g. 0.10 → 0.05) makes the dynamic threshold smaller, so more samples become exceedances, raising the exceedance ratio. Lowering `exceedance_ratio_threshold` (e.g. 0.7 → 0.3) makes it easier to flag. The two knobs are complementary.

**Observed range vs. the default.** Across the three stored calibration sets, `exceedance_ratio`
spans 0.000–0.899 with medians 0.542, 0.550 and 0.000. The default `exceedance_ratio_threshold =
0.7` is therefore above almost the entire observed range: it fired on 6/24, 1/17 and 0/19 of the
recorded signals. **With defaults, the LP method is effectively inert.** See §8.

**Effect of changing it:** Lower ratio → lower dynamic threshold → more exceedances → easier to flag BAD. Higher ratio → higher dynamic threshold → fewer exceedances → harder to flag.

## 2.3 The edge case: zero-residual signals

If the signal is perfectly smooth (all pixels on the same row, or the filter reproduces the signal exactly), then `max_abs_residual ≈ 0`. In this case, `dynamic_threshold = 0` and no exceedances are counted. This correctly classifies a perfectly smooth signal as GOOD.

The code guards against this with:
```python
dynamic_threshold = relative_residual_ratio * max_abs_residual if max_abs_residual > 1e-9 else 0.0
```

## 2.4 Alternative: Mean-based relative threshold (Option B)

An alternative normalisation is to use the mean absolute residual instead of the max:

```
dynamic_threshold = ratio × mean(|residual|)
```

This asks "how many residuals are significantly above average?" rather than "how many residuals are significant relative to the peak?"

**Comparison:**

| Property | Max-based (Option A, current) | Mean-based (Option B) |
|---|---|---|
| Sensitivity to outlier spikes | High — one large spike raises `max`, lowering the exceedance ratio for all other samples | Low — the mean is robust to isolated spikes |
| Behaviour on uniformly noisy signals | `max ≈ mean`, so ratio ≈ mean/max ≈ 1 for most samples | Same as Option A for uniform distributions |
| Behaviour on spiky signals | Spike sets a high max, calm regions fall below threshold → low exceedance ratio | Mean is moderate, spike region exceeds threshold but calm regions may not |
| Calibration range | Typically 0.02–0.50 | Typically 1.0–5.0 (since mean < max, the ratio must be higher to achieve the same dynamic threshold) |
| Discriminating power | **Not demonstrated.** Measured AUC on USMA's own labelled data is 0.656 (FRF, n=12) and 0.368 (PSD, n=17) — the latter inverted; see §2.2 | Untested on USMA data. The mean is a robust statistic, so it would not invert on isolated spikes, but no measurement supports a claim either way |

**Status of the recommendation (revised, 2026-09 audit, finding T-4).** The previous text asserted
that Option A "provides wider separation between Good and Bad populations on typical impact hammer
data, where bad hits tend to produce pervasively elevated residuals rather than isolated spikes."
**No measurement supported that claim, and the data now contradict it** (§2.2). Option A remains the
implemented default for continuity, not because it was shown to be better.

Normalising by `max` also makes the metric depend on a single order statistic, which is the least
robust summary of the residual available: one outlier sample determines the threshold for the whole
signal. If this method is retained rather than replaced by `docs/SPEC_CLASSIFICATION_V2.md` §3, the
candidates worth testing against labelled data — in order of expected robustness — are a high
percentile (e.g. `p95(|residual|)`) or a robust scale estimate (`1.4826 × MAD`) in place of `max`,
and Option B. None of these should be adopted without a measured AUC on the stored calibration sets.

### Exceedance Ratio Threshold (`exceedance_ratio_threshold`)

**What it is:** The minimum fraction of samples (0 to 1) that must exceed the dynamic threshold to flag the signal as BAD.

**What it does:** `lowpass_is_bad = exceedance_ratio > exc_threshold`. So if 0.7 (70%) of all samples have residuals larger than `dynamic_threshold`, it's flagged BAD.

Default `0.7` is intentionally permissive — requiring 70% of the signal to be "rough" before flagging BAD. This means the LP method only fires on very noisy signals. If you want it more sensitive, lower this toward 0.2–0.3.

---

## Practical tuning guide

**FFT method too sensitive (many false BADs):**
Raise `fft_energy_ratio_threshold` or raise `fft_cutoff_frequency`. Either change makes the FFT method accept more high-frequency content before calling a signal BAD.

**FFT method not sensitive enough (missing obvious bad hits):**
Lower `fft_energy_ratio_threshold` or lower `fft_cutoff_frequency`. Lowering the cutoff moves more of the spectrum into the "high frequency" half; lowering the ratio threshold makes it easier for that half to trigger a BAD vote.

**LP method not firing at all:**
Lower `exceedance_ratio_threshold` (e.g. `0.7 → 0.3`) so a smaller fraction of rough samples is enough to flag, or lower `relative_residual_ratio` so more samples count as exceedances in the first place.

**LP method firing on clean hits:**
Raise `relative_residual_ratio` — each exceedance must now be larger relative to the peak residual — or raise `exceedance_ratio_threshold` so a greater fraction of the signal must be rough before it trips. If neither feels right, sanity-check the Y-axis calibration before tuning further.

**LP Order:**
Leave it at 7. The move to `sosfiltfilt` made the spinbox numerically stable across its full range, but the filter is already very steep at this order — `lp_cutoff`, `relative_residual_ratio`, and `exceedance_ratio_threshold` are always the controls that actually matter.

The most important insight: **LP Order is not a real tuning knob.** The effective parameters controlling LP sensitivity are `lp_cutoff`, `relative_residual_ratio`, and `exceedance_ratio_threshold`, in that order of impact.

---

# 3. Coherence analysis

**Source:** `usma/analysis/coherence.py` (`CoherenceAnalyzer.analyze`, lines ~20–92).

Coherence ROIs are treated separately from FRF/PSD ROIs because the coherence function γ²(f) lives in the bounded interval [0, 1] and its degradation has a natural physical interpretation: how much of the response at each frequency is *not* explained by the input.

## 3.1 The badness integral

USMA inverts coherence and integrates it across the visible frequency band:

```python
inverted_signal   = 1.0 - signal_physical          # signal_physical is γ² in [0,1]
badness_integral  = np.trapezoid(inverted_signal, freq_axis)
freq_span         = max(region.x_axis_max - region.x_axis_min, 1.0)
normalized_badness = badness_integral / freq_span
```

`normalized_badness` is therefore the **average value of (1 − γ²)** over the visible frequency range. It is dimensionless and lives in [0, 1].

- 0.0 → perfect coherence everywhere
- 1.0 → zero coherence everywhere
- Typical "good" measurement → 0.05–0.15
- Anything above `coherence_threshold` (default 0.30) is flagged

The frequency-span normalisation is critical: it makes the metric **scale-invariant with respect to the visible band**. A user who zooms into a narrow band gets directly comparable numbers to a user looking at the full sweep, provided `region.x_axis_min/max` are correctly set.

## 3.2 Per-band badness

The same calculation is repeated on four equal-width sub-bands:

```python
band_size = n // 4
for i in range(4):
    start_i = i * band_size
    end_i   = (i + 1) * band_size if i < 3 else n
    band_inv  = inverted_signal[start_i:end_i]
    band_freq = freq_axis[start_i:end_i]
    band_badness.append(
        np.trapezoid(band_inv, band_freq) / max(band_freq[-1] - band_freq[0], 1.0)
    )
```

This gives a 4-element vector that lets the GUI highlight *which* part of the spectrum is degrading — useful for spotting localised resonance/anti-resonance problems that an averaged metric would smooth out.

## 3.3 Run-level degradation

In addition to instantaneous badness, USMA tracks how badness evolves *within a measurement run*. If `normalized_badness` increases by more than `coherence_degradation_pct` (default 0.20 = 20 %) compared to the start of the run, the GUI raises a degradation warning. This is a lightweight trend detector, not a statistical test — it is purely a relative-change check.

| Metric | Symbol | Formula | Default flag |
|---|---|---|---|
| Mean coherence | γ̄² | mean(signal_physical) | — |
| Min coherence | γ²_min | min(signal_physical) | — |
| Normalised badness | B | ∫(1−γ²) df / Δf | > 0.30 |
| Per-band badness | B_k | ∫_band(1−γ²) df / Δf_band | — |
| Degradation | ΔB/B₀ | (B − B_run_start) / B_run_start | > 0.20 |

---

# 4. Hit classification

**Source:** `usma/analysis/classifier.py` (`HitClassifier.classify_hit`, lines ~10–56).

Each frame produces up to **four method votes**: FRF-FFT, FRF-LP, PSD-FFT, PSD-LP. Coherence is *not* part of the hit verdict — it is a separate measurement-quality channel. The classifier turns those four binary votes into one of three labels, with each method individually toggleable in the config.

The logic, in plain words:

- Let `any_bad` = at least one enabled method voted BAD.
- Let `all_bad` = both the FRF side **and** the PSD side have at least one BAD vote (when both sides exist). If only one side exists, `all_bad` requires *both* methods on that side to be BAD.

Then:

- `not any_bad` → **GOOD HIT** (green)
- `all_bad` → **BAD HIT (...)** (red), with the parenthetical listing every failing method
- otherwise → **SUSPECT (...)** (orange), with the parenthetical listing every failing method

This means a single failing method on a single signal type is never enough to fail a hit outright — it always demotes it to SUSPECT first. A SUSPECT hit becomes a BAD hit only when **both** the FRF and PSD signal types independently agree there is a problem (or, when only one signal type exists, when both methods on that side agree).

The detail string concatenates failures as e.g. `"FRF-FFT+PSD-LP"`, which is what shows up in the log and the indicator-light tooltip.

---

# 5. The calibration engine

**Source:** `usma/models.py` (`CalibrationSignal`, `CalibrationSession`, `PercentileBoundaryEstimator`, `BayesianThresholdEstimator`, `ROCYoudenEstimator`, `HybridCalibrationEngine`; lines ~161–845).

USMA's calibration system exists because the threshold parameters described in §2 (`fft_cutoff_frequency`, `fft_energy_ratio_threshold`, `lowpass_cutoff`, `relative_residual_ratio`, `exceedance_ratio_threshold`) cannot be set sensibly without a population of labelled signals. The calibration engine takes a stream of user-judged hits ("this one is GOOD", "this one is BAD") and infers the thresholds that best separate the two populations.

The architecture is deliberately **plural**: three independent estimators are run side-by-side and merged, because each one has different statistical assumptions and breaks down at different sample sizes. The merger is governed by an ordinal **confidence level** computed from the total number of labelled signals.

## 5.1 The data being calibrated

Each calibration sample is a `CalibrationSignal` containing:

- The reconstructed pixel-space signal and its residuals
- Its precomputed FFT (`fft_freqs`, `fft_mags`)
- The current values of the metrics: `energy_ratio`, `exceedance_ratio`, `max_abs_residual`
- A user judgment: `"GOOD"` or `"BAD"`

The estimators target three primary parameters:

- `fft_energy_ratio_threshold`
- `exceedance_ratio_threshold`
- `relative_residual_ratio`

…and (only the percentile estimator) two filter-shape parameters:

- `fft_cutoff_frequency`
- `lowpass_cutoff`

## 5.2 Estimator 1 — `PercentileBoundaryEstimator`

**Minimum data:** ≥ 3 GOOD and ≥ 3 BAD.

**Threshold rule** (`_find_boundary`, line ~216):

```python
max_good = max(good_values)
min_bad  = min(bad_values)

if min_bad > max_good:
    threshold = (max_good + min_bad) / 2.0          # cleanly separated
else:
    threshold = np.percentile(good_values, 95)      # overlapping → 5% FP rate on GOOD
```

The intuition is direct: if the two populations don't overlap on a given metric, place the threshold at the midpoint between the highest GOOD and the lowest BAD. If they overlap, accept that some BADs will leak through but pin the threshold at the 95th percentile of the GOOD distribution so that no more than 5 % of *good* hits get incorrectly flagged.

**Filter-shape sweep** (`_sweep_filter_params`, lines ~232–305):

For `fft_cutoff_frequency`, USMA sweeps candidates over `[0.02, 0.25]` in steps of 0.005. For each candidate it recomputes `energy_ratio` for every signal in the calibration set, then scores the candidate by **mean separation**:

```python
good_mean = np.mean(ratios[labels == 1])
bad_mean  = np.mean(ratios[labels == 0])
separation = bad_mean - good_mean      # higher = better
```

The cutoff with the highest separation wins. The same procedure is applied to `lowpass_cutoff` over `[0.03, 0.20]` in steps of 0.01, where the score is the separation in `exceedance_ratio` after re-running the LP residual computation on pixel-space signals.

> **Fixed in the 2026-09 audit (finding C-2/C-3).** This sweep read the calibration dict key
> `signal_vector`, which the GUI populated with the **residual**, not the signal — so it was
> re-filtering an already high-passed array and measuring the residual *of the residual*. It also
> hardcoded `butter(7, …)` and a test ratio of `0.10`, ignoring the `lowpass_filter_order` and
> `relative_residual_ratio` actually in force, so the cutoff was optimised under a filter
> configuration the runtime might never use. The sweep now reads `signal_pixels` (falling back to
> `signal_physical`, which is an exact substitute because the affine map cancels out of the
> scale-invariant exceedance ratio) and uses each signal's own recorded order and ratio.

> **Caveat on the scoring rule.** `separation = mean(bad) − mean(good)` is an unnormalised
> difference of means: it ignores within-class variance, so it is not a separability measure and it
> can be maximised in regions where the classes merely have wider spread. With the minimum of 3
> GOOD + 3 BAD, five parameters are fitted from six observations with no held-out data. A rank
> statistic (AUC) or a standardised effect size (Cohen's d) would be the appropriate score. This is
> not fixed in code — it is a method change, not a defect repair.

**Relative residual ratio sweep:** The Percentile estimator also sweeps `relative_residual_ratio` over `[0.02, 0.50]` in steps of 0.01. For each candidate ratio, it recomputes `exceedance_ratio` for every signal using that ratio applied to each signal's pixel-space residuals, then picks the ratio that maximises the mean separation between Good and Bad exceedance ratios.

This is why the Percentile estimator dominates the early phase of calibration: it is the only one that can *also* tune the filter shape and the residual ratio, and it works with as few as 6 signals.

## 5.3 Estimator 2 — `BayesianThresholdEstimator`

**Minimum data:** ≥ 6 signals total (no per-class minimum).

**Method:** grid-based Bayesian inference with a sigmoid likelihood, one independent grid per parameter.

**Grids** (`PARAM_GRIDS`, line ~319):

| Parameter | Range | Grid points |
|---|---|---|
| `fft_energy_ratio_threshold` | 0.0005 – 0.15 | 300 |
| `exceedance_ratio_threshold` | 0.05 – 0.99 | 300 |

> **Removed in the 2026-09 audit (finding C-4):** `relative_residual_ratio` was a third grid here.
> A Bayesian update requires a per-signal *observed* metric, and no such observable exists for that
> parameter — the GUI supplied the **current config value**, identical for every signal and
> identical for GOOD and BAD. The posterior was therefore driven entirely by the GOOD/BAD count
> imbalance: with balanced counts the MAP returned the config value it was given (0.1003 for an
> input of 0.10), and with 6 GOOD vs 3 BAD it drifted to 0.1163, with 3 GOOD vs 6 BAD to 0.0826 —
> in every case a function of the counts, never of the signals. `relative_residual_ratio` is now
> estimated only by the percentile and ROC sweeps, both of which recompute the exceedance ratio
> from each signal's own residual for every candidate ratio.

**Prior:** uniform over each grid (`np.ones(n) / n`). USMA does not assume any pre-existing belief about where the threshold lives.

**Likelihood** (lines ~333–380): each new signal updates the posterior using a sigmoid that asks "for each candidate threshold value `t`, how consistent is the new observation with that being the right cutoff?"

```python
steepness = 20.0 / grid_range            # sharper for narrower ranges

if judgment == "GOOD":
    # GOOD signals should sit BELOW the threshold
    exponent  = np.clip(-steepness * (grid - observed), -500, 500)
    likelihood = 1.0 / (1.0 + np.exp(exponent))
else:
    # BAD signals should sit ABOVE the threshold
    exponent  = np.clip(-steepness * (observed - grid), -500, 500)
    likelihood = 1.0 / (1.0 + np.exp(exponent))

posterior  = prior * likelihood
posterior /= posterior.sum()              # renormalise
```

Conceptually: a GOOD observation makes thresholds *above* the observed value more credible; a BAD observation makes thresholds *below* the observed value more credible. The sigmoid steepness controls how sharply the credibility flips around the observation.

**Point estimate:** the MAP (grid point with highest posterior).

**Credible interval** (lines ~399–404): from the posterior CDF,

```python
cumulative   = np.cumsum(posterior)
ci_low_idx   = np.searchsorted(cumulative, 0.025)
ci_high_idx  = np.searchsorted(cumulative, 0.975)
```

…giving a 95 % central credible interval that the GUI displays as a shaded band on the calibration plot. The width of this band is USMA's principled measure of "how sure are we about this threshold yet".

**How to interpret the band.** Because this is a Bayesian credible interval (not a frequentist confidence interval), it is read directly as a probability statement about the parameter: given the GOOD/BAD signals seen so far, there is a 95 % posterior probability that the true optimal threshold lies inside the band. A frequentist confidence interval would instead be a statement about the procedure ("if we repeated the experiment many times, 95 % of such intervals would contain the true value"). USMA uses the Bayesian interpretation throughout.

- **Wide band** → the posterior is diffuse; the engine has not yet seen enough signals to pin the threshold down. Typical for fewer than ~10 labelled signals. Collect more.
- **Narrow band** → the posterior is sharp; the engine is confident, and the Bayesian MAP should be close to the merged threshold shown by the white line on the calibration plot.
- **Merged threshold (white line) falls outside the band** → the three estimators disagree. The white line is a *merged* estimate from Percentile, Bayesian and ROC (see §5.5). If the Percentile or ROC estimator pulled the merged value away from the Bayesian MAP, the white line can sit outside the blue band. Treat this as a prompt to inspect the ROC subplot and, usually, collect more labelled samples.

## 5.4 Estimator 3 — `ROCYoudenEstimator`

**Minimum data:** ≥ 5 GOOD and ≥ 5 BAD.

**Method:** for `fft_energy_ratio_threshold` and `exceedance_ratio_threshold`, sweep candidate thresholds and pick the one that maximises Youden's J statistic on the labelled data.

```python
for thr in candidate_thresholds:
    tp = sum(1 for v in good_values if v <= thr)
    fp = sum(1 for v in bad_values  if v <= thr)
    tpr = tp / n_good                       # true-positive rate
    fpr = fp / n_bad                        # false-positive rate
    J   = tpr - fpr                         # Youden's J
```

The threshold maximising J is the optimal operating point on the ROC curve under the assumption that false positives and false negatives are equally costly.

For `relative_residual_ratio`, the ROC estimator performs a nested sweep: for each candidate ratio in `[0.02, 0.50]`, it recomputes the exceedance ratio for every signal, then applies Youden's J to find the optimal operating point. The ratio that produces the highest J statistic wins.

**AUC** is computed alongside, by trapezoidal integration of the (FPR, TPR) pairs sorted by FPR:

```python
auc = trapezoid(tprs_sorted, fprs_sorted)   # usma.models.trapezoid
auc = max(0.0, min(1.0, abs(auc)))
```

> **Fixed in the 2026-09 audit (finding C-1):** this call was `np.trapz`, which was **removed in
> NumPy 2.0**. The bundled portable runtime ships NumPy 2.2.6, so every invocation raised
> `AttributeError` — meaning `ROCYoudenEstimator` had **never executed** in the shipped
> environment. Because the estimator only becomes eligible at ≥ 5 GOOD *and* ≥ 5 BAD, the failure
> was confined to exactly the Level 3/4 regime this section describes, and the whole of
> `get_estimates()` raised there. Meanwhile `usma/analysis/coherence.py` used `np.trapezoid`,
> which requires NumPy ≥ 2.0 — so no NumPy version could run both code paths. Both now route
> through `usma.models.trapezoid`, which resolves the name once at import.

The AUC is reported per metric and is the headline number on the ROC subplot — it tells you whether the metric *itself* is even a good discriminator (AUC ~0.5 = no information; AUC > 0.9 = excellent).

## 5.5 The hybrid merger and confidence levels

**`HybridCalibrationEngine`** orchestrates everything and decides which estimators are eligible based on data volume.

**Confidence level** (lines ~562–574):

```python
n = total_signals
if not meets_minimum: return 0
elif n <=  7:         return 1   # Percentile only
elif n <= 11:         return 2   # Percentile + Bayesian
elif n <= 15:         return 3   # All three, 20% agreement tolerance
else:                 return 4   # All three, 15% agreement tolerance
```

These cutoffs are **ordinal**, not probabilistic. Level 4 does not mean "90 % certain"; it means "we have ≥ 16 samples and can afford the strictest outlier rejection".

**Merge rules:**

- **Level 1:** use Percentile estimates verbatim.
- **Level 2** (`_merge_two`, line ~601): weighted blend.
  ```python
  merged[key] = 0.4 * percentile[key] + 0.6 * bayesian[key]
  ```
  The Bayesian estimate gets the heavier weight because at this sample size it has integrated more information per parameter than the percentile rule.
- **Level 3 / 4** (`_merge_all`, lines ~634–703): all three estimates are collected and an **agreement check** decides what to do.

  ```python
  mean_val   = np.mean(estimates)
  deviations = [abs(e - mean_val) / mean_val for e in estimates]

  if all(d < agreement_pct for d in deviations):
      merged[key] = mean_val
  else:
      outlier_idx = int(np.argmax(deviations))
      kept        = [e for i, e in enumerate(estimates) if i != outlier_idx]
      merged[key] = float(np.mean(kept))
  ```

  `agreement_pct = 0.20` at Level 3, `0.15` at Level 4. So the engine first checks "do all three estimators agree to within 15–20 %?" — if yes, average them; if no, drop the most-deviant one and average the remaining two. This is a poor man's robust mean: it tolerates one wild estimator without letting it pull the merged value.

## 5.6 What the engine does *not* do

A few things the engine deliberately does **not** attempt, so you don't go looking for them:

- **No likelihood-ratio test** on whether GOOD and BAD distributions are actually distinguishable. The engine will happily produce thresholds even when the classes overlap completely; it is the user's job to look at the calibration plot and notice this.
- **No correlated multivariate model.** All metrics are calibrated independently; cross-parameter correlations (e.g. high energy ratios coinciding with high exceedance ratios) are visible in the plots but not exploited.
- **No online learning.** Each refit re-runs all three estimators from scratch on the full session. There is no incremental Bayesian update across sessions.
- **No automatic re-tuning of `fft_cutoff_frequency` or `lowpass_cutoff` past the initial fit.** Only the Percentile estimator sweeps these, and only when first run.

---

# 6. HSV colour calibration

**Source:** `usma/gui/hsv_calibration.py`, lines ~26–327.

Although it doesn't look like a "math" topic, HSV calibration is a thresholding operation that determines which pixels make it into the signal at all (§1). Mistakes here propagate everywhere downstream.

## 6.1 The thresholding operation

USMA stores three lower bounds and three upper bounds:

```python
hsv_lower = [H_min, S_min, V_min]
hsv_upper = [H_max, S_max, V_max]
```

The mask is computed by OpenCV's per-channel `inRange`:

```python
hsv  = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
mask = cv2.inRange(hsv, np.array(hsv_lower), np.array(hsv_upper))
```

This is a logical AND across the three channels: a pixel is white in the mask iff `H_min ≤ H ≤ H_max` **and** `S_min ≤ S ≤ S_max` **and** `V_min ≤ V ≤ V_max`.

**Channel ranges (OpenCV convention):**

- Hue: 0–179 (note: 8-bit, NOT 0–360 — be careful when copy-pasting values from external HSV pickers)
- Saturation: 0–255
- Value: 0–255

**Defaults** (lines ~318–325): `[0, 0, 0]` to `[179, 255, 240]`. The upper Value is capped at 240 deliberately, to exclude pure white (which is what most plot backgrounds and gridlines are).

## 6.2 No auto-clustering

USMA's HSV calibration is **purely interactive**. There is no k-means on the selected pixels, no Otsu-style automatic thresholding, no statistical estimation of Hue mode and spread. The user adjusts six sliders by hand and watches the live preview update. The reason is that plot traces are not a stationary distribution: anti-aliasing edges, gridline crossings and dashed segments mean that any data-driven estimator has to deal with several distinct sub-populations of pixels and tends to produce overly tight bounds. Manual tuning is faster and more reliable in this domain.

---

# 7. Graphical representations

**Source:** `usma/gui/graph_viewer.py` (per-signal plots and calibration diagnostics, lines ~17–373) and `usma/gui/hsv_calibration.py` (HSV preview, lines ~26–327).

This section maps every plot the GUI can show back to the math it visualises. If you ever look at a plot and aren't sure what you're seeing, look here.

## 7.1 Per-hit diagnostic plots

These are accessible from the graph viewer for any individual hit in history (`MAX_HISTORY = 25`).

**Signal plot.** Plain line plot of `signal_physical` (cyan) vs. pixel index. Y-axis label is the ROI's `y_axis_unit`. This is the post-reconstruction signal in physical units — useful for interpreting amplitude but not used by either analysis method.

**FFT plot.** Magnitude spectrum `fft_mags` (magenta) vs. normalised spatial frequency `fft_freqs` (0 to 0.5 Nyquist). Two overlays:

- A green dashed vertical line at `fft_cutoff_frequency`, marking the boundary between "low" and "high" spatial frequencies for the energy-ratio calculation.
- A red translucent fill under the curve **above** the cutoff, showing visually which part of the spectrum contributes to `high_freq_energy`.
- A text annotation `"Energy ratio: <value>"` so you can read it off without doing arithmetic.

**Lowpass comparison.** Two lines on the same axes:

- The original pixel-space signal (cyan)
- The Butterworth-filtered version (green dashed)

…with the gap between them lightly shaded. The shaded gap **is** the residual; this is the most intuitive way to see what the LP method is operating on. Y-axis is in pixels.

**Residual analysis.** Standalone plot of the pixel-space residual (orange) with two horizontal red dashed lines at `+dynamic_threshold` and `−dynamic_threshold`. The dynamic threshold is computed as `relative_residual_ratio × max(|residual|)`. Any sample whose magnitude exceeds the threshold is overlaid as a red bar. A text annotation reports the ratio percentage, exceedance count, and fraction. The title shows `"Residual (px) - Thr: 10% of max - Exc: 127/500 (25.4%) - GOOD"`.

**Run summary.** Aggregated stats across all hits in the current run: counts of GOOD/SUSPECT/BAD, distributions of `energy_ratio` and `exceedance_ratio`, and (for coherence ROIs) the trend of `normalized_badness` over the run.

## 7.2 Calibration diagnostic plots

`_plot_calibration_diagnostics` (lines ~255–373) chooses one of three layouts depending on confidence level:

- **Level 1–2:** three side-by-side distribution plots, one per primary metric.
- **Level 3+:** a 2×2 grid adding an ROC subplot.
- **≥ 6 signals:** a convergence subplot is appended showing how each threshold has evolved as more samples were added.

### 7.2.1 Distribution subplots (one per metric)

Two overlapping histograms drawn at `density=True` so they are comparable regardless of sample count:

- GOOD population in green (`#2ECC71`), `alpha = 0.6`
- BAD population in red (`#E74C3C`), `alpha = 0.6`

A solid white vertical line marks the **merged threshold** (i.e. whatever the hybrid engine produced after running through §5.5). When the Bayesian estimator has a posterior, a translucent blue band (`#3498DB`, `alpha = 0.15`) spans the **95 % credible interval** `[ci_low, ci_high]`.

#### What is "Density" on the Y-axis?

The histograms are plotted with `ax.hist(..., density=True)`. This is **not** a count — it is a **probability density**. Concretely, for each bin:

```
bar_height = (count_in_bin / total_samples) / bin_width
```

The normalisation ensures that the **total area under all bars equals 1.0**, so the Y-axis represents how probability mass is spread across the X-axis values.

**Units.** The Y-axis has units of `1 / [X-axis unit]`. For the three calibration metrics (FFT energy ratio, exceedance ratio, relative residual ratio), the X-axis is dimensionless, so the Y-axis is also dimensionless. If the X-axis carried physical units such as seconds, the Y-axis would be in `1/s`.

**Why density and not raw count.** The GOOD and BAD populations almost always have different sample sizes during calibration (e.g. 12 Good vs. 5 Bad). If the Y-axis were raw counts, the larger class would visually dominate and the overlap region would be unreadable. Density-normalisation makes the two histograms **directly comparable** regardless of how many signals each class has: the green hump and the red hump are always drawn on the same footing.

**How to read it.** Focus on *where* the two humps sit relative to each other and relative to the white threshold line. The absolute height of any single bar is less important than the **relative position** of the green and red distributions:

- Green and red cleanly separated with the white line between them → the metric is a strong discriminator and the threshold should cut cleanly.
- Green and red heavily overlapping → the metric is a poor discriminator on this data; heavy overlap always means the threshold will have an unavoidable error rate.

#### The 95 % Credible Interval band

The translucent blue band is a **95 % Bayesian credible interval** on the optimal threshold for this metric. Given all the GOOD/BAD signals seen so far, there is a 95 % posterior probability that the true optimal threshold lies inside the shaded band.

**How it's computed.** Directly from the Bayesian posterior CDF in `BayesianThresholdEstimator` (§5.3): the lower edge is the 2.5th percentile and the upper edge is the 97.5th percentile of the grid-based posterior.

**Credible interval vs. confidence interval.** This is a Bayesian credible interval, not a frequentist confidence interval. The distinction matters:

- **Credible interval** (USMA): "We believe the parameter is in this range with 95 % probability." A probability statement about the parameter.
- **Confidence interval** (frequentist): "If we repeated the experiment many times, 95 % of the intervals computed this way would contain the true value." A statement about the *procedure*, not the parameter.

The Bayesian framing is the right one here because we are combining a uniform prior with the observed labelled signals to produce a posterior belief about the threshold.

**How to read it:**

- **Wide band → uncertain.** The engine has not seen enough labelled signals for the posterior to concentrate. Typical for fewer than ~10 signals. Collect more samples before trusting the threshold.
- **Narrow band → converged.** The posterior is sharp; the Bayesian MAP is confident and should be close to the white merged-threshold line.
- **White line outside the blue band → estimators disagree.** The white line is a *merged* estimate from all three estimators (Percentile, Bayesian, ROC), not the Bayesian MAP alone. If the Percentile or ROC estimator pulled the merged value away from the Bayesian MAP, the white line can legitimately sit outside the blue band. This is a visual signal that the three estimators are not in agreement — inspect the ROC subplot and consider collecting more data.

**Quick read of the distribution subplots overall:**

- Two well-separated histograms with a narrow blue band → calibration is converged and trustworthy.
- Heavy overlap between green and red → the metric is inherently a poor discriminator on this data; consider whether your GOOD/BAD labels are consistent.
- Wide blue band → not enough samples; collect more before relying on the threshold.

### 7.2.2 ROC subplot (Level 3+)

One curve per metric in a different colour, plus the diagonal y = x in grey as the no-information reference. Each curve has a marker at the optimal Youden point and the metric's name + AUC in the legend, e.g. `"relative_residual_ratio (AUC=0.94)"`.

#### How to read the ROC curve

- **Axes.** X-axis = **False Positive Rate (FPR)**, the fraction of BAD signals incorrectly classified as GOOD. Y-axis = **True Positive Rate (TPR)**, the fraction of GOOD signals correctly classified as GOOD. Both range 0 to 1.
- **The diagonal** (grey dashed `y = x`) is a random classifier — one that assigns GOOD/BAD by coin flip. Any curve *above* the diagonal is better than random; the further the curve bows toward the top-left corner, the better the metric discriminates between the two classes.
- **AUC (Area Under the Curve).** The number in parentheses next to each metric name. `AUC = 1.0` means perfect separation; `AUC = 0.5` means the metric carries no information at all. Rules of thumb:
  - `AUC > 0.90` — excellent
  - `0.80 – 0.90` — good
  - `0.70 – 0.80` — marginal
  - `< 0.70` — the metric is unreliable for discrimination on this data
- **The dot (optimal operating point).** The marker on each curve is the threshold that maximises Youden's J = TPR − FPR. It is the point on the curve closest to the top-left corner under a balanced-cost assumption (false positives and false negatives weighted equally). The ROC estimator uses this as its threshold recommendation, which then feeds into the merged threshold shown on the distribution subplots.
- **Three curves = three metrics.** Red = FFT Energy Ratio, orange = Exceedance Ratio, blue = Relative Residual Ratio. If one curve hugs the diagonal, that metric is not helping — consider whether your data actually needs it enabled.

**Practical advice.** If all three AUCs are below 0.75, the GOOD and BAD populations in your calibration set are poorly separated regardless of threshold choice. This usually means one of two things: (a) the GOOD/BAD labelling is inconsistent (you judged "by feel" and the calls drifted during the session), or (b) the signal-processing parameters upstream — cutoff frequencies, Y-axis calibration, HSV calibration — need adjusting before calibration can converge. No choice of threshold can fix AUC ≈ 0.5.

### 7.2.3 Convergence subplot

Threshold value vs. number of samples seen, one line per metric. A flat tail means the estimate has stabilised; a still-trending line means more samples will continue to move the threshold.

## 7.3 HSV preview

The HSV calibration window stacks three panels vertically:

1. The original ROI image (top)
2. The current binary mask, white-on-black (middle)
3. The original ROI with the mask applied — only in-range pixels visible (bottom)

All three update live as the six sliders move. There is no histogram plot of pixel HSV values — the tuning is purely "does the mask look right?".

---

# 8. Default parameter table

Aggregated from `AnalysisParams` and `AppConfig` in `usma/models.py`. This is the single source of truth for "what does USMA do out of the box?"

| Parameter | Default | Range | Effect |
|---|---|---|---|
| `fft_cutoff_frequency` | 0.07 | 0.02 – 0.25 | Boundary between low and high spatial frequency for energy ratio |
| `fft_energy_ratio_threshold` | 0.006 | 0.0001 – 0.1 | Min HF energy fraction to flag as BAD by FFT method |
| `lowpass_cutoff` | 0.07 | 0.03 – 0.2 | Butterworth cutoff (normalised) for smooth-envelope extraction |
| `lowpass_filter_order` | 7 | — | Butterworth order; effectively 14 after `sosfiltfilt`. Low-impact tuning knob. |
| `relative_residual_ratio` | 0.10 | 0.02 – 0.50 | Fraction of peak residual amplitude defining the exceedance threshold (dimensionless) |
| `exceedance_ratio_threshold` | 0.7 | 0.2 – 0.99 | Min exceedance fraction to flag as BAD by LP method |
| `coherence_threshold` | 0.30 | 0 – 1 | Max normalised coherence badness before warning |
| `coherence_degradation_pct` | 0.20 | 0 – 1 | Relative within-run badness increase that triggers degradation alert |
| `hsv_lower` | `[0, 0, 0]` | per-channel | HSV mask lower bound |
| `hsv_upper` | `[179, 255, 240]` | per-channel | HSV mask upper bound (V capped to exclude pure white) |
| `screenshot_interval` | 0.25 s | — | Polling period of the monitor loop |
| `hits_per_run` | 5 | — | Number of hits expected per measurement run |
| `monitor_index` | 1 | — | Which physical monitor to capture |

PSD ROIs use a parallel set of analysis parameters (`psd_fft_cutoff_frequency`, `psd_fft_energy_ratio_threshold`, `psd_lowpass_cutoff`, `psd_lowpass_filter_order`, `psd_relative_residual_ratio`) with the same defaults — they exist so PSD and FRF can be tuned independently.

## 8.1 How the defaults compare to real recorded data

*(Added in the 2026-09 audit, finding T-1.)* The two threshold defaults were checked against every
signal stored in the `_calibration` blocks of `configs/`. They are mis-scaled in **opposite**
directions:

| Dataset | n | `energy_ratio` median (min–max) | fires at default 0.006 | `exceedance_ratio` median (min–max) | fires at default 0.70 |
|---|---|---|---|---|---|
| `PSD_FRF_ONLY` | 24 | 0.0896 (0.042–0.265) | **24 / 24** | 0.5419 (0.201–0.899) | 6 / 24 |
| `no_OCR` | 19 | 0.1159 (0.007–0.364) | **19 / 19** | 0.0000 (all zero) | 0 / 19 |
| `PSD_FRF_ONLY_SMNS` | 17 | 0.0092 (0.002–0.027) | 11 / 17 | 0.5501 (0.124–0.782) | 1 / 17 |

Read literally: out of the box the **FFT method votes BAD on essentially every hit** (its default
threshold sits one to two orders of magnitude below the observed values), while the **LP method
almost never votes at all**. Under the §4 rule, one BAD vote demotes a hit to SUSPECT, so the
shipped defaults produce near-universal SUSPECT.

**These defaults have deliberately not been changed.** `energy_ratio` depends on ROI width, on the
plotted frequency span and on how much of the ROI the trace occupies, and the three datasets above
differ by an order of magnitude among themselves — so no single global constant is defensible, and
substituting a value tuned on one setup would trade a visibly wrong default for an invisibly wrong
one. The correct procedure is per-setup calibration (§5) before any measurement session. A
startup-time check that warns when a configured threshold lies outside the observed metric range of
the loaded calibration set is the recommended fix; it is not yet implemented.

**The `no_OCR` row also shows a missing guard:** all 19 of its signals have `exceedance_ratio`
identically 0.000 (it predates the v0.11.0 relative-threshold rework), yet the engine still reports
confidence **Level 4 — "Robust"**. The confidence level counts labelled samples and never checks
whether a metric varies at all, let alone whether the classes separate. §5.6 states this openly;
the README's confidence table does not.

---

# 9. Things this document does not cover

For completeness, these are the parts of USMA that touch data but are intentionally **out of scope** here because they are I/O or bookkeeping rather than analysis:

- The OCR pipeline (`usma/analysis/ocr.py`) — used to read run/hit/status text from the source plot. It is text recognition, not signal analysis.
- The UNV exporter (`usma/export/unv.py`) — formats results into a Universal File Format Dataset 58. It is a serialisation step, not a transformation of the data.
- The image logger (`usma/export/image_logger.py`) — saves diagnostic PNGs. No math.
- The monitor loop (`usma/monitor.py`) — schedules captures and dispatches frames; the math it triggers is everything described above.

If a future change introduces a new mathematical operation in any of these modules, it should be documented here.

---

# Appendix A: Changelog

## LP Rework (v0.10.2+)

The Lowpass method was redesigned with two fundamental changes:

1. **Pixel-space input.** The LP method now operates on `signal_pixels` (the raw HSV-reconstructed signal in pixel coordinates) instead of `signal_physical` (the Y-axis-calibrated signal). This eliminates the dependency on correct `y_axis_min/max` settings, removing the single most common source of calibration drift.

2. **Relative threshold.** The absolute `residual_threshold` (in physical units) was replaced by `relative_residual_ratio` (dimensionless, default 0.10). The dynamic threshold is computed as `ratio × max(|residual|)`, making the LP method scale-invariant and unit-independent.

3. **Numerically stable filtering.** The `butter() → filtfilt()` pattern was replaced with `butter(output='sos') → sosfiltfilt()`, eliminating numerical instability at high filter orders and low cutoff frequencies.

These changes make both analysis methods (FFT and LP) operate entirely in pixel-space. The physical signal is retained for display and UNV export but no longer participates in classification decisions.