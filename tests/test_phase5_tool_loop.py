from app import generator as generator_module
from app import llm_provider
from app import tool_loop
from app.llm_provider import LLMResponse


class FakeToolClient:
    supports_tools = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, system, user, temperature=None, tools=None, tool_choice=None, messages_override=None):
        self.calls.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "tools": tools,
                "tool_choice": tool_choice,
                "messages_override": list(messages_override or []),
            }
        )
        return self.responses.pop(0)


def test_tool_loop_two_step_proxies_tool_call_id(monkeypatch):
    first = LLMResponse(
        text="",
        model="fake",
        backend="fake",
        raw={"id": "step-1"},
        tool_calls=[{"id": "call-123", "name": "check_hallucination", "arguments": {"sql": "SELECT 1"}}],
    )
    second = LLMResponse(text="SELECT 1", model="fake", backend="fake", raw={"id": "step-2"})
    client = FakeToolClient([first, second])
    monkeypatch.setattr(tool_loop.tools_module, "dispatch", lambda name, args: {"ok": True, "tool": name})

    result = tool_loop.run_with_tools(client, "system", "user", tool_specs=[], max_steps=3)

    assert result["final_text"] == "SELECT 1"
    assert result["tool_calls_total"] == 1
    assert result["messages_final"][3]["tool_call_id"] == "call-123"
    assert client.calls[1]["messages_override"][3]["tool_call_id"] == "call-123"
    assert result["tool_compliance"]["ok"] is False
    assert "get_sensitive_fields" in result["tool_compliance"]["missing_tools"]


def test_tool_loop_zero_tool_final_text_marks_non_compliant():
    client = FakeToolClient([
        LLMResponse(text="SELECT 1", model="fake", backend="fake", raw={"id": "final"}),
    ])

    result = tool_loop.run_with_tools(client, "system", "user", tool_specs=[], max_steps=3)

    assert result["stop_reason"] == "no_tools_called"
    assert result["tool_compliance"]["ok"] is False
    assert result["tool_compliance"]["called_tools"] == []
    assert result["tool_compliance"]["missing_tools"] == [
        "check_hallucination",
        "get_sensitive_fields",
        "explain_query",
    ]


def test_tool_loop_cycle_detection_same_args(monkeypatch):
    tool_call = {"id": "call-1", "name": "check_hallucination", "arguments": {"sql": "SELECT 1"}}
    client = FakeToolClient([
        LLMResponse(text="", model="fake", backend="fake", raw={"id": "step-1"}, tool_calls=[tool_call]),
        LLMResponse(text="", model="fake", backend="fake", raw={"id": "step-2"}, tool_calls=[tool_call]),
        LLMResponse(text="SELECT 1", model="fake", backend="fake", raw={"id": "step-3"}),
    ])
    monkeypatch.setattr(tool_loop.tools_module, "dispatch", lambda name, args: {"ok": True})

    result = tool_loop.run_with_tools(client, "system", "user", tool_specs=[], max_steps=5)

    assert result["stop_reason"] == "no_tool_call_loop"
    assert len(client.calls) == 2
    assert result["tool_compliance"]["ok"] is False
    assert "cycle_detected" in result["tool_compliance"]["degraded_reason"]


def test_generator_tool_mode_degrades_for_client_without_tools(monkeypatch):
    class TextOnlyClient:
        supports_tools = False
        backend = "anthropic_cli"
        model = "fake-cli"

        def invoke(self, system, user, temperature=None):
            return LLMResponse(
                text="SELECT id FROM sys_employee LIMIT 10;",
                model=self.model,
                backend=self.backend,
                raw={"id": "cli-1"},
                walltime_sec=0.01,
            )

    monkeypatch.setenv("GENERATOR_TOOL_MODE", "true")
    monkeypatch.setenv("LLM_MULTI_CANDIDATE", "false")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: TextOnlyClient())

    gen = generator_module.SQLGenerator(db_schema={})
    sql = gen.generate("Покажи сотрудников", generation_context="sys_employee(id)", allowed_objects="sys_employee")

    assert sql == "SELECT id FROM sys_employee LIMIT 10;"
    assert gen.last_call["tool_mode"] is False
    assert gen.last_call["tool_mode_degraded"] is True
    assert gen.last_call["degraded_reason"] == "client_without_tool_support"
