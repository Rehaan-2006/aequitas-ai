-- Enable vector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Cases Table
CREATE TABLE IF NOT EXISTS cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    citation TEXT NOT NULL UNIQUE,
    case_name TEXT NOT NULL,
    court TEXT,
    jurisdiction TEXT,
    decision_date DATE,
    is_overruled BOOLEAN DEFAULT false,
    overruled_by UUID REFERENCES cases(id),
    raw_text TEXT NOT NULL,
    source TEXT DEFAULT 'CAP',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Case Chunks Table
CREATE TABLE IF NOT EXISTS case_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding VECTOR(768),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- HNSW Vector Index
CREATE INDEX IF NOT EXISTS case_chunks_embedding_hnsw_idx 
ON case_chunks USING hnsw (embedding vector_cosine_ops);

-- Case Citations Graph Table
CREATE TABLE IF NOT EXISTS case_citations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    citing_case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
    cited_case_id UUID REFERENCES cases(id) ON DELETE SET NULL,
    cited_citation_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Relational Indexes
CREATE INDEX IF NOT EXISTS idx_case_chunks_case_id ON case_chunks (case_id);
CREATE INDEX IF NOT EXISTS idx_case_citations_citing ON case_citations (citing_case_id);
CREATE INDEX IF NOT EXISTS idx_case_citations_cited ON case_citations (cited_case_id);

-- Match Chunks RPC Function
CREATE OR REPLACE FUNCTION match_case_chunks (
  query_embedding VECTOR(768),
  match_threshold FLOAT DEFAULT 0.5,
  match_count INT DEFAULT 5
)
RETURNS TABLE (
  id UUID,
  case_id UUID,
  chunk_index INT,
  chunk_text TEXT,
  similarity FLOAT,
  case_name TEXT,
  citation TEXT,
  court TEXT,
  decision_date DATE
)
LANGUAGE sql STABLE
AS $$
  SELECT
    case_chunks.id,
    case_chunks.case_id,
    case_chunks.chunk_index,
    case_chunks.chunk_text,
    1 - (case_chunks.embedding <=> query_embedding) AS similarity,
    cases.case_name,
    cases.citation,
    cases.court,
    cases.decision_date
  FROM case_chunks
  JOIN cases ON cases.id = case_chunks.case_id
  WHERE 1 - (case_chunks.embedding <=> query_embedding) > match_threshold
  ORDER BY case_chunks.embedding <=> query_embedding
  LIMIT match_count;
$$;

-- Full-Text Search Index (sparse retrieval branch, Module 3)
CREATE INDEX IF NOT EXISTS idx_case_chunks_chunk_text_fts
ON case_chunks USING gin (to_tsvector('english', chunk_text));

-- Keyword Search RPC Function (Module 3 sparse branch)
-- Uses OR-joined query semantics (see migration 0003) -- plainto_tsquery's
-- default AND join requires every term to match, which fails realistic
-- multi-word legal queries.
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
