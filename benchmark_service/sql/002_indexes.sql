CREATE INDEX IF NOT EXISTS pipeline_runs_benchmark_run_idx
    ON benchmark.pipeline_runs(benchmark_run_id);

CREATE INDEX IF NOT EXISTS pipeline_runs_case_id_idx
    ON benchmark.pipeline_runs(case_id);

CREATE INDEX IF NOT EXISTS pipeline_runs_model_key_idx
    ON benchmark.pipeline_runs(model_key);

CREATE INDEX IF NOT EXISTS pipeline_runs_decision_idx
    ON benchmark.pipeline_runs(decision);

CREATE INDEX IF NOT EXISTS pipeline_runs_created_at_idx
    ON benchmark.pipeline_runs(created_at);

CREATE INDEX IF NOT EXISTS pipeline_steps_trace_step_idx
    ON benchmark.pipeline_steps(trace_id, step_index);

CREATE INDEX IF NOT EXISTS llm_calls_trace_id_idx
    ON benchmark.llm_calls(trace_id);

CREATE INDEX IF NOT EXISTS llm_calls_model_idx
    ON benchmark.llm_calls(model);

CREATE INDEX IF NOT EXISTS llm_calls_generation_id_idx
    ON benchmark.llm_calls(generation_id)
    WHERE generation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS findings_trace_id_idx
    ON benchmark.findings(trace_id);

CREATE INDEX IF NOT EXISTS findings_label_idx
    ON benchmark.findings(label);

CREATE INDEX IF NOT EXISTS faiss_hits_trace_id_idx
    ON benchmark.faiss_hits(trace_id);

CREATE INDEX IF NOT EXISTS faiss_hits_vuln_class_idx
    ON benchmark.faiss_hits(vuln_class);

CREATE INDEX IF NOT EXISTS faiss_hits_table_name_idx
    ON benchmark.faiss_hits(table_name);

CREATE INDEX IF NOT EXISTS raw_payloads_ingested_at_idx
    ON benchmark.raw_payloads(ingested_at);
