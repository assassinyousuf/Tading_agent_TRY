# Phase 1 API Reference: Autonomous Quant Research Agent

## 1. Package Architecture Overview

The `trader.agent` package provides deterministic infrastructure, relational ledger management, data partitioning governance, and strategy execution protocols.

Namespace structure:
- `trader.agent.db`: Relational schema definitions, migrations, and append-only ledger operations.
- `trader.agent.core`: Abstract strategy interfaces and validation strategies.
- `trader.agent.tools`: Market data provider, partition slicing, and simulation runners.

---

## 2. Database Module: `trader.agent.db`

### 2.1 `trader.agent.db.schema`

Functions:

#### `init_research_db(conn: sqlite3.Connection) -> None`
Initializes the research database schema, creating all 10 tables, indexes, and immutability triggers.
- Arguments:
  * `conn`: Open SQLite connection object.
- Behavior:
  * Enables WAL mode and foreign key constraints (`PRAGMA foreign_keys = ON`).
  * Creates all relational tables if they do not exist.
  * Attaches `trg_prevent_update_*` and `trg_prevent_delete_*` triggers.
  * Inserts schema version record into `schema_metadata`.

#### `get_schema_version(conn: sqlite3.Connection) -> int`
Returns the active schema migration version (currently 1).

Relational Tables Defined:
1. `schema_metadata`: `version INT PRIMARY KEY`, `description TEXT`, `applied_at TEXT`.
2. `hypotheses`: `hypothesis_id TEXT PRIMARY KEY`, `strategy_name TEXT`, `created_at TEXT`, `economic_rationale TEXT`, `validation_criteria_json TEXT`.
3. `preregistrations`: `prereg_id TEXT PRIMARY KEY`, `hypothesis_id TEXT`, `preregistered_at TEXT`, `universe_json TEXT`, `timeframe TEXT`, `parameter_grid_json TEXT`, `cost_model_json TEXT`, `partition_protocol_json TEXT`, `benchmark_ids_json TEXT`, `status TEXT`.
4. `experiments`: `experiment_id TEXT PRIMARY KEY`, `prereg_id TEXT`, `executed_at TEXT`, `partition_name TEXT`, `parameters_json TEXT`, `random_seed_63bit INT`, `code_git_hash TEXT`, `data_hash TEXT`, `status TEXT`, `error_message TEXT`.
5. `experiment_metrics`: `metric_id TEXT PRIMARY KEY`, `experiment_id TEXT`, `initial_capital REAL`, `final_capital REAL`, `net_total_return_pct REAL`, `gross_total_return_pct REAL`, `cagr_pct REAL`, `annualized_vol_pct REAL`, `sharpe_ratio REAL`, `sortino_ratio REAL`, `max_drawdown_pct REAL`, `total_trades INT`, `win_rate_pct REAL`, `total_friction_usd REAL`, `compounding_error REAL`, `raw_metrics_json TEXT`.
6. `audit_results`: `audit_id TEXT PRIMARY KEY`, `experiment_id TEXT`, `benchmark_id TEXT`, `bootstrap_p_value_raw REAL`, `bootstrap_ci_lower REAL`, `bootstrap_ci_upper REAL`, `screening_category TEXT`, `ar2_max_root REAL`, `dsr_value_raw REAL`, `m_total_used INT`, `m_eff_diagnostic REAL`, `raw_audit_json TEXT`, `adjusted_metrics_json TEXT`.
7. `research_decisions`: `decision_id TEXT PRIMARY KEY`, `hypothesis_id TEXT`, `prereg_id TEXT`, `experiment_id TEXT`, `decision TEXT`, `rationale TEXT`, `decided_at TEXT`, `decided_by TEXT`.
8. `test_evaluation_authorizations`: `auth_id TEXT PRIMARY KEY`, `prereg_id TEXT`, `experiment_id TEXT`, `token_hash TEXT UNIQUE`, `authorized_by TEXT`, `created_at TEXT`, `expires_at TEXT`, `is_revoked INT`, `max_uses INT`, `uses_count INT`, `purpose TEXT`.
9. `test_evaluation_records`: `record_id TEXT PRIMARY KEY`, `auth_id TEXT`, `prereg_id TEXT`, `experiment_id TEXT`, `evaluated_at TEXT`, `caller_identity TEXT`, `parameters_json TEXT`, `access_granted INT`, `failure_reason TEXT`, `execution_duration_ms REAL`.
10. `trial_ledger`: `trial_id INTEGER PRIMARY KEY AUTOINCREMENT`, `timestamp TEXT`, `hypothesis_id TEXT`, `prereg_id TEXT`, `experiment_id TEXT`, `parameters_evaluated_json TEXT`, `disposition TEXT`, `notes TEXT`, `cumulative_m_total INT`, `diagnostic_m_eff REAL`.

