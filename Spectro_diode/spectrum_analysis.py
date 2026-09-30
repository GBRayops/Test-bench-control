"""
Spectrum Analysis Module
Applies calibration factors and fits Planck temperature
Adapted for RAYOPS GUI integration
"""
import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from pathlib import Path


class KFactorCache:
    """
    Cache for interpolated K factors within a scan sequence.
    Avoids redundant interpolation when all scans share the same wavelength grid.
    """
    def __init__(self):
        self._cached_K_interp = None
        self._cached_wl_hash = None
        self._cached_calib_hash = None

    def get_K_interp(self, wl_calib, K_calib, wl_spectrum):
        """
        Get interpolated K factors, using cache if wavelengths match.
        """
        # Create hash from wavelength array (first, last, length)
        wl_hash = (wl_spectrum[0], wl_spectrum[-1], len(wl_spectrum))
        calib_hash = (wl_calib[0], wl_calib[-1], len(wl_calib))

        # Check if cache is valid
        if (self._cached_K_interp is not None and
            self._cached_wl_hash == wl_hash and
            self._cached_calib_hash == calib_hash):
            return self._cached_K_interp

        # Calculate and cache
        self._cached_K_interp = interpolate_K_to_spectrum(wl_calib, K_calib, wl_spectrum)
        self._cached_wl_hash = wl_hash
        self._cached_calib_hash = calib_hash

        return self._cached_K_interp

    def clear(self):
        """Clear the cache (call when starting a new scan sequence)."""
        self._cached_K_interp = None
        self._cached_wl_hash = None
        self._cached_calib_hash = None


def read_calibration_file(filepath):
    """
    Read calibration factors from CSV file
    Returns: (wavelengths, K_factors) as numpy arrays
    """
    try:
        # Try European format (semicolon separator, comma decimal)
        df = pd.read_csv(filepath, sep=';', decimal=',')
    except:
        # Try standard format
        df = pd.read_csv(filepath)
    
    # Get wavelength and K columns
    cols = list(df.columns)
    wl_col = None
    k_col = None
    
    for col in cols:
        col_lower = str(col).lower()
        if 'wavelength' in col_lower:
            wl_col = col
        elif 'k_lambda' in col_lower or col_lower == 'k':
            k_col = col
    
    if wl_col is None or k_col is None:
        # Fallback to first two columns
        wl_col = cols[0]
        k_col = cols[1]
    
    wavelengths = pd.to_numeric(df[wl_col], errors='coerce').to_numpy()
    K_factors = pd.to_numeric(df[k_col], errors='coerce').to_numpy()
    
    # Remove NaN values
    valid = np.isfinite(wavelengths) & np.isfinite(K_factors)
    wavelengths = wavelengths[valid]
    K_factors = K_factors[valid]
    
    # Sort by wavelength
    order = np.argsort(wavelengths)
    wavelengths = wavelengths[order]
    K_factors = K_factors[order]
    
    return wavelengths, K_factors


def interpolate_K_to_spectrum(wl_calib, K_calib, wl_spectrum):
    """
    Interpolate K factors to match spectrum wavelengths
    """
    K_interp = np.interp(wl_spectrum, wl_calib, K_calib, left=np.nan, right=np.nan)
    
    # Fill NaN at edges with nearest values
    if np.isnan(K_interp[0]):
        first_valid = np.flatnonzero(np.isfinite(K_interp))
        if first_valid.size > 0:
            K_interp[:first_valid[0]] = K_interp[first_valid[0]]
    
    if np.isnan(K_interp[-1]):
        last_valid = np.flatnonzero(np.isfinite(K_interp))
        if last_valid.size > 0:
            K_interp[last_valid[-1] + 1:] = K_interp[last_valid[-1]]
    
    # Interpolate remaining NaNs
    bad = ~np.isfinite(K_interp)
    if bad.any():
        x = np.arange(K_interp.size)
        K_interp[bad] = np.interp(x[bad], x[~bad], K_interp[~bad])
    
    return K_interp


def planck_lambda_per_nm(wavelength_nm, T):
    """
    Planck's law for blackbody radiation
    wavelength_nm: wavelength in nanometers
    T: temperature in Kelvin
    Returns: spectral radiance
    """
    lam = np.asarray(wavelength_nm, float) * 1e-9  # Convert to meters
    h = 6.62607015e-34  # Planck constant
    c = 299792458.0     # Speed of light
    k = 1.380649e-23    # Boltzmann constant
    
    a = 2.0 * h * c**2
    b = h * c / (lam * k * T)
    
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        B = a / (lam**5 * (np.exp(b) - 1.0))
    
    return B * 1e-9  # Scale for plotting


