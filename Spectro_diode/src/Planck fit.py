#!/usr/bin/env python3
"""
Apply precomputed calibration/correction factors K(λ) to a matrix of spectra.
Then plot raw vs calibrated (black vs blue), with grid, wavelength limits,
and estimate the blackbody temperature from the calibrated spectrum.

Features:
- 3σ detection vs first N "background" spectra to decide if a spectrum has signal.
- Only detected spectra are fitted/plotted.
- Line finding for Na I, K I, Li I, Rb I, intensity relative to fitted BB.
- NEW: per-line significance test against background at that λ:
    |cal - BB| > LINE_SIGMA_FACTOR * bkg_std
- NEW: sheet "species_by_spectrum" listing which species were seen in each spectrum.
"""

# ===================== USER SETTINGS =====================
# --- File paths ---
CORR_FILE   = r"C:\Users\jfdai\OneDrive - RAYOPS\RAYOPS\Python functions\Spectroscopy\PythonProject\.venv\calibration_factors.csv"
SPECT_FILE  = r"C:\Users\jfdai\OneDrive - RAYOPS\RAYOPS\Quantino\Product development\Lab run on laser testbed\Spectro\Run 123.xlsx"
OUT_XLSX    = r"C:\Users\jfdai\OneDrive - RAYOPS\RAYOPS\Quantino\Product development\Lab run on laser testbed\Spectro\Run 123\corrected spectra.xlsx"
PLOTS_DIR   = r"C:\Users\jfdai\OneDrive - RAYOPS\RAYOPS\\Quantino\Product development\Lab run on laser testbed\Spectro\Run 123\plots"

# --- Global analysis wavelength window (HARD CLIP) ---
RAW_XMIN    = 425.0   # nm
RAW_XMAX    = 800.0   # nm

# --- Plot wavelength limits ---
PLOT_XMIN   = RAW_XMIN
PLOT_XMAX   = RAW_XMAX
PLOT_YMIN   = None
PLOT_YMAX   = None

# --- Planck temperature fit ---
FIT_XMIN    = 550.0     # nm
FIT_XMAX    = 750.0     # nm
T_BOUNDS    = (500.0, 6000.0)
NORM_REF_NM = None
MASK_BANDS  = []
ROBUST_LOSS = "huber"
F_SCALE     = 0.02
BOOTSTRAP_N = 0
RNG_SEED    = 13

# --- Plot controls ---
SHOW_PLANCK_CURVE     = True
PLANCK_ONLY_IN_WINDOW = True

# --- Detection vs background (spectrum-level) ---
BKG_NUM_SPECTRA = 10     # first N spectra are background
SIGMA_FACTOR    = 50.0   # a spectrum must exceed this max-z to be analyzed

# --- Line detection settings ---
LINE_DEFS = [
    ("Na I D1", 589.0),
    ("Na I D2", 589.6),
    ("Li I",    670.8),
    ("K I",     766.5),
    ("K I",     769.9),
    ("Rb I",    780.0),
    ("Rb I",    794.8),
]
LINE_SEARCH_WIN   = 0.4   # nm around the nominal line
LINE_SIGMA_FACTOR = 100.0   # <-- NEW: line must exceed background by > 3σ
# ==========================================================

import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import least_squares


# -------------------- I/O helpers --------------------
def _read_table_any(path: Path, header=0, sheet=None) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(
            path,
            sheet_name=sheet if sheet is not None else 0,
            header=header,
            engine="openpyxl" if ext == ".xlsx" else None,
        )
    return pd.read_csv(path, header=header, sep=None, engine="python")


def _auto_cols_corr(df: pd.DataFrame):
    cols = list(df.columns)
    lmap = {str(c).lower(): c for c in cols}
    wl_col = lmap.get("wavelength_nm") or lmap.get("wavelength") or cols[0]
    k_col  = lmap.get("k_lambda")     or lmap.get("k")         or cols[1]
    wl = pd.to_numeric(df[wl_col], errors="coerce").to_numpy()
    K  = pd.to_numeric(df[k_col],  errors="coerce").to_numpy()
    good = np.isfinite(wl) & np.isfinite(K)
    wl, K = wl[good], K[good]
    order = np.argsort(wl)
    return wl[order], K[order]


