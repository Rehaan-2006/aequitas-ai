CREATE OR REPLACE FUNCTION keyword_search_case_chunks (
  query_text TEXT,
  match_count INT DEFAULT 20
)
RETURNS TABLE (
  id UUID, case_id UUID, chunk_index INT, chunk_text TEXT, rank FLOAT,
  case_name TEXT, citation TEXT, court TEXT, jurisdiction TEXT, decision_date DATE
)
LANGUAGE sql STABLE
AS $$
  WITH cleaned_query AS (
    SELECT regexp_replace(
      query_text,
      '\y(court|case|decided|decide|opinion|appeal|appeals|appellant|appellee|plaintiff|defendant|state|united|states)\y',
      '',
      'gi'
    ) AS cleaned_text
  ),
  parsed_query AS (
    SELECT to_tsquery(
      'english',
      replace(plainto_tsquery('english', cleaned_query.cleaned_text)::text, ' & ', ' | ')
    ) AS tsq
    FROM cleaned_query
  )
  SELECT
    case_chunks.id, case_chunks.case_id, case_chunks.chunk_index, case_chunks.chunk_text,
    ts_rank(to_tsvector('english', case_chunks.chunk_text), parsed_query.tsq) AS rank,
    cases.case_name, cases.citation, cases.court, cases.jurisdiction, cases.decision_date
  FROM case_chunks
  CROSS JOIN parsed_query
  JOIN cases ON cases.id = case_chunks.case_id
  WHERE parsed_query.tsq IS NOT NULL
    AND to_tsvector('english', case_chunks.chunk_text) @@ parsed_query.tsq
  ORDER BY rank DESC
  LIMIT match_count;
$$;