-- Module 9: Backend API tables and credits RPC

-- Research threads table (stores pipeline results and traces for user queries)
CREATE TABLE IF NOT EXISTS research_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    query TEXT NOT NULL,
    result_json JSONB NOT NULL,
    trace_json JSONB NOT NULL,
    feedback SMALLINT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_research_threads_user_id ON research_threads (user_id);
CREATE INDEX IF NOT EXISTS idx_research_threads_created_at ON research_threads (created_at);

-- User credits table (tracks credit balance per user)
CREATE TABLE IF NOT EXISTS user_credits (
    user_id UUID PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 10,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Atomic credit deduction RPC function
-- Returns false if insufficient balance, true if deduction succeeded
-- This is the ONLY way credits are ever deducted in application code
CREATE OR REPLACE FUNCTION deduct_credit(
    p_user_id UUID,
    p_amount INT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_balance INT;
BEGIN
    -- Get current balance (use FOR UPDATE to prevent race conditions)
    SELECT balance INTO v_balance FROM user_credits
    WHERE user_id = p_user_id
    FOR UPDATE;

    -- If no row exists, return false (insufficient credits)
    IF v_balance IS NULL THEN
        RETURN false;
    END IF;

    -- If insufficient balance, return false without deducting
    IF v_balance < p_amount THEN
        RETURN false;
    END IF;

    -- Deduct the credits atomically
    UPDATE user_credits
    SET balance = balance - p_amount,
        updated_at = now()
    WHERE user_id = p_user_id;

    RETURN true;
END;
$$;

-- Add foreign key constraint to legal_drafts.thread_id
-- (this was left off in Module 8 migration 0004)
ALTER TABLE legal_drafts
ADD CONSTRAINT fk_legal_drafts_thread_id
FOREIGN KEY (thread_id) REFERENCES research_threads(id);
