# Quantitative Research Report: H-REP-TEST-01

**Preregistration ID:** `PREREG-F54810F30042`  
**Strategy Primitive:** `SyntheticSMA`  
**Target Universe:** `BTCUSDT`  
**Timeframe:** `1d`  
**Preregistration Status:** `FROZEN`  

---

## 1. Research Objective

Evaluate SyntheticSMAStrategy momentum on BTCUSDT Daily TRAIN data.

## 2. Preregistered Hypothesis

- **Hypothesis ID:** `H-REP-TEST-01`
- **Economic Rationale:** Rationale
- **Pre-registered Validation Hurdles:** {"min_sharpe": 0.5}
- **Evaluation Budget Limit:** 10 trials

## 3. Experimental Protocol

- **Parameter Grid:** `{"fast": [10.0]}`
- **Cost Model:** `{}`
- **Partition Protocol:** `{}`

## 4. Deterministic Numerical Results

Authoritative backtest performance calculated by Python simulation kernel with verified compounding identity (|E_end - E_0 * prod(1+r_t)| < 1e-9):

| Experiment ID | Parameters | Partition | Net Return (%) | CAGR (%) | Ann. Vol (%) | Sharpe Ratio | Sortino Ratio | Max DD (%) | Trades | Friction ($) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `EXP-TEST-INTERRUPTED-01` | fast_period=10.0 | TRAIN | N/A | N/A | N/A | [INTERRUPTED] | N/A | N/A | N/A | N/A |

## 5. Statistical Diagnostics & Multiple-Testing Accounting

- **Hypothesis Trials Consumed:** 0 / 10
- **Cumulative System Trials (M_total):** 0
- **Multiple-Testing Note:** M_total inflates the expected maximum Sharpe ratio under the null hypothesis of zero skill (Bailey & Lopez de Prado, 2014), establishing the significance hurdle for subsequent out-of-sample evaluation.

## 6. Qualitative Scientific Interpretation

> [!NOTE] LLM RESEARCH ASSISTANT INTERPRETATION (NON-AUTHORITATIVE COMMENTARY)
> Audit test report

## 7. Limitations and Failed Experiments

- **Partition Boundary:** This evaluation was conducted strictly within the TRAIN partition (bars 30 to 430).
- **Unsealed Data:** The VALIDATION partition and sealed TEST partition were not queried or evaluated during this phase.
- **Friction Accounting:** Exact fee model applied: 0.10% taker fee and 0.05% slippage per trade leg.
- **Non-Successful / Interrupted Trials:**
  - Experiment `EXP-TEST-INTERRUPTED-01`: Status `INTERRUPTED`. Notes: Process killed by SIGKILL.

## 8. Human Research Decision

- **Final Verdict:** `GO`
- **Decision Rationale:** Pre-registered quantitative hurdles satisfied within TRAIN partition.
- **Authority:** Consecrated by Human Research Director via out-of-band authorization.
