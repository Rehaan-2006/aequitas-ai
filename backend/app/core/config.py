"""
Centralized app configuration.

Every module that needs an API key, URL, or tunable value should read it
from here rather than calling os.environ directly. This keeps config
in one place per Section 7 (Configuration over hardcoding).
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # LLM provider
    llm_provider_api_key: str = ""

    # Agent models (Openrouter model slugs) — reasoning-heavy agents get a
    # Sonnet-class model; cheaper/simpler agents can add their own setting
    # here rather than hardcoding a model name inline.
    query_analyzer_model: str = "anthropic/claude-sonnet-5"
    structured_reasoning_model: str = "anthropic/claude-sonnet-5"
    citation_verifier_model: str = "anthropic/claude-sonnet-5"
    section_drafter_model: str = "anthropic/claude-sonnet-5"

    # Embeddings / retrieval
    embedding_model_name: str = "BAAI/bge-base-en-v1.5"
    retrieval_match_threshold: float = 0.3
    retrieval_match_count: int = 3

    # Hybrid Retrieval Agent (Module 3) -- intentionally wider than the
    # single-query values above, since this stage over-retrieves for a
    # later (separate, not-yet-built) reranker to narrow down.
    hybrid_retrieval_top_k: int = 20
    hybrid_retrieval_candidate_pool: int = 50
    retrieval_rrf_k: int = 60
    retrieval_citation_boost: float = 0.003  # was 0.05; exceeded the whole RRF score range (max ~0.033), see DECISIONS.md

    # Reranker (Module 3.5) -- cross-encoder narrowing retrieval to final top-k
    reranker_model_name: str = "BAAI/bge-reranker-base"
    reranker_top_n: int = 5

    # Credits system
    research_credit_cost: int = 1
    draft_credit_cost: int = 1

    # Stripe (test mode only, added in a later module)
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""

    # General
    environment: str = "development"


settings = Settings()

# PydanticAI's Openrouter provider reads `OPENROUTER_API_KEY` from the
# environment by default. Bridge it from the single settings source so
# agents can use plain "openrouter:<model>" model strings without every
# module having to build its own Provider/api_key wiring.
if settings.llm_provider_api_key:
    os.environ.setdefault("OPENROUTER_API_KEY", settings.llm_provider_api_key)
