"""Mock LLM provider: runs the whole workflow offline with rule-based output.

It implements the same tasks as a real provider and returns JSON text that
goes through the same parsing and schema validation, so the pipeline is
exercised end to end.  ``simulated`` is True, and that flag is stored with
every analysis and policy and shown in the UI and in generated documents.
"""

from __future__ import annotations

import json

from app.services.ai_analysis.providers import mock_analysis, mock_policy
from app.services.ai_analysis.providers.base import LLMError, LLMProvider, LLMRequest, LLMResponse

MOCK_MODEL = "mock-rule-engine"

_TASKS = {
    "extract_findings": mock_analysis.extract_findings,
    "consolidate": mock_analysis.consolidate,
    "generate_policy": mock_policy.generate_policy,
    "revise_sections": mock_policy.revise_sections,
}


class MockProvider(LLMProvider):
    name = "mock"
    display_name = "Mock mode"
    model = MOCK_MODEL
    simulated = True

    def complete(self, request: LLMRequest) -> LLMResponse:
        handler = _TASKS.get(request.task)
        if handler is None:
            raise LLMError(f"The mock provider does not support the task '{request.task}'.", code="llm_unsupported_task")
        result = handler(request.payload)
        return LLMResponse(text=json.dumps(result, ensure_ascii=False), model=self.model, simulated=True)
