-- Module 9 addendum: atomic add_credit RPC (symmetric with deduct_credit)

-- Add credits back to a user's account, atomically
CREATE OR REPLACE FUNCTION add_credit(
    p_user_id UUID,
    p_amount INT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE user_credits
    SET balance = balance + p_amount,
        updated_at = now()
    WHERE user_id = p_user_id;

    -- Return true if a row was updated, false otherwise
    RETURN FOUND;
END;
$$;
