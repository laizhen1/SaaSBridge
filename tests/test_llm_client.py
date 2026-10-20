import json
from unittest.mock import Mock

import httpx
import pytest

from agents.agent_orchestrator import APIAgent, Request
from core.llm_client import (
    HF_BASE_URL, HF_DEFAULT_MODEL, HuggingFaceClient, LLMProviderError,
    GROQ_BASE_URL, GROQ_DEFAULT_MODEL, GroqClient,
    QwenClient, QWEN_DEFAULT_MODEL,
    create_client, llm_config,
)
from core.llm_utils import extract_text_content


def completion(content="ok", calls=None, finish="stop", reasoning=None):
    return {
        "choices": [{"message": {"content": content, "tool_calls": calls,
                                  "reasoning_content": reasoning}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 8},
    }


def tool_call(call_id="call-1", args='{"error_code":"429"}'):
    return {"id": call_id, "type": "function",
            "function": {"name": "lookup_error_code", "arguments": args}}


@pytest.fixture(autouse=True)
def provider_env(monkeypatch):
    monkeypatch.setenv("HF_REASONING_EFFORT", "none")


def test_hf_configuration_never_reuses_anthropic_credentials(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "huggingface")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "different-provider-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid/anthropic")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        llm_config()
    monkeypatch.setenv("HF_TOKEN", "hf_test-only")
    monkeypatch.delenv("HF_MODEL", raising=False)
    assert llm_config() == {"api_key": "hf_test-only", "model": HF_DEFAULT_MODEL,
                            "base_url": HF_BASE_URL}
    monkeypatch.setenv("HF_MODEL", "another/model:deepinfra")
    assert llm_config()["model"] == "another/model:deepinfra"


def test_legacy_configuration_and_factory(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    assert llm_config()["base_url"] == "https://example.invalid"
    factory = Mock()
    assert create_client(anthropic_factory=factory, api_key="test-key", max_retries=0) is factory.return_value
    factory.assert_called_once_with(api_key="test-key", max_retries=0)
    monkeypatch.setenv("LLM_PROVIDER", "typo")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        llm_config()


@pytest.mark.asyncio
async def test_hf_factory_and_text_request(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "huggingface")
    def handler(req):
        assert str(req.url) == HF_BASE_URL + "/chat/completions"
        assert req.headers["authorization"] == "Bearer hf_test-only"
        body = json.loads(req.content)
        assert body["messages"] == [
            {"role": "system", "content": "Be concise."}, {"role": "user", "content": "Hello"},
        ]
        assert body["reasoning_effort"] == "none"
        assert body["max_tokens"] == 64 and body["temperature"] == 0
        assert body["stream"] is False and "tools" not in body
        return httpx.Response(200, json=completion("Hello back"))
    async with create_client(api_key="hf_test-only", max_retries=0,
                             transport=httpx.MockTransport(handler)) as client:
        assert isinstance(client, HuggingFaceClient)
        reply = await client.messages.create(
            model=HF_DEFAULT_MODEL, system="Be concise.", max_tokens=64, temperature=0,
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert extract_text_content(reply.content) == "Hello back"
        assert reply.stop_reason == "end_turn"
        assert (reply.usage.input_tokens, reply.usage.output_tokens) == (25, 8)
    assert client._http.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("client_class", [HuggingFaceClient, GroqClient])
async def test_real_api_agent_executes_and_returns_tool_results(client_class):
    requests = []
    def handler(req):
        body = json.loads(req.content)
        requests.append(body)
        if len(requests) == 1:
            tool = next(t for t in body["tools"] if t["function"]["name"] == "lookup_error_code")
            assert tool["function"]["parameters"]["required"] == ["error_code"]
            return httpx.Response(200, json=completion(
                None, [tool_call()], "tool_calls", "Internal reasoning",
            ))
        assistant, result = body["messages"][-2:]
        assert assistant["tool_calls"][0]["id"] == "call-1"
        assert assistant["reasoning_content"] == "Internal reasoning"
        assert result["role"] == "tool" and result["tool_call_id"] == "call-1"
        assert json.loads(result["content"])["error_code"] == "429"
        assert json.loads(result["content"])["server_log_checked"] is False
        return httpx.Response(200, json=completion("Respect Retry-After and check request limits."))
    async with client_class("test-only", transport=httpx.MockTransport(handler)) as client:
        reply = await APIAgent(client, HF_DEFAULT_MODEL).handle(Request("Explain 429", "u", "c"))
    assert reply.success and reply.tools_used == ["lookup_error_code"]
    assert "Internal reasoning" not in reply.content
    assert len(requests) == 2 and reply.tool_traces[0]["success"]


@pytest.mark.asyncio
async def test_multiple_tool_results_errors_and_forced_choice(monkeypatch):
    monkeypatch.setenv("HF_REASONING_EFFORT", "")
    def handler(req):
        body = json.loads(req.content)
        assert "reasoning_effort" not in body
        assert body["tool_choice"] == {"type": "function", "function": {"name": "lookup_error_code"}}
        assert [m["tool_call_id"] for m in body["messages"][-2:]] == ["one", "two"]
        assert body["messages"][-1]["content"] == "Tool error: unavailable"
        return httpx.Response(200, json=completion("Partial answer", finish="length"))
    async with HuggingFaceClient("hf_test-only", transport=httpx.MockTransport(handler)) as client:
        reply = await client.messages.create(model="test", max_tokens=64, messages=[
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": name, "name": "lookup_error_code", "input": {"error_code": "429"}}
                for name in ("one", "two")
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "one",
                 "content": [{"type": "text", "text": "result"}]},
                {"type": "tool_result", "tool_use_id": "two", "content": "unavailable", "is_error": True},
            ]},
        ], tool_choice={"type": "tool", "name": "lookup_error_code"})
    assert reply.stop_reason == "max_tokens"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {}, {"choices": []}, completion(None), completion(None, reasoning="Only reasoning"),
    completion(None, [tool_call(args="bad json")], "tool_calls"),
    completion(None, [tool_call(args="[]")], "tool_calls"),
    completion(None, [tool_call(), tool_call()], "tool_calls"),
    completion(None, [tool_call()], "length"), completion(None, finish="tool_calls"),
    completion("filtered", finish="content_filter"),
])
async def test_invalid_completion_never_executes_tools(bad):
    async with HuggingFaceClient("hf_test-only", transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=bad)
    )) as client:
        result = await APIAgent(client, "test").handle(Request("Explain 429", "u", "c"))
    assert not result.success and not result.tools_used


