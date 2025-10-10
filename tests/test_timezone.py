"""Tests for timezone utilities"""

import pytest
from datetime import datetime
import pytz
from src.utils.timezone import now_helsinki, format_helsinki, to_helsinki, HELSINKI_TZ


class TestTimezone:
    """Tests for timezone helper functions"""

    def test_now_helsinki_returns_helsinki_time(self):
        """Test now_helsinki returns datetime with Helsinki timezone"""
        dt = now_helsinki()
        # Check timezone name (pytz returns different objects for DST/STD)
        assert dt.tzinfo.zone == 'Europe/Helsinki'
        assert isinstance(dt, datetime)

    def test_format_helsinki_default_format(self):
        """Test format_helsinki with default format"""
        result = format_helsinki()
        # Should return "YYYY-MM-DD HH:MM:SS" format
        assert len(result) == 19
        assert result[4] == '-' and result[7] == '-'
        assert result[10] == ' '
        assert result[13] == ':' and result[16] == ':'

    def test_format_helsinki_custom_format(self):
        """Test format_helsinki with custom format"""
        result = format_helsinki(fmt="%Y-%m-%d")
        # Should return "YYYY-MM-DD" format
        assert len(result) == 10
        assert result[4] == '-' and result[7] == '-'

    def test_format_helsinki_converts_utc(self):
        """Test format_helsinki converts UTC to Helsinki"""
        # Create UTC datetime at noon
        utc_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=pytz.UTC)
        result = format_helsinki(utc_dt)

        # Helsinki is UTC+2 (winter) or UTC+3 (summer)
        # January 15 is winter time (UTC+2)
        assert "14:00:00" in result

    def test_format_helsinki_with_none_uses_current(self):
        """Test format_helsinki with None uses current time"""
        result1 = format_helsinki(None)
        result2 = format_helsinki()

        # Both should be valid timestamps
        assert len(result1) == 19
        assert len(result2) == 19

    def test_to_helsinki_naive_datetime(self):
        """Test to_helsinki assumes UTC for naive datetime"""
        naive_dt = datetime(2025, 1, 15, 12, 0, 0)
        result = to_helsinki(naive_dt)

        # Check timezone name (pytz returns different objects for DST/STD)
        assert result.tzinfo.zone == 'Europe/Helsinki'
        # Should be 14:00 (UTC+2 in winter)
        assert result.hour == 14

    def test_to_helsinki_with_timezone(self):
        """Test to_helsinki converts from different timezone"""
        # Create datetime in New York timezone (UTC-5)
        ny_tz = pytz.timezone('America/New_York')
        ny_dt = ny_tz.localize(datetime(2025, 1, 15, 12, 0, 0))

        result = to_helsinki(ny_dt)

        # Check timezone name (pytz returns different objects for DST/STD)
        assert result.tzinfo.zone == 'Europe/Helsinki'
        # New York 12:00 EST → UTC 17:00 → Helsinki 19:00 (UTC+2)
        assert result.hour == 19

    def test_helsinki_tz_constant(self):
        """Test HELSINKI_TZ constant is correct"""
        assert str(HELSINKI_TZ) == 'Europe/Helsinki'

    def test_format_helsinki_consistency(self):
        """Test that multiple calls give consistent formats"""
        dt = datetime(2025, 10, 10, 15, 30, 45, tzinfo=HELSINKI_TZ)

        result1 = format_helsinki(dt)
        result2 = format_helsinki(dt)

        assert result1 == result2
        assert "2025-10-10" in result1
        assert "15:30:45" in result1


class TestTimezoneEdgeCases:
    """Tests for timezone edge cases"""

    def test_format_helsinki_summer_time(self):
        """Test timezone during summer (UTC+3)"""
        # July is summer time in Helsinki
        utc_dt = datetime(2025, 7, 15, 12, 0, 0, tzinfo=pytz.UTC)
        result = format_helsinki(utc_dt)

        # Should be 15:00 (UTC+3 in summer)
        assert "15:00:00" in result

    def test_format_helsinki_winter_time(self):
        """Test timezone during winter (UTC+2)"""
        # January is winter time in Helsinki
        utc_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=pytz.UTC)
        result = format_helsinki(utc_dt)

        # Should be 14:00 (UTC+2 in winter)
        assert "14:00:00" in result

    def test_to_helsinki_preserves_date(self):
        """Test that timezone conversion preserves date correctly"""
        # Test date at edge of day
        utc_dt = datetime(2025, 1, 15, 23, 30, 0, tzinfo=pytz.UTC)
        result = to_helsinki(utc_dt)

        # 23:30 UTC → 01:30 Helsinki next day (UTC+2)
        assert result.day == 16  # Next day
        assert result.hour == 1
        assert result.minute == 30
