"""Small live connection check: python -m tooling.check_llm [--tools].

Loads the project .env. Uses synthetic prompts only; no Redis/ChromaDB required.
"""
import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from core.llm_client import create_client, llm_config
from core.llm_utils import extract_text_content


def _value(block, key):
    return block.get(key) if isinstance(block, dict) else getattr(block, key, None)


async def check(use_tools=False):
    cfg = llm_config()
    model = cfg.pop("model")
    print(f"Model: {model}")
    async with create_client(**cfg, max_retries=0) as client:
        reply = await client.messages.create(
            model=model, max_tokens=64, temperature=0,
            messages=[{"role": "user", "content": "Reply exactly: SaaSBridge connection OK."}],
        )
        if "SaaSBridge connection OK" not in extract_text_content(reply.content):
            raise RuntimeError("The model responded, but the connection check phrase was missing")
        print("PASS: text generation")
        print(f"Tokens: input={reply.usage.input_tokens}, output={reply.usage.output_tokens}")
        if not use_tools:
            return

        from agents.tools import lookup_error_code, technical_tools

        spec = technical_tools()["lookup_error_code"]
        tools = [{"name": spec.name, "description": spec.description,
                  "input_schema": spec.input_schema}]
        messages = [{"role": "user", "content": (
            "Call lookup_error_code for error_code 429. After receiving its result, "
            "give one sentence in English about this error."
        )}]
        reply = await client.messages.create(
            model=model, max_tokens=256, temperature=0, messages=messages, tools=tools,
            tool_choice={"type": "tool", "name": "lookup_error_code"},
        )
        calls = [b for b in reply.content if _value(b, "type") == "tool_use"]
        if len(calls) != 1 or _value(calls[0], "name") != spec.name:
            raise RuntimeError("The model did not return the expected tool call")
        args = _value(calls[0], "input")
        if args != {"error_code": "429"}:
            raise RuntimeError("The model returned unexpected tool arguments")
        messages.extend([
            {"role": "assistant", "content": reply.content},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": _value(calls[0], "id"),
                "content": json.dumps(lookup_error_code(None, args), ensure_ascii=False),
            }]},
        ])
        answer = await client.messages.create(
            model=model, max_tokens=128, temperature=0, messages=messages,
            tools=tools, tool_choice={"type": "none"},
        )
        if not extract_text_content(answer.content).strip() or answer.stop_reason != "end_turn":
            raise RuntimeError("The model did not finish its answer after the tool result")
        print("PASS: error-code tool call and final answer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", action="store_true", help="also test a read-only tool round trip")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    try:
        asyncio.run(check(args.tools))
    except Exception as exc:
        # Third-party SDK errors may include request/response bodies. Only our
        # fixed diagnostic messages are printed; never dump provider responses.
        from core.llm_client import LLMProviderError
        if isinstance(exc, LLMProviderError) or type(exc) is RuntimeError:
            print(f"FAIL: {exc}")
        else:
            status = getattr(exc, "status_code", None)
            print(f"FAIL: {type(exc).__name__}" + (f" (HTTP {status})" if status else ""))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
