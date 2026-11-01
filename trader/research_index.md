# Binance Quantitative Strategy Research Index (Frozen Baselines V1–V5)

**Repository:** `E:\projects\binance`  
**Status:** V1–V5 Research Formally Closed & Frozen (Offline Research Only)  
**Safety Gate:** All live trading, testnet connectivity, and paper execution remain strictly disabled.

---

## 1. Master Research Registry (V1 to V5)

| Version | Strategy Name | Architecture / Timeframe | Universe | Horizon / Candles | Net Total Return (Test / Overall) | Max Drawdown (Test / Overall) | Transaction Friction | Statistical Verdict | Audit Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **V1** | `BINANCE_SPOT_ORB_20D_V1` | 15-minute Opening Range Breakout | `SOLUSDT` | 60 days (5,759 bars) | -5.40% (Test) | -12.80% | Excessive fee erosion | Negative Expectancy | **FAIL** |
| **V2** | `HTF_BREAKOUT_V2` | 1h and 4h Donchian Breakouts | `BTCUSDT`, `SOLUSDT` | 180 days (4,319 1h bars) | -12.40% (Test) | -19.60% | Moderate fee drag | Negative Expectancy | **FAIL** |
| **V3** | `MEAN_REVERSION_V3` | 1h/4h Liquidity Sweeps & BB Reversion | `BTCUSDT`, `SOLUSDT` | 180 days (1,079 4h bars) | -8.70% (Test) | -17.20% | Moderate fee drag | Negative Expectancy | **FAIL** |
| **V4.2** | `V4-B_TOP2` | Daily Relative Strength Cross-Sectional Rotation | `BTC`, `ETH`, `SOL`, `BNB` | 729 days (673 return days) | +23.07% (Test) / +32.72% (Overall) | -14.73% / -28.78% | \$372.86 (Test) / \$1,663.13 (Overall) | Lagged BTC (+23.49%) & EW (+29.71%); $p = 0.3845$ | **NO-GO** (V4.3 audit) |
| **V5** | `VP-TF_H5-B_CFG1` | Daily Volatility-Parity Trend Following with ATR Stops | `BTC`, `ETH`, `SOL`, `BNB` | 729 days (698 return days) | +1.08% (Test) / -9.33% (Overall) | -8.92% / -23.98% | \$89.72 (Test) / \$386.76 (Overall) | Lagged BTC (+21.48%) & EW (+27.15%); $p = 0.9570$ | **VERIFIED NO-GO** (V5.1 audit) |
| **Direction C** | `BENCHMARK_ENGINE` | Standardized Benchmark Suite & Walk-Forward Statistical Engine | `BTC`, `ETH`, `SOL`, `BNB`, Cash | 729 days (698 return days) | Evaluates 5 standardized benchmarks & MDE power limits | Range: 0.0% to -60.2% | Exact rebalance friction modeled | Establishes MDE (+77.2% vs BTC) & Deflated Sharpe hurdle | **FRAMEWORK VERIFIED FOR RESEARCH USE** |

---

## 2. Frozen Baseline Implementations & Artifacts

All past research code, test suites, and audit ledgers are preserved as immutable historical records:

- **V1–V3**:
  - Baseline audit: `trader-timemachine/audit_baseline.py`
  - Experiments engine: `trader-timemachine/experiments.py`
  - Forward test harness: `trader-timemachine/forward.py`
  - HTF research: `trader-timemachine/research_htf.py`
  - Mean reversion research: `trader-timemachine/research_v3_mean_reversion.py`
- **V4 / V4.1 / V4.2 / V4.3**:
  - Corrected strategy engine: `trader-timemachine/research_v4_relative_strength.py`
  - V4.1 accounting audit: `trader-timemachine/audit_v4_accounting.py`
  - V4.3 reproducibility audit: `trader-timemachine/audit_v4_3_reproducibility.py`
  - Machine ledgers: `trader/v4_corrected_results.json`, `trader/v4_3_audit_reproduction.json`
- **V5 / V5.1**:
  - Strategy engine: `trader-timemachine/research_v5_trend_following.py`
  - Experiment registry: `trader/v5_experiment_registry.json`
  - Detailed results: `trader/v5_experiment_results.json`
  - V5.1 independent audit engine: `trader-timemachine/audit_v5_1.py`
  - Reconciliation ledger: `trader/v5_1_audit_reconciliation.json`
  - Audit report: `trader/v5_1_independent_audit.md`
  - Unit tests: `tests/test_research_v5.py`, `tests/test_audit_v5_1.py`
- **Direction C (Research Infrastructure)**:
  - Benchmark engine: `trader-timemachine/benchmark_engine.py`
  - Benchmark catalog: `trader/direction_c_benchmark_catalog.json`
  - Research report: `trader/direction_c_benchmark_report.md`
  - Regression unit tests: `tests/test_benchmark_engine.py` (12 unit tests)

---

## 3. Confirmed Empirical Facts vs Unresolved Limitations

### Confirmed by Reproducible Calculations
1. **Transaction Cost Vulnerability**: High-frequency spot strategies (V1/V2) suffer fatal friction erosion. At 15 bps per turn, trading $> 50$ times per year on low-margin breakouts eliminates profitability.
2. **Event-Driven Cost Reduction**: Event-driven trailing stops (V5) slash transaction costs by **76.9%** relative to calendar rebalancing (V4) on identical calendar dates.
3. **Downside Risk Mitigation**: Volatility-parity position sizing and ATR trailing stops effectively cut maximum drawdowns by roughly half during macro bear markets (V5 max DD -11.2% in Train vs -22.5% for BTC).
4. **Collinearity of Universe**: Pairwise correlation across `BTC`, `ETH`, `SOL`, `BNB` averages $\bar{\rho} \approx 0.77$. Cross-sectional rotation trades correlated beta proxies rather than idiosyncratic alpha.
5. **Recovery Lag (Cash Drag)**: Both binary moving-average filters (V4) and trailing breakout stops (V5) lag sharp V-bottom market recoveries, causing strategies to miss the initial explosive gains of new bull cycles.
6. **Compounding Identity**: All simulation engines achieve exact conservation of capital with numerical error $< 10^{-11}$ USD.

### Remaining Uncertainties
1. **Intraday Microstructure**: Order book depth, bid-ask spread dynamics during liquidations, and true market-impact slippage cannot be observed in daily OHLCV bars.
2. **Decoupled Sector Potential**: Whether cross-sectional rotation generates alpha across less correlated tokens (DeFi, Layer-2s, AI tokens with $\rho < 0.40$) remains untested due to dataset boundaries.
3. **Asymmetric Fat-Tail Capture**: Whether multi-year crypto bull runs provide sufficient $+5\text{R}$ to $+10\text{R}$ payouts to compensate for consolidation chop remains unproven on this 2-year sample.

---

## 4. Current Research State: All Cycles Closed

Strategy V5 is closed. No live, testnet, or paper orders are permitted. Any further work must proceed strictly in an offline research and benchmarking capacity.

