"""Agent 工具定义与实现。

所有 Agent 工具集中在这里，编排器只负责：
  1. 根据 Agent 类型暴露工具白名单
  2. 执行 LLM 返回的 tool_use
  3. 将工具结果回传给 LLM

工具本身保持确定性、可测试，并明确区分：
  - 当前请求分析
  - 技术排障建议
  - 账单字段核验
  - 人工升级摘要
  - 共享知识库 RAG

订单查询、退款执行、账单修改等需要真实业务系统授权的动作不在这里伪造。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING, Union, Tuple

if TYPE_CHECKING:
    from agents.agent_orchestrator import Request


AgentToolHandler = Callable[["Request", Dict[str, Any]], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class AgentToolSpec:
    """Agent 可见工具的定义和执行函数。"""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: AgentToolHandler
    allowed_agents: Optional[Tuple[str, ...]] = None
    retry_safe: bool = True


def make_tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    handler: AgentToolHandler,
    required: Optional[List[str]] = None,
) -> AgentToolSpec:
    """创建带 JSON Schema 的 Agent 工具。"""
    return AgentToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        handler=handler,
    )


def inspect_request_context(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """通用客服工具：返回脱敏后的当前请求快照。"""
    return {
        "intent": req.intent.value if req.intent else None,
        "intent_group": req.intent_group,
        "urgency": req.urgency.name if req.urgency else None,
        "intent_confidence": round(req.intent_confidence, 4),
        "entities": req.entities or {},
        "context_available": bool(req.context),
        "requested_focus": str(args.get("focus", "general"))[:40],
    }


def suggest_required_fields(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """通用客服工具：按业务类型计算下一轮只需询问的字段。"""
    intent = req.intent.value if req.intent else "other"
    fields: List[str] = []
    if intent in {"order_status", "logistics"}:
        fields = ["订单号或下单时间"]
    elif intent in {"account", "account_security"}:
        fields = ["登录方式或账号标识", "问题发生时间"]
    elif intent in {"complaint", "request"}:
        fields = ["事件时间", "期望处理方式"]
    elif intent == "other":
        fields = ["希望解决的具体问题"]
    saas_fields = {
        "api_integration": {"endpoint": "请求方法与接口路径（去掉凭据和查询串）", "api_version": "API 版本", "error_code": "错误码", "request_id": "请求追踪 ID"},
        "enterprise_mgmt": {"tenant_id": "组织 ID（仅用于定位，不作为授权）", "contract_id": "仅合同问题需要合同编号"},
        "subscription": {"subscription_id": "仅账户查询需要订阅 ID"},
        "onboarding": {},
        "feature_inquiry": {},
    }
    if intent in saas_fields:
        fields = [label for key, label in saas_fields[intent].items() if not req.entities.get(key)]
        if intent == "onboarding":
            fields = ["希望完成的目标和当前配置步骤"]
        elif intent == "feature_inquiry":
            fields = ["具体功能和正在使用的产品版本"]
    return {
        "intent": intent,
        "required_fields": fields,
        "known_entities": req.entities or {},
        "guidance": "仅询问当前问题缺少的必要字段；通用功能或政策咨询不需要账户标识。字段齐全不代表已授权。",
    }


def lookup_error_code(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """技术工具：解释常见错误码的排查方向，不声称读取了服务端日志。"""
    code = str(args.get("error_code", "")).upper().strip()
    mapping = {
        "RATE_LIMIT_EXCEEDED": ("请求频率超限", ["查阅产品限流策略", "遵循 Retry-After", "使用有上限的指数退避"]),
        "QUOTA_EXCEEDED": ("用量配额不足", ["核对用量周期和配额", "联系管理员确认套餐"]),
        "SUBSCRIPTION_INACTIVE": ("订阅未激活", ["由授权管理员核对订阅状态", "不要重复提交付款"]),
        "SEAT_LIMIT_REACHED": ("席位数量已达上限", ["核对已使用席位", "由管理员确认增购流程"]),
        "INSUFFICIENT_SCOPE": ("访问范围不足", ["核对文档所需 scope", "由管理员按最小权限授权"]),
        "TENANT_ACCESS_DENIED": ("组织访问被拒绝", ["核对组织上下文", "联系本组织管理员"]),
        "WEBHOOK_SIGNATURE_INVALID": ("回调签名校验失败", ["按文档核对原始请求体与签名算法", "检查时间戳允许偏差，不要发送签名密钥"]),
        "IDEMPOTENCY_CONFLICT": ("幂等键与原请求不一致", ["核对原请求结果与请求体", "不要为支付或写操作盲目换键重试"]),
        "API_VERSION_UNSUPPORTED": ("接口版本不受支持", ["核对 API 版本与发布日志", "按迁移文档验证兼容性"]),
        "VALIDATION_ERROR": ("请求字段校验失败", ["按 OpenAPI 核对必填字段、类型和枚举", "提供去除凭据的最小请求样例"]),
        "409": ("资源状态冲突", ["核对资源当前版本和幂等键", "先查询结果再决定是否重试写入"]),
        "422": ("请求语义或字段校验失败", ["核对请求体与 OpenAPI schema", "检查错误响应中的字段路径"]),
        "503": ("服务暂时不可用", ["查看服务状态与 Retry-After", "只对安全或有幂等保护的请求有限重试"]),
        "429": ("请求频率超限", ["遵循 Retry-After", "检查配额和并发"]),
        "401": ("认证失败", ["确认 Token/API Key 是否过期", "确认请求时间戳和签名", "确认账号登录状态"]),
        "403": ("权限不足", ["确认账号或套餐权限", "确认资源权限和 IP 白名单"]),
        "404": ("资源或路径不存在", ["确认接口路径和环境", "确认资源标识是否正确"]),
        "500": ("服务端处理异常", ["记录 request_id 和发生时间", "检查依赖服务、参数格式和服务端日志"]),
    }
    meaning, steps = mapping.get(
        code,
        ("暂未识别的错误码", ["补充完整错误信息、发生时间和运行环境"]),
    )
    return {
        "error_code": code,
        "meaning": meaning,
        "next_steps": steps,
        "server_log_checked": False,
        "known_code": code in mapping,
        "source": "generic_support_catalog",
        "guidance": "通用排查方向；业务错误码以当前产品文档为准，未查询账户、配额或订阅实际状态。",
    }


def build_diagnostic_plan(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """技术工具：生成低风险排障顺序。"""
    environment = str(args.get("environment", "unknown"))[:80]
    reproduced = bool(args.get("reproduced", False))
    steps = [
        "复现并记录完整错误信息",
        "确认网络、DNS、代理和证书",
        "确认版本、配置和权限",
    ]
    if reproduced:
        steps.append("用最小请求复现并记录 request_id")
    if req.intent and req.intent.value == "api_integration":
        steps = ["检索对应版本的接口文档与发布日志", "核对请求方法、路径、必填字段与认证 scope",
                 "核对错误码、request_id 和发生时间", "在测试环境使用去除密钥的最小请求复现",
                 "仅对安全请求或有幂等保护的操作，按文档有限重试"]
    return {
        "environment": environment,
        "reproduced": reproduced,
        "diagnostic_steps": steps,
    }


def check_billing_fields(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """账单工具：检查必要核验字段是否齐全。"""
    fields = {
        "order_id": bool(req.entities.get("order_id")),
        "amount": bool(req.entities.get("amount")),
        "date": bool(req.entities.get("date")),
        "payment_channel": bool(args.get("payment_channel")),
    }
    if req.intent and req.intent.value == "subscription":
        fields = {name: bool(req.entities.get(name)) for name in ("tenant_id", "subscription_id")}
    return {
        "fields": fields,
        "missing_fields": [name for name, present in fields.items() if not present],
        "can_confirm_refund": False,
        "reason": "当前工具只做字段检查，不连接订单或支付系统",
        "required_for": "仅账户具体账单/订阅核验；通用套餐、取消或续费规则应先查文档，无需收集付款信息。",
    }


def compare_amounts(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """账单工具：只做用户明确提供金额之间的算术。"""
    try:
        first = float(args["amount_a"])
        second = float(args["amount_b"])
    except (KeyError, TypeError, ValueError):
        return {"success": False, "error": "amount_a 和 amount_b 必须是数字"}
    return {
        "success": True,
        "amount_a": first,
        "amount_b": second,
        "difference": round(first - second, 2),
        "interpretation": "仅表示金额差值，不代表重复扣款或退款结论",
    }


def create_handoff_summary(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """升级工具：生成可交给人工客服的结构化摘要。"""
    return {
        "request_id": req.request_id,
        "reason": str(args.get("reason", "需要人工客服继续核验"))[:120],
        "intent": req.intent.value if req.intent else "unknown",
        "urgency": req.urgency.name if req.urgency else "UNKNOWN",
        "entities": req.entities or {},
        "sensitive_data_required": False,
    }


def build_shared_rag_tools(tool_manager: Any) -> Dict[str, AgentToolSpec]:
    """构建所有 Agent 可共享的 RAG 工具。"""

    async def search_knowledge_base(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or req.message or "").strip()
        top_k = args.get("top_k", 5)
        if not query:
            return {"success": False, "error": "query 不能为空", "results": []}
        if tool_manager is None:
            return {"success": False, "error": "RAG 工具未初始化", "results": []}

        result = await tool_manager.search_with_rewrite(
            "knowledge_search",
            query,
            top_k=top_k,
        )
        if not getattr(result, "success", False):
            return {
                "success": False,
                "query": query,
                "error": getattr(result, "error", "知识库检索失败"),
                "results": [],
                "reranked": False,
            }

        return {
            "success": True,
            "query": query,
            "top_k": top_k,
            "results": result.data,
            "reranked": bool(getattr(result, "reranked", False)),
        }

    return {
        "search_knowledge_base": make_tool(
            "search_knowledge_base",
            "检索知识库并返回最相关的文档片段；可用于通用、技术、账单和升级场景。",
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "用户问题或检索关键词"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回结果条数"},
            },
            search_knowledge_base,
            required=["query"],
        )
    }


def general_tools() -> Dict[str, AgentToolSpec]:
    return {
        "inspect_request_context": make_tool(
            "inspect_request_context",
            "查看当前请求的意图、紧急度、实体和上下文可用性；不查询外部业务系统。",
            {"focus": {"type": "string", "description": "希望关注的业务方向"}},
            inspect_request_context,
        ),
        "suggest_required_fields": make_tool(
            "suggest_required_fields",
            "根据当前意图建议下一轮只需向用户补充的字段。",
            {},
            suggest_required_fields,
        ),
    }


def technical_tools() -> Dict[str, AgentToolSpec]:
    return {
        "lookup_error_code": make_tool(
            "lookup_error_code",
            "解释 HTTP 与 SaaS 业务错误码的可能含义和低风险排查方向；不会读取服务端日志。",
            {"error_code": {"type": "string", "minLength": 1, "maxLength": 80, "description": "例如 401、RATE_LIMIT_EXCEEDED、INSUFFICIENT_SCOPE"}},
            lookup_error_code,
            required=["error_code"],
        ),
        "build_diagnostic_plan": make_tool(
            "build_diagnostic_plan",
            "根据运行环境和是否可复现生成排障顺序，不执行修改配置等操作。",
            {
                "environment": {"type": "string", "description": "App、浏览器、服务端或 Docker 等"},
                "reproduced": {"type": "boolean", "description": "问题是否可以稳定复现"},
            },
            build_diagnostic_plan,
            required=["environment", "reproduced"],
        ),
    }


def billing_tools() -> Dict[str, AgentToolSpec]:
    return {
        "check_billing_fields": make_tool(
            "check_billing_fields",
            "检查账单核验字段是否齐全；不连接订单、支付或退款系统。",
            {"payment_channel": {"type": "string", "description": "支付渠道，例如微信、支付宝、银行卡"}},
            check_billing_fields,
        ),
        "compare_amounts": make_tool(
            "compare_amounts",
            "计算用户明确提供的两笔金额差值；不判断是否重复扣款，也不执行退款。",
            {
                "amount_a": {"type": "number", "description": "第一笔金额"},
                "amount_b": {"type": "number", "description": "第二笔金额"},
            },
            compare_amounts,
            required=["amount_a", "amount_b"],
        ),
    }


def escalation_tools() -> Dict[str, AgentToolSpec]:
    return {
        "create_handoff_summary": make_tool(
            "create_handoff_summary",
            "生成交给人工客服的结构化交接摘要，不会创建真实工单。",
            {"reason": {"type": "string", "description": "需要升级的原因"}},
            create_handoff_summary,
        ),
    }
