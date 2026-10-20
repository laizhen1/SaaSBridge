"""SaaS support regressions: local fakes only, no provider credentials required."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import fakeredis.aioredis
import httpx
import pytest
import yaml
from mcp.types import Tool as RemoteTool, ToolAnnotations

from agents.agent_orchestrator import APIAgent, AgentOrchestrator, AgentType, BillingAgent, EnterpriseAgent, EscalationAgent, OnboardingAgent, Request
from agents.tools import check_billing_fields, lookup_error_code, suggest_required_fields
from core.conversation_state import merge_state
from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel
from core.skill_loader import SkillManager
from memory.conversation_memory import MemoryManager
from tooling.mcp_client import MCPServerConfig, MCPRemoteError, StandardMCPClient, load_servers
from tooling.openapi_import import knowledge_documents, openapi_documents
from tooling.tool_manager import MCPToolManager
from test_knowledge_base import make_kb


@pytest.mark.parametrize("message,intent,agent", [
    ("SDK returns INSUFFICIENT_SCOPE", "api_integration", "api"),
    ("回调签名失败", "api_integration", "api"),
    ("Where are the release notes?", "feature_inquiry", "onboarding"),
    ("查看发布日志", "feature_inquiry", "onboarding"),
    ("Guide me through initial setup", "onboarding", "onboarding"),
    ("首次使用如何配置", "onboarding", "onboarding"),
    ("How do I invite members?", "enterprise_mgmt", "enterprise"),
    ("合同续签流程", "enterprise_mgmt", "enterprise"),
    ("How do I disable auto-renew?", "subscription", "billing"),
    ("取消自动续费", "subscription", "billing"),
    ("API error 401, I need a human agent", "human_handoff", "escalation"),
])
def test_saas_bilingual_fallback_and_routes(message, intent, agent):
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    actual = recognizer._pattern_recognize(message)["intent"]
    assert actual == IntentCategory(intent)
    orchestrator = AgentOrchestrator(api_key="test", model="test")
    request = Request(message, "u", "c", intent=actual, urgency=UrgencyLevel.LOW)
    assert orchestrator._route_decision(request).primary_agent == AgentType(agent)


def test_english_substrings_do_not_trigger_api_or_greeting():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._pattern_recognize("capitalization shipment")['intent'] == IntentCategory.OTHER
    orchestrator = AgentOrchestrator(api_key="test", model="test")
    request = Request("capitalization shipment", "u", "c", intent=IntentCategory.OTHER)
    assert orchestrator._domain_scores(request)[AgentType.API] == 0
    assert AgentType.API not in orchestrator._collaboration_targets(request)


def test_saas_entities_and_endpoint_query_redaction():
    message = ("tenant_id: org_demo workspace_id: ws_demo subscription_id: sub_demo "
               "invoice_id: inv_demo contract_id: contract_demo request_id: req_demo "
               "GET https://api.example.com/v1/items?api_key=private#secret "
               "POST /v1/tasks?token=hidden API version: v2.1 staging INSUFFICIENT_SCOPE API_KEY "
               "https://user:password@api.example.com/private")
    entities = IntentRecognizer.__new__(IntentRecognizer)._extract_entities(message)
    assert entities['tenant_id'] == ['org_demo']
    assert entities['subscription_id'] == ['sub_demo']
    assert entities['request_id'] == ['req_demo']
    assert entities['http_method'] == ['GET', 'POST']
    assert set(entities['endpoint']) == {'https://api.example.com/v1/items', '/v1/tasks'}
    assert entities['api_version'] == ['v2.1'] and entities['environment'] == ['staging']
    assert entities['error_code'] == ['INSUFFICIENT_SCOPE']
    assert all(secret not in json.dumps(entities) for secret in ('private', 'hidden', 'password', 'secret'))


@pytest.mark.parametrize('field', ['tenant_id', 'workspace_id', 'subscription_id'])
def test_changing_business_scope_clears_previous_fields(field):
    before = merge_state({}, {field: ['old'], 'invoice_id': ['inv_old']}, 'billing', '订阅查询', 'r1')
    after = merge_state(before, {field: ['new'], 'invoice_id': []}, 'billing', '查询新的', 'r2')
    assert not after['entities']['invoice_id']
    assert 'invoice_id' not in after['sources']


def test_same_scope_followup_keeps_fields_and_subscription_avoids_order_checklist():
    before = merge_state({}, {'tenant_id': ['org_demo'], 'subscription_id': ['sub_demo']}, 'billing', '订阅查询', 'r1')
    after = merge_state(before, {'request_id': ['req_demo']}, 'billing', '继续查', 'r2')
    req = Request('查询订阅', 'u', 'c', entities=after['entities'], intent=IntentCategory.SUBSCRIPTION)
    assert check_billing_fields(req, {})['missing_fields'] == []
    packet = json.loads(BillingAgent(None, 'test')._build_role_packet(req))
    assert 'order_id' not in packet['verification_fields']['fields']
    assert suggest_required_fields(req, {})['required_fields'] == []


@pytest.mark.parametrize('code', ['WEBHOOK_SIGNATURE_INVALID', 'IDEMPOTENCY_CONFLICT', 'API_VERSION_UNSUPPORTED', '422', '503'])
def test_new_error_codes_are_guidance_not_account_verification(code):
    result = lookup_error_code(Request('help', 'u', 'c'), {'error_code': code})
    assert result['known_code'] and not result['server_log_checked']
    assert result['source'] == 'generic_support_catalog'


def test_unknown_error_code_remains_unknown():
    assert not lookup_error_code(Request('help', 'u', 'c'), {'error_code': 'UNRECOGNIZED'})['known_code']


def test_skills_automatically_refresh_add_edit_delete_and_invalid_file(tmp_path):
    manager = SkillManager(str(tmp_path))
    assert manager.prompt_for('help', 'api') == ''
    path = tmp_path / 'api.json'
    path.write_text(json.dumps({'name': 'API rules', 'agents': ['api'], 'content': 'First version'}))
    assert 'First version' in manager.prompt_for('help', 'api')
    assert manager.prompt_for('help', 'billing') == ''
    path.write_text(json.dumps({'name': 'API rules', 'agents': ['api'], 'content': 'Updated rule text'}))
    assert 'Updated rule text' in manager.prompt_for('help', 'api')
    assert 'First version' not in manager.prompt_for('help', 'api')
    path.write_text('{invalid')
    assert manager.prompt_for('help', 'api') == ''
    assert manager.errors
    path.unlink()
    assert manager.prompt_for('help', 'api') == '' and not manager.errors


def test_role_skills_fit_budget_and_precede_keyword_playbooks():
    manager = SkillManager('skills', max_prompt_chars=2000)
    prompt = manager.prompt_for('取消订阅退款', 'billing')
    assert len(prompt) <= 2000
    assert '通用政策咨询先查知识库' in prompt
    assert 'SaaSBridge SaaS subscriptions' in prompt


def test_prometheus_alert_rules_match_exposed_histogram_and_compose_mount():
    config = yaml.safe_load(Path('config/alerts/saasbridge.yml').read_text(encoding='utf-8'))
    rules = config['groups'][0]['rules']
    assert len(rules) == 3
    assert all(rule['for'] and rule['annotations']['summary'] for rule in rules)
    assert 'chat_latency_ms_bucket' in rules[2]['expr'] and '> 30000' in rules[2]['expr']
    compose = yaml.safe_load(Path('compose.app.yaml').read_text(encoding='utf-8'))
    assert './config/alerts:/etc/prometheus/alerts:ro' in compose['services']['prometheus']['volumes']


def spec():
    return {'openapi': '3.1.0', 'info': {'title': 'Example API', 'version': '1.0'},
            'servers': [{'url': 'https://root.example.com'}], 'security': [{'bearer': []}],
            'paths': {'/items': {'servers': [{'url': 'https://path.example.com'}],
                                'get': {'responses': {'200': {'description': 'OK'}}},
                                'post': {'servers': [{'url': 'https://operation.example.com'}], 'security': [], 'responses': {'201': {'description': 'Created'}}}}}}


def test_openapi_inheritance_version_and_repeatable_identity():
    docs = openapi_documents(spec())
    get, post = (json.loads(doc['content']) for doc in docs[1:])
    assert get['servers'][0]['url'] == 'https://path.example.com'
    assert post['servers'][0]['url'] == 'https://operation.example.com'
    assert get['security'] == [{'bearer': []}] and post['security'] == []
    assert docs[1]['product_version'] == '1.0' and docs[1]['doc_type'] == 'openapi'
    assert docs == openapi_documents(spec())
    changed = spec()
    changed['info']['version'] = '2.0'
    assert openapi_documents(changed)[1]['document_id'] != docs[1]['document_id']


def test_local_path_ref_is_resolved_but_circular_and_remote_refs_rejected():
    data = spec()
    data['paths']['/alias'] = {'$ref': '#/paths/~1items'}
    assert any('GET /alias' in doc['title'] for doc in openapi_documents(data))
    for ref in ('#/paths/~1alias', 'https://example.com/spec', '#/missing'):
        data['paths']['/alias'] = {'$ref': ref}
        with pytest.raises(ValueError):
            openapi_documents(data)


@pytest.mark.parametrize('text,suffix', [
    ('x: &x {child: *x}', '.yaml'), ('!!python/object/apply:os.system [echo blocked]', '.yaml'),
    ('not json', '.json'), ('[]', '.yaml'), ('{"documents": []}', '.json'),
])
def test_invalid_knowledge_inputs_fail_before_import(text, suffix):
    with pytest.raises(ValueError):
        knowledge_documents(text, 'input' + suffix)


def test_chunking_preserves_openapi_code_and_metadata():
    kb = make_kb()
    docs = openapi_documents(spec())
    docs[1]['content'] += '\n' + 'GET /v1/items\nAuthorization: Bearer <TOKEN>\n' * 30
    kb.add_documents(docs)
    rows = [(text, meta) for text, meta in kb._collection.rows.values() if meta['title'] == docs[1]['title']]
    assert ''.join(text for text, meta in sorted(rows, key=lambda row: row[1]['chunk_index'])) == docs[1]['content']
    assert all(len(text) <= 500 and meta['product_version'] == '1.0' for text, meta in rows)
    before = len(kb._collection.rows)
    kb.add_documents(docs)
    assert len(kb._collection.rows) == before


@pytest.mark.parametrize('suffix', ['.json', '.yaml'])
def test_openapi_http_upload_and_validation(monkeypatch, suffix):
    async def run():
        import api.main as api
        kb = make_kb()
        kb.doc_count_async = AsyncMock(side_effect=lambda: len(kb._collection.rows))
        manager = SimpleNamespace(_tools={'knowledge_search': SimpleNamespace(handler=kb.search_handler)}, invalidate_cache=Mock())
        monkeypatch.setattr(api, '_tool_manager', manager)
        monkeypatch.setattr(api, '_eval_tool_manager', None)
        monkeypatch.setenv('ECHOMIND_ENV', 'development')
        monkeypatch.delenv('ECHOMIND_API_TOKENS', raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://test') as client:
            encoded = json.dumps(spec()) if suffix == '.json' else yaml.safe_dump(spec())
            response = await client.post('/knowledge/upload', files={'file': ('openapi' + suffix, encoded)})
            assert response.status_code == 200, response.text
            assert response.json()['imported_documents'] == 3
            manager.invalidate_cache.assert_called_with('knowledge_search')
            before = len(kb._collection.rows)
            response = await client.post('/knowledge/upload', files={'file': ('bad.json', b'\xff')})
            assert response.status_code == 400 and len(kb._collection.rows) == before
            response = await client.post('/knowledge/add', json={'documents': [dict(title='notes', content='Release info', doc_type='release_notes', source='release.md', product_version='1.0')]})
            assert response.status_code == 200
            assert any(meta.get('doc_type') == 'release_notes' for _, meta in kb._collection.rows.values())
    asyncio.run(run())


def test_mcp_business_context_allowlist_and_readonly_policy():
    async def run():
        manager = MCPToolManager(api_key='test')
        config = MCPServerConfig(name='business', transport='streamable_http', url='https://example.com/mcp',
                                 allowed_tools=['get_account', 'create_ticket'], agents=['enterprise'],
                                 require_user_context=True, cache_ttl=0)
        client = StandardMCPClient(manager, [config])
        connection = client.connections['business']
        session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(isError=False, content=[], structuredContent={'account': 'org_demo'})))
        connection.session = session
        schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
        client._register_catalog(connection, [RemoteTool(name='get_account', inputSchema=schema, annotations=ToolAnnotations(readOnlyHint=True)),
                                              RemoteTool(name='create_ticket', inputSchema=schema, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))])
        alias = 'mcp_business_get_account'
        enterprise = EnterpriseAgent(None, 'test')
        enterprise.set_shared_tools(client.agent_tools())
        onboarding = OnboardingAgent(None, 'test')
        onboarding.set_shared_tools(client.agent_tools())
        assert alias in enterprise.get_tools() and alias not in onboarding.get_tools()
        assert 'mcp_business_create_ticket' not in client.agent_tools()
        try:
            with pytest.raises(MCPRemoteError):
                await client._call_remote('business', 'get_account', {})
            session.call_tool.assert_not_called()
            response = await enterprise.get_tools()[alias].handler(Request('account', 'verified-user', 'c'), {})
            assert response['success']
            session.call_tool.assert_awaited_once_with('get_account', {}, meta={'saasbridge/user_id': 'verified-user'})
        finally:
            await client.close()
            await manager._client.close()
    asyncio.run(run())


def test_mcp_saas_examples_are_disabled_and_new_agent_types_are_valid():
    assert load_servers(Path('config/mcp_saas.example.json')) == []
    for entry in json.loads(Path('config/mcp_saas.example.json').read_text())['servers']:
        entry.pop('enabled')
        MCPServerConfig(**entry)


def test_working_memory_ttl_keeps_receipt_retention_independent(monkeypatch):
    async def run():
        monkeypatch.setenv('ECHOMIND_WORKING_MEMORY_TTL_SECONDS', '7200')
        memory = MemoryManager.__new__(MemoryManager)
        memory.working_ttl = memory.configured_working_ttl()
        memory._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        memory._migrate_legacy_keys = AsyncMock()
        try:
            await memory.commit_turn('u', 'c', 'r', 'hello', {'status': 'success', 'response': 'hi'}, state={'domain': 'account'})
            assert 7198 <= await memory._redis.ttl(memory._wm_key('u', 'c')) <= 7200
            assert 7198 <= await memory._redis.ttl(memory._state_key('u', 'c')) <= 7200
            assert 86398 <= await memory._redis.ttl(memory.receipt_key('u', 'c', 'r')) <= 86400
        finally:
            await memory._redis.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('value', ['0', '-1', '1000000', 'not a number'])
def test_invalid_memory_ttl_fails_early(monkeypatch, value):
    monkeypatch.setenv('ECHOMIND_WORKING_MEMORY_TTL_SECONDS', value)
    with pytest.raises(ValueError):
        MemoryManager.configured_working_ttl()


def test_english_handoff_is_honest_about_missing_ticket_queue():
    result = asyncio.run(EscalationAgent(None, 'test').handle(Request('human agent please', 'u', 'c')))
    assert 'no ticket has been created' in result.content and result.escalate
