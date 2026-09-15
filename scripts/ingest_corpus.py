import os
import re
from datasets import load_dataset
from sentence_transformers import SentenceTransformer
from supabase import create_client, Client
from dotenv import load_dotenv
from dateutil import parser
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Load environment variables
load_dotenv()
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# Configuration
TARGET_COURTS = ["Fifth Circuit", "Ninth Circuit"]
TARGET_COUNT = 1800  # Change to 1800 after the test run is successful
CHUNK_SIZE_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200
MIN_TEXT_LENGTH = 2000

# Initialize Clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
print("Loading 768-dimension embedding model (BAAI/bge-base-en-v1.5)...")
embedder = SentenceTransformer("BAAI/bge-base-en-v1.5")
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_CHARS,
    chunk_overlap=CHUNK_OVERLAP_CHARS,
    separators=["\n\n", "\n", ".", " ", ""]
)

def parse_case_header(text):
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    case_name = lines[0] if lines else None
    if case_name and len(case_name) < 15 and len(lines) > 1:
        case_name = f"{case_name} {lines[1]}"

    docket_match = re.search(r"No\.\s*([\d\-]+)", text[:500])
    court_match = re.search(r"(United States Court of Appeals[^\n.]*|Supreme Court[^\n.]*|District Court[^\n.]*)", text[:500])
    date_match = re.search(r"([A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})", text[:500])

    return {
        "case_name": case_name,
        "docket_number": docket_match.group(1) if docket_match else None,
        "court": court_match.group(1).strip() if court_match else None,
        "decision_date_raw": date_match.group(1) if date_match else None,
    }

def is_valid_case(parsed, text):
    if not parsed["docket_number"] or not parsed["court"] or not parsed["case_name"]:
        return False
    if len(text) < MIN_TEXT_LENGTH:
        return False
    return any(target in parsed["court"] for target in TARGET_COURTS)

def build_synthetic_citation(parsed):
    return f"{parsed['docket_number']} ({parsed['court']} {parsed['decision_date_raw']})"

def pull_subset():
    print("Streaming dataset from Hugging Face...")
    ds = load_dataset("common-pile/caselaw_access_project", split="train", streaming=True)
    collected = []
    
    for row in ds:
        parsed = parse_case_header(row["text"])
        if not is_valid_case(parsed, row["text"]):
            continue
            
        collected.append({
            "parsed": parsed,
            "text": row["text"],
        })
        
        if len(collected) >= TARGET_COUNT:
            break
        if len(collected) % 5 == 0:
            print(f"Collected {len(collected)}/{TARGET_COUNT} valid cases...")
            
    return collected

def insert_case(parsed, text):
    citation = build_synthetic_citation(parsed)
    
    # Fuzzy Date Parsing
    parsed_date = None
    if parsed["decision_date_raw"]:
        try:
            parsed_date = parser.parse(parsed["decision_date_raw"], fuzzy=True).strftime("%Y-%m-%d")
        except Exception:
            pass 

    result = supabase.table("cases").insert({
        "citation": citation,
        "case_name": parsed["case_name"],
        "court": parsed["court"],
        "jurisdiction": "Federal",
        "decision_date": parsed_date,
        "is_overruled": False,
        "raw_text": text,
        "source": "CAP",
    }).execute()
    
    return result.data[0]["id"]

def embed_and_insert_chunks(case_id, text):
    chunks = text_splitter.split_text(text)
    embeddings = embedder.encode(chunks, normalize_embeddings=True)
    
    rows = []
    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        rows.append({
            "case_id": case_id,
            "chunk_index": idx,
            "chunk_text": chunk,
            "embedding": emb.tolist(),
        })
        
    batch_size = 50
    for i in range(0, len(rows), batch_size):
        supabase.table("case_chunks").insert(rows[i:i + batch_size]).execute()
        
    return len(chunks)

def run():
    subset = pull_subset()
    print(f"\nProcessing and embedding {len(subset)} cases...")

    for i, item in enumerate(subset):
        try:
            case_id = insert_case(item["parsed"], item["text"])
            chunk_count = embed_and_insert_chunks(case_id, item["text"])
            print(f"[{i+1}/{TARGET_COUNT}] Embedded {chunk_count} chunks for: {item['parsed']['case_name'][:40]}...")
        except Exception as e:
            print(f"Failed on case {i+1}: {e}")
            continue

    print("\nPipeline complete. Check your Supabase database.")

if __name__ == "__main__":
    run()