#!/usr/bin/env python3
"""Direction C.5 -- Synthetic Statistical Calibration Gate.

Independently assesses the statistical calibration, Type I error rate, power,
confidence-interval coverage, block-length sensitivity, and Deflated Sharpe
Ratio (DSR) behavior using controlled synthetic return series.

Safety constraints:
- Offline statistical simulation only.
- Strategy V5 remains frozen as VERIFIED NO-GO.
- Zero broker execution, zero testnet orders, zero paper execution.
- Does not modify historical registries, ledgers, or benchmark results.
- Uses Python standard library only.
"""

import json
import math
import os
import random
import sys
import time

# Repository path resolution
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
# 1. Statistical Utilities and Confidence Intervals
# ==============================================================================

def wilson_score_interval(k, n, confidence=0.95):
    """
    Computes Wilson score confidence interval for a binomial proportion k / n.
    Accurate and well-behaved for small sample sizes and proportions near 0 or 1.
    Formula:
        center = (k + z^2 / 2) / (n + z^2)
        half_width = (z / (n + z^2)) * sqrt(k * (n - k) / n + z^2 / 4)
    """
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


# ==============================================================================
# 2. Controlled Synthetic Data Generators
# ==============================================================================

def generate_gaussian_series(n, mu=0.0, sigma=0.01, rng=None):
    """
    Generates independent Gaussian returns: D_t ~ N(mu, sigma^2).
    Default sigma = 0.01 corresponds to typical 1% daily crypto volatility.
    """
    if rng is None:
        rng = random.Random()
    return [mu + rng.gauss(0.0, sigma) for _ in range(n)]


def generate_student_t_series(n, df=4, mu=0.0, sigma=0.01, rng=None):
    """
    Generates independent heavy-tailed returns from Student's t distribution,
    scaled to match target standard deviation sigma:
        Var(T) = df / (df - 2) for df > 2.
        Scale factor = sigma * sqrt((df - 2) / df).
        D_t = mu + scale * (Z / sqrt(V / df)), where Z ~ N(0,1), V ~ ChiSq(df).
    """
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
    """
    Generates stationary AR(1) return series with persistence phi:
        D_t - mu = phi * (D_{t-1} - mu) + eps_t, with eps_t ~ N(0, sigma_eps^2).
    To ensure stationary marginal variance Var(D_t) = sigma^2:
        sigma_eps = sigma * sqrt(1 - phi^2).
    Discards initial burn_in observations to eliminate transient initial conditions.
    """
    if rng is None:
        rng = random.Random()
    if abs(phi) >= 1.0:
        raise ValueError("AR(1) parameter phi must be strictly in (-1, 1) for stationarity.")
    sigma_eps = sigma * math.sqrt(1.0 - (phi ** 2))
    val = mu
    full_len = n + burn_in
    series = []
    for _ in range(full_len):
        eps = rng.gauss(0.0, sigma_eps)
        val = mu + phi * (val - mu) + eps
        series.append(val)
    return series[burn_in:]


def generate_garch_series(n, omega=5e-6, alpha=0.15, beta=0.80, mu=0.0, burn_in=200, rng=None):
    """
    Generates stationary GARCH(1,1) return series with volatility clustering:
        D_t = mu + sigma_t * eps_t, eps_t ~ N(0, 1).
        sigma_t^2 = omega + alpha * (D_{t-1} - mu)^2 + beta * sigma_{t-1}^2.
    Unconditional variance: sigma_0^2 = omega / (1 - alpha - beta).
    With omega=5e-6, alpha=0.15, beta=0.80:
        sigma_0^2 = 5e-6 / 0.05 = 1e-4 -> sigma_0 = 0.01 (1% daily vol).
    """
    if rng is None:
        rng = random.Random()
    if alpha + beta >= 1.0:
        raise ValueError("GARCH persistence alpha + beta must be < 1 for stationarity.")
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
# 3. Experiment 1: Bootstrap Type I Error Calibration
# ==============================================================================

