from typing import Optional
from uuid import UUID
from fastapi import Header, HTTPException, Query, status


def get_session_id(
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
    sid: Optional[str] = Query(default=None),
) -> str:
    """
    Resolves the anonymous per-browser-tab session id for the current request.
    Read from the X-Session-Id header (used by fetch() calls) or, for the few
    endpoints loaded via a raw URL that can't set custom headers (PDF/report
    downloads), the sid query param.
    """
    raw_value = x_session_id or sid
    if not raw_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing session id.",
        )
    try:
        # Validates UUID shape without normalizing case/format - the client
        # value is used as-is so it round-trips exactly for lookups.
        UUID(raw_value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session id format.",
        )
    return raw_value
