"""Provider selection and text/tool adapters for Qwen, Groq and Hugging Face.

The inherited agents use Anthropic message blocks. Keep that contract at the
boundary so routing, memory, retrieval and tool authorization stay unchanged.
"""
import json
import os
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
from anthropic import AsyncAnthropic

HF_BASE_URL = "https://router.huggingface.co/v1"
HF_DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash:deepinfra"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
QWEN_DEFAULT_MODEL = "qwen-flash"


def _qwen_endpoint(value: str) -> str:
    """Accept only explicitly configured HTTPS Alibaba Model Studio endpoints."""
    endpoint = value.strip().rstrip("/")
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        official = host in {
            "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
        } or host.endswith(".maas.aliyuncs.com")
        valid = (
            official and url.scheme == "https" and url.port in {None, 443}
            and not url.username and not url.password and not url.query
            and not url.fragment and url.path == "/compatible-mode/v1"
            and not any(char.isspace() for char in endpoint)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Set QWEN_BASE_URL to the HTTPS compatible-mode/v1 URL from Model Studio")
    return endpoint


def _provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if provider not in {"anthropic", "huggingface", "groq", "qwen"}:
        raise RuntimeError("LLM_PROVIDER must be anthropic, huggingface, groq or qwen")
    return provider


def llm_config() -> dict[str, Any]:
    """Load one provider's credentials without mixing endpoints or keys."""
    provider = _provider()
    if provider == "qwen":
        key = os.getenv("QWEN_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Set QWEN_API_KEY in the backend .env")
        return {
            "api_key": key,
            "model": os.getenv("QWEN_MODEL", "").strip() or QWEN_DEFAULT_MODEL,
            "base_url": _qwen_endpoint(os.getenv("QWEN_BASE_URL", "")),
        }
    if provider == "groq":
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Set GROQ_API_KEY in .env; use a Groq Free Plan account")
        return {
            "api_key": key,
            "model": os.getenv("GROQ_MODEL", "").strip() or GROQ_DEFAULT_MODEL,
            "base_url": GROQ_BASE_URL,
        }
    if provider == "huggingface":
        key = os.getenv("HF_TOKEN", "").strip()
        if not key:
            raise RuntimeError("Set HF_TOKEN in .env with Inference Providers permission")
        return {
            "api_key": key,
            "model": os.getenv("HF_MODEL", "").strip() or HF_DEFAULT_MODEL,
            "base_url": HF_BASE_URL,
        }
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    model = os.getenv("ANTHROPIC_MODEL", "").strip()
    if not key or not model:
        raise RuntimeError("Set ANTHROPIC_API_KEY and ANTHROPIC_MODEL in .env")
    config = {"api_key": key, "model": model}
    if base_url := os.getenv("ANTHROPIC_BASE_URL", "").strip():
        config["base_url"] = base_url
    return config


def create_client(*, anthropic_factory=AsyncAnthropic, **kwargs):
    provider = _provider()
    if provider in {"huggingface", "groq", "qwen"}:
        # Never automatically retry or switch to a different provider. Existing
        # agent timeouts and same-provider agent fallback behavior still apply.
        kwargs.pop("max_retries", None)
        factory = {"groq": GroqClient, "huggingface": HuggingFaceClient, "qwen": QwenClient}[provider]
        return factory(**kwargs)
    return anthropic_factory(**kwargs)


class LLMProviderError(RuntimeError):
    """Safe to log: provider response bodies and request credentials are omitted."""


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str) for block in content
    ):
        return "\n".join(block["text"] for block in content)
    raise ValueError("This LLM adapter supports text content and tool calls only")


def _messages(messages: list[dict], system: Any = None) -> list[dict]:
    result = []
    if system:
        result.append({"role": "system", "content": _text(system)})
    for message in messages:
        role, content = message["role"], message["content"]
        if role not in {"user", "assistant"}:
            raise ValueError("Unsupported message role")
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise ValueError("Unsupported message content")
        if role == "assistant":
            texts, calls, reasoning = [], [], []
            for block in content:
                kind = block.get("type")
                if kind == "text":
                    texts.append(_text(block["text"]))
                elif kind == "hf_reasoning":
                    reasoning.append(_text(block["text"]))
                elif kind == "tool_use":
                    calls.append({
                        "id": block["id"], "type": "function",
                        "function": {"name": block["name"],
                                     "arguments": json.dumps(block["input"], ensure_ascii=False)},
                    })
                else:
                    raise ValueError("Unsupported assistant content block")
            converted = {"role": role, "content": "\n".join(texts) or None}
            if calls:
                converted["tool_calls"] = calls
            if reasoning:
                converted["reasoning_content"] = "\n".join(reasoning)
            result.append(converted)
        else:
            for block in content:
                if block.get("type") == "tool_result":
                    value = _text(block.get("content", ""))
                    if block.get("is_error"):
                        value = "Tool error: " + value
                    result.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                                   "content": value})
                elif block.get("type") == "text":
                    result.append({"role": "user", "content": _text(block["text"])})
                else:
                    raise ValueError("Unsupported user content block")
    return result


