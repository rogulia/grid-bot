"""Timestamp conversion utilities for exchange data"""

from datetime import datetime
import pytz
from .timezone import format_helsinki


class TimestampConverter:
    """
    Utility class for converting timestamps from exchange format to Helsinki timezone

    Eliminates code duplication for timestamp conversions across the codebase.
    """

    @staticmethod
    def exchange_ms_to_helsinki(timestamp_ms: int) -> str:
        """
        Convert exchange timestamp (milliseconds) to Helsinki timezone string

        Args:
            timestamp_ms: Timestamp in milliseconds from epoch (UTC)

        Returns:
            Formatted datetime string in Helsinki timezone (format: "YYYY-MM-DD HH:MM:SS")

        Example:
            >>> TimestampConverter.exchange_ms_to_helsinki(1609459200000)
            "2021-01-01 02:00:00"  # Helsinki time (UTC+2 in winter)
        """
        if timestamp_ms == 0 or timestamp_ms is None:
            return None

        # Convert milliseconds to seconds and create UTC datetime
        utc_dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=pytz.UTC)

        # Convert to Helsinki timezone and format
        return format_helsinki(utc_dt)

    @staticmethod
    def exchange_sec_to_helsinki(timestamp_sec: float) -> str:
        """
        Convert exchange timestamp (seconds) to Helsinki timezone string

        Args:
            timestamp_sec: Timestamp in seconds from epoch (UTC)

        Returns:
            Formatted datetime string in Helsinki timezone

        Example:
            >>> TimestampConverter.exchange_sec_to_helsinki(1609459200)
            "2021-01-01 02:00:00"
        """
        if timestamp_sec == 0 or timestamp_sec is None:
            return None

        # Convert seconds timestamp to milliseconds and use main converter
        return TimestampConverter.exchange_ms_to_helsinki(int(timestamp_sec * 1000))

    @staticmethod
    def is_valid_timestamp_ms(timestamp_ms: int) -> bool:
        """
        Check if timestamp is valid (not None, not 0, reasonable range)

        Args:
            timestamp_ms: Timestamp in milliseconds

        Returns:
            True if valid, False otherwise
        """
        if timestamp_ms is None or timestamp_ms == 0:
            return False

        # Reasonable range: 2020-01-01 to 2050-01-01
        MIN_TIMESTAMP_MS = 1577836800000  # 2020-01-01 00:00:00 UTC
        MAX_TIMESTAMP_MS = 2524608000000  # 2050-01-01 00:00:00 UTC

        return MIN_TIMESTAMP_MS <= timestamp_ms <= MAX_TIMESTAMP_MS
