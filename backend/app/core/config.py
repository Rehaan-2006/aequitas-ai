"""
Centralized app configuration.

Every module that needs an API key, URL, or tunable value should read it
from here rather than calling os.environ directly. This keeps config
in one place per Section 7 (Configuration over hardcoding).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # LLM provider
    llm_provider_api_key: str = ""

    # Stripe (test mode only, added in a later module)
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""

    # General
    environment: str = "development"


settings = Settings()