def _read_correction(path: Path):
    df = _read_table_any(path, header=0)
    wl, K = _auto_cols_corr(df)
    if wl.size < 5:
        raise ValueError("Correction factor file has too few valid rows.")
    return wl, K


def _read_spectra_matrix(path: Path):
    # try to find header row
    df0 = _read_table_any(path, header=None)
    header_row = None
    for i in range(min(20, len(df0))):
        v = str(df0.iloc[i, 0])
        if "wavelength" in v.lower():
            header_row = i
            break
    if header_row is not None:
        df = _read_table_any(path, header=header_row)
    else:
        df = _read_table_any(path, header=0)

    df = df.dropna(how="all").dropna(axis=1, how="all")
    wl = pd.to_numeric(df.iloc[:, 0], errors="coerce").to_numpy()
    good_rows = np.isfinite(wl)
    wl = wl[good_rows]
    spectra_df = df.iloc[good_rows, 1:].copy()
    for c in spectra_df.columns:
        spectra_df[c] = pd.to_numeric(spectra_df[c], errors="coerce")
    spectra_df = spectra_df.fillna(0.0)
    return wl, spectra_df


def _interp_K_to_grid(wl_src, K_src, wl_dst):
    return np.interp(wl_dst, wl_src, K_src, left=np.nan, right=np.nan)


def _safe_extrap(y):
    y2 = y.copy()
    if np.isnan(y2[0]):
        first = np.flatnonzero(np.isfinite(y2))
        if first.size:
            y2[:first[0]] = y2[first[0]]
    if np.isnan(y2[-1]):
        last = np.flatnonzero(np.isfinite(y2))
        if last.size:
            y2[last[-1] + 1:] = y2[last[-1]]
    bad = ~np.isfinite(y2)
    if bad.any():
        x = np.arange(y2.size)
        y2[bad] = np.interp(x[bad], x[~bad], y2[~bad])
    return y2


# -------------------- Physics & Fitting --------------------
def planck_lambda_per_nm(lambda_nm: np.ndarray, T: float) -> np.ndarray:
    lam = np.asarray(lambda_nm, float) * 1e-9
    h = 6.62607015e-34
    c = 299792458.0
    k = 1.380649e-23
    a = 2.0 * h * c**2
    b = h * c / (lam * k * T)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        B = a / (lam**5 * (np.exp(b) - 1.0))
    return B * 1e-9


def _mask_bands(x_nm: np.ndarray, bands):
    m = np.zeros_like(x_nm, dtype=bool)
    for lo, hi in (bands or []):
        m |= (x_nm >= lo) & (x_nm <= hi)
    return m


def _resids_linearA_logshape(x_nm, y, T, norm_ref_nm=None):
    B = planck_lambda_per_nm(x_nm, T)
    if norm_ref_nm is None:
        logA = np.mean(np.log(np.clip(y, 1e-300, None)) - np.log(np.clip(B, 1e-300, None)))
        A = np.exp(logA)
    else:
        B_ref = float(np.interp(norm_ref_nm, x_nm, B))
        y_ref = float(np.interp(norm_ref_nm, x_nm, y))
        A = (y_ref / B_ref) if B_ref > 0 else 1.0
    model = A * B
    return np.log(np.clip(y, 1e-300, None)) - np.log(np.clip(model, 1e-300, None))


def fit_temperature_anysource(lambda_nm, calibrated,
                              x_min, x_max,
                              t_bounds=(1000, 12000),
                              norm_ref_nm=None,
                              mask_bands=None,
                              robust_loss="huber",
                              f_scale=0.02):
    x = np.asarray(lambda_nm, float)
    y = np.asarray(calibrated, float)

    sel = np.isfinite(x) & np.isfinite(y) & (x >= x_min) & (x <= x_max)
    if mask_bands:
        sel &= ~_mask_bands(x, mask_bands)
    x = x[sel]
    y = y[sel]
    pos = y > 0
    x = x[pos]
    y = y[pos]
    if x.size < 10:
        return np.nan, np.nan, False

    def fun(p):
        T = p[0]
        return _resids_linearA_logshape(x, y, T, norm_ref_nm)

    T0 = np.clip(5500.0, t_bounds[0], t_bounds[1])
    res = least_squares(
        fun,
        x0=np.array([T0]),
        bounds=(np.array([t_bounds[0]]), np.array([t_bounds[1]])),
        loss=robust_loss,
        f_scale=f_scale,
        max_nfev=20000,
    )

    if not res.success:
        return np.nan, np.nan, False

    T_hat = float(res.x[0])
    B = planck_lambda_per_nm(x, T_hat)
    if norm_ref_nm is None:
        logA = np.mean(np.log(y) - np.log(np.clip(B, 1e-300, None)))
        A_hat = float(np.exp(logA))
    else:
        B_ref = float(np.interp(norm_ref_nm, x, B))
        y_ref = float(np.interp(norm_ref_nm, x, y))
        A_hat = (y_ref / B_ref) if B_ref > 0 else 1.0
    return T_hat, A_hat, True


