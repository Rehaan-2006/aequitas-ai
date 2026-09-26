import pytest


@pytest.mark.integration
def test_vector_search():
    from app.core.config import settings
    from app.db.supabase_client import get_supabase_client
    from app.services.embedding_service import embed_query

    assert settings.supabase_url and settings.supabase_service_role_key, "Missing SUPABASE environment variables"
    supabase = get_supabase_client()

    raw_query = "Fourth amendment unreasonable search and seizure of vehicle without warrant"
    print(f"Executing query: '{raw_query}'")

    query_vector = embed_query(raw_query).tolist()

    response = supabase.rpc(
        "match_case_chunks",
        {
            "query_embedding": query_vector,
            "match_threshold": settings.retrieval_match_threshold,
            "match_count": settings.retrieval_match_count,
        },
    ).execute()

    results = response.data
    assert len(results) > 0, "No chunks returned. Did you run the SQL migration or ingest cases?"

    print(f"\nSuccessfully retrieved {len(results)} chunks!\n" + "=" * 60)
    for idx, r in enumerate(results, 1):
        print(f"[{idx}] Cosine Similarity: {r['similarity']:.4f}")
        print(f"    Case:     {r['case_name']}")
        print(f"    Citation: {r['citation']}")
        print(f"    Court:    {r['court']}")
        print(f"    Snippet:  {r['chunk_text'][:160]}...\n")


if __name__ == "__main__":
    test_vector_search()
