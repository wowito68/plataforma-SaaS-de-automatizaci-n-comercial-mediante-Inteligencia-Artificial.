import json
import logging
from uuid import uuid4

from saas_platform.observability import JsonFormatter, bind_context, redact, reset_context


def test_redaction_removes_nested_secrets() -> None:
    value = {
        "adapter_token": "secret-value",
        "nested": [{"client_secret": "hidden"}],
        "safe": "ok",
    }

    assert redact(value) == {
        "adapter_token": "[REDACTED]",
        "nested": [{"client_secret": "[REDACTED]"}],
        "safe": "ok",
    }


def test_json_formatter_includes_bound_trace_context() -> None:
    request_id = uuid4()
    tenant_id = uuid4()
    bound = bind_context(request_id=request_id, tenant_id=tenant_id)
    try:
        record = logging.LogRecord("test.module", logging.INFO, __file__, 1, "handled", (), None)
        record.operation = "test.operation"
        payload = json.loads(JsonFormatter(environment="test", service="test").format(record))
    finally:
        reset_context(bound)

    assert payload["request_id"] == str(request_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["operation"] == "test.operation"


def test_json_formatter_includes_safe_ai_metadata_without_sensitive_payloads() -> None:
    record = logging.LogRecord(
        "test.extraction",
        logging.INFO,
        __file__,
        1,
        "structured extraction finished",
        (),
        None,
    )
    record.execution_id = uuid4()
    record.provider = "openai"
    record.model = "gpt-test"
    record.prompt_version = "telecom-extraction.v1"
    record.schema_version = "telecom-extraction-result.v1"
    record.duration_ms = 42
    record.outcome = "completed"
    record.input_tokens = 100
    record.output_tokens = 50
    record.prompt = "never log me"
    record.message_content = "private conversation"

    payload = json.loads(JsonFormatter(environment="test", service="test").format(record))

    assert payload["provider"] == "openai"
    assert payload["duration_ms"] == 42
    assert payload["input_tokens"] == 100
    assert "prompt" not in payload
    assert "message_content" not in payload
    assert "private conversation" not in json.dumps(payload)
