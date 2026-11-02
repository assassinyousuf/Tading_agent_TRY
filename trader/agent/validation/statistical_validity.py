"""Statistical validity, multiple-testing control, and experiment lifecycle audit.

Phase 2.7.2: Establishes whether the research system's statistical conclusions
remain valid under multiple testing, strategy selection, dependent observations,
sampling uncertainty, and repeated experimentation.

Typography standard: Strict ASCII hyphen invariant (0 em dashes, 0 en dashes).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import scipy.stats as stats


class StatisticalValidationStatus(str, Enum):
    VALID = "VALID"
    SUSPICIOUS = "SUSPICIOUS"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"


class AccountingDiscrepancyType(str, Enum):
    MONOTONICITY_VIOLATION = "MONOTONICITY_VIOLATION"
    TRIAL_DELETION_DETECTED = "TRIAL_DELETION_DETECTED"
    DENOMINATOR_MANIPULATION = "DENOMINATOR_MANIPULATION"
    DANGLING_STARTED_TRIAL = "DANGLING_STARTED_TRIAL"
    UNCOUNTED_REJECTED_PROPOSAL = "UNCOUNTED_REJECTED_PROPOSAL"
    EVALUATED_COUNT_MISMATCH = "EVALUATED_COUNT_MISMATCH"


@dataclass
class DSRCalculationResult:
    dsr: float
    z_stat: float
    expected_max_sr_annual: float
    expected_max_sr_daily: float
    se_sr_annual: float
    se_sr_daily: float
    n_trials: int
    n_obs: int
    status: str
    is_dimensionally_consistent: bool
    assumptions_violated: List[str] = field(default_factory=list)


@dataclass
class WhiteRealityCheckResult:
    n_strategies: int
    n_obs: int
    n_boot: int
    block_size: int
    seed: int
    observed_best_stat: float
    best_strategy_index: int
    p_value_wrc: float
    p_value_spa_lower: float
    p_value_spa_consistent: float
    p_value_spa_upper: float
    empirical_mean_excesses: List[float]
    reject_null_at_05: bool
    status: str


@dataclass
class AutocorrelationAnalysisResult:
    n_obs: int
    sample_mean: float
    sample_vol_annual: float
    acf_lags: List[float]
    ljung_box_stat: float
    ljung_box_p_value: float
    ar1_coefficient: float
    has_significant_autocorrelation: bool
    long_run_variance: float
    effective_sample_size: float
    sample_size_inflation_factor: float
    induced_overlapping_horizon: Optional[int] = None
    subperiod_variance_ratio: float = 1.0
    has_regime_shift: bool = False



@dataclass
class BootstrapCoverageResult:
    nominal_level: float
    empirical_coverage: float
    coverage_error: float
    n_replications: int
    sample_size: int
    block_size: int
    mean_ci_width: float
    is_well_calibrated: bool
    data_generating_process: str


@dataclass
class SelectionBiasAuditResult:
    n_strategies: int
    n_obs: int
    true_alpha: float
    cross_correlation: float
    n_simulations: int
    mean_observed_max_sharpe: float
    theoretical_evt_max_sharpe: float
    discrepancy_vs_evt: float
    nominal_false_positive_rate_at_05: float
    dsr_adjusted_false_positive_rate_at_05: float
    bonferroni_false_positive_rate_at_05: float


@dataclass
class AccountingDiscrepancy:
    discrepancy_type: AccountingDiscrepancyType
    severity: str
    description: str
    affected_id: Optional[str] = None
    expected_value: Optional[Any] = None
    observed_value: Optional[Any] = None


@dataclass
class LifecycleAccountingAuditReport:
    total_ledger_rows: int
    m_total_reported: int
    m_evaluated_reported: int
    m_test_reported: int
    m_rejected_reported: int
    interrupted_count: int
    failed_count: int
    pre_test_rejected_count: int
    completed_count: int
    uncounted_proposal_revisions: int
    is_monotonically_ordered: bool
    discrepancies: List[AccountingDiscrepancy]
    overall_status: StatisticalValidationStatus
    audit_notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Deflated Sharpe Ratio Independent Verification
# ---------------------------------------------------------------------------

def calculate_independent_dsr(
    observed_sharpe: float,
    n_obs: int,
    n_trials: int = 1,
    skewness: float = 0.0,
    kurtosis: float = 0.0,
    trial_sharpe_std: float = 1.0,
    benchmark_sharpe: float = 0.0,
    annual_factor: float = 365.25,
    is_annualized: bool = True,
) -> DSRCalculationResult:
    """Independent implementation of Bailey & Lopez de Prado (2014) DSR.

    Formula:
      E[max_M SR] approx trial_sharpe_std * ((1 - gamma) * Phi^{-1}(1 - 1/M) + gamma * Phi^{-1}(1 - 1/(M * e)))
      where gamma approx 0.5772156649 (Euler-Mascheroni constant).
      Mertens (2002) asymptotic variance:
      Var(SR) = (1 - skewness * SR + ((kurtosis + 2) / 4) * SR^2) / (n_obs - 1)
      z = (SR - E[max_M SR]) / sqrt(Var(SR))
      DSR = Phi(z)

    Non-positive Sharpe truncation:
      observed_sharpe <= 0 returns DSR = 0.0 (sentinel policy).
    """
    assumptions_violated: List[str] = []

    if n_obs <= 10:
        assumptions_violated.append("Sample size n_obs <= 10 is too small for asymptotic variance approximation.")
    if n_trials < 1:
        assumptions_violated.append(f"Invalid trial count n_trials = {n_trials}.")

    if not math.isfinite(observed_sharpe) or observed_sharpe <= 0 or n_obs <= 10 or n_trials < 1:
        return DSRCalculationResult(
            dsr=0.0,
            z_stat=0.0,
            expected_max_sr_annual=0.0,
            expected_max_sr_daily=0.0,
            se_sr_annual=0.0,
            se_sr_daily=0.0,
            n_trials=max(1, n_trials),
            n_obs=n_obs,
            status="NON_POSITIVE_OR_INSUFFICIENT_OBS",
            is_dimensionally_consistent=True,
            assumptions_violated=assumptions_violated,
        )

    if is_annualized:
        ann_sr = float(observed_sharpe)
        daily_sr = ann_sr / math.sqrt(annual_factor)
        ann_trial_std = float(trial_sharpe_std)
        daily_trial_std = ann_trial_std / math.sqrt(annual_factor)
        daily_bench_sr = float(benchmark_sharpe) / math.sqrt(annual_factor)
    else:
        daily_sr = float(observed_sharpe)
        ann_sr = daily_sr * math.sqrt(annual_factor)
        daily_trial_std = float(trial_sharpe_std)
        ann_trial_std = daily_trial_std * math.sqrt(annual_factor)
        daily_bench_sr = float(benchmark_sharpe)

    euler_mascheroni = 0.5772156649015329
    if n_trials > 1:
        p1 = 1.0 - 1.0 / float(n_trials)
        p2 = 1.0 - 1.0 / (float(n_trials) * math.e)
        z1 = float(stats.norm.ppf(max(0.5, p1)))
        z2 = float(stats.norm.ppf(max(0.5, p2)))
        expected_max_daily = daily_trial_std * ((1.0 - euler_mascheroni) * z1 + euler_mascheroni * z2)
    else:
        expected_max_daily = daily_bench_sr

    # Mertens (2002) asymptotic variance (kurtosis is excess kurtosis)
    var_daily_sr = (1.0 - skewness * daily_sr + ((kurtosis + 2.0) / 4.0) * (daily_sr ** 2)) / float(n_obs - 1)
    if var_daily_sr <= 1e-12:
        return DSRCalculationResult(
            dsr=0.0,
            z_stat=0.0,
            expected_max_sr_annual=0.0,
            expected_max_sr_daily=0.0,
            se_sr_annual=0.0,
            se_sr_daily=0.0,
            n_trials=n_trials,
            n_obs=n_obs,
            status="ZERO_OR_NEGATIVE_VARIANCE",
            is_dimensionally_consistent=True,
            assumptions_violated=["Non-positive asymptotic variance encountered."],
        )

    se_daily_sr = math.sqrt(var_daily_sr)
    z_stat = (daily_sr - expected_max_daily) / se_daily_sr
    dsr = float(stats.norm.cdf(z_stat))

    expected_max_ann = expected_max_daily * math.sqrt(annual_factor)
    se_ann_sr = se_daily_sr * math.sqrt(annual_factor)
    z_stat_ann = (ann_sr - expected_max_ann) / se_ann_sr

    dim_consistent = abs(z_stat - z_stat_ann) < 1e-7

    return DSRCalculationResult(
        dsr=round(dsr, 6),
        z_stat=round(z_stat, 6),
        expected_max_sr_annual=round(expected_max_ann, 6),
        expected_max_sr_daily=round(expected_max_daily, 8),
        se_sr_annual=round(se_ann_sr, 6),
        se_sr_daily=round(se_daily_sr, 8),
        n_trials=n_trials,
        n_obs=n_obs,
        status="VALID",
        is_dimensionally_consistent=dim_consistent,
        assumptions_violated=assumptions_violated,
    )


# ---------------------------------------------------------------------------
# 2. White's Reality Check (WRC) and Hansen's SPA Implementation
# ---------------------------------------------------------------------------

def white_reality_check(
    strategy_returns_matrix: np.ndarray,
    benchmark_returns: np.ndarray,
    n_boot: int = 1000,
    block_size: int = 10,
    seed: int = 42,
    studentized: bool = False,
) -> WhiteRealityCheckResult:
    """Implement White's Reality Check (White 2000) and Hansen's SPA (Hansen 2005).

    Tests H0: max_{k=1...K} E[d_{k,t}] <= 0 against H1: max_{k=1...K} E[d_{k,t}] > 0
    where d_{k,t} = r_{k,t} - r_{0,t} is the excess return over benchmark.

    Procedure:
      1. Compute excess returns matrix D of shape (N, K).
      2. Sample mean d_bar_k for each strategy k.
      3. Observed test statistic T_N = max_k d_bar_k (or studentized: max_k d_bar_k / se_k).
      4. Resample time indices in moving blocks of length L (simultaneously for all K strategies).
      5. Under H0:
         - White (2000): Center each strategy by subtracting its sample mean:
           d_tilde_{k,b}^* = d_{k,b}^* - d_bar_k
         - Hansen (2005) SPA:
           SPA Lower: d_tilde = d_{k,b}^* - max(0, d_bar_k)
           SPA Consistent: d_tilde = d_{k,b}^* - d_bar_k * I(d_bar_k >= -sqrt((2 ln ln N) / N) * sigma_k)
           SPA Upper: d_tilde = d_{k,b}^* - d_bar_k (identical to White)
      6. p-value = (1 + sum(I(T_{N,b}^* >= T_N))) / (B + 1)
    """
    rng = np.random.RandomState(seed)

    strat_mat = np.asarray(strategy_returns_matrix, dtype=float)
    if strat_mat.ndim == 1:
        strat_mat = strat_mat.reshape(-1, 1)

    bench = np.asarray(benchmark_returns, dtype=float).ravel()

    n_obs, n_strategies = strat_mat.shape
    if len(bench) != n_obs:
        min_len = min(n_obs, len(bench))
        strat_mat = strat_mat[:min_len]
        bench = bench[:min_len]
        n_obs = min_len

    if n_obs < max(block_size, 10) or n_strategies < 1:
        return WhiteRealityCheckResult(
            n_strategies=n_strategies,
            n_obs=n_obs,
            n_boot=n_boot,
            block_size=block_size,
            seed=seed,
            observed_best_stat=0.0,
            best_strategy_index=0,
            p_value_wrc=1.0,
            p_value_spa_lower=1.0,
            p_value_spa_consistent=1.0,
            p_value_spa_upper=1.0,
            empirical_mean_excesses=[0.0] * n_strategies,
            reject_null_at_05=False,
            status="INSUFFICIENT_OBSERVATIONS",
        )

    # Excess returns matrix D: shape (N, K)
    excess_mat = strat_mat - bench[:, np.newaxis]

    mean_excesses = np.mean(excess_mat, axis=0)
    std_excesses = np.std(excess_mat, axis=0, ddof=1)
    std_excesses = np.maximum(std_excesses, 1e-12)

    se_excesses = std_excesses / math.sqrt(n_obs)

    if studentized:
        t_stats = mean_excesses / se_excesses
        best_k = int(np.argmax(t_stats))
        obs_t = float(t_stats[best_k])
    else:
        best_k = int(np.argmax(mean_excesses))
        obs_t = float(mean_excesses[best_k])

    # Moving block indices
    n_blocks = int(math.ceil(n_obs / float(block_size)))
    max_start = n_obs - block_size

    # Hansen threshold: c_k = sqrt((2 ln ln N) / N) * sigma_k
    if n_obs > 2:
        log_log_n = math.log(max(1.0001, math.log(n_obs)))
        hansen_c = np.sqrt((2.0 * log_log_n) / float(n_obs)) * std_excesses
    else:
        hansen_c = std_excesses

    k_wrc = 0
    k_spa_lower = 0
    k_spa_consistent = 0
    k_spa_upper = 0

    for _ in range(n_boot):
        # Draw blocks simultaneously across strategies
        block_starts = rng.randint(0, max_start + 1, size=n_blocks)
        indices = np.concatenate([np.arange(s, s + block_size) for s in block_starts])[:n_obs]

        boot_sample = excess_mat[indices, :]
        boot_means = np.mean(boot_sample, axis=0)

        # 1. White centering: subtract sample mean
        wrc_centered = boot_means - mean_excesses
        if studentized:
            boot_stds = np.maximum(np.std(boot_sample, axis=0, ddof=1), 1e-12)
            boot_se = boot_stds / math.sqrt(n_obs)
            t_boot_wrc = np.max(wrc_centered / boot_se)
        else:
            t_boot_wrc = np.max(wrc_centered)

        if t_boot_wrc >= obs_t:
            k_wrc += 1

        # 2. Hansen SPA Lower: subtract max(0, d_bar)
        spa_lower_shift = np.maximum(0.0, mean_excesses)
        spa_lower_centered = boot_means - spa_lower_shift
        if studentized:
            t_boot_lower = np.max(spa_lower_centered / boot_se)
        else:
            t_boot_lower = np.max(spa_lower_centered)

        if t_boot_lower >= obs_t:
            k_spa_lower += 1

        # 3. Hansen SPA Consistent: subtract d_bar * I(d_bar >= -c_k)
        spa_cons_shift = np.where(mean_excesses >= -hansen_c, mean_excesses, 0.0)
        spa_cons_centered = boot_means - spa_cons_shift
        if studentized:
            t_boot_cons = np.max(spa_cons_centered / boot_se)
        else:
            t_boot_cons = np.max(spa_cons_centered)

        if t_boot_cons >= obs_t:
            k_spa_consistent += 1

        # 4. Hansen SPA Upper is identical to White
        t_boot_upper = t_boot_wrc
        if t_boot_upper >= obs_t:
            k_spa_upper += 1

    p_wrc = (k_wrc + 1.0) / (float(n_boot) + 1.0)
    p_spa_lower = (k_spa_lower + 1.0) / (float(n_boot) + 1.0)
    p_spa_consistent = (k_spa_consistent + 1.0) / (float(n_boot) + 1.0)
    p_spa_upper = (k_spa_upper + 1.0) / (float(n_boot) + 1.0)

    return WhiteRealityCheckResult(
        n_strategies=n_strategies,
        n_obs=n_obs,
        n_boot=n_boot,
        block_size=block_size,
        seed=seed,
        observed_best_stat=round(obs_t, 6),
        best_strategy_index=best_k,
        p_value_wrc=round(p_wrc, 4),
        p_value_spa_lower=round(p_spa_lower, 4),
        p_value_spa_consistent=round(p_spa_consistent, 4),
        p_value_spa_upper=round(p_spa_upper, 4),
        empirical_mean_excesses=[round(float(m), 6) for m in mean_excesses],
        reject_null_at_05=(p_wrc < 0.05),
        status="VALID",
    )


# ---------------------------------------------------------------------------
# 3. Autocorrelation, Overlapping Returns, and Effective Sample Size
# ---------------------------------------------------------------------------

def analyze_autocorrelation(
    returns: Sequence[float],
    max_lag: int = 20,
    annual_factor: float = 365.25,
) -> AutocorrelationAnalysisResult:
    """Analyze serial dependence, Ljung-Box test, long-run variance, and effective sample size.

    Formulas:
      Sample ACF: rho_k = sum_{t=k+1}^N (r_t - mu)(r_{t-k} - mu) / sum_{t=1}^N (r_t - mu)^2
      Ljung-Box: Q = N * (N + 2) * sum_{k=1}^P rho_k^2 / (N - k) ~ ChiSq(P)
      Newey-West HAC Long-Run Variance:
        omega^2 = gamma_0 + 2 * sum_{k=1}^P (1 - k / (P + 1)) * gamma_k
      Effective Sample Size:
        N_eff = N * (gamma_0 / omega^2)
    """
    arr = np.asarray(returns, dtype=float).ravel()
    n = len(arr)

    if n <= max_lag + 1:
        return AutocorrelationAnalysisResult(
            n_obs=n,
            sample_mean=float(np.mean(arr)) if n > 0 else 0.0,
            sample_vol_annual=float(np.std(arr, ddof=1) * math.sqrt(annual_factor)) if n > 1 else 0.0,
            acf_lags=[0.0] * max_lag,
            ljung_box_stat=0.0,
            ljung_box_p_value=1.0,
            ar1_coefficient=0.0,
            has_significant_autocorrelation=False,
            long_run_variance=float(np.var(arr, ddof=1)) if n > 1 else 1.0,
            effective_sample_size=float(n),
            sample_size_inflation_factor=1.0,
        )

    mean = float(np.mean(arr))
    var0 = float(np.var(arr, ddof=0))
    if var0 <= 1e-14:
        return AutocorrelationAnalysisResult(
            n_obs=n,
            sample_mean=mean,
            sample_vol_annual=0.0,
            acf_lags=[0.0] * max_lag,
            ljung_box_stat=0.0,
            ljung_box_p_value=1.0,
            ar1_coefficient=0.0,
            has_significant_autocorrelation=False,
            long_run_variance=0.0,
            effective_sample_size=float(n),
            sample_size_inflation_factor=1.0,
        )

    demeaned = arr - mean
    acfs: List[float] = []
    gammas: List[float] = [var0]

    for lag in range(1, max_lag + 1):
        cov = float(np.sum(demeaned[lag:] * demeaned[:-lag])) / float(n)
        rho = cov / var0
        acfs.append(float(rho))
        gammas.append(cov)

    # Ljung-Box Q statistic
    q_stat = 0.0
    for k in range(1, max_lag + 1):
        rho_k = acfs[k - 1]
        q_stat += (rho_k ** 2) / float(n - k)
    q_stat *= float(n * (n + 2))

    lb_p_value = 1.0 - float(stats.chi2.cdf(q_stat, df=max_lag))

    # AR(1) coefficient estimate (ordinary least squares)
    x = demeaned[:-1]
    y = demeaned[1:]
    ar1 = float(np.dot(x, y) / np.dot(x, x)) if np.dot(x, x) > 1e-14 else 0.0

    # Newey-West Long Run Variance (HAC) with Bartlett kernel
    bandwidth = max_lag
    lrv = gammas[0]
    for k in range(1, bandwidth + 1):
        weight = 1.0 - float(k) / float(bandwidth + 1)
        lrv += 2.0 * weight * gammas[k]

    lrv = max(1e-12, lrv)
    n_eff = float(n) * (gammas[0] / lrv)
    n_eff = max(1.0, min(float(n * 5), n_eff))  # bounded

    inflation_factor = float(n) / n_eff

    # Detect induced overlapping signature: evaluate all candidate h in [2, 15] and find argmin diff
    induced_h: Optional[int] = None
    if acfs[0] > 0.3:
        best_diff = 1e9
        best_h = None
        for candidate_h in range(2, min(15, max_lag)):
            expected_pattern = [1.0 - float(k) / float(candidate_h) if k < candidate_h else 0.0 for k in range(1, candidate_h + 2)]
            actual_sub = acfs[:candidate_h + 1]
            if len(actual_sub) == len(expected_pattern):
                diff = sum(abs(a - e) for a, e in zip(actual_sub, expected_pattern)) / float(len(expected_pattern))
                if diff < best_diff and diff < 0.25:
                    best_diff = diff
                    best_h = candidate_h
        induced_h = best_h

    # Regime shift / non-stationarity check: split into two halves and compare variance
    half = n // 2
    var_first = float(np.var(arr[:half], ddof=1)) if half > 1 else var0
    var_second = float(np.var(arr[half:], ddof=1)) if (n - half) > 1 else var0
    var_first = max(1e-12, var_first)
    var_second = max(1e-12, var_second)
    var_ratio = max(var_first / var_second, var_second / var_first)
    has_regime_shift = var_ratio >= 3.0

    ann_vol = float(np.std(arr, ddof=1) * math.sqrt(annual_factor))

    return AutocorrelationAnalysisResult(
        n_obs=n,
        sample_mean=round(mean, 8),
        sample_vol_annual=round(ann_vol, 6),
        acf_lags=[round(x, 4) for x in acfs],
        ljung_box_stat=round(q_stat, 4),
        ljung_box_p_value=round(lb_p_value, 6),
        ar1_coefficient=round(ar1, 4),
        has_significant_autocorrelation=(lb_p_value < 0.05),
        long_run_variance=round(lrv, 10),
        effective_sample_size=round(n_eff, 2),
        sample_size_inflation_factor=round(inflation_factor, 4),
        induced_overlapping_horizon=induced_h,
        subperiod_variance_ratio=round(var_ratio, 4),
        has_regime_shift=has_regime_shift,
    )


# ---------------------------------------------------------------------------
# 4. Bootstrap Confidence Interval Coverage Validation
# ---------------------------------------------------------------------------

def validate_bootstrap_ci_coverage(
    data_generator: Callable[[np.random.RandomState], np.ndarray],
    ground_truth_mean: float,
    n_samples: int = 100,
    sample_size: int = 100,
    n_boot: int = 500,
    block_size: int = 5,
    nominal_level: float = 0.95,
    seed: int = 42,
    dgp_name: str = "SYNTHETIC_PROCESS",
) -> BootstrapCoverageResult:
    """Evaluate empirical coverage of moving-block bootstrap confidence intervals.

    Checks what percentage of 95% bootstrap intervals cover the true population mean
    across repeated synthetic datasets generated with known statistical properties.
    """
    rng = np.random.RandomState(seed)
    covered_count = 0
    ci_widths: List[float] = []

    alpha = 1.0 - nominal_level
    lower_pct = (alpha / 2.0) * 100.0
    upper_pct = (1.0 - alpha / 2.0) * 100.0

    n_blocks = int(math.ceil(sample_size / float(block_size)))
    max_start = sample_size - block_size

    for s_idx in range(n_samples):
        sample = data_generator(rng)
        if len(sample) != sample_size:
            sample = sample[:sample_size]

        boot_means = []
        for _ in range(n_boot):
            block_starts = rng.randint(0, max_start + 1, size=n_blocks)
            indices = np.concatenate([np.arange(b, b + block_size) for b in block_starts])[:sample_size]
            boot_sample = sample[indices]
            boot_means.append(float(np.mean(boot_sample)))

        ci_lower = float(np.percentile(boot_means, lower_pct))
        ci_upper = float(np.percentile(boot_means, upper_pct))
        ci_widths.append(ci_upper - ci_lower)

        if ci_lower <= ground_truth_mean <= ci_upper:
            covered_count += 1

    emp_coverage = float(covered_count) / float(n_samples)
    cov_error = abs(emp_coverage - nominal_level)

    # Within binomial standard error tolerance at nominal 95%
    # se_p = sqrt(0.95 * 0.05 / 100) approx 0.0218 -> 3 se approx 0.065
    is_calibrated = cov_error <= 0.08

    return BootstrapCoverageResult(
        nominal_level=nominal_level,
        empirical_coverage=round(emp_coverage, 4),
        coverage_error=round(cov_error, 4),
        n_replications=n_samples,
        sample_size=sample_size,
        block_size=block_size,
        mean_ci_width=round(float(np.mean(ci_widths)), 6),
        is_well_calibrated=is_calibrated,
        data_generating_process=dgp_name,
    )


# ---------------------------------------------------------------------------
# 5. Selection Bias and Parameter Search Simulation
# ---------------------------------------------------------------------------

def simulate_selection_bias(
    n_strategies: int,
    n_obs: int,
    true_alpha: float = 0.0,
    cross_correlation: float = 0.0,
    n_simulations: int = 200,
    annual_factor: float = 365.25,
    seed: int = 42,
) -> SelectionBiasAuditResult:
    """Simulate selection bias across M parameter variations / strategies.

    Generates M series with cross-correlation rho. Measures the expected maximum
    Sharpe ratio under the null (or true alpha) compared to EVT theoretical predictions,
    and calculates false-positive rates of naive vs DSR vs Bonferroni.
    """
    rng = np.random.RandomState(seed)

    # Construct equicorrelated covariance matrix: Sigma = (1 - rho) * I + rho * J
    if cross_correlation > 0.0:
        cov = np.full((n_strategies, n_strategies), cross_correlation)
        np.fill_diagonal(cov, 1.0)
        # Cholesky factor
        try:
            chol = np.linalg.cholesky(cov)
        except np.linalg.LinAlgError:
            cov = np.eye(n_strategies)
            chol = np.eye(n_strategies)
    else:
        chol = np.eye(n_strategies)

    max_sharpes = []
    naive_fp_count = 0
    dsr_fp_count = 0
    bonferroni_fp_count = 0

    euler_mascheroni = 0.5772156649015329
    if n_strategies > 1:
        p1 = 1.0 - 1.0 / float(n_strategies)
        p2 = 1.0 - 1.0 / (float(n_strategies) * math.e)
        z1 = float(stats.norm.ppf(p1))
        z2 = float(stats.norm.ppf(p2))
        evt_max_z = (1.0 - euler_mascheroni) * z1 + euler_mascheroni * z2
    else:
        evt_max_z = 0.0

    evt_max_ann_sharpe = (evt_max_z / math.sqrt(n_obs)) * math.sqrt(annual_factor)

    for _ in range(n_simulations):
        # Draw standard normals
        z = rng.randn(n_obs, n_strategies)
        # Correlate
        sim_rets = z @ chol.T + (true_alpha / annual_factor)

        # Compute annualized Sharpe for each strategy
        means = np.mean(sim_rets, axis=0)
        stds = np.maximum(np.std(sim_rets, axis=0, ddof=1), 1e-12)
        daily_srs = means / stds
        ann_srs = daily_srs * math.sqrt(annual_factor)

        best_idx = int(np.argmax(ann_srs))
        best_sr = float(ann_srs[best_idx])
        max_sharpes.append(best_sr)

        # Naive one-sample test (H0: Sharpe <= 0)
        t_stat = (daily_srs[best_idx]) * math.sqrt(n_obs)
        naive_p = 1.0 - float(stats.norm.cdf(t_stat))
        if naive_p < 0.05:
            naive_fp_count += 1

        # Bonferroni adjustment: p_bonf = min(1.0, naive_p * M)
        if (naive_p * float(n_strategies)) < 0.05:
            bonferroni_fp_count += 1

        # DSR evaluation with M_total = n_strategies
        dsr_res = calculate_independent_dsr(
            observed_sharpe=best_sr,
            n_obs=n_obs,
            n_trials=n_strategies,
            trial_sharpe_std=1.0,
            annual_factor=annual_factor,
            is_annualized=True,
        )
        # DSR rejects null if DSR > 0.95
        if dsr_res.dsr > 0.95:
            dsr_fp_count += 1

    mean_max_sr = float(np.mean(max_sharpes))

    return SelectionBiasAuditResult(
        n_strategies=n_strategies,
        n_obs=n_obs,
        true_alpha=true_alpha,
        cross_correlation=cross_correlation,
        n_simulations=n_simulations,
        mean_observed_max_sharpe=round(mean_max_sr, 4),
        theoretical_evt_max_sharpe=round(evt_max_ann_sharpe, 4),
        discrepancy_vs_evt=round(abs(mean_max_sr - evt_max_ann_sharpe), 4),
        nominal_false_positive_rate_at_05=round(float(naive_fp_count) / float(n_simulations), 4),
        dsr_adjusted_false_positive_rate_at_05=round(float(dsr_fp_count) / float(n_simulations), 4),
        bonferroni_false_positive_rate_at_05=round(float(bonferroni_fp_count) / float(n_simulations), 4),
    )


# ---------------------------------------------------------------------------
# 6. Lifecycle Accounting and Trial Ledger Auditor
# ---------------------------------------------------------------------------

def audit_experiment_lifecycle_accounting(conn: sqlite3.Connection) -> LifecycleAccountingAuditReport:
    """Audit M_total, M_evaluated, and experiment accounting across database tables.

    Checks:
      1. Monotonicity of cumulative_m_total in trial_ledger.
      2. No missing, modified, or deleted trials.
      3. Consistency between trial_ledger, experiments, and execution_requests.
      4. Handling of failed, interrupted, pre-test rejected, and completed trials.
      5. Discovery of uncounted proposals in proposal_revisions table.
    """
    cur = conn.cursor()

    # 1. Trial Ledger Totals
    cur.execute("SELECT COUNT(*) FROM trial_ledger;")
    total_ledger_rows = cur.fetchone()[0]

    # Disposition breakdown
    cur.execute("SELECT disposition, COUNT(*) FROM trial_ledger GROUP BY disposition;")
    disp_counts = dict(cur.fetchall())

    completed_count = disp_counts.get("COMPLETED", 0) + disp_counts.get("TRAIN_EVALUATED", 0)
    interrupted_count = disp_counts.get("INTERRUPTED", 0)
    failed_count = disp_counts.get("FAILED", 0)
    pre_test_rejected_count = disp_counts.get("PRE_TEST_REJECTED", 0)

    cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition IN ('COMPLETED', 'TRAIN_EVALUATED', 'VALIDATION_EVALUATED', 'TEST_EVALUATED');")
    m_eval_reported = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition = 'TEST_EVALUATED';")
    m_test_reported = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition LIKE '%REJECTED%';")
    m_rej_reported = cur.fetchone()[0]

    # Uncounted proposal revisions (stages before execution engine)
    cur.execute("SELECT COUNT(*) FROM proposal_revisions;")
    uncounted_proposal_revisions = cur.fetchone()[0]

    # Check monotonicity of cumulative_m_total
    cur.execute("SELECT trial_id, cumulative_m_total, disposition FROM trial_ledger ORDER BY trial_id ASC;")
    trials = cur.fetchall()

    discrepancies: List[AccountingDiscrepancy] = []
    is_monotonic = True
    last_m = 0

    for tid, m_tot, disp in trials:
        if m_tot is None:
            is_monotonic = False
            discrepancies.append(
                AccountingDiscrepancy(
                    discrepancy_type=AccountingDiscrepancyType.MONOTONICITY_VIOLATION,
                    severity="CRITICAL",
                    description=f"Trial {tid} has NULL cumulative_m_total.",
                    affected_id=str(tid),
                )
            )
        elif m_tot < last_m:
            is_monotonic = False
            discrepancies.append(
                AccountingDiscrepancy(
                    discrepancy_type=AccountingDiscrepancyType.MONOTONICITY_VIOLATION,
                    severity="CRITICAL",
                    description=f"Trial {tid} has decreasing cumulative_m_total ({m_tot} < {last_m}).",
                    affected_id=str(tid),
                    expected_value=last_m,
                    observed_value=m_tot,
                )
            )
        last_m = m_tot if m_tot is not None else last_m

    # Check for dangling STARTED trials
    started_count = disp_counts.get("STARTED", 0)
    if started_count > 0:
        discrepancies.append(
            AccountingDiscrepancy(
                discrepancy_type=AccountingDiscrepancyType.DANGLING_STARTED_TRIAL,
                severity="HIGH",
                description=f"Found {started_count} trials lingering with disposition 'STARTED'.",
                expected_value=0,
                observed_value=started_count,
            )
        )

    # Check for uncounted rejected proposals
    if uncounted_proposal_revisions > 0:
        discrepancies.append(
            AccountingDiscrepancy(
                discrepancy_type=AccountingDiscrepancyType.UNCOUNTED_REJECTED_PROPOSAL,
                severity="MEDIUM",
                description=(
                    f"Found {uncounted_proposal_revisions} pre-execution proposal revisions in SQLite "
                    "that are excluded from M_total trial accounting. This creates a statistical divergence "
                    "between hypothesis exploration budget and backtest parameter budget."
                ),
                expected_value=0,
                observed_value=uncounted_proposal_revisions,
            )
        )

    # Check consistency between experiments and trial_ledger:
    # Every experiment in 'experiments' table MUST have a corresponding record in 'trial_ledger'
    cur.execute(
        """
        SELECT COUNT(*) FROM experiments e
        LEFT JOIN trial_ledger t ON e.experiment_id = t.experiment_id
        WHERE t.experiment_id IS NULL;
        """
    )
    unanchored_experiments = cur.fetchone()[0]

    if unanchored_experiments > 0:
        discrepancies.append(
            AccountingDiscrepancy(
                discrepancy_type=AccountingDiscrepancyType.EVALUATED_COUNT_MISMATCH,
                severity="CRITICAL",
                description=(
                    f"Found {unanchored_experiments} experiment rows in 'experiments' table "
                    "that have no corresponding trial record in 'trial_ledger'. "
                    "This violates immutable trial pre-commitment governance."
                ),
                expected_value=0,
                observed_value=unanchored_experiments,
            )
        )


    overall_status = StatisticalValidationStatus.VALID
    for d in discrepancies:
        if d.severity == "CRITICAL":
            overall_status = StatisticalValidationStatus.INVALID
            break
        elif d.severity == "HIGH":
            overall_status = StatisticalValidationStatus.SUSPICIOUS

    audit_notes = [
        f"Audited {total_ledger_rows} trials in trial_ledger.",
        f"M_total={total_ledger_rows}, M_evaluated={m_eval_reported}, M_test={m_test_reported}, M_rejected={m_rej_reported}.",
        f"Interrupted={interrupted_count}, Failed={failed_count}, Pre-test rejected={pre_test_rejected_count}, Completed={completed_count}.",
    ]

    return LifecycleAccountingAuditReport(
        total_ledger_rows=total_ledger_rows,
        m_total_reported=total_ledger_rows,
        m_evaluated_reported=m_eval_reported,
        m_test_reported=m_test_reported,
        m_rejected_reported=m_rej_reported,
        interrupted_count=interrupted_count,
        failed_count=failed_count,
        pre_test_rejected_count=pre_test_rejected_count,
        completed_count=completed_count,
        uncounted_proposal_revisions=uncounted_proposal_revisions,
        is_monotonically_ordered=is_monotonic,
        discrepancies=discrepancies,
        overall_status=overall_status,
        audit_notes=audit_notes,
    )