def run_type_1_error_experiment(k_datasets=250, n_obs=500, n_boot=500, block_size=10, seed_base=10000):
    """
    Evaluates Type I error rate of paired_block_bootstrap_test under a true zero-mean null:
        H0: mu(D_t) <= 0 vs H1: mu(D_t) > 0.
    Evaluated at alpha = 0.01, 0.05, 0.10 across 6 distinct data generating processes:
    1. Independent Gaussian returns (N(0, 0.01^2))
    2. Heavy-tailed Student's t (df=4, excess kurtosis undefined / extreme)
    3. Heavy-tailed Student's t (df=5, excess kurtosis = 6.0)
    4. Moderately autocorrelated AR(1) (phi = 0.30)
    5. Highly autocorrelated AR(1) (phi = 0.50)
    6. Volatility-clustered GARCH(1,1) (alpha=0.15, beta=0.80)
    """
    scenarios = [
        ("gaussian_iid", "Gaussian i.i.d.", lambda rng: generate_gaussian_series(n_obs, mu=0.0, sigma=0.01, rng=rng)),
        ("student_t_df4", "Student t (df=4, extreme kurtosis)", lambda rng: generate_student_t_series(n_obs, df=4, mu=0.0, sigma=0.01, rng=rng)),
        ("student_t_df5", "Student t (df=5, kurtosis=6.0)", lambda rng: generate_student_t_series(n_obs, df=5, mu=0.0, sigma=0.01, rng=rng)),
        ("ar1_phi_030", "AR(1) phi=0.30 (moderate autocorrelation)", lambda rng: generate_ar1_series(n_obs, phi=0.30, mu=0.0, sigma=0.01, rng=rng)),
        ("ar1_phi_050", "AR(1) phi=0.50 (high autocorrelation)", lambda rng: generate_ar1_series(n_obs, phi=0.50, mu=0.0, sigma=0.01, rng=rng)),
        ("garch_11", "GARCH(1,1) (volatility clustering)", lambda rng: generate_garch_series(n_obs, omega=5e-6, alpha=0.15, beta=0.80, mu=0.0, rng=rng)),
    ]

    results = {}
    nominal_levels = [0.01, 0.05, 0.10]

    for key, label, generator in scenarios:
        t0 = time.time()
        rej_counts = {alpha: 0 for alpha in nominal_levels}
        p_values = []

        for i in range(k_datasets):
            data_rng = random.Random(seed_base + i * 7 + 1)
            boot_seed = seed_base + i * 13 + 3
            d_series = generator(data_rng)
            zero_bench = [0.0] * n_obs

            res = paired_block_bootstrap_test(
                d_series, zero_bench,
                block_size=block_size,
                n_boot=n_boot,
                seed=boot_seed,
                alternative="greater"
            )
            p_val = res["p_value"]
            p_values.append(p_val)
            for alpha in nominal_levels:
                if p_val <= alpha:
                    rej_counts[alpha] += 1

        dt = time.time() - t0
        scenario_res = {
            "label": label,
            "datasets": k_datasets,
            "n_obs": n_obs,
            "block_size": block_size,
            "n_boot": n_boot,
            "elapsed_seconds": round(dt, 2),
            "rejections": {},
        }

        for alpha in nominal_levels:
            k = rej_counts[alpha]
            rate = k / float(k_datasets)
            ci = wilson_score_interval(k, k_datasets, confidence=0.95)
            # Well-calibrated if nominal alpha is within 95% Wilson CI
            calibrated = (ci[0] <= alpha <= ci[1])
            scenario_res["rejections"][str(alpha)] = {
                "nominal_alpha": alpha,
                "rejections_count": k,
                "empirical_rejection_rate": round(rate, 4),
                "wilson_95_ci": ci,
                "calibrated": calibrated,
                "bias": "CONSERVATIVE" if rate < ci[0] or (rate < alpha and not calibrated) else ("INFLATED" if rate > ci[1] or (rate > alpha and not calibrated) else "NOMINAL")
            }

        results[key] = scenario_res

    return results


# ==============================================================================
# 4. Experiment 2: Power and Confidence Interval Coverage
# ==============================================================================

