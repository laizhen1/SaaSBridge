# SaaSBridge

Connecting your customers to answers.

A Python multi-agent support foundation for B2B SaaS, with a Vue support console.

## Project layout

```text
D:\SaaSBridge\
  agents/ core/ api/       Python backend and SaaS agents
  memory/ tooling/         Memory, knowledge retrieval and tool integrations
  evaluation/ monitor/     Evaluation and monitoring
  skills/                 Original and SaaS-specific support instructions
  frontend/               Imported Vue support console
  config/                 MCP and monitoring configuration
  examples/               Example OpenAPI specification
  tests/                  Backend regression tests
  compose.yaml            Local Redis and ChromaDB services
  compose.app.yaml        Python application and frontend services
```

The frontend provides SaaS support prompts, chat, knowledge management,
evaluation and monitoring. The active deployment uses the Python backend.

## Agents

| Intent | Agent | Responsibility |
|---|---|---|
| `api_integration` | APIAgent | API documentation, redacted diagnostics, error codes |
| `feature_inquiry` | OnboardingAgent | Documented product capabilities |
| `onboarding` | OnboardingAgent | Setup and first-use guidance |
| `enterprise_mgmt` | EnterpriseAgent | Team roles, enterprise accounts, renewal guidance |
| `subscription` | BillingAgent | Plan and billing guidance |
| Existing technical / billing intents | TechnicalAgent / BillingAgent | Original support workflows |
| General / human handoff | GeneralAgent / EscalationAgent | Triage and handoff summaries |

EchoMind's FastAPI service, memory, retrieval, tool loop, performance routing,
evaluation, authentication and tests are retained. New specialists participate in
the existing circuit breaker and fallback behavior. Intent examples and keyword
patterns support English and Chinese. Agent replies follow the user's language.

## Local setup (PowerShell, Python 3.12)

```powershell
cd D:\SaaSBridge
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Configure the Qwen connection below in `.env`.
Alternatively, set `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` and
`ANTHROPIC_MODEL`; an Anthropic-compatible provider also needs its
`ANTHROPIC_BASE_URL`. No original credentials were copied.
If `py` is unavailable, use the full path to your installed Python executable.

Start Redis and ChromaDB with Docker Desktop running, then start the backend:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Interactive API documentation: http://127.0.0.1:8000/docs

```powershell
Invoke-RestMethod http://127.0.0.1:8000/chat -Method Post -ContentType 'application/json' -Body '{"message":"Help me debug my API integration","user_id":"demo"}'
```

The first knowledge ingestion can download ChromaDB's embedding model.
On Windows the HTTP-only ChromaDB client avoids a native C++ build dependency;
the ChromaDB Docker service is required (local persistent fallback is unavailable).
External MCP connections and retail demo knowledge seeding are disabled by default.
Inherited `ECHOMIND_*` configuration names remain compatible with the upstream code.
The Compose file runs local development storage services; it is not a production deployment.

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

## Frontend

The imported Vue console includes chat, tool traces, knowledge search/import,
evaluation and monitoring details. With the Python backend running locally:

```powershell
cd D:\SaaSBridge\frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5175. The frontend proxies `/api/python` to the backend on
port 8000. Model credentials belong in the backend `.env`; the connection dialog
accepts the backend's Bearer token when authentication is enabled.
Node.js 22.12+ or a supported newer release is needed by the supplied Vite version.

## Run the complete application with Docker

Create and configure `.env` from `.env.example`, then with Docker Desktop running:

```powershell
cd D:\SaaSBridge
docker compose -f compose.yaml -f compose.app.yaml up -d --build
```

Open http://127.0.0.1:5175. Backend API documentation remains available on port 8000.
Storage runs in named Docker volumes. The backend image downloads its embedding
model while building. This stack binds ports to localhost for development.

```powershell
docker compose -f compose.yaml -f compose.app.yaml down
```

The optional `--profile monitoring` starts the supplied Prometheus integration on
port 9090. Its scrape configuration assumes development mode; authenticated
production deployments need a dedicated metrics credential configuration.
Use the root Compose files for the SaaSBridge deployment.

## Product knowledge and OpenAPI

Supply your actual API documentation, feature release notes and integration FAQs.
No product capabilities, prices or contracts have been invented.

Upload an **OpenAPI 3.0/3.1 JSON or YAML** file from the knowledge panel or
`POST /knowledge/upload`. The console now accepts `.yaml` and `.yml` too.
Alternatively, convert a local spec into the existing knowledge API format:

```powershell
.\.venv\Scripts\python.exe -m tooling.openapi_import .\your-openapi.json .\data\openapi-documents.json
Invoke-RestMethod http://127.0.0.1:8000/knowledge/add -Method Post -ContentType 'application/json' -InFile .\data\openapi-documents.json
```

The converter includes operations, inherited authentication and server URLs, path
parameters and component definitions. Local path-item references are resolved;
other local references are validated and retained with component documents.
External references, YAML aliases and Swagger 2.0 require prior conversion.
Review documents for secrets before ingestion.
Import only trusted product documentation. The existing API limits apply to each
batch; split large payloads if necessary.

Other documentation uses the same endpoint:

```json
{"documents":[{"title":"Product release notes","content":"Your actual release notes here.","doc_type":"release_notes","source":"release-notes.md","product_version":"1.0"}]}
```

Document metadata is retained in search results. Reimporting the same OpenAPI
product/version/operation updates its chunks. Removed operations are not deleted
automatically; review obsolete documents before importing a replacement spec.

See [SaaS support configuration](docs/SAAS_SUPPORT.md) for the intent/agent map,
automatic Skill refresh, disabled external-system templates, working-memory TTL
and Prometheus alerts. Knowledge templates for release notes and integration
FAQs are in `examples/saas-knowledge.template.json`; replace placeholders before import.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm test
npm run build
```

`tests/test_saasbridge.py` covers bilingual classification, specialist routing,
compound API/subscription requests, degraded-agent fallback, emergency handoff,
mocked agent turns, skill loading, error codes and OpenAPI conversion.

## Foundation boundaries

This is a backend foundation with the imported support console. The agents
provide support guidance; they cannot change subscriptions, issue refunds,
modify permissions, execute customer APIs or sign contracts. The added business
error codes are example mappings to adapt to your actual product.

The inherited identity model is user-based, and the knowledge collection is
shared. Do not load tenant-private documents or expose this as a production
multi-tenant service until tenant-scoped storage, authorization and business
integrations are implemented. Production authentication requires
`ECHOMIND_ENV=production` and a valid `ECHOMIND_API_TOKENS` mapping.

Source provenance: derived from the user-supplied Python EchoMind code. No new
license is asserted for that upstream material.
