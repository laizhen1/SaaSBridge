"""会话中的业务字段；只继承已提取的信息，不把字段齐全视为身份核验。"""
import re


def merge_state(previous, entities, intent_group, message, request_id):
    previous = previous if isinstance(previous, dict) else {}
    domain = {"billing": "commerce", "query": "commerce"}.get(intent_group, intent_group)
    neutral = domain in {"greeting", "feedback", "other", "complaint", "request", "escalation"}
    old = previous.get("entities", {})
    sources = previous.get("sources", {})
    old = old if isinstance(old, dict) else {}
    sources = sources if isinstance(sources, dict) else {}
    changed_scope = any(entities.get(key) and old.get(key) and entities[key] != old[key]
                        for key in ("order_id", "tenant_id", "workspace_id", "subscription_id"))
    new_task = bool(re.search(r"(?:换个|新的|另一个)(?:问题|订单|租户|组织|工作区)|另一笔订单|\b(?:another|new|different) (?:issue|question|tenant|workspace)\b", message, re.I))
    if neutral:
        domain = ("commerce" if entities.get("order_id") else "general") if new_task else previous.get("domain", "commerce" if entities.get("order_id") else "general")
    keep = not changed_scope and not new_task and (neutral or domain == previous.get("domain"))
    merged = {key: list(value) for key, value in old.items() if isinstance(value, list)} if keep else {}
    origins = dict(sources) if keep else {}
    for key, value in entities.items():
        if value:
            merged[key] = list(value)
            origins[key] = request_id
        else:
            merged.setdefault(key, [])
    return {"domain": domain,
            "entities": merged, "sources": origins}
