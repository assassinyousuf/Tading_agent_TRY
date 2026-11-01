"""In-process OpenAI-compatible local test server for reproducible local model evaluation.

Runs on standard library http.server and socketserver on 127.0.0.1 (loopback),
providing an authentic HTTP REST server for testing LocalResearchAssistant,
simulating model inference across temperatures, token usages, and failure modes
without external dependencies or GPU hardware.
"""

from datetime import datetime, timezone
import http.server
import json
import random
import socketserver
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class LocalOpenAITestServer:
    """In-process HTTP server simulating OpenAI-compatible and Ollama endpoints."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        response_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        simulate_latency_ms: float = 0.0,
        inject_error: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.response_handler = response_handler or self._default_benchmark_responder
        self.simulate_latency_ms = simulate_latency_ms
        self.inject_error = inject_error
        self.httpd: Optional[socketserver.TCPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.actual_port: int = port
        self.request_log: list = []

    @property
    def endpoint_url(self) -> str:
        """Return the OpenAI-compatible chat completions endpoint URL."""
        return f"http://{self.host}:{self.actual_port}/v1/chat/completions"

    @property
    def ollama_endpoint_url(self) -> str:
        """Return the Ollama-native chat endpoint URL."""
        return f"http://{self.host}:{self.actual_port}/api/chat"

    def start(self) -> "LocalOpenAITestServer":
        """Start the HTTP server on a background daemon thread."""
        server_instance = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Suppress default stdout logging during tests
                pass

            def do_POST(self) -> None:
                # Check error injection
                if server_instance.inject_error == "TIMEOUT":
                    time.sleep(2.0)
                    return

                if server_instance.inject_error == "INTERNAL_ERROR":
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "Simulated Internal Server Error"}')
                    return

                if server_instance.inject_error == "MALFORMED_JSON":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{malformed: json invalid')
                    return

                if server_instance.inject_error == "EXTERNAL_REDIRECT":
                    self.send_response(302)
                    self.send_header("Location", "http://external-evil.com/leak")
                    self.end_headers()
                    return

                content_len = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(content_len)
                try:
                    payload = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    payload = {}

                server_instance.request_log.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "payload": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                if server_instance.simulate_latency_ms > 0:
                    time.sleep(server_instance.simulate_latency_ms / 1000.0)

                # Process response
                response_data = server_instance.response_handler(payload)

                is_ollama = self.path.rstrip("/").endswith("/api/chat")
                if is_ollama:
                    content_str = response_data.get("content", "{}")
                    out_body = {
                        "model": payload.get("model", "local-test-model"),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "message": {"role": "assistant", "content": content_str},
                        "done": True,
                        "prompt_eval_count": response_data.get("prompt_tokens", 45),
                        "eval_count": response_data.get("completion_tokens", 60),
                    }
                else:
                    content_str = response_data.get("content", "{}")
                    prompt_tok = response_data.get("prompt_tokens", 50)
                    comp_tok = response_data.get("completion_tokens", 75)
                    out_body = {
                        "id": "chatcmpl-test-001",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": payload.get("model", "local-test-model"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": content_str},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": prompt_tok,
                            "completion_tokens": comp_tok,
                            "total_tokens": prompt_tok + comp_tok,
                        },
                    }

                resp_bytes = json.dumps(out_body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.end_headers()
                self.wfile.write(resp_bytes)

        class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.actual_port = self.httpd.server_address[1]

        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        """Shutdown the HTTP server and release port."""
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None

    def __enter__(self) -> "LocalOpenAITestServer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    @staticmethod
    def _default_benchmark_responder(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Calibrated benchmark responder simulating local LLM completions across tasks and temperatures."""
        messages = payload.get("messages", [])
        prompt = ""
        role = "Coordinator"
        for m in messages:
            if m.get("role") == "system":
                role_text = m.get("content", "")
                if "Hypothesis Researcher" in role_text:
                    role = "Hypothesis Researcher"
                elif "Skeptical Reviewer" in role_text:
                    role = "Skeptical Reviewer"
                elif "Research Coordinator" in role_text:
                    role = "Research Coordinator"
            elif m.get("role") == "user":
                prompt = m.get("content", "")

        temperature = float(payload.get("temperature", 0.0))
        prompt_upper = prompt.upper()

        # Exact non-colliding task identification (avoid general instruction keywords like 'LOOKAHEAD')
        task_id = "TASK-01"
        for tid in [f"TASK-{i:02d}" for i in range(1, 21)]:
            if tid in prompt_upper:
                task_id = tid
                break

        # Classify task
        sound_tasks = {"TASK-01", "TASK-10", "TASK-14", "TASK-15", "TASK-16", "TASK-20"}
        clarification_tasks = {"TASK-17"}

        # Fallback signature detection if task ID is stripped
        is_sound = (
            task_id in sound_tasks
            or "VALID" in prompt_upper
            or "WEEKLY MACRO BREAKOUT" in prompt_upper
            or "CONTRARIAN MEAN-REVERSION" in prompt_upper
            or "DAILY REBALANCING EXECUTED STRICTLY AT 00:00:05 UTC" in prompt_upper
        ) and not (
            "TASK-02" in prompt_upper
            or "TASK-03" in prompt_upper
            or "TASK-04" in prompt_upper
            or "TASK-05" in prompt_upper
            or "TASK-06" in prompt_upper
            or "TASK-07" in prompt_upper
            or "TASK-08" in prompt_upper
            or "TASK-09" in prompt_upper
            or "TASK-11" in prompt_upper
            or "TASK-12" in prompt_upper
            or "TASK-13" in prompt_upper
            or "TASK-17" in prompt_upper
            or "TASK-18" in prompt_upper
            or "TASK-19" in prompt_upper
            or "DIVIDING BY THE MAXIMUM" in prompt_upper
            or "FUTURE PERIODS T+1" in prompt_upper
            or "SMA_CENTERED" in prompt_upper
            or "2,000 PARAMETER" in prompt_upper
            or "2000 PARAMETER" in prompt_upper
            or "12TH-DEGREE POLYNOMIAL" in prompt_upper
            or "P-VALUE OF 0.048" in prompt_upper
            or "0.00% TAKER FEES" in prompt_upper
            or "UNSEEDED FLOATING" in prompt_upper
            or "BEST-PERFORMING TOKENS OF 2025" in prompt_upper
            or "0.001% INCREMENTS" in prompt_upper
            or "TEST PARTITION FOR PARAMETER TUNING" in prompt_upper
            or "OUT-OF-SAMPLE TEST PARTITION" in prompt_upper
            or "UNSPECIFIED VOLATILITY CHANNEL" in prompt_upper
            or "23:59:59 UTC, BUT THE EXECUTION ORDER IS SIMULATED AT TODAY'S OPEN" in prompt_upper
            or "ONLY 15 DAILY BARS" in prompt_upper
            or "WITHOUT LOSING MONEY" in prompt_upper
        )

        is_clarification = task_id in clarification_tasks or "UNSPECIFIED VOLATILITY CHANNEL" in prompt_upper
        is_flawed = not is_sound and not is_clarification

        content_dict: Dict[str, Any] = {}

        if role == "Hypothesis Researcher":
            strat_name = "SyntheticSMAStrategy"
            if "RSI" in prompt_upper or task_id in {"TASK-10", "TASK-20"}:
                strat_name = "SyntheticRSIStrategy"
            elif "ATR" in prompt_upper or task_id == "TASK-14":
                strat_name = "SyntheticATRStrategy"

            content_dict = {
                "hypothesis_id": f"H-MOM-{task_id}",
                "strategy_name": strat_name,
                "economic_rationale": "Sustained momentum in daily spot volume leads to persistent trend continuation.",
                "validation_criteria": {"min_sharpe": 0.60, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 5,
            }

        elif role == "Skeptical Reviewer":
            # At higher temperatures (e.g. 0.8), introduce minor stochastic drift
            decision_is_reject = is_flawed
            if temperature >= 0.8 and random.random() < 0.15:
                # Stochastic variation at high temperature
                decision_is_reject = not decision_is_reject

            if is_clarification:
                content_dict = {
                    "lookahead_risk_score": 1,
                    "snooping_risk_score": 3,
                    "granularity_risk_score": 3,
                    "turnover_realism_score": 2,
                    "overall_recommendation": "REVIEW_REQUIRED",
                    "critique_rationale": "Methodological ambiguity: Strategy mentions volatility channel without specifying indicator primitive (ATR vs Bollinger vs Keltner). Clarification required.",
                    "defect_type": "CLARIFICATION_REQUIRED",
                    "affected_field": "strategy_name",
                    "invalidation_reason": "Ambiguous indicator specification prevents deterministic execution and validation.",
                    "corrective_action": "Specify concrete channel calculation primitive and parameter bounds.",
                    "required_revisions": ["Specify concrete channel calculation primitive and parameter bounds."],
                }
            elif decision_is_reject:
                # Determine flaw specifics
                defect = "GENERAL_METHODOLOGICAL_RISK"
                affected = "parameter_grid"
                reason = "Proposal contains methodological defect violating empirical research rigor."
                action = "Correct methodological design before proceeding."

                if "TASK-03" in prompt_upper or "DIVIDING BY THE MAXIMUM" in prompt_upper or "FUTURE PERIODS T+1" in prompt_upper:
                    defect = "DATA_LEAKAGE"
                    affected = "feature_normalization"
                    reason = "Normalizing price features against global sample maximum including future periods creates target leakage."
                    action = "Use expanding or rolling normalization strictly causal up to bar t."
                elif "TASK-04" in prompt_upper or "SMA_CENTERED" in prompt_upper or "TASK-18" in prompt_upper or "23:59:59" in prompt_upper:
                    defect = "LOOKAHEAD_BIAS"
                    affected = "execution_timing"
                    reason = "Using future bar information (centered average or retrospective execution) violates causality."
                    action = "Execute strictly at next bar open t+1 following bar t close signal."
                elif "TASK-05" in prompt_upper or "2,000 PARAMETER" in prompt_upper or "TASK-12" in prompt_upper or "0.001% INCREMENTS" in prompt_upper:
                    defect = "MULTIPLE_TESTING_INFLATION"
                    affected = "parameter_grid"
                    reason = "Massive parameter sweep inflates false positive rate without Deflated Sharpe Ratio control."
                    action = "Bound parameter grid to M <= 5 points and adjust hurdles for degrees of freedom."
                elif "TASK-06" in prompt_upper or "12TH-DEGREE POLYNOMIAL" in prompt_upper or "TASK-19" in prompt_upper or "ONLY 15 DAILY BARS" in prompt_upper:
                    defect = "OVERFITTING_SAMPLE_DEFICIT"
                    affected = "degrees_of_freedom"
                    reason = "Excessive parameters relative to small sample size results in extreme variance and curve fitting."
                    action = "Increase sample size or reduce free parameter count to preserve degrees of freedom."
                elif "TASK-07" in prompt_upper or "P-VALUE OF 0.048" in prompt_upper:
                    defect = "AUTOCORRELATED_SIGNIFICANCE"
                    affected = "validation_criteria"
                    reason = "Raw p-value on strongly autocorrelated non-stationary returns produces spurious statistical significance."
                    action = "Perform stationary block bootstrap or Deflated Sharpe Ratio calculation."
                elif "TASK-08" in prompt_upper or "0.00% TAKER FEES" in prompt_upper:
                    defect = "UNREALISTIC_FRICTION"
                    affected = "cost_model"
                    reason = "Assuming 0.00% fees on high turnover intraday trading guarantees alpha will be erased in live execution."
                    action = "Incorporate realistic Binance taker fees (0.10%) and slippage buffers."
                elif "TASK-09" in prompt_upper or "UNSEEDED FLOATING" in prompt_upper:
                    defect = "NON_REPRODUCIBLE_PROPOSAL"
                    affected = "parameter_sampling"
                    reason = "Unseeded floating parameters cannot be verified, preregistered, or audited in SQLite."
                    action = "Specify deterministic seeds and fixed parameter bounds."
                elif "TASK-11" in prompt_upper or "BEST-PERFORMING TOKENS OF 2025" in prompt_upper:
                    defect = "SURVIVORSHIP_BIAS"
                    affected = "universe_selection"
                    reason = "Selecting winners ex-post creates severe selection bias and overestimates backtest return."
                    action = "Select universe based strictly on point-in-time metrics available on start date."
                elif "TASK-13" in prompt_upper or "TEST PARTITION FOR PARAMETER TUNING" in prompt_upper:
                    defect = "TEST_DATA_CONTAMINATION"
                    affected = "partition_protocol"
                    reason = "Tuning parameters on TEST partition breaks out-of-sample guarantees (M_test must be 0)."
                    action = "Confine all exploration and optimization strictly to TRAIN partition."
                elif "TASK-02" in prompt_upper or "WITHOUT LOSING MONEY" in prompt_upper:
                    defect = "UNDERSPECIFIED_HYPOTHESIS"
                    affected = "economic_rationale"
                    reason = "Unfalsifiable mandate without mathematical rules cannot be validated."
                    action = "Specify concrete mathematical strategy and explicit performance hurdles."

                content_dict = {
                    "lookahead_risk_score": 5 if defect in {"LOOKAHEAD_BIAS", "DATA_LEAKAGE"} else 2,
                    "snooping_risk_score": 5 if defect in {"MULTIPLE_TESTING_INFLATION", "OVERFITTING_SAMPLE_DEFICIT", "SURVIVORSHIP_BIAS", "TEST_DATA_CONTAMINATION"} else 2,
                    "granularity_risk_score": 2,
                    "turnover_realism_score": 5 if defect == "UNREALISTIC_FRICTION" else 2,
                    "overall_recommendation": "RECOMMEND_REJECTION",
                    "critique_rationale": reason,
                    "defect_type": defect,
                    "affected_field": affected,
                    "invalidation_reason": reason,
                    "corrective_action": action,
                    "required_revisions": [action],
                }
            else:
                content_dict = {
                    "lookahead_risk_score": 1,
                    "snooping_risk_score": 1,
                    "granularity_risk_score": 1,
                    "turnover_realism_score": 1,
                    "overall_recommendation": "RECOMMEND_APPROVAL",
                    "critique_rationale": "Parameter grid is strictly bounded to minimal points with realistic Binance taker fees, sound statistical controls, and no lookahead bias.",
                    "defect_type": None,
                    "affected_field": None,
                    "invalidation_reason": None,
                    "corrective_action": None,
                    "required_revisions": [],
                }

        elif role == "Research Coordinator":
            content_dict = {
                "proposal_id": f"PROP-{task_id}",
                "hypothesis_id": f"H-MOM-{task_id}",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0, 15.0], "slow_period": [30.0]},
                "partition_protocol_id": "CHRONOLOGICAL_V1",
            }

        # Convert to string (occasionally wrap in markdown fences at temperature > 0 to simulate real LLM behavior)
        json_str = json.dumps(content_dict, indent=2)
        if temperature > 0.0 and random.random() < 0.5:
            content = f"```json\n{json_str}\n```"
        else:
            content = json_str

        prompt_tokens = len(prompt) // 4 + 25
        completion_tokens = len(content) // 4 + 15

        return {
            "content": content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
