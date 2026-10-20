import asyncio
from types import SimpleNamespace

import pytest

from agents.agent_orchestrator import (
    APIAgent, AgentOrchestrator, AgentType, EnterpriseAgent, OnboardingAgent, Request,
)
from agents.tools import lookup_error_code
from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel
from core.skill_loader import SkillManager
from tooling.openapi_import import openapi_documents


CASES = [
    ("API integration returns 401", IntentCategory.API_INTEGRATION, AgentType.API),
    ("接口对接失败", IntentCategory.API_INTEGRATION, AgentType.API),
    ("Explain this feature", IntentCategory.FEATURE_INQUIRY, AgentType.ONBOARDING),
    ("支持哪些功能", IntentCategory.FEATURE_INQUIRY, AgentType.ONBOARDING),
    ("Help me get started", IntentCategory.ONBOARDING, AgentType.ONBOARDING),
    ("新用户初始化配置", IntentCategory.ONBOARDING, AgentType.ONBOARDING),
    ("Change team permissions", IntentCategory.ENTERPRISE_MGMT, AgentType.ENTERPRISE),
    ("企业合同续费", IntentCategory.ENTERPRISE_MGMT, AgentType.ENTERPRISE),
    ("Cancel subscription", IntentCategory.SUBSCRIPTION, AgentType.BILLING),
    ("升级订阅套餐", IntentCategory.SUBSCRIPTION, AgentType.BILLING),
]


@pytest.mark.parametrize("message,intent,agent", CASES)
def test_bilingual_classification_and_routing(message, intent, agent):
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._pattern_recognize(message)["intent"] == intent
    orchestrator = AgentOrchestrator(api_key="test-only", model="test-model")
    req = Request(message, "u1", "c1", intent=intent, urgency=UrgencyLevel.LOW)
    assert orchestrator._route_decision(req).primary_agent == agent


def test_compound_api_subscription_and_fallback():
    orchestrator = AgentOrchestrator(api_key="test-only", model="test-model")
    req = Request("API request fails; also upgrade subscription", "u1", "c1",
                  intent=IntentCategory.API_INTEGRATION, urgency=UrgencyLevel.LOW)
    decision = orchestrator._route_decision(req)
    assert decision.primary_agent == AgentType.API
    assert AgentType.BILLING in decision.supporting_agents
    orchestrator._pool[AgentType.API][0].stats.set_penalty(0.9)
    assert orchestrator._route_decision(req).primary_agent != AgentType.API
    req.urgency = UrgencyLevel.CRITICAL
    assert orchestrator._route_decision(req).primary_agent == AgentType.ESCALATION


def test_new_agents_receive_skills_and_complete_mocked_turn():
    skills = SkillManager("skills")
    skills.load()

    class Client:
        class messages:
            @staticmethod
            async def create(**kwargs):
                assert "SaaSBridge" in kwargs["system"]
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Check the documented configuration.")],
                                       stop_reason="end_turn", usage=None)

    for cls in (APIAgent, OnboardingAgent, EnterpriseAgent):
        agent = cls(Client(), "test-model", skills)
        assert "SaaSBridge" in skills.prompt_for("help", agent.agent_type.value)
        response = asyncio.run(agent.handle(Request("help", "u1", "c1")))
        assert response.success
        assert response.agent_type == agent.agent_type
    assert "lookup_error_code" in APIAgent(Client(), "test-model").get_tools()


def test_saas_error_code_is_recognized():
    req = Request("quota exceeded", "u1", "c1")
    result = lookup_error_code(req, {"error_code": "QUOTA_EXCEEDED"})
    assert "配额" in str(result)


def test_openapi_contains_operations_security_and_components():
    spec = {
        "openapi": "3.1.0", "info": {"title": "Example", "version": "1"},
        "security": [{"bearerAuth": []}],
        "paths": {"/items": {"get": {"responses": {"200": {
            "description": "OK", "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Item"}}}}}}}},
        "components": {"schemas": {"Item": {"type": "object"}}},
    }
    docs = openapi_documents(spec)
    assert len(docs) == 3
    assert "GET /items" in docs[1]["title"]
    assert "bearerAuth" in docs[1]["content"]
    assert "#/components/schemas/Item" in docs[2]["title"]


@pytest.mark.parametrize("spec", [
    {"swagger": "2.0", "paths": {}},
    {"openapi": "3.0.0", "paths": {"/x": {"$ref": "https://example.com/api"}}},
    {"openapi": "3.0.0", "paths": {"/x": {"$ref": "#/missing"}}},
])
def test_openapi_rejects_unsupported_specs(spec):
    with pytest.raises(ValueError):
        openapi_documents(spec)
