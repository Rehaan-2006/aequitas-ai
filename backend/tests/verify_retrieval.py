import os
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from supabase import create_client, Client

# Load environment variables
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

assert SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, "Missing SUPABASE environment variables"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

print("Loading BAAI/bge-base-en-v1.5 model...")
embedder = SentenceTransformer("BAAI/bge-base-en-v1.5")

# BGE official retrieval instruction prefix
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

def test_vector_search():
    raw_query = "Fourth amendment unreasonable search and seizure of vehicle without warrant"
    # Prefix only applied to the query side
    instructed_query = f"{BGE_QUERY_PREFIX}{raw_query}"
    
    print(f"\nExecuting query: '{raw_query}'")
    print(f"Encoded with instruction: '{instructed_query}'")
    
    query_vector = embedder.encode(instructed_query, normalize_embeddings=True).tolist()
    
    response = supabase.rpc(
        "match_case_chunks",
        {
            "query_embedding": query_vector,
            "match_threshold": 0.3,
            "match_count": 3
        }
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