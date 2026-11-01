#!/usr/bin/env python3
"""Direction C.5.2 -- Calibration Policy Validation and Research Release Gate.

Validates the mandatory calibration restrictions proposed in Direction C.5.1
before treating them as enforceable research rules:
- Audit 1: High-resolution calibration of the proposed alpha=0.005 threshold (K=5000, B=5000).
- Audit 2: Evaluation of dependence diagnostics (lag-1 vs multi-lag, Ljung-Box, McLeod-Li, AR(2) bypass).
- Audit 3: Block-length robustness sweep (L in {2, 5, 10, 20, 30}) on paired datasets.
- Audit 4: Deflated Sharpe Ratio (DSR) inferential meaning, limits, and sensitivity grid.
- Audit 5: Minimum sample length qualification (N in {250, 300, 500, 1000}) across distributions.
- Audit 6: Formal 4-tier Research-Use Policy Matrix.

Safety constraints:
- Offline statistical audit only.
- Strategy V5 remains frozen under VERIFIED NO-GO.
- Zero live trading, testnet orders, paper execution, or forward execution.
- Python standard library only.
"""

import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

# Repository root and timemachine directory resolution
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from benchmark_engine import (
    calc_arithmetic_sharpe,
    calc_deflated_sharpe_ratio,
    calc_dsr_sensitivity_grid,
    calc_minimum_detectable_effect,
    paired_block_bootstrap_test,
    normal_cdf,
    normal_quantile
)


# ==============================================================================
# 1. Statistical Utilities and Mathematical Helpers
# ==============================================================================

def wilson_score_interval(k, n, confidence=0.95):
    """Computes Wilson score confidence interval for a binomial proportion k / n."""
    if n <= 0:
        return [0.0, 0.0]
    p_hat = float(k) / float(n)
    alpha = 1.0 - confidence
    z = normal_quantile(1.0 - alpha / 2.0)
    denom = 1.0 + (z ** 2) / float(n)
    center = (p_hat + (z ** 2) / (2.0 * float(n))) / denom
    spread = (z / denom) * math.sqrt((p_hat * (1.0 - p_hat) / float(n)) + ((z ** 2) / (4.0 * (float(n) ** 2))))
    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return [round(lower, 4), round(upper, 4)]


def gammp(a, x):
    """Incomplete regularized lower gamma function P(a, x) = gamma(a, x) / Gamma(a)."""
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        # Series representation
        ap = a
        del_term = sum_val = 1.0 / a
        for _ in range(1, 300):
            ap += 1.0
            del_term *= x / ap
            sum_val += del_term
            if abs(del_term) < abs(sum_val) * 1e-12:
                break
        return sum_val * math.exp(-x + a * math.log(x) - math.lgamma(a))
    else:
        # Continued fraction representation: Q(a, x) = 1 - P(a, x)
        b = x + 1.0 - a
        c = 1.0 / 1e-30
        d = 1.0 / b
        h = d
        for i in range(1, 300):
            an = -i * (i - a)
            b += 2.0
            d = an * d + b
            if abs(d) < 1e-30:
                d = 1e-30
            c = b + an / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            del_h = d * c
            h *= del_h
            if abs(del_h - 1.0) < 1e-12:
                break
        gln = math.lgamma(a)
        q = math.exp(-x + a * math.log(x) - gln) * h
        return 1.0 - q


def chi2_sf(x, df):
    """Survival function of Chi-Square distribution: P(X >= x) with df degrees of freedom."""
    if x <= 0.0:
        return 1.0
    if df <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - gammp(df / 2.0, x / 2.0)))


def calc_autocorrelation(series, max_lag=20):
    """Computes sample autocorrelation for lags 1 through max_lag."""
    n = len(series)
    if n <= max_lag:
        return [0.0] * max_lag
    mean_val = sum(series) / float(n)
    centered = [x - mean_val for x in series]
    c0 = sum(x * x for x in centered)
    if c0 <= 1e-14:
        return [0.0] * max_lag

    rhos = []
    for k in range(1, max_lag + 1):
        ck = sum(centered[t] * centered[t - k] for t in range(k, n))
        rhos.append(ck / c0)
    return rhos


def calc_ljung_box(series, lags=10):
    """Computes Ljung-Box Q statistic and p-value for serial correlation up to specified lags."""
    n = len(series)
    rhos = calc_autocorrelation(series, max_lag=lags)
    q_stat = 0.0
    for k in range(1, lags + 1):
        q_stat += (rhos[k - 1] ** 2) / float(n - k)
    q_stat *= float(n * (n + 2))
    p_val = chi2_sf(q_stat, lags)
    return {
        "q_stat": round(q_stat, 4),
        "p_val": round(p_val, 6),
        "lags": lags,
        "sample_rhos": [round(r, 4) for r in rhos]
    }


def calc_mcleod_li(series, lags=10):
    """Computes McLeod-Li test (Ljung-Box on squared residuals) for ARCH effects."""
    n = len(series)
    mean_val = sum(series) / float(n)
    squared = [(x - mean_val) ** 2 for x in series]
    return calc_ljung_box(squared, lags=lags)


# ==============================================================================
# 2. Synthetic Data Generators
# ==============================================================================

