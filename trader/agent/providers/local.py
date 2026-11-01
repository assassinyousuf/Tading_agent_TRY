"""Local Research Assistant provider interface and benchmark harness.

Provides a standardized HTTP interface for local model backends
(e.g. Ollama, llama.cpp, vLLM, local server) without downloading
external weights or performing automatic model fine-tuning.

Enforces loopback-only network containment by default to prevent
research prompts or system state from leaking to external hosts.
"""

from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Dict, List, Optional, Type
import urllib.error
import urllib.parse
import urllib.request
from pydantic import BaseModel

from trader.agent.providers.base import LLMProvider, LLMResponse

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class LocalEndpointSecurityError(PermissionError):
    """Raised when an unauthorized non-loopback endpoint is configured for local LLM."""
    pass


class StrictLoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevents HTTP redirects from escaping loopback boundaries to external hosts."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Optional[urllib.request.Request]:
        parsed = urllib.parse.urlparse(newurl)
        hostname = (parsed.hostname or "").lower()
        if hostname not in LOOPBACK_HOSTS:
            raise LocalEndpointSecurityError(
                f"Security violation: HTTP redirect to non-loopback host '{newurl}' blocked."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def extract_json_object(text: str) -> Dict[str, Any]:
    """Robustly extract a JSON object from raw model completion text.

    Handles raw JSON, markdown-fenced ```json ... ``` blocks, and
    embedded outer braces with preamble/postscript text.
    """
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Model completion is empty; cannot extract JSON.")

    # 1. Direct parse attempt
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 2. Markdown code block extraction
    fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    matches = fence_pattern.findall(cleaned)
    for block in matches:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    # 3. Outer brace extraction
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = cleaned[start_idx : end_idx + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Failed to extract valid JSON dictionary from model completion: {cleaned[:200]}")


class LocalResearchAssistant(LLMProvider):
    """Provider interface connecting to an OpenAI-compatible or Ollama local LLM endpoint."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:11434/v1/chat/completions",
        model_identifier: str = "local-research-assistant",
        temperature: float = 0.0,
        timeout_seconds: float = 30.0,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        seed: Optional[int] = None,
        api_key: str = "local-key",
        allow_external_endpoint: bool = False,
    ) -> None:
        super().__init__(name="local", model_identifier=model_identifier)

        parsed = urllib.parse.urlparse(endpoint_url)
        if parsed.scheme not in ("http", "https"):
            raise LocalEndpointSecurityError(
                f"Invalid URL scheme '{parsed.scheme}'. Only 'http' and 'https' are supported."
            )

        hostname = (parsed.hostname or "").lower()
        if not allow_external_endpoint and hostname not in LOOPBACK_HOSTS:
            raise LocalEndpointSecurityError(
                f"Security violation: Non-loopback endpoint '{endpoint_url}' rejected. "
                "LocalResearchAssistant is restricted to loopback (localhost, 127.0.0.1, ::1) "
                "to prevent sending research prompts to external hosts. "
                "Pass allow_external_endpoint=True to explicitly override."
            )

        self.endpoint_url = endpoint_url
        self.temperature = float(temperature)
        self.timeout_seconds = float(timeout_seconds)
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.seed = seed
        self.api_key = api_key
        self.allow_external_endpoint = allow_external_endpoint
        self.call_history: List[Dict[str, Any]] = []

        if not allow_external_endpoint:
            self.opener = urllib.request.build_opener(StrictLoopbackRedirectHandler)
        else:
            self.opener = urllib.request.build_opener()

    def generate(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        """Query local model endpoint with role prompt and parse structured output."""
        start_time = time.perf_counter()
        prompt_sha = self.compute_sha256(prompt)

        system_instruction = (
            f"You are acting as the {role} in an autonomous quantitative research system. "
            "You must respond with valid JSON matching the requested schema. Do not include markdown code fences."
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # Format request payload: check for Ollama native /api/chat vs OpenAI /v1/chat/completions
        is_ollama_native = self.endpoint_url.rstrip("/").endswith("/api/chat")
        if is_ollama_native:
            options: Dict[str, Any] = {"temperature": self.temperature}
            if self.max_tokens is not None:
                options["num_predict"] = self.max_tokens
            if self.top_p is not None:
                options["top_p"] = self.top_p
            if self.seed is not None:
                options["seed"] = self.seed

            request_body: Dict[str, Any] = {
                "model": self.model_identifier,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": options,
            }
        else:
            request_body = {
                "model": self.model_identifier,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt},
                ],
                "temperature": self.temperature,
            }
            if self.max_tokens is not None:
                request_body["max_tokens"] = self.max_tokens
            if self.top_p is not None:
                request_body["top_p"] = self.top_p
            if self.seed is not None:
                request_body["seed"] = self.seed

        req = urllib.request.Request(
            self.endpoint_url,
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with self.opener.open(req, timeout=self.timeout_seconds) as response:
                res_bytes = response.read()
                res_data = json.loads(res_bytes.decode("utf-8"))

                if is_ollama_native:
                    content = res_data.get("message", {}).get("content", "")
                    prompt_tokens = res_data.get("prompt_eval_count", len(prompt) // 4)
                    completion_tokens = res_data.get("eval_count", len(content) // 4)
                else:
                    content = res_data["choices"][0]["message"]["content"]
                    usage = res_data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", len(prompt) // 4)
                    completion_tokens = usage.get("completion_tokens", len(content) // 4)
        except LocalEndpointSecurityError:
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            raise ConnectionError(
                f"LocalResearchAssistant failed to reach endpoint '{self.endpoint_url}': {str(exc)}"
            ) from exc

        response_sha = self.compute_sha256(content)
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Parse structured data
        structured_data: Optional[Dict[str, Any]] = None
        if schema_class is not None:
            extracted_dict = extract_json_object(content)
            validated = schema_class.model_validate(extracted_dict)
            structured_data = validated.model_dump()

        response_obj = LLMResponse(
            content=content,
            structured_data=structured_data,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=round(latency_ms, 3),
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            provider_name=self.name,
            model_identifier=self.model_identifier,
        )

        self.call_history.append({
            "role": role,
            "prompt": prompt,
            "response": response_obj,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return response_obj
