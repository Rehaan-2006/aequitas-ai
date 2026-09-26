"""
Shared Supabase client access.

Builds a single service-role Supabase client per process and exposes it
via get_supabase_client(). Any service that queries Supabase directly
(retrieval, and future citation-verification/validity modules) should
call this instead of instantiating its own client per module.
"""

from functools import lru_cache

from supabase import Client, create_client

from app.core.config import settings


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
