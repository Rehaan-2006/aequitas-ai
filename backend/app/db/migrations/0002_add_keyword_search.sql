-- Migration: Add full-text search for the Hybrid Retrieval Agent's sparse branch
-- Purpose: keyword_search_case_chunks operates at the same case_chunks
-- granularity as match_case_chunks so dense and sparse rankings can be
-- fused directly (RRF) without a case-vs-chunk granularity mismatch.
-- Note: deviates from the original brief's "to_tsvector('english', raw_text)"
-- by indexing case_chunks.chunk_text instead of cases.raw_text -- see
-- docs/DECISIONS.md for why.

CREATE INDEX IF NOT EXISTS idx_case_chunks_chunk_text_fts
ON case_chunks USING gin (to_tsvector('english', chunk_text));

CREATE OR REPLACE FUNCTION keyword_search_case_chunks (
  query_text TEXT,
  match_count INT DEFAULT 20
)
RETURNS TABLE (
  id UUID,
  case_id UUID,
  chunk_index INT,
  chunk_text TEXT,
  rank FLOAT,
  case_name TEXT,
  citation TEXT,
  court TEXT,
  jurisdiction TEXT,
  decision_date DATE
)
LANGUAGE sql STABLE
AS $$
  SELECT
    case_chunks.id,
    case_chunks.case_id,
    case_chunks.chunk_index,
    case_chunks.chunk_text,
    ts_rank(to_tsvector('english', case_chunks.chunk_text), plainto_tsquery('english', query_text)) AS rank,
    cases.case_name,
    cases.citation,
    cases.court,
    cases.jurisdiction,
    cases.decision_date
  FROM case_chunks
  JOIN cases ON cases.id = case_chunks.case_id
  WHERE to_tsvector('english', case_chunks.chunk_text) @@ plainto_tsquery('english', query_text)
  ORDER BY rank DESC
  LIMIT match_count;
$$;