def fit_planck_temperature(wavelengths, calibrated_spectrum, 
                           fit_range=(550.0, 750.0),
                           T_bounds=(500.0, 4000.0)):
    """
    Fit Planck curve to calibrated spectrum to estimate temperature
    
    Returns: (T_estimated, amplitude, fit_ok, model_curve)
    """
    # Select fitting range
    fit_min, fit_max = fit_range
    mask = (wavelengths >= fit_min) & (wavelengths <= fit_max)
    mask &= np.isfinite(calibrated_spectrum) & (calibrated_spectrum > 0)
    
    wl_fit = wavelengths[mask]
    y_fit = calibrated_spectrum[mask]
    
    if len(wl_fit) < 10:
        return np.nan, np.nan, False, None
    
    # Objective function for least squares
    def residuals(params):
        T = params[0]
        
        # Calculate Planck curve
        B = planck_lambda_per_nm(wl_fit, T)
        
        # Linear amplitude fit
        logA = np.mean(np.log(np.clip(y_fit, 1e-300, None)) - 
                      np.log(np.clip(B, 1e-300, None)))
        A = np.exp(logA)
        
        model = A * B
        
        # Log-space residuals for better fitting
        return np.log(np.clip(y_fit, 1e-300, None)) - np.log(np.clip(model, 1e-300, None))
    
    # Initial guess
    T_init = 2000.0
    
    try:
        result = least_squares(
            residuals,
            [T_init],
            bounds=([T_bounds[0]], [T_bounds[1]]),
            loss='huber',
            f_scale=0.02
        )
        
        T_est = result.x[0]
        
        # Calculate final amplitude
        B_full = planck_lambda_per_nm(wl_fit, T_est)
        logA = np.mean(np.log(np.clip(y_fit, 1e-300, None)) - 
                      np.log(np.clip(B_full, 1e-300, None)))
        A_est = np.exp(logA)
        
        # Generate full model curve
        model_curve = A_est * planck_lambda_per_nm(wavelengths, T_est)
        
        fit_ok = result.success and np.isfinite(T_est)
        
        return T_est, A_est, fit_ok, model_curve
        
    except Exception as e:
        print(f"Planck fit error: {e}")
        return np.nan, np.nan, False, None


def detect_atomic_lines(wavelengths, calibrated, planck_model, 
                       line_definitions, search_window=0.4):
    """
    Detect atomic emission lines
    
    line_definitions: list of (name, wavelength_nm) tuples
    Returns: list of detected lines with info
    """
    if planck_model is None or not np.isfinite(planck_model).any():
        return []
    
    detected_lines = []
    
    for line_name, line_wl in line_definitions:
        # Search window around line
        mask = np.abs(wavelengths - line_wl) <= search_window
        
        if not mask.any():
            continue
        
        wl_region = wavelengths[mask]
        cal_region = calibrated[mask]
        bb_region = planck_model[mask]
        
        # Find peak in calibrated - blackbody
        excess = cal_region - bb_region
        peak_idx = np.argmax(excess)
        peak_excess = excess[peak_idx]
        peak_wl = wl_region[peak_idx]
        
        # Simple threshold for detection
        if peak_excess > 0.1 * np.max(calibrated):  # 10% of max signal
            detected_lines.append({
                'line_name': line_name,
                'nominal_wl': line_wl,
                'detected_wl': peak_wl,
                'excess': peak_excess
            })
    
    return detected_lines


def analyze_spectrum(wavelengths, raw_counts, wl_calib, K_calib,
                     fit_range=(550.0, 750.0), T_bounds=(500.0, 4000.0),
                     k_cache=None):
    """
    Complete analysis of a single spectrum

    Args:
        wavelengths: spectrum wavelength array
        raw_counts: raw spectrum counts
        wl_calib: calibration wavelengths
        K_calib: calibration K factors
        fit_range: (min, max) wavelength range for Planck fit
        T_bounds: (min, max) temperature bounds for fit
        k_cache: optional KFactorCache instance for performance

    Returns: dict with all results
    """
    # Interpolate K factors (use cache if provided)
    if k_cache is not None:
        K_interp = k_cache.get_K_interp(wl_calib, K_calib, wavelengths)
    else:
        K_interp = interpolate_K_to_spectrum(wl_calib, K_calib, wavelengths)
    
    # Apply calibration
    calibrated = raw_counts * K_interp
    
    # Fit temperature
    T_est, A_est, fit_ok, planck_model = fit_planck_temperature(
        wavelengths, calibrated, fit_range, T_bounds
    )
    
    # Detect atomic lines
    line_defs = [
        ("Na I D1", 589.0),
        ("Na I D2", 589.6),
        ("Li I", 670.8),
        ("K I", 766.5),
        ("K I", 769.9),
        ("Rb I", 780.0),
        ("Rb I", 794.8),
    ]
    
    detected_lines = []
    if fit_ok and planck_model is not None:
        detected_lines = detect_atomic_lines(
            wavelengths, calibrated, planck_model, line_defs
        )
    
    # Extract species names
    species = set()
    for line in detected_lines:
        species_name = line['line_name'].split()[0]  # "Na", "K", etc
        species.add(species_name)
    
    return {
        'raw': raw_counts,
        'calibrated': calibrated,
        'planck_model': planck_model,
        'temperature_K': T_est,
        'amplitude': A_est,
        'fit_ok': fit_ok,
        'fit_range': fit_range,
        'detected_lines': detected_lines,
        'species': sorted(species)
    }
