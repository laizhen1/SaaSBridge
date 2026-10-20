"""Shared, deterministic SaaS vocabulary. Extracted IDs are hints, never authorization."""
import re
from urllib.parse import urlsplit, urlunsplit


SAAS_KEYWORDS = {
    "api_integration": ("api", "webhook", "sdk", "oauth", "接口", "接口对接", "回调签名"),
    "feature_inquiry": ("feature", "features", "功能", "支持哪些", "data export", "release notes", "发布日志"),
    "onboarding": ("onboarding", "get started", "getting started", "initial setup", "新用户", "初始化配置", "首次使用", "入门"),
    "enterprise_mgmt": ("enterprise", "team permissions", "team roles", "invite members", "contract renewal", "sso", "scim", "企业", "团队权限", "成员邀请", "合同续费", "合同续签", "席位分配"),
    "subscription": ("subscription", "订阅", "套餐", "upgrade plan", "cancel plan", "downgrade", "billing cycle", "auto-renew", "自动续费"),
}


def keyword_matches(message, keyword):
    """Keep English tokens intact (api must not match capital); allow Chinese phrases."""
    if keyword.isascii() and re.search(r"[a-z0-9]", keyword, re.I):
        return re.search(r"(?<![a-z0-9_])" + re.escape(keyword) + r"(?![a-z0-9_])", message, re.I) is not None
    return keyword.casefold() in message.casefold()


def extract_saas_entities(message):
    original = message
    message = without_request_urls(message)
    def unique(values):
        return list(dict.fromkeys(values))[:12]

    fields = {}
    labels = {
        "tenant_id": r"tenant[ _-]?id|org(?:anization)?[ _-]?id|租户(?:ID|编号)|组织(?:ID|编号)",
        "workspace_id": r"workspace[ _-]?id|工作区(?:ID|编号)",
        "subscription_id": r"subscription[ _-]?id|订阅(?:ID|编号)",
        "invoice_id": r"invoice[ _-]?id|账单(?:ID|编号)|发票(?:ID|编号)",
        "contract_id": r"contract[ _-]?id|合同(?:ID|编号)",
        "request_id": r"(?:request|trace)[ _-]?id|请求(?:ID|编号)",
    }
    for field, label in labels.items():
        fields[field] = unique(re.findall(
            rf"(?<![a-z0-9_])(?:{label})\s*[:：=#]?\s*([A-Za-z0-9][A-Za-z0-9_.-]{{1,63}})(?![A-Za-z0-9_.-])",
            message, re.I))
    fields["http_method"] = unique(re.findall(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(?=/|https?://)", original, re.I))
    fields["http_method"] = [method.upper() for method in fields["http_method"]]
    endpoints = re.findall(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s<>\"']*)", original, re.I)
    endpoints += re.findall(r"https?://[^\s<>\"']+", original, re.I)
    safe_endpoints = []
    for endpoint in endpoints:
        try:
            parsed = urlsplit(endpoint.rstrip(".,;，。；)）"))
            # Credentials and query strings are never collected as diagnostic entities.
            if parsed.username or parsed.password:
                continue
            safe_endpoints.append(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
        except ValueError:
            continue
    fields["endpoint"] = unique(safe_endpoints)
    fields["api_version"] = unique(re.findall(r"(?:api[ _-]?version|API版本|接口版本)\s*[:：=]?\s*(v?\d+(?:\.\d+){0,2})\b", message, re.I))
    fields["environment"] = unique(re.findall(r"\b(sandbox|staging|production)\b|(?:环境[:：]?\s*)(测试|预发布|生产)", message, re.I))
    fields["environment"] = [next(part for part in match if part).lower() for match in fields["environment"]]
    return fields


def without_request_urls(message):
    """URL fragments and query values must not be misread as order or account IDs."""
    message = re.sub(r"https?://[^\s<>\"']+", " ", message, flags=re.I)
    return re.sub(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/[^\s<>\"']*", r"\1 ", message, flags=re.I)