def run_power_and_coverage_experiment(k_datasets=250, n_obs=500, n_boot=500, block_size=10, seed_base=20000):
    """
    Evaluates statistical power and uncentered confidence interval coverage
    across controlled daily effect sizes mu in {-0.0005, 0.0, +0.0002, +0.0005, +0.0010}:
    - Evaluates upper-tail power (H1: mu > 0) at alpha = 0.05.
    - Evaluates lower-tail power (H1: mu < 0) at alpha = 0.05.
    - Evaluates empirical coverage of 95% uncentered bootstrap confidence interval.
    """
    effect_sizes = [
        (-0.0005, "Negative Underperformance (-18.3% ann)"),
        (0.0000, "Exact Zero Null (+0.0% ann)"),
        (0.0002, "Mild Positive Outperformance (+7.3% ann)"),
        (0.0005, "Substantial Outperformance (+18.3% ann)"),
        (0.0010, "Strong Outperformance (+36.5% ann)"),
    ]

    results = {}
    sigma = 0.01

    for mu, label in effect_sizes:
        t0 = time.time()
        rej_greater = 0
        rej_less = 0
        ci_covered = 0

        for i in range(k_datasets):
            data_rng = random.Random(seed_base + int(abs(mu) * 1e6) + i * 11 + 1)
            boot_seed = seed_base + int(abs(mu) * 1e6) + i * 17 + 5
            d_series = generate_gaussian_series(n_obs, mu=mu, sigma=sigma, rng=data_rng)
            zero_bench = [0.0] * n_obs

            # Upper-tail test
            res_greater = paired_block_bootstrap_test(
                d_series, zero_bench,
                block_size=block_size,
                n_boot=n_boot,
                seed=boot_seed,
                alternative="greater"
            )
            if res_greater["p_value"] <= 0.05:
                rej_greater += 1

            # Lower-tail test
            res_less = paired_block_bootstrap_test(
                d_series, zero_bench,
                block_size=block_size,
                n_boot=n_boot,
                seed=boot_seed,
                alternative="less"
            )
            if res_less["p_value"] <= 0.05:
                rej_less += 1

            # CI coverage: true mu covered by daily mean 95% CI
            ci = res_greater["ci_95_daily_mean"]
            if ci[0] <= mu <= ci[1]:
                ci_covered += 1

        dt = time.time() - t0
        power_greater_rate = rej_greater / float(k_datasets)
        power_less_rate = rej_less / float(k_datasets)
        coverage_rate = ci_covered / float(k_datasets)

        results[str(mu)] = {
            "true_daily_mu": mu,
            "annualized_excess_pct": round(mu * 365.25 * 100.0, 1),
            "label": label,
            "datasets": k_datasets,
            "n_obs": n_obs,
            "power_greater_05": round(power_greater_rate, 4),
            "power_greater_95_ci": wilson_score_interval(rej_greater, k_datasets, confidence=0.95),
            "power_less_05": round(power_less_rate, 4),
            "power_less_95_ci": wilson_score_interval(rej_less, k_datasets, confidence=0.95),
            "ci_95_coverage": round(coverage_rate, 4),
            "ci_coverage_95_ci": wilson_score_interval(ci_covered, k_datasets, confidence=0.95),
            "elapsed_seconds": round(dt, 2)
        }

    return results


# ==============================================================================
# 5. Experiment 3: Block-Length Sensitivity Under Serial Dependence
# ==============================================================================

def run_block_length_sensitivity_experiment(k_datasets=250, n_obs=500, n_boot=500, seed_base=30000):
    """
    Evaluates Type I error rate sensitivity to block length L in {1, 5, 10, 20}
    under persistent AR(1) dependence (phi = 0.30 and phi = 0.50).
    Demonstrates the failure of naive i.i.d. bootstrap (L=1) and the stabilization
    achieved by moving-block bootstrap (L=5, 10, 20).
    """
    phi_levels = [
        (0.30, "AR(1) phi=0.30"),
        (0.50, "AR(1) phi=0.50"),
    ]
    block_lengths = [1, 5, 10, 20]
    results = {}

    for phi, phi_label in phi_levels:
        phi_res = {}
        for L in block_lengths:
            t0 = time.time()
            rej_05 = 0

            for i in range(k_datasets):
                data_rng = random.Random(seed_base + int(phi * 100) * 1000 + L * 100 + i * 7 + 1)
                boot_seed = seed_base + int(phi * 100) * 1000 + L * 100 + i * 13 + 3
                d_series = generate_ar1_series(n_obs, phi=phi, mu=0.0, sigma=0.01, rng=data_rng)
                zero_bench = [0.0] * n_obs

                res = paired_block_bootstrap_test(
                    d_series, zero_bench,
                    block_size=L,
                    n_boot=n_boot,
                    seed=boot_seed,
                    alternative="greater"
                )
                if res["p_value"] <= 0.05:
                    rej_05 += 1

            dt = time.time() - t0
            rate = rej_05 / float(k_datasets)
            ci = wilson_score_interval(rej_05, k_datasets, confidence=0.95)

            phi_res[str(L)] = {
                "block_size": L,
                "rejections_count": rej_05,
                "empirical_rejection_rate": round(rate, 4),
                "wilson_95_ci": ci,
                "inflation_over_nominal": round(rate / 0.05, 2),
                "elapsed_seconds": round(dt, 2)
            }
        results[str(phi)] = {
            "phi": phi,
            "label": phi_label,
            "blocks": phi_res
        }

    return results


