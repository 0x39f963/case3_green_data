-- TZ-4 follow-up F-7/F-8: separate numeric risk_score from textual severity,
-- and store evidence spans as an array instead of repr-stringified text.
ALTER TABLE benchmark.findings
    ADD COLUMN IF NOT EXISTS risk_score NUMERIC,
    ADD COLUMN IF NOT EXISTS evidence_spans TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

-- Backfill: any existing severity value that parses as a number becomes the
-- numeric risk_score; the textual severity column stays for backward compat
-- but new ingests normalize it through the parser.
UPDATE benchmark.findings
SET risk_score = CASE
        WHEN severity ~ '^-?[0-9]+(\.[0-9]+)?$' THEN severity::numeric
        ELSE risk_score
    END
WHERE risk_score IS NULL;

-- Backfill arrays from the legacy single-line evidence_span when it looks like
-- a python repr of a list or a newline-joined string.
UPDATE benchmark.findings
SET evidence_spans = string_to_array(evidence_span, E'\n')
WHERE cardinality(evidence_spans) = 0
  AND evidence_span IS NOT NULL
  AND evidence_span <> '';

CREATE INDEX IF NOT EXISTS findings_risk_score_idx
    ON benchmark.findings (risk_score)
    WHERE risk_score IS NOT NULL;
