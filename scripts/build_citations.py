import os
import re
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Reporter abbreviations recognized by Bluebook standards
REPORTER_ABBREVIATIONS = {
    "U.S.", "S. Ct.", "L. Ed.", "L. Ed. 2d", "L. Ed. 3d",
    "F.", "F.2d", "F.3d", "F.4th",
    "F. Supp.", "F. Supp. 2d", "F. Supp. 3d",
    "T.C.", "B.T.A.", "Ct. Cl.",
    "N.E.", "N.E.2d", "N.E.3d",
    "N.W.", "N.W.2d", "N.W.3d",
    "S.E.", "S.E.2d", "S.E.3d",
    "S.W.", "S.W.2d", "S.W.3d",
    "A.", "A.2d", "A.3d",
    "P.", "P.2d", "P.3d",
    "So.", "So.2d", "So.3d",
    "Ill.", "Ill.2d", "Ill.3d",
    "N.Y.", "N.Y.2d", "N.Y.3d",
    "Cal.", "Cal.2d", "Cal.3d",
    "N.D.", "S.D.", "M.D.", "E.D.", "W.D."
}

# Regex: (number)(1-2 spaces)(known reporter abbr)(number)
# e.g., "395 U.S. 762" or "483 F.2d 1234"
CITATION_REGEX = r"\b(\d{1,4})\s+(" + "|".join(re.escape(abbr) for abbr in REPORTER_ABBREVIATIONS) + r")\s+(\d{1,4})\b"

def build_citations():
    print("Fetching cases from Supabase...")
    response = supabase.table("cases").select("id, citation, raw_text").execute()
    cases = response.data

    # Build a lookup for resolving internal citations
    citation_to_id = {c["citation"]: c["id"] for c in cases}

    # Idempotency guard: truncate existing citations to prevent duplicates on re-run
    print("Clearing existing citations to ensure idempotency...")
    supabase.table("case_citations").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

    insert_payload = []

    print("Scanning text for citations...")
    for case in cases:
        citing_id = case["id"]
        text = case["raw_text"]

        matches = re.finditer(CITATION_REGEX, text)
        found_citations = set()

        for match in matches:
            cit_text = match.group(0).strip()
            if 5 < len(cit_text) < 30 and not cit_text.isnumeric():
                found_citations.add(cit_text)

        for cit in found_citations:
            insert_payload.append({
                "citing_case_id": citing_id,
                "cited_case_id": citation_to_id.get(cit),  # resolves to a real UUID if the cited case is in our corpus, else NULL
                "cited_citation_text": cit,
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