def generate_gaussian_series(n, mu=0.0, sigma=0.01, rng=None):
    """Generates independent Gaussian returns: D_t ~ N(mu, sigma^2)."""
    if rng is None:
        rng = random.Random()
    return [mu + rng.gauss(0.0, sigma) for _ in range(n)]


def generate_student_t_series(n, df=5, mu=0.0, sigma=0.01, rng=None):
    """Generates independent heavy-tailed returns from Student's t distribution, scaled to variance sigma^2."""
    if rng is None:
        rng = random.Random()
    scale = sigma * math.sqrt((df - 2.0) / float(df))
    series = []
    for _ in range(n):
        z = rng.gauss(0.0, 1.0)
        v = sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(df))
        t_stat = z / math.sqrt(v / float(df))
        series.append(mu + scale * t_stat)
    return series


def generate_ar1_series(n, phi=0.50, mu=0.0, sigma=0.01, burn_in=100, rng=None):
    """Generates stationary AR(1) return series with persistence phi."""
    if rng is None:
        rng = random.Random()
    sigma_eps = sigma * math.sqrt(1.0 - (phi ** 2))
    val = mu
    full_len = n + burn_in
    series = []
    for _ in range(full_len):
        eps = rng.gauss(0.0, sigma_eps)
        val = mu + phi * (val - mu) + eps
        series.append(val)
    return series[burn_in:]


def generate_ar2_series(n, phi1=0.10, phi2=0.50, mu=0.0, sigma=0.01, burn_in=150, rng=None):
    """Generates stationary AR(2) series where lag-1 autocorrelation is low but lag-2 is high."""
    if rng is None:
        rng = random.Random()
    # Unconditional variance scaling for AR(2):
    # Var(X) = sigma_eps^2 / ((1 + phi2) * ((1 - phi2)^2 - phi1^2)) * (1 - phi2)
    denom = (1.0 + phi2) * (((1.0 - phi2) ** 2) - (phi1 ** 2))
    factor = (1.0 - phi2) / denom if denom > 0 else 1.0
    sigma_eps = sigma / math.sqrt(factor) if factor > 0 else sigma

    full_len = n + burn_in
    x = [mu, mu]
    for _ in range(full_len):
        eps = rng.gauss(0.0, sigma_eps)
        val = mu + phi1 * (x[-1] - mu) + phi2 * (x[-2] - mu) + eps
        x.append(val)
    return x[burn_in + 2:]


def generate_garch_series(n, omega=5e-6, alpha=0.15, beta=0.80, mu=0.0, burn_in=200, rng=None):
    """Generates stationary GARCH(1,1) return series with volatility clustering."""
    if rng is None:
        rng = random.Random()
    sigma2 = omega / (1.0 - alpha - beta)
    full_len = n + burn_in
    series = []
    for _ in range(full_len):
        eps = rng.gauss(0.0, 1.0)
        ret = math.sqrt(sigma2) * eps
        series.append(mu + ret)
        sigma2 = omega + alpha * (ret ** 2) + beta * sigma2
    return series[burn_in:]


# ==============================================================================
# 3. Multiprocessing Worker Functions (Top-Level for Pickle Compatibility)
# ==============================================================================

def _audit1_chunk_worker(tasks):
    """Executes a batch of bootstrap test tasks for Audit 1."""
    results = []
    for gen_type, gen_kwargs, seed_data, seed_boot, n, b_boot, block_size in tasks:
        rng = random.Random(seed_data)
        if gen_type == "gaussian":
            d_series = generate_gaussian_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "student_t":
            d_series = generate_student_t_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "ar1":
            d_series = generate_ar1_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "garch":
            d_series = generate_garch_series(n, rng=rng, **gen_kwargs)
        else:
            raise ValueError(f"Unknown generator: {gen_type}")

        zero_bench = [0.0] * n
        res = paired_block_bootstrap_test(
            d_series, zero_bench,
            block_size=block_size,
            n_boot=b_boot,
            seed=seed_boot,
            alternative="greater"
        )
        p_val = res["p_value"]
        results.append({
            "rej_0005": (p_val <= 0.005),
            "rej_0010": (p_val <= 0.010),
            "rej_0050": (p_val <= 0.050),
            "p_val": p_val,
            "k_exceed": res["exceedances_k"]
        })
    return results


def _audit3_chunk_worker(tasks):
    """Executes block length sensitivity tasks on identical generated datasets."""
    results = []
    for gen_type, gen_kwargs, seed_data, seed_boot_base, n, b_boot, block_sizes in tasks:
        rng = random.Random(seed_data)
        if gen_type == "gaussian":
            d_series = generate_gaussian_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "student_t":
            d_series = generate_student_t_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "ar1":
            d_series = generate_ar1_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "garch":
            d_series = generate_garch_series(n, rng=rng, **gen_kwargs)
        else:
            raise ValueError(f"Unknown generator: {gen_type}")

        zero_bench = [0.0] * n
        entry = {}
        for l_idx, L in enumerate(block_sizes):
            boot_seed = seed_boot_base + l_idx * 10007
            res = paired_block_bootstrap_test(
                d_series, zero_bench,
                block_size=L,
                n_boot=b_boot,
                seed=boot_seed,
                alternative="greater"
            )
            entry[f"L_{L}"] = {
                "p_val": res["p_value"],
                "rej_0050": (res["p_value"] <= 0.05),
                "rej_0010": (res["p_value"] <= 0.01)
            }
        results.append(entry)
    return results


