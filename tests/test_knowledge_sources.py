from core.knowledge_sources import knowledge_sources, sources_from_traces


def test_sources_deduplicate_chunks_preserve_versions_and_exclude_content():
    row = {"title": "API guide", "document_id": "guide", "version": "v1", "content": "private body"}
    result = knowledge_sources([row, dict(row, chunk=2), dict(row, version="v2"), None])
    assert len(result) == 2
    assert "content" not in result[0] and "chunk" not in result[0]
    assert result[1]["version"] == "v2"


def test_sources_reject_paths_credentials_and_strip_query_secrets():
    assert "source" not in knowledge_sources([{"title":"guide", "source":"C:/private/file.md"}])[0]
    assert "source" not in knowledge_sources([{"title":"guide", "source":"https://user:pass@example.com/docs"}])[0]
    assert knowledge_sources([{"title":"guide", "source":"https://example.com/docs?token=secret#fragment"}])[0]["source"] == "https://example.com/docs"
    assert knowledge_sources(None) == []
    assert knowledge_sources([{"title": ""}]) == []


def test_only_successful_knowledge_calls_supply_references():
    valid={"tool_name":"search_knowledge_base", "success":True, "result_success":True, "knowledge_sources":[{"title":"guide"}]}
    assert sources_from_traces([dict(valid, success=False), dict(valid, result_success=False), dict(valid,tool_name="other")]) == []
    assert sources_from_traces([valid, valid]) == [{"title":"guide"}]
