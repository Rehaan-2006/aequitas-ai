-- Migration: Module 8 drafting tables (document_templates, legal_drafts)
-- Purpose: document_templates holds the fixed section structure each
-- drafted document must satisfy (structure_schema, read by
-- app/services/drafting.py's Template Matcher step); legal_drafts holds
-- the resulting drafted documents, always created with
-- approval_status='pending_review' -- no row this module produces is
-- ever exportable without a separate, explicit future approval action.
--
-- Note: legal_drafts.thread_id intentionally has NO foreign key yet.
-- research_threads (Module 9, Backend API) does not exist in this schema
-- yet -- Module 8 never reads or writes research_threads itself, per its
-- module boundary (research inputs arrive as function parameters, not DB
-- reads). thread_id is a plain nullable UUID for now; add the FK
-- constraint in a follow-up migration once Module 9 creates
-- research_threads. See docs/DECISIONS.md.

CREATE TABLE IF NOT EXISTS document_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    jurisdiction VARCHAR(100) NOT NULL,
    category VARCHAR(100) NOT NULL,
    structure_schema JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legal_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID,
    template_id UUID REFERENCES document_templates(id),
    content_json JSONB NOT NULL,
    verification_status VARCHAR(50) DEFAULT 'unverified',
    approval_status VARCHAR(50) DEFAULT 'pending_review',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_legal_drafts_template_id ON legal_drafts (template_id);

-- Seed the 3 templates required for Module 8. Section lists reflect real
-- legal-drafting convention, not placeholder data.

INSERT INTO document_templates (title, jurisdiction, category, structure_schema)
VALUES (
    'Motion to Dismiss',
    'U.S. Federal',
    'Motion',
    '{
        "sections": [
            {"name": "Caption", "description": "Court name, case caption (parties), docket/case number, and the motion title.", "required": true},
            {"name": "Introduction", "description": "Brief statement identifying the motion, the rule it is brought under (e.g. Fed. R. Civ. P. 12(b)(6)), and the relief sought.", "required": true},
            {"name": "Statement of Facts", "description": "The well-pleaded facts as alleged in the complaint, presented neutrally for purposes of the motion.", "required": true},
            {"name": "Argument", "description": "The applicable legal standard and the argument for why the claims fail as a matter of law, applying supporting case law to the alleged facts.", "required": true},
            {"name": "Conclusion", "description": "Concise summary of why dismissal is warranted.", "required": true},
            {"name": "Prayer for Relief", "description": "The specific relief requested: dismissal of the complaint (or specified claims), with or without prejudice, and costs if applicable.", "required": true}
        ]
    }'::jsonb
);

INSERT INTO document_templates (title, jurisdiction, category, structure_schema)
VALUES (
    'Demand Letter',
    'U.S. Federal',
    'Correspondence',
    '{
        "sections": [
            {"name": "Header", "description": "Date, sender information, and recipient name/address.", "required": true},
            {"name": "Introduction", "description": "Identify the sender, the recipient, and the nature of the claim being asserted.", "required": true},
            {"name": "Statement of Facts", "description": "The facts giving rise to the claim, stated clearly and in chronological order.", "required": true},
            {"name": "Legal Basis", "description": "The legal grounds and authority supporting the claim, applying relevant case law to the facts.", "required": true},
            {"name": "Demand", "description": "The specific demand being made -- amount owed, corrective action required, and the deadline for compliance.", "required": true},
            {"name": "Consequences of Non-Compliance", "description": "A statement of the sender''s intent to pursue further legal action, including litigation, if the demand is not met by the stated deadline.", "required": true}
        ]
    }'::jsonb
);

INSERT INTO document_templates (title, jurisdiction, category, structure_schema)
VALUES (
    'Legal Memorandum',
    'U.S. Federal',
    'Memorandum',
    '{
        "sections": [
            {"name": "Heading", "description": "To/From/Re/Date block identifying the memo''s author, recipient, subject matter, and date.", "required": true},
            {"name": "Question Presented", "description": "The precise legal question(s) the memorandum answers.", "required": true},
            {"name": "Brief Answer", "description": "A concise, direct answer to the question presented, stated up front before the full analysis.", "required": true},
            {"name": "Statement of Facts", "description": "The relevant facts underlying the legal question, presented objectively.", "required": true},
            {"name": "Discussion", "description": "The full legal analysis supporting the brief answer, applying the governing rule and relevant case law to the facts.", "required": true},
            {"name": "Conclusion", "description": "Restates the answer and its practical implications for the client or matter.", "required": true}
        ]
    }'::jsonb
);
