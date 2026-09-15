import os
import re
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Standard regex to catch reporter citations like "395 U.S. 762" or "483 F.2d 1234"
CITATION_REGEX = r"\b(\d{1,4})\s+([A-Za-z\.\s]+)\s+(\d{1,4})\b"

def build_citations():
    print("Fetching 502 cases from Supabase...")
    response = supabase.table("cases").select("id, raw_text").execute()
    cases = response.data
    
    insert_payload = []
    
    print("Scanning text for citations...")
    for case in cases:
        citing_id = case["id"]
        text = case["raw_text"]
        
        # Find all strings that match standard legal citation formats
        matches = re.finditer(CITATION_REGEX, text)
        found_citations = set()
        
        for match in matches:
            cit_text = match.group(0).strip()
            # Basic filter to drop weird OCR anomalies
            if 5 < len(cit_text) < 30 and not cit_text.isnumeric():
                found_citations.add(cit_text)
                
        for cit in found_citations:
            insert_payload.append({
                "citing_case_id": citing_id,
                "cited_case_id": None, # Expected to be NULL for an external case
                "cited_citation_text": cit
            })

    print(f"Found {len(insert_payload)} external citations across {len(cases)} cases.")
    
    # Insert in batches to keep Supabase happy
    batch_size = 500
    for i in range(0, len(insert_payload), batch_size):
        supabase.table("case_citations").insert(insert_payload[i:i+batch_size]).execute()
        print(f"Inserted batch {i//batch_size + 1}")
        
    print("\nCitation graph populated successfully!")

if __name__ == "__main__":
    build_citations()