def _clip_wl(wl_nm: np.ndarray, yminp: float, ymaxp: float):
    return (wl_nm >= yminp) & (wl_nm <= ymaxp)


# -------------------- Line detection --------------------
def detect_lines_in_spectrum(wl_nm, calibrated, model_bb, bkg_std,
                             line_defs, win_nm, line_sigma_factor):
    """
    For each line:
      - scan ±win_nm
      - pick wavelength with max |cal - bb|
      - require |cal - bb| > line_sigma_factor * bkg_std_at_that_lambda
      - return only those that pass
    """
    results = []
    for name, lam0 in line_defs:
        m = (wl_nm >= lam0 - win_nm) & (wl_nm <= lam0 + win_nm)
        if not m.any():
            continue
        lam_seg = wl_nm[m]
        cal_seg = calibrated[m]
        bb_seg  = model_bb[m]
        sig_seg = bkg_std[m]
        sig_seg = np.clip(sig_seg, 1e-12, None)

        diff = cal_seg - bb_seg
        idx_local = np.argmax(np.abs(diff))
        peak_diff = float(diff[idx_local])
        peak_sigma = float(sig_seg[idx_local])

        if abs(peak_diff) > line_sigma_factor * peak_sigma:
            results.append(
                {
                    "line_name": name,
                    "lambda_nominal_nm": lam0,
                    "lambda_measured_nm": float(lam_seg[idx_local]),
                    "rel_intensity": float((cal_seg[idx_local] - bb_seg[idx_local]) /
                                           np.clip(bb_seg[idx_local], 1e-12, None)),
                    "abs_diff": peak_diff,
                    "sigma_at_line": peak_sigma,
                    "snr_line": abs(peak_diff) / peak_sigma,
                }
            )
    return results