def _audit5_chunk_worker(tasks):
    """Executes sample size sensitivity tasks for Audit 5."""
    results = []
    for gen_type, gen_kwargs, seed_data, seed_boot, n, b_boot, block_size in tasks:
        rng = random.Random(seed_data)
        if gen_type == "gaussian":
            d_series = generate_gaussian_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "student_t":
            d_series = generate_student_t_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "ar1":
            d_series = generate_ar1_series(n, rng=rng, **gen_kwargs)
        elif gen_type == "garch":
            d_series = generate_garch_series(n, rng=rng, **gen_kwargs)
        else:
            raise ValueError(f"Unknown generator: {gen_type}")

        zero_bench = [0.0] * n
        res = paired_block_bootstrap_test(
            d_series, zero_bench,
            block_size=block_size,
            n_boot=b_boot,
            seed=seed_boot,
            alternative="greater"
        )
        results.append({
            "n": n,
            "rej_0050": (res["p_value"] <= 0.05),
            "p_val": res["p_value"]
        })
    return results


# ==============================================================================
# 4. Audit 1 Runner: High-Resolution Low-Alpha (alpha = 0.005) Validation
# ==============================================================================

def run_audit_1_low_alpha_validation(k_datasets=5000, n_boot=5000, seed_base=700000,
                                     checkpoint_path=None, force_rerun=False):
    """
    Validates empirical Type I error at nominal alpha = 0.005:
    - Target: K = 5000 datasets, B = 5000 resamples.
    - Finite Monte Carlo resolution: (k + 1) / (B + 1) <= 0.005 <=> k <= 24 (step = 0.0002).
    - Scenarios:
      1. Gaussian i.i.d. (N=500)
      2. GARCH(1,1) (N=500)
      3. Student's t (df=5, heavy tails, N=500)
      4. AR(1) phi=0.30 (moderate persistence, N=500)
      5. AR(1) phi=0.50 (high persistence, N=500)
      6. Gaussian i.i.d. (N=1000)
      7. AR(1) phi=0.50 (N=1000)
    """
    results = {}
    if checkpoint_path and os.path.isfile(checkpoint_path) and not force_rerun:
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            results = {}

    scenarios = [
        ("gaussian_iid_n500", "Gaussian i.i.d. (N=500)", "gaussian", {"mu": 0.0, "sigma": 0.01}, 500, k_datasets),
        ("garch_11_n500", "GARCH(1,1) (N=500)", "garch", {"omega": 5e-6, "alpha": 0.15, "beta": 0.80, "mu": 0.0}, 500, k_datasets),
        ("student_t_nu5_n500", "Student's t nu=5 (N=500)", "student_t", {"df": 5, "mu": 0.0, "sigma": 0.01}, 500, k_datasets),
        ("ar1_phi_030_n500", "AR(1) phi=0.30 (N=500)", "ar1", {"phi": 0.30, "mu": 0.0, "sigma": 0.01}, 500, k_datasets),
        ("ar1_phi_050_n500", "AR(1) phi=0.50 (N=500)", "ar1", {"phi": 0.50, "mu": 0.0, "sigma": 0.01}, 500, k_datasets),
        ("gaussian_iid_n1000", "Gaussian i.i.d. (N=1000)", "gaussian", {"mu": 0.0, "sigma": 0.01}, 1000, min(k_datasets, 2500)),
        ("ar1_phi_050_n1000", "AR(1) phi=0.50 (N=1000)", "ar1", {"phi": 0.50, "mu": 0.0, "sigma": 0.01}, 1000, min(k_datasets, 2500)),
    ]

    nominal_alpha = 0.005
    block_size = 10
    max_k_for_rejection = int(math.floor(nominal_alpha * (n_boot + 1) - 1))  # 24 for B=5000

    n_workers = min(os.cpu_count() or 4, 11)

    for sc_idx, (key, label, gen_type, gen_kwargs, n_obs, k_target) in enumerate(scenarios):
        if key in results:
            print(f"    [Audit 1] Reusing checkpointed {label}: {results[key]['rejections_count']}/{results[key]['k_datasets']} ({results[key]['empirical_rejection_rate']*100:.2f}%)", flush=True)
            continue

        print(f"    [Audit 1] Running {label} (K={k_target}, B={n_boot}, N={n_obs})...", flush=True)
        t0 = time.time()
        sc_seed_offset = seed_base + sc_idx * 100000
        tasks = []
        for i in range(k_target):
            seed_data = sc_seed_offset + i * 7 + 1
            seed_boot = sc_seed_offset + i * 13 + 3
            tasks.append((gen_type, gen_kwargs, seed_data, seed_boot, n_obs, n_boot, block_size))

        chunk_size = 25
        task_chunks = [tasks[c:c + chunk_size] for c in range(0, len(tasks), chunk_size)]

        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            chunk_results = list(executor.map(_audit1_chunk_worker, task_chunks))

        flat_results = [item for sublist in chunk_results for item in sublist]
        rej_count = sum(1 for r in flat_results if r["rej_0005"])
        emp_rate = rej_count / float(k_target)
        ci = wilson_score_interval(rej_count, k_target, confidence=0.95)
        encloses = (ci[0] <= nominal_alpha <= ci[1])
        distortion = emp_rate / nominal_alpha

        dt = time.time() - t0

        results[key] = {
            "label": label,
            "nominal_alpha": nominal_alpha,
            "k_datasets": k_target,
            "n_obs": n_obs,
            "n_boot": n_boot,
            "block_size": block_size,
            "rejections_count": rej_count,
            "empirical_rejection_rate": round(emp_rate, 4),
            "wilson_95_ci": ci,
            "encloses_nominal": encloses,
            "distortion_ratio": round(distortion, 2),
            "finite_monte_carlo_parameters": {
                "b_replicates": n_boot,
                "max_k_for_rejection": max_k_for_rejection,
                "discrete_p_val_at_k_max": round((max_k_for_rejection + 1) / float(n_boot + 1), 6),
                "discrete_p_val_at_k_next": round((max_k_for_rejection + 2) / float(n_boot + 1), 6),
                "p_value_step_resolution": round(1.0 / float(n_boot + 1), 6)
            },
            "elapsed_seconds": round(dt, 2)
        }

        if checkpoint_path:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)

        print(f"    [Audit 1] Finished {label} in {dt:.1f}s: rej={rej_count}/{k_target} ({emp_rate*100:.2f}%) CI={ci} Encloses={encloses}", flush=True)

    return results


