"""Tests for TimestampConverter utility"""

import pytest
from datetime import datetime
import pytz
from src.utils.timestamp_converter import TimestampConverter


class TestTimestampConverter:
    """Test TimestampConverter utility class"""

    def test_exchange_ms_to_helsinki_valid_timestamp(self):
        """Test converting valid millisecond timestamp to Helsinki time"""
        # 2025-01-15 10:30:00 UTC = 1736939400000 ms
        timestamp_ms = 1736939400000

        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        # Should be in format "YYYY-MM-DD HH:MM:SS"
        assert result is not None
        assert isinstance(result, str)
        assert len(result) == 19  # Format: "2025-01-15 12:30:00"

        # Parse back to check correctness
        dt = datetime.strptime(result, "%Y-%m-%d %H:%M:%S")
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15

    def test_exchange_ms_to_helsinki_zero_timestamp(self):
        """Test handling of zero timestamp"""
        result = TimestampConverter.exchange_ms_to_helsinki(0)

        assert result is None

    def test_exchange_ms_to_helsinki_none_timestamp(self):
        """Test handling of None timestamp"""
        result = TimestampConverter.exchange_ms_to_helsinki(None)

        assert result is None

    def test_exchange_ms_to_helsinki_known_date(self):
        """Test conversion with a known date/time"""
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        # Helsinki is UTC+2 in winter, so should be 02:00:00
        timestamp_ms = 1704067200000

        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        # Should contain "2024-01-01" and "02:00:00" (Helsinki time)
        assert "2024-01-01" in result
        assert "02:00:00" in result

    def test_exchange_ms_to_helsinki_summer_time(self):
        """Test conversion during Helsinki summer time (UTC+3)"""
        # 2024-07-01 12:00:00 UTC = 1719835200000 ms
        # Helsinki is UTC+3 in summer, so should be 15:00:00
        timestamp_ms = 1719835200000

        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        assert "2024-07-01" in result
        assert "15:00:00" in result

    def test_exchange_ms_to_helsinki_recent_timestamp(self):
        """Test with a recent timestamp (2025)"""
        # 2025-10-12 14:30:45 UTC
        utc_dt = datetime(2025, 10, 12, 14, 30, 45, tzinfo=pytz.UTC)
        timestamp_ms = int(utc_dt.timestamp() * 1000)

        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        assert result is not None
        assert "2025-10-12" in result
        # In October, Helsinki is UTC+3 (summer time), so 14:30 UTC = 17:30 Helsinki
        assert "17:30:45" in result

    def test_is_valid_timestamp_ms_valid_range(self):
        """Test validation of timestamps in valid range"""
        # 2025-01-15
        timestamp_2025 = 1736899200000

        assert TimestampConverter.is_valid_timestamp_ms(timestamp_2025) is True

    def test_is_valid_timestamp_ms_min_boundary(self):
        """Test validation at minimum boundary (2020-01-01)"""
        min_timestamp = 1577836800000  # 2020-01-01 00:00:00 UTC

        assert TimestampConverter.is_valid_timestamp_ms(min_timestamp) is True
        assert TimestampConverter.is_valid_timestamp_ms(min_timestamp - 1) is False

    def test_is_valid_timestamp_ms_max_boundary(self):
        """Test validation at maximum boundary (2050-01-01)"""
        max_timestamp = 2524608000000  # 2050-01-01 00:00:00 UTC

        assert TimestampConverter.is_valid_timestamp_ms(max_timestamp) is True
        assert TimestampConverter.is_valid_timestamp_ms(max_timestamp + 1) is False

    def test_is_valid_timestamp_ms_too_old(self):
        """Test validation of timestamp before 2020"""
        # 2019-12-31
        old_timestamp = 1577750400000

        assert TimestampConverter.is_valid_timestamp_ms(old_timestamp) is False

    def test_is_valid_timestamp_ms_too_future(self):
        """Test validation of timestamp after 2050"""
        # 2051-01-01
        future_timestamp = 2556144000000

        assert TimestampConverter.is_valid_timestamp_ms(future_timestamp) is False

    def test_is_valid_timestamp_ms_zero(self):
        """Test validation of zero timestamp"""
        assert TimestampConverter.is_valid_timestamp_ms(0) is False

    def test_is_valid_timestamp_ms_negative(self):
        """Test validation of negative timestamp"""
        assert TimestampConverter.is_valid_timestamp_ms(-1000) is False

    def test_convert_actual_bybit_timestamp(self):
        """Test with actual Bybit-style timestamp format"""
        # Bybit uses 13-digit millisecond timestamps
        bybit_timestamp = 1705406123456  # Typical Bybit timestamp

        result = TimestampConverter.exchange_ms_to_helsinki(bybit_timestamp)

        assert result is not None
        assert len(result) == 19
        # Should be valid timestamp format
        datetime.strptime(result, "%Y-%m-%d %H:%M:%S")

    def test_conversion_preserves_seconds(self):
        """Test that seconds are correctly preserved in conversion"""
        # Create timestamp with specific seconds value
        utc_dt = datetime(2025, 3, 15, 10, 25, 37, tzinfo=pytz.UTC)
        timestamp_ms = int(utc_dt.timestamp() * 1000)

        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        # Should end with ":37" (seconds preserved)
        assert result.endswith(":37")

    def test_multiple_conversions_consistency(self):
        """Test that multiple conversions of same timestamp are consistent"""
        timestamp_ms = 1736939400000

        result1 = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)
        result2 = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)
        result3 = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        assert result1 == result2 == result3

    def test_static_method_no_instance_needed(self):
        """Test that methods are static and work without instance"""
        # Should work without creating instance
        timestamp_ms = 1736939400000
        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        assert result is not None

    def test_edge_case_midnight(self):
        """Test conversion at midnight UTC"""
        # 2025-06-15 00:00:00 UTC
        utc_dt = datetime(2025, 6, 15, 0, 0, 0, tzinfo=pytz.UTC)
        timestamp_ms = int(utc_dt.timestamp() * 1000)

        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        # In June, Helsinki is UTC+3, so 00:00 UTC = 03:00 Helsinki
        assert "03:00:00" in result
        # But date should still be June 15
        assert "2025-06-15" in result

    def test_edge_case_end_of_day(self):
        """Test conversion near end of day"""
        # 2025-12-31 23:59:59 UTC
        utc_dt = datetime(2025, 12, 31, 23, 59, 59, tzinfo=pytz.UTC)
        timestamp_ms = int(utc_dt.timestamp() * 1000)

        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)

        assert result is not None
        # In December, Helsinki is UTC+2 (winter time)
        # 23:59:59 UTC = 01:59:59 next day Helsinki
        # But this would be Jan 1, 2026
        parsed_dt = datetime.strptime(result, "%Y-%m-%d %H:%M:%S")
        assert parsed_dt.year == 2026
        assert parsed_dt.month == 1
        assert parsed_dt.day == 1

    def test_round_trip_validation(self):
        """Test that we can validate timestamps we create"""
        # Create a timestamp
        utc_dt = datetime(2025, 5, 20, 14, 30, 0, tzinfo=pytz.UTC)
        timestamp_ms = int(utc_dt.timestamp() * 1000)

        # Should be valid
        assert TimestampConverter.is_valid_timestamp_ms(timestamp_ms) is True

        # Should convert successfully
        result = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)
        assert result is not None

    def test_invalid_type_handling(self):
        """Test handling of invalid input types"""
        # Should handle None gracefully
        assert TimestampConverter.exchange_ms_to_helsinki(None) is None

        # Zero should return None
        assert TimestampConverter.exchange_ms_to_helsinki(0) is None