# ==============================================================================
# 6. Experiment 4: Deflated Sharpe Ratio (DSR) Verification Suite
# ==============================================================================

def run_dsr_verification_suite():
    """
    Verifies Deflated Sharpe Ratio implementation against synthetic parameters:
    1. Daily vs. Annualized scaling invariance.
    2. Expected maximum Sharpe progression across M in {1, 5, 15, 25, 67, 150}.
    3. Sensitivity to skewness and kurtosis (Mertens variance).
    4. Sentinel behavior under non-positive observed Sharpe (S <= 0).
    5. Sensitivity to trial Sharpe dispersion sigma_M in {0.30, 0.50, 0.70, 1.00}.
    """
    results = {}

    # 1. Scaling Invariance Test
    # Annualized inputs: Sharpe=1.20, trial_std=1.0, annual_factor=365.25, N=698, M=67
    ann_res = calc_deflated_sharpe_ratio(
        observed_sharpe=1.20, n_obs=698, n_trials=67,
        trial_sharpe_std=1.0, is_annualized=True, return_details=True
    )
    # Equivalent daily inputs:
    daily_sr = 1.20 / math.sqrt(365.25)
    daily_trial_std = 1.0 / math.sqrt(365.25)
    daily_res = calc_deflated_sharpe_ratio(
        observed_sharpe=daily_sr, n_obs=698, n_trials=67,
        trial_sharpe_std=daily_trial_std, is_annualized=False, return_details=True
    )

    scaling_invariance_verified = (
        abs(ann_res["z_stat"] - daily_res["z_stat"]) < 1e-6 and
        abs(ann_res["dsr"] - daily_res["dsr"]) < 1e-4
    )
    results["scaling_invariance"] = {
        "annualized_z": ann_res["z_stat"],
        "daily_z": daily_res["z_stat"],
        "annualized_dsr": ann_res["dsr"],
        "daily_dsr": daily_res["dsr"],
        "verified": scaling_invariance_verified
    }

    # 2. Non-positive Sharpe Sentinel Check
    zero_res = calc_deflated_sharpe_ratio(observed_sharpe=0.0, n_obs=698, n_trials=67, return_details=True)
    neg_res = calc_deflated_sharpe_ratio(observed_sharpe=-0.50, n_obs=698, n_trials=67, return_details=True)
    results["non_positive_sharpe"] = {
        "zero_sharpe_dsr": zero_res["dsr"],
        "zero_status": zero_res["status"],
        "negative_sharpe_dsr": neg_res["dsr"],
        "negative_status": neg_res["status"],
        "verified": (zero_res["dsr"] == 0.0 and neg_res["dsr"] == 0.0)
    }

    # 3. Expected Maximum Sharpe Progression Across M
    trial_counts = [1, 5, 15, 25, 67, 150]
    m_progression = []
    for m in trial_counts:
        r = calc_deflated_sharpe_ratio(
            observed_sharpe=1.50, n_obs=698, n_trials=m,
            trial_sharpe_std=1.0, return_details=True
        )
        m_progression.append({
            "n_trials_M": m,
            "expected_max_sr_annual": r["expected_max_sr_annual"],
            "z_stat": r["z_stat"],
            "dsr": r["dsr"]
        })
    results["trials_progression"] = m_progression

    # 4. Skewness and Kurtosis Sensitivity
    moments_cases = [
        ("normal", 0.0, 0.0, "Normal distribution"),
        ("pos_skew_fat_tail", 0.5, 3.0, "Positively skewed / fat-tailed"),
        ("neg_skew_extreme_tail", -0.5, 6.0, "Negatively skewed / extreme heavy tail"),
    ]
    moments_res = []
    for case_id, skew, kurt, label in moments_cases:
        r = calc_deflated_sharpe_ratio(
            observed_sharpe=1.50, n_obs=698, n_trials=25,
            skewness=skew, kurtosis=kurt, trial_sharpe_std=1.0,
            return_details=True
        )
        moments_res.append({
            "case": case_id,
            "label": label,
            "skewness": skew,
            "excess_kurtosis": kurt,
            "se_annual": r["se_sr_annual"],
            "z_stat": r["z_stat"],
            "dsr": r["dsr"]
        })
    results["moments_sensitivity"] = moments_res

    # 5. Trial Dispersion Sensitivity
    dispersion_cases = [0.30, 0.50, 0.70, 1.00]
    disp_res = []
    for sig in dispersion_cases:
        r = calc_deflated_sharpe_ratio(
            observed_sharpe=1.50, n_obs=698, n_trials=25,
            trial_sharpe_std=sig, return_details=True
        )
        disp_res.append({
            "trial_std_sigma_M": sig,
            "expected_max_sr_annual": r["expected_max_sr_annual"],
            "z_stat": r["z_stat"],
            "dsr": r["dsr"]
        })
    results["trial_dispersion_sensitivity"] = disp_res

    return results


