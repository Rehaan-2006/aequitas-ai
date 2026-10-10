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
      '\y(court|case|decided|decide|opinion|appeal|appeals|appellant|appellee|plaintiff|defendant|state|united|states|v|vs)\y',
      '', 'gi'
    ) AS cleaned_text
  ),
  q AS (
    SELECT
      plainto_tsquery('english', cleaned_text) AS and_q,
      to_tsquery('english', replace(plainto_tsquery('english', cleaned_text)::text, ' & ', ' | ')) AS or_q
    FROM cleaned_query
  ),
  and_hits AS (
    SELECT cc.id, cc.case_id, cc.chunk_index, cc.chunk_text,
           ts_rank(cc.chunk_tsv, q.and_q)::float AS rank,
           c.case_name, c.citation, c.court, c.jurisdiction, c.decision_date
    FROM case_chunks cc
    CROSS JOIN q
    JOIN cases c ON c.id = cc.case_id
    WHERE numnode(q.and_q) > 0 AND cc.chunk_tsv @@ q.and_q
    ORDER BY rank DESC
    LIMIT match_count
  ),
  or_hits AS (
    SELECT cc.id, cc.case_id, cc.chunk_index, cc.chunk_text,
           ts_rank(cc.chunk_tsv, q.or_q)::float AS rank,
           c.case_name, c.citation, c.court, c.jurisdiction, c.decision_date
    FROM case_chunks cc
    CROSS JOIN q
    JOIN cases c ON c.id = cc.case_id
    WHERE NOT EXISTS (SELECT 1 FROM and_hits)
      AND numnode(q.or_q) > 0 AND cc.chunk_tsv @@ q.or_q
    ORDER BY rank DESC
    LIMIT match_count
  )
  SELECT * FROM and_hits
  UNION ALL
  SELECT * FROM or_hits
  ORDER BY rank DESC;
$$;