@pytest.mark.asyncio
@pytest.mark.parametrize("status,hint", [(401, "HF_TOKEN"), (403, "permission"), (402, "credits"),
                                      (429, "rate limit"), (503, "availability"), (302, "availability")])
async def test_provider_failure_is_sanitized_and_not_retried(status, hint):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, text="sensitive-provider-response hf_test-only",
                              headers={"Location": "https://example.invalid"})
    async with HuggingFaceClient("hf_test-only", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMProviderError, match=hint) as exc:
            await client.messages.create(model="test", max_tokens=64,
                                         messages=[{"role": "user", "content": "hi"}])
    assert "hf_test-only" not in str(exc.value) and "sensitive" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_connection_failure_is_sanitized():
    def handler(req):
        raise httpx.ConnectError("sensitive-provider-response", request=req)
    async with HuggingFaceClient("hf_test-only", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMProviderError, match="Unable to connect") as exc:
            await client.messages.create(model="test", max_tokens=64,
                                         messages=[{"role": "user", "content": "hi"}])
    assert "sensitive" not in str(exc.value)


def test_hf_token_cannot_be_sent_to_another_provider():
    with pytest.raises(ValueError, match="router endpoint"):
        HuggingFaceClient("hf_test-only", base_url="https://example.invalid")


def test_groq_configuration_never_uses_paid_provider_credentials(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("HF_TOKEN", "hf_test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        llm_config()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test-only")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    assert llm_config() == {"api_key": "gsk_test-only", "model": GROQ_DEFAULT_MODEL,
                            "base_url": GROQ_BASE_URL}
    with pytest.raises(ValueError, match="router endpoint"):
        GroqClient("gsk_test-only", base_url=HF_BASE_URL)


@pytest.mark.asyncio
async def test_groq_factory_and_request_options_are_isolated(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("HF_REASONING_EFFORT", "high")
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "none")
    monkeypatch.setenv("GROQ_REASONING_FORMAT", "hidden")
    def handler(req):
        assert str(req.url) == GROQ_BASE_URL + "/chat/completions"
        assert req.headers["authorization"] == "Bearer gsk_test-only"
        body = json.loads(req.content)
        assert body["reasoning_effort"] == "none"
        assert body["reasoning_format"] == "hidden"
        assert body["model"] == GROQ_DEFAULT_MODEL
        return httpx.Response(200, json=completion())
    async with create_client(api_key="gsk_test-only", max_retries=0,
                             transport=httpx.MockTransport(handler)) as client:
        assert isinstance(client, GroqClient)
        await client.messages.create(model=GROQ_DEFAULT_MODEL, max_tokens=64,
                                     messages=[{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_groq_quota_failure_does_not_retry_or_change_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    calls = []
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(429, text="sensitive-provider-response")
    async with create_client(api_key="gsk_test-only", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMProviderError, match="Groq HTTP 429"):
            await client.messages.create(model=GROQ_DEFAULT_MODEL, max_tokens=64,
                                         messages=[{"role": "user", "content": "hi"}])
    assert calls == [GROQ_BASE_URL + "/chat/completions"]


QWEN_TEST_URL = "https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def test_qwen_configuration_uses_only_its_own_key_and_explicit_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("HF_TOKEN", "hf_test-only")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test-only")
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
        llm_config()
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="QWEN_BASE_URL"):
        llm_config()
    monkeypatch.setenv("QWEN_BASE_URL", QWEN_TEST_URL + "/")
    monkeypatch.delenv("QWEN_MODEL", raising=False)
    assert llm_config() == {"api_key": "test-key", "model": QWEN_DEFAULT_MODEL,
                            "base_url": QWEN_TEST_URL}


@pytest.mark.parametrize("endpoint", [
    "", "http://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://example.invalid/compatible-mode/v1",
    "https://dashscope.aliyuncs.com.example.invalid/compatible-mode/v1",
    QWEN_TEST_URL + "?key=not-allowed", QWEN_TEST_URL + "#fragment",
    "https://user:password@dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com:8443/compatible-mode/v1",
])
def test_qwen_rejects_endpoints_that_could_expose_credentials(endpoint):
    with pytest.raises(ValueError, match="QWEN_BASE_URL"):
        QwenClient("test-key", endpoint)


@pytest.mark.asyncio
async def test_qwen_agent_tool_round_trip_and_provider_specific_options(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("HF_REASONING_EFFORT", "high")
    monkeypatch.setenv("GROQ_REASONING_FORMAT", "parsed")
    requests = []
    def handler(req):
        assert str(req.url) == QWEN_TEST_URL + "/chat/completions"
        assert req.headers["authorization"] == "Bearer test-key"
        body = json.loads(req.content)
        requests.append(body)
        assert body["enable_thinking"] is False
        assert "reasoning_effort" not in body and "reasoning_format" not in body
        if len(requests) == 1:
            return httpx.Response(200, json=completion(None, [tool_call()], "tool_calls"))
        tool_result = body["messages"][-1]
        assert tool_result["role"] == "tool" and tool_result["tool_call_id"] == "call-1"
        assert json.loads(tool_result["content"])["error_code"] == "429"
        return httpx.Response(200, json=completion("Check Retry-After."))
    async with create_client(api_key="test-key", base_url=QWEN_TEST_URL,
                             transport=httpx.MockTransport(handler)) as client:
        assert isinstance(client, QwenClient)
        reply = await APIAgent(client, QWEN_DEFAULT_MODEL).handle(Request("Explain 429", "u", "c"))
    assert reply.success and reply.tools_used == ["lookup_error_code"]
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_qwen_quota_error_uses_safe_diagnostic_and_never_retries():
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(403, json={"error": {"code": "AllocationQuota.FreeTierOnly",
                                                 "message": "private account details"}})
    async with QwenClient("test-key", QWEN_TEST_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMProviderError, match="free quota exhausted") as exc:
            await client.messages.create(model=QWEN_DEFAULT_MODEL, max_tokens=64,
                                         messages=[{"role": "user", "content": "hi"}])
    assert len(requests) == 1 and "private" not in str(exc.value)