---

### 2.2 `trader.agent.db.ledger.ResearchLedger`

Class: `ResearchLedger(db_path: str = ":memory:")`
High-level ACID interface managing hypothesis registration, parameter validation, cryptographic test tokens, and append-only trial tracking.

Methods:

#### `record_hypothesis(hypothesis_id: str, strategy_name: str, economic_rationale: str, validation_criteria: Dict[str, Any]) -> str`
Records a formal research hypothesis.
- Returns: `hypothesis_id`

#### `preregister_experiment(hypothesis_id: str, universe: List[str], timeframe: str, parameter_grid: Dict[str, List[float]], cost_model: Dict[str, float], partition_protocol: Dict[str, Any], benchmark_ids: List[str]) -> str`
Registers a frozen experimental protocol bound to an existing hypothesis.
- Returns: Unique `prereg_id` (`PREREG-<hex12>`).

#### `verify_preregistration(prereg_id: str, config_params: Dict[str, float], partition: str, universe: Optional[List[str]] = None) -> Tuple[bool, str]`
Validates proposed parameters and asset universe against the frozen preregistration record.
- Returns: `(is_valid: bool, rejection_reason: str)`.

#### `generate_test_vault_token(prereg_id: str, authorized_by: str, purpose: str, expires_in_seconds: int = 3600, experiment_id: Optional[str] = None) -> str`
Issues a single-use cryptographic token (`TEST-TOKEN-<hex24>`).
- Security Invariant: Returns raw secret token to caller; stores only the SHA-256 digest in the database.

#### `verify_and_consume_test_token(raw_token: str, prereg_id: str, experiment_id: Optional[str] = None, caller_identity: str = "test_runner", parameters: Optional[Dict[str, Any]] = None) -> bool`
Validates and consumes a test vault token. Checks hash match, preregistration binding, expiration, revocation, and use count. Logs attempt to `test_evaluation_records`.
- Raises: `PermissionError` on any failure.
- Returns: `True` on successful consumption.

#### `consume_test_vault_token(...)`
Convenience alias for `verify_and_consume_test_token`.

#### `revoke_test_token(token_or_hash: str) -> bool`
Marks an authorization token as revoked.

#### `record_experiment_result(experiment_data: Dict[str, Any], metrics_data: Dict[str, Any], audit_data: Optional[Dict[str, Any]] = None) -> None`
Atomically records an executed experiment, backtest metrics, and optional adversarial audit results.

#### `record_decision(hypothesis_id: str, decision: str, rationale: str, decided_by: str, prereg_id: Optional[str] = None, experiment_id: Optional[str] = None) -> str`
Records an immutable formal decision (`GO`, `NO_GO`, or `INCONCLUSIVE`).

#### `record_trial(hypothesis_id: str, prereg_id: str, experiment_id: Optional[str], parameters_evaluated: Dict[str, Any], disposition: str, notes: Optional[str] = None, diagnostic_m_eff: Optional[float] = None) -> int`
Appends an evaluation or pre-test rejection to the trial ledger.
- Returns: Updated cumulative raw $M_{\text{total}}$.

#### `get_trial_counts() -> Dict[str, int]`
Returns summary dictionary: `{"M_total": int, "M_evaluated": int, "M_rejected": int}`.

