"""Small display-only projections of successful retrievals, not answer citations."""
from urllib.parse import urlsplit, urlunsplit
from core.redaction import redact


def knowledge_sources(rows):
    if not isinstance(rows, list):
        return []
    sources, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {key: redact(row[key].strip())[:limit] for key, limit in
                (("title", 300), ("document_id", 128), ("version", 128), ("product_version", 128))
                if isinstance(row.get(key), str) and row[key].strip()}
        if not item.get("title"):
            continue
        # Sources are optional public document URLs; omit local paths and credentials.
        try:
            url = urlsplit(row.get("source", ""))
            if url.scheme in {"https", "http"} and url.hostname and not url.username and not url.password:
                item["source"] = redact(urlunsplit((url.scheme, url.netloc, url.path, "", "")))[:1000]
        except (ValueError, TypeError, AttributeError):
            pass
        identity = (item.get("document_id") or item["title"], item.get("version", ""), item.get("product_version", ""), item.get("source", ""))
        if identity in seen:
            continue
        seen.add(identity)
        sources.append(item)
        if len(sources) >= 20:
            break
    return sources


def sources_from_traces(traces):
    return knowledge_sources([
        source for trace in traces
        if trace.get("tool_name") == "search_knowledge_base"
        and trace.get("success") is True and trace.get("result_success") is True
        and isinstance(trace.get("knowledge_sources"), list)
        for source in trace["knowledge_sources"]
    ])