# -------------------- Core --------------------
def apply_correction_to_matrix(corr_path: Path, spect_path: Path, out_xlsx: Path, plots_dir: Path):
    wl_corr, K_corr = _read_correction(corr_path)
    wl_mat_full, spectra_df_full = _read_spectra_matrix(spect_path)

    # clip spectra to analysis window
    m_mat = _clip_wl(wl_mat_full, RAW_XMIN, RAW_XMAX)
    if not m_mat.any():
        raise SystemExit(f"No spectra samples within [{RAW_XMIN}, {RAW_XMAX}] nm.")
    wl_mat = wl_mat_full[m_mat]
    spectra_df = spectra_df_full.iloc[m_mat, :].reset_index(drop=True)

    # clip correction factors
    m_corr = _clip_wl(wl_corr, RAW_XMIN, RAW_XMAX)
    if not m_corr.any():
        raise SystemExit(f"No correction-factor samples within [{RAW_XMIN}, {RAW_XMAX}] nm.")
    wl_corr = wl_corr[m_corr]
    K_corr  = K_corr[m_corr]

    # ensure plot/fit inside window
    if not (RAW_XMIN <= PLOT_XMIN < PLOT_XMAX <= RAW_XMAX):
        P_min = max(PLOT_XMIN, RAW_XMIN)
        P_max = min(PLOT_XMAX, RAW_XMAX)
        if P_min >= P_max:
            P_min, P_max = RAW_XMIN, RAW_XMAX
        globals()['PLOT_XMIN'], globals()['PLOT_XMAX'] = P_min, P_max
    if not (RAW_XMIN <= FIT_XMIN < FIT_XMAX <= RAW_XMAX):
        F_min = max(FIT_XMIN, RAW_XMIN)
        F_max = min(FIT_XMAX, RAW_XMAX)
        if F_min >= F_max:
            F_min, F_max = RAW_XMIN, RAW_XMAX
        globals()['FIT_XMIN'], globals()['FIT_XMAX'] = F_min, F_max

    # interpolate K
    K_on_mat = _safe_extrap(_interp_K_to_grid(wl_corr, K_corr, wl_mat))

    # background from first N spectra
    n_cols = len(spectra_df.columns)
    n_bkg  = min(BKG_NUM_SPECTRA, n_cols)
    bkg_array = spectra_df.iloc[:, :n_bkg].to_numpy(dtype=float)
    bkg_mean  = np.mean(bkg_array, axis=1)
    bkg_std   = np.std(bkg_array, axis=1)
    bkg_std[bkg_std < 1e-9] = 1e-9

    corrected = {}
    temp_rows = []
    line_rows = []
    species_by_spec = []

    plots_dir.mkdir(parents=True, exist_ok=True)

    for col in spectra_df.columns:
        counts = spectra_df[col].to_numpy(dtype=float)
        calibrated = counts * K_on_mat
        corrected[col] = calibrated

        # spectrum-level detection
        z = (counts - bkg_mean) / bkg_std
        detection_score = float(np.nanmax(z))
        detection_ok = detection_score >= SIGMA_FACTOR

        if detection_ok:
            # Fit
            T_hat, A_hat, ok = fit_temperature_anysource(
                wl_mat, calibrated,
                x_min=FIT_XMIN, x_max=FIT_XMAX,
                t_bounds=T_BOUNDS,
                norm_ref_nm=NORM_REF_NM,
                mask_bands=MASK_BANDS,
                robust_loss=ROBUST_LOSS,
                f_scale=F_SCALE,
            )

            if ok and np.isfinite(T_hat) and np.isfinite(A_hat):
                model_bb = A_hat * planck_lambda_per_nm(wl_mat, T_hat)
            else:
                model_bb = np.full_like(wl_mat, np.nan)

            # line detection with sigma test
            detected_species = set()
            if ok and np.isfinite(T_hat) and np.isfinite(A_hat):
                lines_here = detect_lines_in_spectrum(
                    wl_mat,
                    calibrated,
                    model_bb,
                    bkg_std,
                    LINE_DEFS,
                    LINE_SEARCH_WIN,
                    LINE_SIGMA_FACTOR,
                )
                for ln in lines_here:
                    ln["spectrum"] = str(col)
                    ln["T_K"] = float(T_hat)
                    line_rows.append(ln)
                    species_name = ln["line_name"].split()[0]  # "Na", "K", "Li", "Rb"
                    detected_species.add(species_name)
            else:
                lines_here = []

            # record species summary
            species_by_spec.append(
                {
                    "spectrum": str(col),
                    "detected_3sigma": True,
                    "species_detected": ", ".join(sorted(detected_species)) if detected_species else "",
                }
            )

            # record temperature
            temp_rows.append(
                {
                    "spectrum": str(col),
                    "detected_3sigma": True,
                    "detection_score": detection_score,
                    "T_K": T_hat,
                    "T_lo": np.nan,
                    "T_hi": np.nan,
                    "fit_ok": bool(ok),
                    "fit_window_nm": f"[{FIT_XMIN:.0f},{FIT_XMAX:.0f}]",
                }
            )

            # plot
            fig, ax = plt.subplots()
            ax.set_title(f"Spectrum: {col}")
            ax.plot(wl_mat, counts,     color="black", label="Raw (counts)",            linewidth=1.2)
            ax.plot(wl_mat, calibrated, color="C0",    label="Calibrated = counts × K", linewidth=1.6)

            if SHOW_PLANCK_CURVE and ok and np.isfinite(T_hat) and np.isfinite(A_hat):
                if PLANCK_ONLY_IN_WINDOW:
                    mplot = (wl_mat >= FIT_XMIN) & (wl_mat <= FIT_XMAX)
                    ax.plot(wl_mat[mplot], model_bb[mplot], "--", color="tab:red",
                            label=f"Planck fit (T ≈ {T_hat:,.0f} K)")
                else:
                    ax.plot(wl_mat, model_bb, "--", color="tab:red",
                            label=f"Planck fit (T ≈ {T_hat:,.0f} K)")

            ax.set_xlim(PLOT_XMIN, PLOT_XMAX)
            if PLOT_YMIN is not None or PLOT_YMAX is not None:
                ax.set_ylim(PLOT_YMIN, PLOT_YMAX)
            ax.set_xlabel("Wavelength (nm)")
            ax.set_ylabel("Signal (relative units)")
            ax.grid(True, alpha=0.35)

            if ok and np.isfinite(T_hat):
                ax.text(0.02, 0.95,
                        f"Planck T = {T_hat:,.0f} K\nfit window: [{FIT_XMIN:.0f}, {FIT_XMAX:.0f}] nm",
                        transform=ax.transAxes,
                        va="top", ha="left",
                        bbox=dict(facecolor="white", alpha=0.6, edgecolor="none", boxstyle="round"))

            ax.legend(loc="best")
            fig.tight_layout()
            fname = plots_dir / f"{str(col).replace('/', '_').replace(':', '-')}.png"
            fig.savefig(fname, dpi=150)
            plt.close(fig)

        else:
            # not detected
            temp_rows.append(
                {
                    "spectrum": str(col),
                    "detected_3sigma": False,
                    "detection_score": detection_score,
                    "T_K": np.nan,
                    "T_lo": np.nan,
                    "T_hi": np.nan,
                    "fit_ok": False,
                    "fit_window_nm": f"[{FIT_XMIN:.0f},{FIT_XMAX:.0f}]",
                }
            )
            species_by_spec.append(
                {
                    "spectrum": str(col),
                    "detected_3sigma": False,
                    "species_detected": "",
                }
            )

    # Save everything
    corrected_df = pd.DataFrame(corrected)
    corrected_df.insert(0, "wavelength_nm", wl_mat)
    raw_df = spectra_df.copy()
    raw_df.insert(0, "wavelength_nm", wl_mat)

    out_xlsx = out_xlsx if isinstance(out_xlsx, Path) else Path(out_xlsx)
    if out_xlsx.suffix.lower() != ".xlsx":
        out_xlsx = out_xlsx.with_suffix(".xlsx")

    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as xlw:
        corrected_df.to_excel(xlw, sheet_name="corrected", index=False)
        raw_df.to_excel(xlw, sheet_name="raw", index=False)
        pd.DataFrame(temp_rows).to_excel(xlw, sheet_name="temperature_fit", index=False)
        pd.DataFrame(line_rows).to_excel(xlw, sheet_name="lines", index=False)
        pd.DataFrame(species_by_spec).to_excel(xlw, sheet_name="species_by_spectrum", index=False)

    print(f" Wrote corrected spectra Excel: {out_xlsx}")
    print(f" Plotted only spectra with detection_score ≥ {SIGMA_FACTOR} vs first {BKG_NUM_SPECTRA}")
    print(f" Logged {len(line_rows)} line(s) that passed {LINE_SIGMA_FACTOR}σ line test")
    print(f" Wrote species-by-spectrum summary")

# -------------------- Main --------------------
def main():
    parser = argparse.ArgumentParser(description="Apply K(λ), detect spectra, fit Planck, detect atomic lines, summarize species.")
    parser.add_argument("corr_file", nargs="?", help="Path to correction factors (CSV/XLSX)")
    parser.add_argument("spect_file", nargs="?", help="Path to spectra matrix (CSV/XLSX)")
    parser.add_argument("--out-xlsx", help="Output Excel path")
    parser.add_argument("--plots-dir", help="Directory to save per-spectrum plots")
    args = parser.parse_args()

    corr = Path(args.corr_file or CORR_FILE)
    spect = Path(args.spect_file or SPECT_FILE)
    if not str(corr) or not str(spect):
        raise SystemExit("ERROR: Provide paths via CLI or set CORR_FILE and SPECT_FILE at the top.")

    out_xlsx = Path(args.out_xlsx or OUT_XLSX or (spect.with_name(spect.stem + "_corrected.xlsx")))
    plots_dir = Path(args.plots_dir or PLOTS_DIR or (spect.parent / (spect.stem + "_plots")))

    apply_correction_to_matrix(corr, spect, out_xlsx, plots_dir)

if __name__ == "__main__":
    main()