#### `calculate_effective_trials_diagnostic(return_series_list: List[List[float]]) -> float`
Static method computing the eigenvalue-based participation ratio diagnostic:
$$M_{\text{eff}} = \frac{(\sum \lambda_i)^2}{\sum \lambda_i^2}$$
- Note: This value is an informational diagnostic only and is never substituted into DSR formulas.

---

## 3. Strategy Module: `trader.agent.core`

### 3.1 `trader.agent.core.strategy_base.StrategyInterface`

Abstract base class for all research strategies.

Abstract Methods:
- `on_warmup(bars: List[Dict[str, Any]]) -> None`: Ingests historical warmup bars strictly to initialize indicators (e.g., Donchian channels, moving averages). Emits zero trading signals.
- `on_bar(current_bar: Dict[str, Any], portfolio_state: Dict[str, Any]) -> Dict[str, Any]`: Evaluates current candle close and portfolio state to return target allocation/signals for the subsequent bar.
- `reset() -> None`: Clears internal indicator buffers and state.

### 3.2 `trader.agent.core.synthetic_strategy.SyntheticSMAStrategy`

Concrete implementation of `StrategyInterface` used for end-to-end infrastructure validation:
- Parameters: `fast_period` (float), `slow_period` (float).
- Signal Logic: Generates target weight +1.0 when fast SMA > slow SMA, and 0.0 otherwise.

---

## 4. Market Bridge Module: `trader.agent.tools`

### 4.1 `trader.agent.tools.market_bridge.MarketDataProvider`

Class managing historical daily kline caches, physical partition slicing, and sealed vault access control.

Methods:

#### `__init__(trader_dir: Optional[str] = None, ledger: Optional[ResearchLedger] = None) -> None`
Initializes data provider and verifies SHA-256 digests and 86,400s continuity across all 4 cached symbols (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT).

#### `get_data_hash(symbol: str) -> str`
Returns the 64-character SHA-256 hex digest of the raw candle cache for the given symbol.

#### `get_partition_data(partition: str, symbol: str = "BTCUSDT", test_token: Optional[str] = None, warmup_bars: int = 30, prereg_id: Optional[str] = None) -> Dict[str, Any]`
Provides sliced candle data for the requested partition.
- Partitions:
  * `"TRAIN"`: Bars 30 to 429 (400 trading bars).
  * `"VALIDATION"`: Bars 430 to 579 (150 trading bars).
  * `"TEST"`: Bars 580 to 728 (149 trading bars). Requires valid `test_token`.
- Enforces:
  * If `partition == "TEST"`, checks token validity. If `ledger` is attached, automatically consumes the token.
  * Prepends `warmup_bars` (bars immediately preceding `start_bar`) strictly for indicator smoothing.
  * Provides fresh initial portfolio state: `{"initial_capital": 10000.0, "cash": 10000.0, "positions": {}, "equity": 10000.0}`.
- Raises:
  * `SealedPartitionAccessViolation`: If un-tokenized access to the TEST partition is attempted.

### 4.2 Simulation Runner

#### `execute_strategy_simulation(strategy: StrategyInterface, partition_data: Dict[str, Any], params: Dict[str, float], cost_model: Optional[Dict[str, float]] = None, initial_capital: float = 10000.0) -> Dict[str, Any]`
Executes an offline, bar-by-bar deterministic backtest.
- Friction Accounting:
  * Default taker fee: 0.10% (`0.0010`).
  * Default slippage: 0.05% (`0.0005`).
- Returns:
  * Detailed dictionary containing `net_total_return_pct`, `gross_total_return_pct`, `cagr_pct`, `annualized_vol_pct`, `sharpe_ratio`, `sortino_ratio`, `max_drawdown_pct`, `total_trades`, `win_rate_pct`, `total_friction_usd`, `compounding_error`, `daily_returns`, and `daily_gross_returns`.
- Precision Guarantee:
  * Compounding error between equity curve and return chain product is verified to be $< 10^{-10}$.
