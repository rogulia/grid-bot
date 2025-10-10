"""Timezone utilities for Helsinki (Europe/Helsinki) timezone"""

from datetime import datetime
import pytz


# Helsinki timezone (UTC+2 winter, UTC+3 summer)
HELSINKI_TZ = pytz.timezone('Europe/Helsinki')


def now_helsinki() -> datetime:
    """
    Get current time in Helsinki timezone

    Returns:
        datetime object with Helsinki timezone

    Example:
        >>> dt = now_helsinki()
        >>> dt.tzinfo.zone
        'Europe/Helsinki'
    """
    return datetime.now(HELSINKI_TZ)


def format_helsinki(dt: datetime = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format datetime in Helsinki timezone

    Args:
        dt: datetime object (if None, uses current time)
        fmt: strftime format string

    Returns:
        Formatted string in Helsinki timezone

    Example:
        >>> format_helsinki()  # Current time in Helsinki
        '2025-10-10 09:30:15'

        >>> utc_dt = datetime.now(pytz.UTC)
        >>> format_helsinki(utc_dt)  # Converts UTC to Helsinki
        '2025-10-10 12:30:15'  # +3 hours in summer
    """
    if dt is None:
        dt = now_helsinki()

    # If datetime is naive (no timezone), assume UTC
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)

    # Convert to Helsinki timezone
    helsinki_dt = dt.astimezone(HELSINKI_TZ)

    return helsinki_dt.strftime(fmt)


def to_helsinki(dt: datetime) -> datetime:
    """
    Convert any datetime to Helsinki timezone

    Args:
        dt: datetime object (naive or timezone-aware)

    Returns:
        datetime object in Helsinki timezone
    """
    # If naive, assume UTC
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)

    return dt.astimezone(HELSINKI_TZ)
