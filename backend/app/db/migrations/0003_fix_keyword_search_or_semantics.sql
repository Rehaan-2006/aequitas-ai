-- Migration: Fix AND-only semantics in keyword_search_case_chunks
-- Purpose: plainto_tsquery joins every term with & (AND), so a natural
-- multi-word legal question (e.g. "Fourth amendment unreasonable search
-- and seizure of vehicle without warrant") requires ALL ~10 words to
-- appear verbatim in a single chunk, which almost never happens -- the
-- sparse branch was returning 0 rows for realistic queries while working
-- fine for short 3-4 word literal queries. See docs/DECISIONS.md.
--
-- Fix: convert plainto_tsquery's AND-joined output to OR-joined by
-- replacing '&' with '|' before re-parsing as a tsquery. Keeps
-- plainto_tsquery's stemming/stopword normalization; ts_rank still
-- scores chunks matching more terms higher, so this doesn't flatten
-- ranking quality -- it just stops requiring every term to match.

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
  WITH parsed_query AS (
    SELECT to_tsquery(
      'english',
      replace(plainto_tsquery('english', query_text)::text, ' & ', ' | ')
    ) AS tsq
  )
  SELECT
    case_chunks.id,
    case_chunks.case_id,
    case_chunks.chunk_index,
    case_chunks.chunk_text,
    ts_rank(to_tsvector('english', case_chunks.chunk_text), parsed_query.tsq) AS rank,
    cases.case_name,
    cases.citation,
    cases.court,
    cases.jurisdiction,
    cases.decision_date
  FROM case_chunks
  CROSS JOIN parsed_query
  JOIN cases ON cases.id = case_chunks.case_id
  WHERE to_tsvector('english', case_chunks.chunk_text) @@ parsed_query.tsq
  ORDER BY rank DESC
  LIMIT match_count;
$$;
