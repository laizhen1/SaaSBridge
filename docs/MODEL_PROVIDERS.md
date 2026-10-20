# Model provider configuration

[Back to the project README](../README.md)

Connection examples and provider notes moved from the README. Any dated pricing,
quota or availability figures below are historical observations; verify current
terms in the provider console before making live requests. Account settings,
not the number of Agent roles, determine billing.

## Qwen API connection

The default configuration uses **Qwen Flash** (`qwen-flash`) through Alibaba
Model Studio. The endpoint must match the workspace and region that issued your
key. Copy the exact OpenAI-compatible Base URL from the provider console:

```dotenv
LLM_PROVIDER=qwen
QWEN_API_KEY=replace_with_your_private_key
QWEN_BASE_URL=https://YOUR_WORKSPACE.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-flash
```

Set these in the backend `.env` only. The real file is excluded from Git and
Docker build context; `.env.example` contains placeholders. Never put the key in
frontend settings, screenshots or source code. Requests send it only in the HTTPS
Authorization header. The adapter rejects non-Alibaba endpoints, does not follow
redirects, and omits raw provider errors from diagnostics.

Qwen calls are **usage-based**, including calls made by the connection check.
Beijing is not covered by the current international Singapore trial offer.
For eligible Singapore trial models, turn on **Free Quota Only** in the provider
console if you need requests to stop when free tokens run out. A local setting
does not control provider billing. See
[Alibaba's quota rules](https://www.alibabacloud.com/help/en/model-studio/new-free-quota).

Run `python -m tooling.check_llm --tools` from an activated backend environment
to check text generation and a read-only tool round trip. This uses at most three
small requests and prints status/token counts without the key. The adapter turns
off thinking mode for short support answers, intent JSON and forced tool tests.
There is no automatic provider switch or HTTP retry. Restart the backend after
editing `.env`.

## Optional free model API for the demo

The optional free provider is **Groq**, using `qwen/qwen3.8-27b` (a preview model with
tool calling and multilingual support). Use a **Groq Free Plan account**.
Groq's [Free Plan FAQ](https://community.groq.com/t/do-i-get-charged-for-anything-on-the-groq-free-plan/832)
states that usage on the Free Plan is not charged; exceeding its limits returns
HTTP 429. Do not upgrade to the paid Developer plan for this free demo.
The account plan determines billing: the same model is billable on paid plans.

1. Sign up or log in at [Groq Console](https://console.groq.com).
2. Stay on the Free Plan and create a key at [API Keys](https://console.groq.com/keys).
3. Save the key locally in `D:\SaaSBridge\.env`:

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_replace_with_your_key
GROQ_MODEL=qwen/qwen3.8-27b
GROQ_REASONING_EFFORT=none
GROQ_REASONING_FORMAT=hidden
```

No local GPU or chat model download is required; Groq hosts the model. The local
knowledge embedding model is separate. A Hugging Face token does not work as a
Groq key. Existing `HF_*` settings are ignored while `LLM_PROVIDER=groq`.

Keep `.env` private. Do not place the key in frontend settings. Restart the
backend after changing model configuration. Docker deployments need the app
container recreated to reload `.env`.

Test the real API before starting storage or the web application:

```powershell
cd D:\SaaSBridge
.\.venv\Scripts\python.exe -m tooling.check_llm --tools
```

This spends up to three small model requests: a synthetic greeting, an error-code
tool call, and its final answer. It checks both text generation and tool calling
without accessing customer records. It prints status and token counts, never the
key. HTTP 401/403 indicates credentials/permissions; HTTP 429 means wait for the
relevant quota to reset.

All agents, intent detection, retrieval rewriting/reranking, memory maintenance
and evaluation use the selected provider. The adapter translates the inherited
Anthropic text/tool format to Groq's chat endpoint, preserving existing tool
validation and allowlists. There are no automatic HTTP retries or automatic
fallback to Hugging Face, Anthropic or any other provider. Agent fallback within
the application still uses the same configured Groq connection. SaaSBridge does
not upgrade accounts or buy credits.

On 2026-10-20, Groq's published
[Free Plan limits](https://console.groq.com/docs/rate-limits) for this model were:
30 requests/minute, 1,000 requests/day, 8,000 tokens/minute and 200,000 tokens/day.
The first limit reached applies. Exact account limits and model availability can
change; check your Groq Console. This is suitable for light demo testing rather
than unrestricted traffic.

A basic uncached support turn normally makes two foreground model requests
(intent detection and the answer), plus a background profile update. Retrieval,
tool loops, collaboration and occasional memory compression add requests.
The seven roles share the configured API model; they do not require seven local
models. The workload is modest for a development demo, but production throughput
depends on concurrent users and prompt/context length.

## Optional paid Hugging Face connection

This is not used by the default Qwen configuration. To explicitly opt in,
set `LLM_PROVIDER=huggingface`, `HF_TOKEN`, and
`HF_MODEL=deepseek-ai/DeepSeek-V4-Flash:deepinfra`.
Set `HF_REASONING_EFFORT=none` for short support/JSON tasks.
Create a fine-grained token with **Make calls to Inference Providers** permission
at [Hugging Face token settings](https://huggingface.co/settings/tokens).

The [HF model catalog](https://router.huggingface.co/v1/models) listed this
DeepInfra route at **US$0.09 per million input tokens / $0.18 per million output
tokens** when checked on 2026-10-20. For illustration, 5,000 input + 1,000 output
tokens combined across a turn's calls would cost $0.00063, or $0.63 per 1,000
such turns. These token counts are an example, not a measured application average.
Rates and availability can change. Hugging Face is pay-as-you-go. Its current
pricing page lists **no included monthly credits for free accounts**; purchasing
credits is required. HTTP 402 indicates insufficient credits. Older references to a $0.10 free
credit are outdated.
See [pricing](https://huggingface.co/docs/inference-providers/en/pricing) and
[authentication](https://huggingface.co/docs/inference-providers/en/index#authentication).
