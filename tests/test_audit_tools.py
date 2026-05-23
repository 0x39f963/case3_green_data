from app import audit_tools
from app import llm_provider
from app.llm_provider import LLMResponse


def _labels(items):
    return {item["label"] for item in items}


def test_audit_tools_smoke_labels():
    cases = [
        (audit_tools.check_statement_boundary, "SELECT 1; DROP TABLE x", {"MULTI_STATEMENT"}),
        (audit_tools.check_classic_sqli, "SELECT * FROM sys_employee WHERE 1=1 OR 1=1", {"TAUTOLOGY"}),
        (audit_tools.check_plpgsql, "DO $$ BEGIN EXECUTE 'SELECT ' || x; END $$;", {"PLPGSQL_UNSAFE"}),
        (audit_tools.check_mutation, "DELETE FROM scp_application", {"DML_NO_WHERE"}),
        (audit_tools.check_data_exposure, "SELECT email FROM sys_employee", {"DIRECT_SENSITIVE"}),
        (audit_tools.check_reliability, "SELECT a.*, b.* FROM t1 a, t2 b", {"CROSS_JOIN_EXPLOSION"}),
        (audit_tools.check_generation_quality, "SELECT * FROM missing_table", {"HALLUCINATED_TABLE"}),
    ]
    for tool_obj, sql, expected in cases:
        actual = _labels(tool_obj.invoke({"sql": sql, "ctx": {}}))
        assert expected <= actual


def test_judge_semantic_correction_delegates(monkeypatch):
    class FakeClient:
        def invoke(self, system, user, temperature=None):
            return LLMResponse(
                text='{"findings":[{"label":"DIRECT_SENSITIVE","severity":6,'
                     '"confidence":0.9,"evidence_span":"email",'
                     '"revision_note":"mask email"}]}',
                model="fake",
                backend="fake",
                raw={"temperature": temperature},
            )

    monkeypatch.setattr(llm_provider, "get_llm", lambda role: FakeClient())
    items = audit_tools.judge_semantic_correction.invoke(
        {
            "sql": "SELECT email FROM sys_employee",
            "ctx": {
                "task": "посчитай сотрудников",
                "schema_context": "sys_employee email",
                "sensitive_fields": {"sys_employee": ["email"]},
                "allowed_tables": ["sys_employee"],
            },
        }
    )
    assert [item["label"] for item in items] == ["DIRECT_SENSITIVE"]
