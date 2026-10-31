from datetime import date, datetime

from fastapi import HTTPException, status


def parse_legacy_date(value: str, *, field_name: str = "date") -> date:
    """Parse legacy ``dd-mm-yyyy`` query dates."""
    try:
        return datetime.strptime(value, "%d-%m-%Y").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid {field_name} format, expected dd-mm-yyyy",
        ) from exc
