"""
Credits system for tracking and deducting research/drafting costs.

The atomic deduct_credit RPC function in Supabase is the ONLY way
credits are ever deducted in application code. This module wraps that RPC
and provides helper functions for credit initialization and refunds.
"""

from app.db.supabase_client import get_supabase_client
from app.core.config import settings


def ensure_credits_row(user_id: str) -> None:
    """
    Ensure a user_credits row exists with default balance.

    Called once per request by get_current_user or a shared dependency,
    before any credit check. If the row already exists, this is a no-op.
    """
    client = get_supabase_client()
    # Upsert: insert if not exists, do nothing if exists
    client.table("user_credits").upsert(
        {"user_id": user_id, "balance": 10},
        on_conflict="user_id",
    ).execute()


def check_and_deduct_credits(user_id: str, amount: int) -> bool:
    """
    Atomically check balance and deduct credits via the deduct_credit RPC.

    Returns:
        True if deduction succeeded, False if insufficient balance.
        No partial deductions — either the full amount is deducted
        atomically, or nothing is deducted.
    """
    client = get_supabase_client()
    response = client.rpc("deduct_credit", {"p_user_id": user_id, "p_amount": amount}).execute()
    # RPC returns the boolean result in data[0]
    return response.data[0] if response.data else False


def add_credit(user_id: str, amount: int) -> None:
    """
    Add credits back to a user (used for refunds when sanitization rejects a query).

    Direct UPDATE, not an RPC — we only need atomicity for deductions.
    """
    client = get_supabase_client()
    client.table("user_credits").update(
        {"balance": f"balance + {amount}"}  # Supabase will increment
    ).eq("user_id", user_id).execute()
