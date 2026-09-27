"""
JWT authentication dependency for FastAPI.

Validates the Authorization: Bearer <jwt> header via Supabase's
client.auth.get_user(token), which handles all signing key types
(HS256, ECC/JWKS, etc.) transparently and is signing-key-agnostic.
"""

from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.db.supabase_client import get_supabase_client

security = HTTPBearer()


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    Extract and validate user_id from a JWT token via Supabase.

    Delegates token validation to Supabase's client.auth.get_user(),
    which validates against the current signing key(s) and handles
    key rotation transparently.

    Raises:
        HTTPException: 401 if token is missing, invalid, or expired
    """
    token = credentials.credentials
    try:
        client = get_supabase_client()
        user_response = client.auth.get_user(token)

        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        user_id = user_response.user.id
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
            )
        return user_id
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}",
        )