def _response(data: dict, provider_name: str) -> SimpleNamespace:
    """Validate a complete response before the agent can execute any tools."""
    try:
        choice = data["choices"][0]
        message, finish = choice["message"], choice["finish_reason"]
        if finish not in {"stop", "length", "tool_calls"}:
            raise ValueError("Unexpected completion state")
        blocks = []
        if reasoning := message.get("reasoning_content"):
            blocks.append({"type": "hf_reasoning", "text": _text(reasoning)})
        if text := message.get("content"):
            blocks.append({"type": "text", "text": _text(text)})
        calls = message.get("tool_calls") or []
        if calls and finish == "length":
            raise ValueError("Truncated tool call")
        call_ids = set()
        for call in calls:
            function = call["function"]
            args = json.loads(function["arguments"])
            if (call.get("type") != "function" or not isinstance(args, dict)
                    or not isinstance(call["id"], str) or not call["id"]
                    or call["id"] in call_ids
                    or not isinstance(function["name"], str) or not function["name"]):
                raise ValueError("Invalid tool call")
            call_ids.add(call["id"])
            blocks.append({"type": "tool_use", "id": call["id"],
                           "name": function["name"], "input": args})
        if (finish == "tool_calls" and not calls) or not any(
            b["type"] in {"text", "tool_use"} for b in blocks
        ):
            raise ValueError("Empty completion")
        usage = data.get("usage") or {}
        return SimpleNamespace(
            content=blocks,
            stop_reason="tool_use" if calls else "max_tokens" if finish == "length" else "end_turn",
            usage=SimpleNamespace(input_tokens=usage.get("prompt_tokens", 0),
                                  output_tokens=usage.get("completion_tokens", 0)),
        )
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise LLMProviderError(f"{provider_name} returned an empty or invalid completion") from None


class ChatCompletionClient:
    """The subset of AsyncAnthropic used by SaaSBridge: text and function tools."""

    provider_name: str
    endpoint: str
    key_env: str
    reasoning_env: str

    def __init__(self, api_key: str, base_url: str | None = None, *, transport=None):
        if base_url and base_url.rstrip("/") != self.endpoint:
            raise ValueError(f"{self.provider_name} requires its configured router endpoint")
        if not api_key.strip():
            raise ValueError(f"Set {self.key_env} in .env")
        self.base_url = self.endpoint
        self.messages = self
        self._http = httpx.AsyncClient(
            base_url=self.endpoint + "/",
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=40.0, follow_redirects=False, transport=transport,
        )

    def _generation_options(self):
        # Non-thinking mode keeps short classifications within their token budget.
        # An empty setting omits this for other models.
        effort = os.getenv(self.reasoning_env, "none").strip()
        return {"reasoning_effort": effort} if effort else {}

    async def create(self, *, model, messages, max_tokens, system=None,
                     temperature=None, tools=None, tool_choice=None):
        payload = {
            "model": model, "messages": _messages(messages, system),
            "max_tokens": max_tokens, "stream": False,
        }
        payload.update(self._generation_options())
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": tool["name"], "description": tool.get("description", ""),
                    "parameters": tool["input_schema"],
                }} for tool in tools
            ]
        if tool_choice:
            kind = tool_choice["type"]
            if kind == "tool":
                payload["tool_choice"] = {"type": "function",
                                          "function": {"name": tool_choice["name"]}}
            elif kind in {"auto", "none", "any"}:
                payload["tool_choice"] = "required" if kind == "any" else kind
            else:
                raise ValueError("Unsupported tool choice")
        try:
            response = await self._http.post("chat/completions", json=payload)
        except httpx.TimeoutException:
            raise LLMProviderError(f"{self.provider_name} request timed out") from None
        except httpx.RequestError:
            raise LLMProviderError(f"Unable to connect to {self.provider_name}") from None
        if response.status_code >= 300:
            hints = {
                401: f"check {self.key_env}",
                403: "check the key's permissions and model access",
                402: "check available Hugging Face inference credits" if self.provider_name == "Hugging Face"
                     else "check provider balance and model access; no billing settings were changed",
                429: "rate limit reached; try again later",
            }
            hint = hints.get(response.status_code, "check model availability and provider status")
            if self.provider_name == "Qwen":
                # Allowlist this diagnostic rather than printing an external body.
                try:
                    error = response.json()
                    code = (error.get("error") or {}).get("code") or error.get("code")
                except (ValueError, AttributeError):
                    code = None
                if code == "AllocationQuota.FreeTierOnly":
                    hint = "free quota exhausted; requests stopped, with no paid fallback"
            raise LLMProviderError(f"{self.provider_name} HTTP {response.status_code}: {hint}")
        try:
            data = response.json()
        except ValueError:
            raise LLMProviderError(f"{self.provider_name} returned invalid JSON") from None
        return _response(data, self.provider_name)

    async def close(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


class HuggingFaceClient(ChatCompletionClient):
    provider_name = "Hugging Face"
    endpoint = HF_BASE_URL
    key_env = "HF_TOKEN"
    reasoning_env = "HF_REASONING_EFFORT"


class GroqClient(ChatCompletionClient):
    provider_name = "Groq"
    endpoint = GROQ_BASE_URL
    key_env = "GROQ_API_KEY"
    reasoning_env = "GROQ_REASONING_EFFORT"

    def _generation_options(self):
        options = super()._generation_options()
        # Qwen supports hidden reasoning; the support UI should receive answers.
        # Leave empty if switching to a model that does not accept this field.
        if mode := os.getenv("GROQ_REASONING_FORMAT", "hidden").strip():
            options["reasoning_format"] = mode
        return options


class QwenClient(ChatCompletionClient):
    provider_name = "Qwen"
    key_env = "QWEN_API_KEY"

    def __init__(self, api_key: str, base_url: str | None = None, *, transport=None):
        self.endpoint = _qwen_endpoint(base_url or "")
        super().__init__(api_key, self.endpoint, transport=transport)

    def _generation_options(self):
        # Qwen's native option is a top-level field in the compatible HTTP API.
        # Non-thinking mode supports forced tool calls and short JSON budgets.
        return {"enable_thinking": False}