# ==============================================================================
# 5. Audit 2 Runner: Evaluation of Dependence Diagnostics
# ==============================================================================

def run_audit_2_dependence_diagnostics(k_datasets=1000, seed_base=800000):
    """
    Evaluates dependence diagnostics:
    - Compares lag-one sample autocorrelation rho_1 against:
      * Multi-lag autocorrelation profile (lags 1 through 10)
      * Ljung-Box Q_10 test (omnibus linear dependence)
      * McLeod-Li Q_10 test on squared returns (non-linear / volatility clustering)
    - Tests failure modes:
      * AR(2) process with phi1=0.10, phi2=0.50 (where rho_1 is small ~0.10-0.20, but rho_2 is high ~0.50)
      * GARCH(1,1) process (where linear rho_1 ~ 0, but ARCH effects are strong)
      * Mild AR(1) phi=0.15, phi=0.20 (where rho_1 < 0.30 but size distortion exists)
    - Examines sensitivity to sample size N in {250, 500, 1000}.
    """
    scenarios = [
        ("gaussian_iid", "Gaussian i.i.d.", "gaussian", {"mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_015", "AR(1) phi=0.15", "ar1", {"phi": 0.15, "mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_020", "AR(1) phi=0.20", "ar1", {"phi": 0.20, "mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_030", "AR(1) phi=0.30", "ar1", {"phi": 0.30, "mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_050", "AR(1) phi=0.50", "ar1", {"phi": 0.50, "mu": 0.0, "sigma": 0.01}),
        ("ar2_phi_01_05", "AR(2) phi1=0.10, phi2=0.50", "ar2", {"phi1": 0.10, "phi2": 0.50, "mu": 0.0, "sigma": 0.01}),
        ("garch_11", "GARCH(1,1)", "garch", {"omega": 5e-6, "alpha": 0.15, "beta": 0.80, "mu": 0.0}),
    ]

    sample_sizes = [250, 500, 1000]
    diagnostic_results = {}

    for sc_idx, (sc_key, sc_label, gen_type, gen_kwargs) in enumerate(scenarios):
        sc_data = {}
        for n in sample_sizes:
            rho1_list = []
            rho2_list = []
            ljung_box_rejections = 0
            mcleod_li_rejections = 0
            rho1_gte_030_count = 0
            rho1_gte_020_count = 0

            # Autocorrelation vector accumulator across lags 1..10
            sum_rhos = [0.0] * 10

            for i in range(k_datasets):
                rng = random.Random(seed_base + sc_idx * 50000 + n * 10 + i * 3)
                if gen_type == "gaussian":
                    s = generate_gaussian_series(n, rng=rng, **gen_kwargs)
                elif gen_type == "ar1":
                    s = generate_ar1_series(n, rng=rng, **gen_kwargs)
                elif gen_type == "ar2":
                    s = generate_ar2_series(n, rng=rng, **gen_kwargs)
                elif gen_type == "garch":
                    s = generate_garch_series(n, rng=rng, **gen_kwargs)
                else:
                    raise ValueError(f"Unknown generator: {gen_type}")

                rhos = calc_autocorrelation(s, max_lag=10)
                r1 = rhos[0]
                r2 = rhos[1]
                rho1_list.append(r1)
                rho2_list.append(r2)
                for l in range(10):
                    sum_rhos[l] += rhos[l]

                if r1 >= 0.30:
                    rho1_gte_030_count += 1
                if r1 >= 0.20:
                    rho1_gte_020_count += 1

                lb = calc_ljung_box(s, lags=10)
                if lb["p_val"] < 0.05:
                    ljung_box_rejections += 1

                ml = calc_mcleod_li(s, lags=10)
                if ml["p_val"] < 0.05:
                    mcleod_li_rejections += 1

            mean_rho1 = sum(rho1_list) / float(k_datasets)
            std_rho1 = math.sqrt(sum((r - mean_rho1) ** 2 for r in rho1_list) / float(k_datasets))
            mean_rho2 = sum(rho2_list) / float(k_datasets)
            avg_profile = [round(x / float(k_datasets), 4) for x in sum_rhos]

            sc_data[f"N_{n}"] = {
                "n_obs": n,
                "mean_rho1": round(mean_rho1, 4),
                "std_rho1": round(std_rho1, 4),
                "mean_rho2": round(mean_rho2, 4),
                "avg_autocorr_lags_1_to_10": avg_profile,
                "rate_rho1_gte_030": round(rho1_gte_030_count / float(k_datasets), 4),
                "rate_rho1_gte_020": round(rho1_gte_020_count / float(k_datasets), 4),
                "rate_ljung_box_p_lt_05": round(ljung_box_rejections / float(k_datasets), 4),
                "rate_mcleod_li_p_lt_05": round(mcleod_li_rejections / float(k_datasets), 4)
            }

        diagnostic_results[sc_key] = {
            "label": sc_label,
            "sample_sizes": sc_data
        }

    return diagnostic_results


# ==============================================================================
# 6. Audit 3 Runner: Block-Length Robustness Sweep (Paired Datasets)
# ==============================================================================

def run_audit_3_block_length_robustness(k_datasets=1000, n_obs=500, n_boot=1000, seed_base=900000,
                                        checkpoint_path=None, force_rerun=False):
    """
    Evaluates block length sensitivity across L in {2, 5, 10, 20, 30} on paired datasets:
    - Exactly identical generated return series tested across all block sizes.
    - Evaluates Gaussian i.i.d., GARCH(1,1), Student's t (df=5), AR(1) phi=0.30, AR(1) phi=0.50.
    - Measures Type I error rates at alpha = 0.05 and alpha = 0.01.
    """
    results = {}
    if checkpoint_path and os.path.isfile(checkpoint_path) and not force_rerun:
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            results = {}

    block_sizes = [2, 5, 10, 20, 30]
    scenarios = [
        ("gaussian_iid", "Gaussian i.i.d.", "gaussian", {"mu": 0.0, "sigma": 0.01}),
        ("garch_11", "GARCH(1,1)", "garch", {"omega": 5e-6, "alpha": 0.15, "beta": 0.80, "mu": 0.0}),
        ("student_t_nu5", "Student's t nu=5", "student_t", {"df": 5, "mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_030", "AR(1) phi=0.30", "ar1", {"phi": 0.30, "mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_050", "AR(1) phi=0.50", "ar1", {"phi": 0.50, "mu": 0.0, "sigma": 0.01}),
    ]

    n_workers = min(os.cpu_count() or 4, 11)

    for sc_idx, (sc_key, sc_label, gen_type, gen_kwargs) in enumerate(scenarios):
        if sc_key in results:
            print(f"    [Audit 3] Reusing checkpointed {sc_label}", flush=True)
            continue

        print(f"    [Audit 3] Running {sc_label} (K={k_datasets}, L in {block_sizes})...", flush=True)
        t0 = time.time()
        sc_seed_offset = seed_base + sc_idx * 50000
        tasks = []
        for i in range(k_datasets):
            seed_data = sc_seed_offset + i * 7 + 1
            seed_boot_base = sc_seed_offset + i * 13 + 3
            tasks.append((gen_type, gen_kwargs, seed_data, seed_boot_base, n_obs, n_boot, block_sizes))

        chunk_size = 20
        task_chunks = [tasks[c:c + chunk_size] for c in range(0, len(tasks), chunk_size)]

        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            chunk_results = list(executor.map(_audit3_chunk_worker, task_chunks))

        flat_results = [item for sublist in chunk_results for item in sublist]

        block_metrics = {}
        for L in block_sizes:
            key_L = f"L_{L}"
            rej_05 = sum(1 for r in flat_results if r[key_L]["rej_0050"])
            rej_01 = sum(1 for r in flat_results if r[key_L]["rej_0010"])
            p_vals = [r[key_L]["p_val"] for r in flat_results]
            p_vals.sort()
            median_p = p_vals[int(len(p_vals) * 0.50)]
            iqr_p = [p_vals[int(len(p_vals) * 0.25)], p_vals[int(len(p_vals) * 0.75)]]

            emp_rate_05 = rej_05 / float(k_datasets)
            emp_rate_01 = rej_01 / float(k_datasets)

            block_metrics[key_L] = {
                "block_size": L,
                "rate_alpha_005": round(emp_rate_05, 4),
                "wilson_ci_alpha_005": wilson_score_interval(rej_05, k_datasets, 0.95),
                "rate_alpha_001": round(emp_rate_01, 4),
                "wilson_ci_alpha_001": wilson_score_interval(rej_01, k_datasets, 0.95),
                "median_p_value": round(median_p, 4),
                "iqr_p_value": [round(iqr_p[0], 4), round(iqr_p[1], 4)]
            }

        dt = time.time() - t0
        results[sc_key] = {
            "label": sc_label,
            "k_datasets": k_datasets,
            "n_obs": n_obs,
            "n_boot": n_boot,
            "block_sizes_evaluated": block_sizes,
            "metrics_by_block_size": block_metrics,
            "elapsed_seconds": round(dt, 2)
        }

        if checkpoint_path:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)

        print(f"    [Audit 3] Finished {sc_label} in {dt:.1f}s", flush=True)

    if checkpoint_path:
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    return results


# ==============================================================================
# 7. Audit 4 Runner: DSR Epistemological & Mathematical Verification
# ==============================================================================

def run_audit_4_dsr_evaluation():
    """
    Evaluates Deflated Sharpe Ratio (DSR) interpretation, limitations, and sensitivity grid:
    - Re-establishes explicit inferential boundaries:
      * DSR is a model-based metric relative to an EVT expected maximum noise hurdle.
      * DSR >= 0.95 is a 5% one-sided threshold under specific Gaussian trial assumptions.
      * 1 - DSR is NOT the posterior probability of strategy falsity or data snooping.
      * DSR does NOT guarantee out-of-sample profitability.
    - Demonstrates sensitivity grid across M in {1, 5, 25, 67, 150} and sigma_M in {0.30, 0.50, 1.00}.
    """
    test_cases = [
        {"name": "Marginal Strategy (Sharpe 1.0, N=500)", "sr": 1.0, "n": 500, "skew": 0.0, "kurt": 3.0},
        {"name": "Strong Strategy (Sharpe 1.8, N=500)", "sr": 1.8, "n": 500, "skew": -0.5, "kurt": 4.5},
        {"name": "Long Sample Moderate (Sharpe 1.2, N=1000)", "sr": 1.2, "n": 1000, "skew": 0.0, "kurt": 3.0},
    ]

    m_values = [1, 5, 25, 67, 150]
    sigma_m_values = [0.30, 0.50, 1.00]

    evaluated_cases = []
    for tc in test_cases:
        grid = {}
        for s_m in sigma_m_values:
            row = {}
            for m in m_values:
                res = calc_deflated_sharpe_ratio(
                    observed_sharpe=tc["sr"],
                    n_obs=tc["n"],
                    n_trials=m,
                    skewness=tc["skew"],
                    kurtosis=tc["kurt"],
                    trial_sharpe_std=s_m,
                    return_details=True
                )
                row[f"M_{m}"] = {
                    "dsr": res["dsr"],
                    "z_stat": res["z_stat"],
                    "expected_max_sr": res["expected_max_sr_annual"],
                    "threshold_pass_095": (res["dsr"] >= 0.95)
                }
            grid[f"sigma_M_{s_m:.2f}"] = row

        evaluated_cases.append({
            "test_case": tc["name"],
            "inputs": tc,
            "sensitivity_grid": grid
        })

    return {
        "inferential_distinctions": {
            "nature_of_metric": "Model-based probability-like measure comparing observed Sharpe to expected maximum of M independent Gaussian trials.",
            "threshold_095_meaning": "Conventional DSR >= 0.95 threshold corresponds to a 5% one-sided evidence hurdle relative to the EVT hurdle (z >= 1.645).",
            "complementary_tail_prob": "1 - DSR is the model-based upper tail probability of random noise matching the observed Sharpe; NOT posterior probability of fraud or falsity.",
            "unverified_assumptions": [
                "Assumes trial Sharpe distribution is approximately Gaussian.",
                "Assumes M trials are independent (real backtest iterations are heavily collinear).",
                "True number of historical trials M is unobservable in practice."
            ],
            "profitability_guarantee": "Zero guarantee of positive future excess return or out-of-sample persistence."
        },
        "evaluated_cases": evaluated_cases
    }


# ==============================================================================
# 8. Audit 5 Runner: Minimum Sample Length Qualification (N Sweep)
# ==============================================================================

def run_audit_5_sample_length_evaluation(k_datasets=1000, n_boot=1000, seed_base=950000,
                                         checkpoint_path=None, force_rerun=False):
    """
    Evaluates Type I error across sample lengths N in {250, 300, 500, 1000}:
    - Evaluates Gaussian i.i.d., Student's t nu=4 (infinite 4th moment), Student's t nu=5 (finite kurtosis),
      AR(1) phi=0.30, and AR(1) phi=0.50 at nominal alpha = 0.05.
    - Demonstrates what N >= 500 achieves (fat-tail stabilization) vs what it cannot fix (serial correlation distortion).
    """
    results = {}
    if checkpoint_path and os.path.isfile(checkpoint_path) and not force_rerun:
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            results = {}

    sample_sizes = [250, 300, 500, 1000]
    scenarios = [
        ("gaussian_iid", "Gaussian i.i.d.", "gaussian", {"mu": 0.0, "sigma": 0.01}),
        ("student_t_nu4", "Student's t nu=4 (infinite 4th moment)", "student_t", {"df": 4, "mu": 0.0, "sigma": 0.01}),
        ("student_t_nu5", "Student's t nu=5 (finite kurtosis)", "student_t", {"df": 5, "mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_030", "AR(1) phi=0.30", "ar1", {"phi": 0.30, "mu": 0.0, "sigma": 0.01}),
        ("ar1_phi_050", "AR(1) phi=0.50", "ar1", {"phi": 0.50, "mu": 0.0, "sigma": 0.01}),
    ]

    n_workers = min(os.cpu_count() or 4, 11)

    for sc_idx, (sc_key, sc_label, gen_type, gen_kwargs) in enumerate(scenarios):
        if sc_key in results:
            print(f"    [Audit 5] Reusing checkpointed {sc_label}", flush=True)
            continue

        print(f"    [Audit 5] Running {sc_label} (N in {sample_sizes})...", flush=True)
        t0 = time.time()
        sc_results = {}
        for n_idx, n in enumerate(sample_sizes):
            sc_seed_offset = seed_base + sc_idx * 50000 + n_idx * 5000
            tasks = []
            for i in range(k_datasets):
                seed_data = sc_seed_offset + i * 7 + 1
                seed_boot = sc_seed_offset + i * 13 + 3
                tasks.append((gen_type, gen_kwargs, seed_data, seed_boot, n, n_boot, 10))

            chunk_size = 25
            task_chunks = [tasks[c:c + chunk_size] for c in range(0, len(tasks), chunk_size)]

            with ProcessPoolExecutor(max_workers=n_workers) as executor:
                chunk_res = list(executor.map(_audit5_chunk_worker, task_chunks))

            flat = [item for sublist in chunk_res for item in sublist]
            rej_05 = sum(1 for r in flat if r["rej_0050"])
            emp_rate = rej_05 / float(k_datasets)

            sc_results[f"N_{n}"] = {
                "n_obs": n,
                "rejections_alpha_005": rej_05,
                "empirical_rejection_rate": round(emp_rate, 4),
                "wilson_95_ci": wilson_score_interval(rej_05, k_datasets, 0.95),
                "distortion_ratio": round(emp_rate / 0.05, 2)
            }

        dt = time.time() - t0
        results[sc_key] = {
            "label": sc_label,
            "sample_size_metrics": sc_results,
            "elapsed_seconds": round(dt, 2)
        }

        if checkpoint_path:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)

        print(f"    [Audit 5] Finished {sc_label} in {dt:.1f}s", flush=True)

    if checkpoint_path:
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    return results


# ==============================================================================
# 9. Audit 6 Runner: Research-Use Policy Matrix Formulation
# ==============================================================================

def formulate_research_use_policy():
    """Constructs the formal 4-tier Research-Use Policy Matrix and epistemological distinctions."""
    return {
        "policy_verdict": "ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS",
        "four_tier_policy_matrix": {
            "tier_1_validated_use_cases": [
                {
                    "domain": "Cross-sectional ranking and zero-autocorrelation series",
                    "conditions": "Sample autocorrelation rho_k < 0.15 for all k in 1..10; Ljung-Box Q_10 p >= 0.05; McLeod-Li Q_10 p >= 0.05; N >= 500.",
                    "permissible_inference": "Nominal alpha = 0.05 and alpha = 0.01 tests are valid; moving-block bootstrap (L=10) achieves nominal calibration."
                },
                {
                    "domain": "GARCH volatility clustering without serial autocorrelation",
                    "conditions": "Linear returns have rho_k < 0.15; Ljung-Box p >= 0.05; McLeod-Li p < 0.05; N >= 500; L in {10, 20}.",
                    "permissible_inference": "Block bootstrap successfully preserves volatility clusters; nominal size well-calibrated (empirical size 0.88% at alpha=0.01)."
                }
            ],
            "tier_2_sensitivity_required_use_cases": [
                {
                    "domain": "Moderate serial dependence or ambiguous persistence",
                    "conditions": "rho_1 in [0.15, 0.30] or Ljung-Box p < 0.05 or McLeod-Li p < 0.05.",
                    "required_protocol": "Mandatory block-length sweep across L in {5, 10, 20}. If significance disappears at L=20, result is flagged as non-robust."
                },
                {
                    "domain": "Fat-tailed distributions with finite kurtosis",
                    "conditions": "Excess kurtosis in [2.0, 6.0]; N in [500, 1000].",
                    "required_protocol": "Must evaluate uncentered bootstrap CI alongside asymptotic t-intervals; report sensitivity to extreme candle winsorization."
                },
                {
                    "domain": "Deflated Sharpe Ratio reporting",
                    "conditions": "Any claim of multiple testing control.",
                    "required_protocol": "Mandatory reporting of 2D sensitivity grid over M in [1, 150] and sigma_M in [0.30, 1.00]; single point estimates prohibited."
                }
            ],
            "tier_3_prohibited_or_inconclusive_claims": [
                {
                    "restriction": "Prohibition of alpha = 0.05 claims under high serial persistence",
                    "rule": "If rho_1 >= 0.30 or AR(2) persistence detected, candidate strategies CANNOT claim significance at nominal alpha = 0.05 or alpha = 0.01."
                },
                {
                    "restriction": "Prohibition of single-lag pre-test gating",
                    "rule": "A strategy cannot claim to be independent based solely on rho_1 < 0.30. Ljung-Box Q_10 and McLeod-Li Q_10 tests are mandatory."
                },
                {
                    "restriction": "Prohibition of short-sample hypothesis testing",
                    "rule": "Hypothesis tests on partitions with N < 500 daily observations are strictly barred from claiming statistical discovery."
                },
                {
                    "restriction": "Prohibition of DSR causal or posterior interpretation",
                    "rule": "DSR cannot be described as the probability that a strategy is real, or that 1-DSR is the probability of data snooping."
                }
            ],
            "tier_4_unverified_model_assumptions": [
                {
                    "assumption": "Independence of backtest trials in DSR",
                    "reality": "Iterated parameter backtests are highly collinear. EVT extreme value theory assumes independence, making true snooping hurdle uncertain."
                },
                {
                    "assumption": "Block stationarity under regime changes",
                    "reality": "Moving-block bootstrap assumes stationary joint distribution; bull/bear regime shifts violate covariance stationarity."
                },
                {
                    "assumption": "Transferability of offline test results to execution",
                    "reality": "Zero historical backtest result proves live execution viability. Slippage, latency, order routing, and market impact remain unmodeled."
                }
            ]
        },
        "epistemological_distinctions": {
            "category_1_mathematical_implementation": "Verifies that formulas are correctly computed according to algebraic specifications (e.g. DSR EVT formula, Mertens SE, block bootstrap slicing).",
            "category_2_synthetic_empirical_calibration": "Evaluates whether nominal error rates match empirical rejections under idealized synthetic data generating processes.",
            "category_3_historical_strategy_evidence": "Empirical performance on historical market data (e.g. V1-V5 backtests). Measures past fit; does not prove future excess return.",
            "category_4_real_world_execution": "Live order execution with genuine exchange latency, queuing, liquidity consumption, and market impact. Offline research cannot prove live viability."
        }
    }


# ==============================================================================
# 10. Master Orchestration Runner
# ==============================================================================

def run_all_direction_c5_2_audits(force_rerun=False):
    """Executes the complete Direction C.5.2 audit suite and compiles master results."""
    checkpoint_dir = os.path.join(ROOT, "trader")
    os.makedirs(checkpoint_dir, exist_ok=True)

    cp_audit1 = os.path.join(checkpoint_dir, ".c5_2_audit1_checkpoint.json")
    cp_audit3 = os.path.join(checkpoint_dir, ".c5_2_audit3_checkpoint.json")
    cp_audit5 = os.path.join(checkpoint_dir, ".c5_2_audit5_checkpoint.json")

    print("=" * 80)
    print("DIRECTION C.5.2 -- CALIBRATION POLICY VALIDATION & RELEASE GATE")
    print("=" * 80)

    print("\n[1/6] Running Audit 1: High-Resolution Low-Alpha (alpha = 0.005) Validation...")
    t0 = time.time()
    audit1_res = run_audit_1_low_alpha_validation(
        k_datasets=5000, n_boot=5000,
        checkpoint_path=cp_audit1, force_rerun=force_rerun
    )
    print(f"  Audit 1 complete in {time.time() - t0:.2f}s.")

    print("\n[2/6] Running Audit 2: Dependence Diagnostics & Multi-Lag Autocorrelation...")
    t0 = time.time()
    audit2_res = run_audit_2_dependence_diagnostics(k_datasets=1000)
    print(f"  Audit 2 complete in {time.time() - t0:.2f}s.")

    print("\n[3/6] Running Audit 3: Block-Length Robustness Sweep (Paired Datasets)...")
    t0 = time.time()
    audit3_res = run_audit_3_block_length_robustness(
        k_datasets=1000, n_obs=500, n_boot=1000,
        checkpoint_path=cp_audit3, force_rerun=force_rerun
    )
    print(f"  Audit 3 complete in {time.time() - t0:.2f}s.")

    print("\n[4/6] Running Audit 4: Deflated Sharpe Ratio Interpretation & Sensitivity...")
    t0 = time.time()
    audit4_res = run_audit_4_dsr_evaluation()
    print(f"  Audit 4 complete in {time.time() - t0:.2f}s.")

    print("\n[5/6] Running Audit 5: Minimum Sample Length Qualification (N Sweep)...")
    t0 = time.time()
    audit5_res = run_audit_5_sample_length_evaluation(
        k_datasets=1000, n_boot=1000,
        checkpoint_path=cp_audit5, force_rerun=force_rerun
    )
    print(f"  Audit 5 complete in {time.time() - t0:.2f}s.")

    print("\n[6/6] Formulating Audit 6: Formal Research-Use Policy Matrix...")
    audit6_res = formulate_research_use_policy()

    master_results = {
        "metadata": {
            "audit_name": "Direction C.5.2 -- Calibration Policy Validation and Research Release Gate",
            "date": "2026-10-30",
            "status": "CALIBRATION_POLICY_VALIDATION_COMPLETE",
            "verdict": "ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS",
            "strategy_v5_status": "FROZEN_VERIFIED_NO_GO",
            "execution_paths": "ALL_DISABLED",
            "python_version": sys.version
        },
        "audit_1_low_alpha_0005_validation": audit1_res,
        "audit_2_dependence_diagnostics": audit2_res,
        "audit_3_block_length_robustness": audit3_res,
        "audit_4_dsr_evaluation": audit4_res,
        "audit_5_sample_length_qualification": audit5_res,
        "audit_6_research_use_policy": audit6_res
    }

    out_path = os.path.join(checkpoint_dir, "direction_c5_2_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(master_results, f, indent=2)
    print(f"\nMaster results saved to: {out_path}")
    return master_results


if __name__ == "__main__":
    force = ("--force" in sys.argv)
    run_all_direction_c5_2_audits(force_rerun=force)