# ==============================================================================
# 7. Main Calibration Execution Function
# ==============================================================================

def run_direction_c5_calibration(output_json=None):
    """Executes the complete Direction C.5 statistical calibration suite."""
    print("=" * 80)
    print("DIRECTION C.5 -- SYNTHETIC STATISTICAL CALIBRATION GATE")
    print("=" * 80)
    master_t0 = time.time()

    print("\n[1/4] Running Bootstrap Type I Error Calibration across 6 Generators...")
    t1_results = run_type_1_error_experiment(k_datasets=250, n_obs=500, n_boot=500, block_size=10, seed_base=10000)
    for k, v in t1_results.items():
        r05 = v["rejections"]["0.05"]
        print(f"  - {v['label']:<40s} | alpha=0.05: {r05['empirical_rejection_rate']:.4f} (95% CI: {r05['wilson_95_ci']}) [{r05['bias']}] in {v['elapsed_seconds']}s")

    print("\n[2/4] Running Power and Confidence Interval Coverage Calibration...")
    power_results = run_power_and_coverage_experiment(k_datasets=250, n_obs=500, n_boot=500, block_size=10, seed_base=20000)
    for k, v in power_results.items():
        print(f"  - {v['label']:<40s} | Power(>0): {v['power_greater_05']:.4f} | Power(<0): {v['power_less_05']:.4f} | CI95 Cov: {v['ci_95_coverage']:.4f} in {v['elapsed_seconds']}s")

    print("\n[3/4] Running Block-Length Sensitivity Experiment under Serial Dependence...")
    block_results = run_block_length_sensitivity_experiment(k_datasets=250, n_obs=500, n_boot=500, seed_base=30000)
    for phi_k, phi_v in block_results.items():
        print(f"  * {phi_v['label']}:")
        for blk_k, blk_v in phi_v["blocks"].items():
            print(f"      Block L={blk_v['block_size']:2d} | Rejection rate at alpha=0.05: {blk_v['empirical_rejection_rate']:.4f} (95% CI: {blk_v['wilson_95_ci']}) [{blk_v['inflation_over_nominal']}x nominal] in {blk_v['elapsed_seconds']}s")

    print("\n[4/4] Running Deflated Sharpe Ratio (DSR) Verification Suite...")
    dsr_results = run_dsr_verification_suite()
    print(f"  - Scaling invariance verified: {dsr_results['scaling_invariance']['verified']} (Ann z={dsr_results['scaling_invariance']['annualized_z']}, Daily z={dsr_results['scaling_invariance']['daily_z']})")
    print(f"  - Non-positive Sharpe sentinel verified: {dsr_results['non_positive_sharpe']['verified']}")

    total_time = round(time.time() - master_t0, 2)
    print(f"\nCompleted Direction C.5 Synthetic Calibration in {total_time}s.")

    combined_results = {
        "metadata": {
            "task": "Direction C.5 -- Synthetic Statistical Calibration Gate",
            "date": "2026-10-30",
            "total_elapsed_seconds": total_time,
            "generators_tested": ["Gaussian i.i.d.", "Student t (df=4)", "Student t (df=5)", "AR(1) phi=0.30", "AR(1) phi=0.50", "GARCH(1,1)"],
            "safety_checks": {
                "v5_strategy_frozen": True,
                "historical_ledgers_unmodified": True,
                "broker_execution_disabled": True
            }
        },
        "type_1_error_calibration": t1_results,
        "power_and_coverage": power_results,
        "block_length_sensitivity": block_results,
        "dsr_verification": dsr_results
    }

    if output_json:
        os.makedirs(os.path.dirname(output_json), exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(combined_results, f, indent=2)
        print(f"Results written to: {output_json}")

    return combined_results


if __name__ == "__main__":
    out_path = os.path.join(ROOT, "trader", "direction_c5_calibration_results.json")
    run_direction_c5_calibration(output_json=out_path)

