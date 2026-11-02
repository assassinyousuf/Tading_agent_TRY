# Phase 2.5.2 Research Integrity and Methodology Audit

**Audit Date:** 2026-11-02  
**Auditor:** Independent Red-Team Quantitative Software Auditor  
**Repository:** `E:\projects\binance`  
**Classification:** Quantitative Methodology and Statistical Soundness Audit  

---

## 1. Executive Summary

This audit evaluates the quantitative research methodology, statistical controls, reviewer heuristics, and epistemic boundaries of the Phase 2 research agent architecture. Specifically, we investigate whether the agent:
1. Applies sound quantitative rigor or relies on superficial heuristics.
2. Hardens the reviewer against heuristic shortcuts, such as granting automatic approval to canonical parameter combinations.
3. Enforces execution causality and realistic trading frictions.
4. Accounts for multiple testing and parameter selection bias ($M \le 5$).
5. Interprets confidence intervals and benchmark metrics with epistemic honesty.

Our findings confirm that while significant methodological safeguards exist (preregistration, train-only partitions, Holm-Bonferroni corrections), several heuristics required explicit hardening to prevent subtle forms of overfitting and data snooping.

---

## 2. Hardening Canonical Parameter Review

### The Problem of Canonical Heuristics
In Phase 2.5.1 calibration, prompt guidance was added to instruct the skeptical reviewer that canonical technical indicators (such as 12/26 EMA, 20-period Donchian channels, and 14-period RSI) are standard literature baselines rather than arbitrary curve fits.

However, an independent red-team audit revealed an adversarial loophole: an LLM reviewer instructed to tolerate standard parameters might treat their mere presence as a safe harbor, granting automatic approval to strategies that contain severe execution leakage, unrealistic fill assumptions, or fatal economic flaws, simply because the parameters look familiar.

### Reviewer Rubric Hardening
In Phase 2.5.2, `trader/agent/coordinator.py` was hardened with explicit instructions in `_build_reviewer_prompt()`:
1. Standard Literature Is Not an Exemption: Recognizing standard parameters (12/26 EMA, 20 Donchian, 14 RSI) must NEVER confer automatic approval. Standard parameters tested on an ill-suited asset or without economic justification remain flawed.
2. Execution Causality Mandate: Every proposal using moving averages or indicators must demonstrate causal execution. Orders must fill on the open of bar $t+1$ following a close at bar $t$, or intrabar via realistic limit queues. Any fill executed at the exact closing price of the signal bar without latency or spread constitutes fatal lookahead leakage.
3. Realistic Frictions: Every proposal must deduct realistic Binance taker fees (0.10% spot or 0.05% futures) and non-zero slippage. Zero-fee or midpoint-fill assumptions require immediate rejection.

---

## 3. Multiple Testing and Parameter Selection Provenance ($M \le 5$)

### The Fallacy of the Small Grid
A common heuristic in quantitative finance is that evaluating a small number of parameters (for example, $M \le 5$) poses minimal multiple-testing risk. The coordinator enforces an evaluation budget of $M_{max} = 5$ trials per hypothesis.

However, from an adversarial research perspective:
* An investigator can evaluate 500 combinations in exploratory analysis, select the 3 best-performing variations, and submit only those 3 variations to the coordinator.
* In that scenario, reporting $M = 3$ is deceptive. The true number of trials ($M_{total}$) was 500, and the effective p-values must be adjusted for all 500 trials, not just the 3 reported trials.

### Safeguards Enforced
The reviewer prompt and evaluation criteria require documented parameter selection provenance:
1. Hypotheses must declare whether parameters were selected a priori from economic principles or literature, or whether they were derived from pre-screening.
2. Undocumented exploration on the same dataset requires conservative multiple-testing penalties (such as Holm-Bonferroni or White Reality Check).
3. The coordinator records all trials in `trials` and calculates diagnostic metrics (`calculate_effective_trials_diagnostic`), tracking $M_{total}$ monotonically.

---

## 4. Sample Size and Statistical Power Requirements

Quantitative strategies evaluated on small sample sizes exhibit extreme return variance and high false discovery rates.

### Thresholds Enforced
1. Minimum Trade Count: A strategy producing fewer than 30 independent trades over the training period lacks sufficient statistical power. The central limit theorem cannot justify normality of mean returns with $N < 30$.
2. Reviewer Policy: The skeptical reviewer is instructed to classify backtest proposals that produce fewer than 30 trades as either `RECOMMEND_REVISION` (to expand the asset universe or observation window) or `REVIEW_REQUIRED` / `RECOMMEND_REJECTION`.
3. Degrees of Freedom: The number of estimated parameters must be small relative to the number of independent trading events ($N_{trades} / K_{params} \ge 10$).

---

## 5. Epistemic Interpretation of Confidence Intervals

### What Wilson Score Confidence Intervals Measure
Throughout Phase 2.4 and Phase 2.5, benchmark accuracy, sensitivity, and specificity are reported with 95% Wilson score confidence intervals.

It is critical to document the exact epistemic meaning of these confidence intervals:
* The Wilson interval describes the binomial sampling error of the model conditional on the specific task distribution present in the benchmark suite.
* For example, a sensitivity of 0.35 with a 95% CI of [0.1812, 0.5671] means that if we were to draw repeated samples of size $N=20$ from the identical task population with the same difficulty and category balance, the observed sensitivity would fall within this range 95% of the time.

### What Wilson Score Confidence Intervals Do NOT Measure
* The interval does NOT measure generalization to arbitrary, open-ended real-world trading strategy proposals.
* The interval does NOT guarantee that the LLM will catch subtle mathematical flaws outside the specific flaw typologies represented in the benchmark.
* Performance on a curated benchmark represents an upper bound on model capability under controlled conditions, not a guarantee of autonomy in unconstrained environments.

---

## 6. Summary of Methodology Status

| Methodological Check | Status | Implementation Mechanism |
|:---|:---|:---|
| Partition Isolation | ENFORCED | Strictly TRAIN context; M_test = 0 frozen |
| Reviewer Safe-Harbor Hardening | ENFORCED | Explicit prompt rules in coordinator.py |
| Execution Causality | ENFORCED | Mandatory next-bar execution checks |
| Transaction Cost Deductions | ENFORCED | Binance fee schedule (0.10% / 0.05%) |
| Multiple Testing Adjustments | ENFORCED | Monotonic trial counter, Holm-Bonferroni |
| Sample Size Hurdle | ENFORCED | N >= 30 trades threshold |
| Epistemic Boundary Transparency | DOCUMENTED | Wilson intervals scoped to task set |

The quantitative methodology satisfies rigorous research integrity standards for Phase 2 offline evaluation.

