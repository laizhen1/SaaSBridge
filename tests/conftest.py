"""Keep offline regression tests independent of private provider configuration."""
import pytest


@pytest.fixture(autouse=True)
def isolated_llm_environment(monkeypatch):
    # General tests use fake keys and mock the inherited Anthropic interface.
    # Provider-specific tests explicitly select their provider with monkeypatch.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    for name in (
        "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL",
        "HF_TOKEN", "HF_MODEL", "HF_REASONING_EFFORT",
        "GROQ_API_KEY", "GROQ_MODEL", "GROQ_REASONING_EFFORT", "GROQ_REASONING_FORMAT",
        "QWEN_API_KEY", "QWEN_MODEL", "QWEN_BASE_URL", "SILICONFLOW_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
