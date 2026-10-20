# SaaS 产品支持扩展

## 角色与意图

| 意图 | 主 Agent | 主要能力 |
| --- | --- | --- |
| `API_INTEGRATION` | `APIAgent` | 查询版本化 API 文档、解释错误码、给出最小复现步骤 |
| `FEATURE_INQUIRY` | `OnboardingAgent` | 查询功能说明、发布日志和版本适用范围 |
| `ONBOARDING` | `OnboardingAgent` | 初始化配置和分步骤引导 |
| `ENTERPRISE_MGMT` | `EnterpriseAgent` | 组织、团队角色、席位、企业合同续签 |
| `SUBSCRIPTION` | `BillingAgent` | 套餐、账期、续费、取消与订阅定位字段 |

技术与账单工具继续复用。组合问题可派发多个角色，例如“API 报错，同时要调整订阅”。明确转人工请求优先进入升级节点。中英文关键词及 few-shot 已补充；LLM 仍负责复杂表达，规则并不是完整自然语言理解。

实体包括组织、工作区、订阅、账单、合同、request_id、HTTP 方法、接口地址、API 版本和环境。URL 查询串、片段和内嵌凭据不会收集为接口实体；这些定位信息不构成身份授权。同一领域追问可继承实体，切换组织、工作区或订阅时清空旧实体，避免串用。

## Skill

`skills/00_saas_foundation` 提供公共边界，`saas_api`、`saas_onboarding`、`saas_enterprise`、`saas_billing` 定义角色流程。
每次构建请求 prompt 时检查文件变化；新增、修改、删除会在下一次请求生效，不需要重启。`GET /skills` 查看状态，`POST /skills/reload` 可强制重载。文件解析错误会显示出来并排除该文件，其他 Skill 继续使用。

## 知识导入

知识面板和 `POST /knowledge/upload` 支持 TXT、Markdown、文档 JSON 数组、`{"documents":[...]}` 对象及 OpenAPI 3.0/3.1 JSON/YAML。文件需 UTF-8，最大 10 MB。结构化文档单批最多 10,000 篇。

```powershell
curl.exe -F "file=@examples/openapi.json" http://127.0.0.1:8000/knowledge/upload
```

启用访问认证后，请通过已登录的控制台上传，或在 API 客户端配置管理员 Bearer 凭据。

OpenAPI 导入保留根、路径、操作层的 servers 覆盖关系以及显式空 security；本地 path-item 引用可解析，schema 引用保留并另建组件文档。只处理本地引用，不访问网络。暂不接受 Swagger 2.0、远程引用、YAML alias、超过深度/节点限制的文档；YAML 版本、响应码和日期请用引号表示字符串。

每篇文档可附 `doc_type`、`source`、`product_version`，检索结果会返回这些字段。文档切片保持原始换行和代码内容。相同文档身份重复导入更新已有片段；不同 API 版本分开保存。删除于新 spec 的操作不会自动从旧知识库清除，需另外管理旧文档生命周期。

`examples/saas-knowledge.template.json` 提供发布日志、集成 FAQ、功能与权限、订阅规则的填写模板，不会自动导入。以真实产品文档替换全部占位符后再使用。零售演示资料默认不加载。共享知识库仍只适合公共产品文档，不用于存放企业私有合同或账户记录。

## 可配置业务系统接入

当前没有真实企业、订阅或工单服务。默认 `MCP_EXTERNAL_ENABLED=false`，运行配置 `config/mcp_servers.json` 为空；`config/mcp_saas.example.json` 中的所有示例也均为 `enabled: false`。

有真实 MCP 服务后：

1. 将需要的示例条目复制到 `config/mcp_servers.json`，填写真实 HTTP(S) MCP 地址和工具白名单，设置 `enabled: true`。
2. 认证值只保存在本地 `.env` 的 `SAAS_MCP_AUTHORIZATION`，例如带 Bearer 前缀的服务凭据。配置文件只引用环境变量名。
3. 在 `.env` 设置 `MCP_EXTERNAL_ENABLED=true`，重启后查看管理接口 `GET /integrations/mcp`。这是服务连接配置，区别于可自动刷新的 Skill。
4. 先保持 `read_only: true`。远端工具还必须声明 `readOnlyHint: true` 且不能声明 destructive；未在白名单或对应 Agent 列表中的工具不会暴露给模型。

建议远端实现的只读工具：

| 工具 | 参数示例 | 返回内容示例 |
| --- | --- | --- |
| `get_account` / `list_team_roles` | `tenant_id` | 已授权组织资料 / 允许查看的角色列表 |
| `get_contract` | `contract_id` | 已授权合同的必要状态与日期 |
| `get_subscription` / `get_invoice` | `subscription_id` / `invoice_id` | 当前账期、状态与金额（含币种） |
| `get_ticket` | `ticket_id` | 已授权工单状态 |
| `search_api_docs` / `search_release_notes` | `query`、可选版本 | 带来源和版本的文档片段 |

这些名称是适配契约建议，项目不会伪造响应；实际参数校验使用远端 `tools/list` 返回的 JSON Schema。

涉及账户的示例设置 `require_user_context: true`，应用在 MCP `tools/call` 的 `_meta` 中发送 `saasbridge/user_id`，而不是让模型填写身份。缺少用户上下文时，调用在发送前失败。生产环境这个值来自 `ECHOMIND_API_TOKENS` 对应的已认证用户；开发模式的客户端自报 user_id 不具备认证保证。

业务服务必须验证网关服务凭据，并将 `_meta` 中的用户映射到自身成员/角色权限，对每一个目标组织、订阅和合同进行授权检查。不要信任模型参数里的 user_id/tenant_id 或用户声称的管理员身份。账户查询模板关闭缓存，避免过期权限或账单信息。管理员无上下文调试接口不适用于这些私有查询。

真实创建工单、调整订阅、退款和权限变更未配置。未来加入写工具时，需由业务服务实现明确确认、授权校验、幂等键与审计，再单独配置白名单及 `read_only: false`。框架会标记非安全重试工具，并将其排除于离线评测；这不能代替业务系统的事务和授权保证。当前升级回复只生成交接说明，不代表工单已创建。

## 工作记忆与监控

`ECHOMIND_WORKING_MEMORY_TTL_SECONDS` 控制 Redis 工作消息、会话实体状态和摘要，默认 86400 秒，可设 3600–604800 秒。重启后新写入/刷新使用新值；请求幂等回执仍保留 24 小时，轨迹仍保留 7 天。Chroma 情景记忆与画像的持久化规则不受该参数影响。

`config/alerts/saasbridge.yml` 提供服务不可用、HTTP 5xx 比例与聊天 p95 延迟告警；延迟单位是毫秒。低流量下错误率和延迟规则至少需要 5 分钟内 20 次请求。Compose 的 monitoring profile 已挂载规则目录：

```powershell
docker compose -f compose.yaml -f compose.app.yaml --profile monitoring up -d --build
```

Prometheus 界面在 `http://127.0.0.1:9090`。这些规则产生告警状态；尚未配置 Alertmanager 或通知接收人，因此不会发送邮件或消息。生产 `/metrics` 的认证凭据需单独配置。HTTP 200 内的模型降级不是 5xx，应结合 Agent 指标与请求轨迹检查。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

新增测试使用模拟模型、内存知识库、Redis 协议模拟器以及 MCP 协议测试服务，不调用付费模型或真实业务服务。部署后仍需用实际文档、模型和业务服务做集成验收。
