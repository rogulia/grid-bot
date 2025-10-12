"""Tests for EmergencyStopManager utility"""

import pytest
import json
import logging
from pathlib import Path
from unittest.mock import Mock, patch
from src.utils.emergency_stop_manager import EmergencyStopManager


class TestEmergencyStopManager:
    """Test EmergencyStopManager utility class"""

    @pytest.fixture
    def test_data_dir(self, tmp_path):
        """Create temporary data directory for tests"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        return data_dir

    @pytest.fixture
    def mock_logger(self):
        """Create mock logger"""
        return Mock(spec=logging.Logger)

    @pytest.fixture
    def manager(self, mock_logger, test_data_dir, monkeypatch):
        """Create EmergencyStopManager with temporary directory"""
        # Patch the DATA_DIR to use temporary directory
        monkeypatch.setattr(EmergencyStopManager, 'DATA_DIR', test_data_dir)
        return EmergencyStopManager(logger=mock_logger)

    def test_initialization(self, mock_logger, test_data_dir, monkeypatch):
        """Test EmergencyStopManager initialization"""
        monkeypatch.setattr(EmergencyStopManager, 'DATA_DIR', test_data_dir)
        manager = EmergencyStopManager(logger=mock_logger)

        assert manager.logger == mock_logger

    def test_initialization_without_logger(self, test_data_dir, monkeypatch):
        """Test initialization without custom logger"""
        monkeypatch.setattr(EmergencyStopManager, 'DATA_DIR', test_data_dir)
        manager = EmergencyStopManager()

        assert manager.logger is not None
        assert isinstance(manager.logger, logging.Logger)

    def test_get_file_path(self, test_data_dir, monkeypatch):
        """Test getting emergency stop file path"""
        monkeypatch.setattr(EmergencyStopManager, 'DATA_DIR', test_data_dir)

        # Test various account IDs
        path1 = EmergencyStopManager.get_file_path(1)
        assert path1 == test_data_dir / ".001_emergency_stop"

        path2 = EmergencyStopManager.get_file_path(42)
        assert path2 == test_data_dir / ".042_emergency_stop"

        path3 = EmergencyStopManager.get_file_path(999)
        assert path3 == test_data_dir / ".999_emergency_stop"

    def test_exists_no_file(self, manager, test_data_dir):
        """Test checking existence when file doesn't exist"""
        assert EmergencyStopManager.exists(1) is False

    def test_exists_file_present(self, manager, test_data_dir):
        """Test checking existence when file exists"""
        # Create emergency stop file
        file_path = test_data_dir / ".001_emergency_stop"
        file_path.write_text('{"reason": "test"}')

        assert EmergencyStopManager.exists(1) is True

    def test_get_data_no_file(self, manager):
        """Test getting data when file doesn't exist"""
        data = EmergencyStopManager.get_data(1)

        assert data is None

    def test_get_data_valid_file(self, manager, test_data_dir):
        """Test getting data from valid file"""
        # Create emergency stop file
        file_path = test_data_dir / ".001_emergency_stop"
        test_data = {
            "timestamp": "2025-01-15T10:30:00+02:00",
            "account_id": 1,
            "symbol": "DOGEUSDT",
            "reason": "MM Rate exceeded threshold"
        }
        file_path.write_text(json.dumps(test_data))

        data = EmergencyStopManager.get_data(1)

        assert data is not None
        assert data["timestamp"] == "2025-01-15T10:30:00+02:00"
        assert data["account_id"] == 1
        assert data["symbol"] == "DOGEUSDT"
        assert data["reason"] == "MM Rate exceeded threshold"

    def test_get_data_corrupted_file(self, manager, test_data_dir):
        """Test getting data from corrupted JSON file"""
        # Create corrupted file
        file_path = test_data_dir / ".001_emergency_stop"
        file_path.write_text("not valid json{")

        data = EmergencyStopManager.get_data(1)

        assert data is None

    @patch('src.utils.timezone.now_helsinki')
    def test_create_emergency_stop_file(self, mock_now, manager, test_data_dir, mock_logger):
        """Test creating emergency stop file"""
        from datetime import datetime
        import pytz

        # Mock current time
        mock_time = datetime(2025, 1, 15, 10, 30, 0, tzinfo=pytz.timezone('Europe/Helsinki'))
        mock_now.return_value = mock_time

        # Create emergency stop
        manager.create(
            account_id=1,
            symbol="DOGEUSDT",
            reason="Test emergency stop"
        )

        # Check file was created
        file_path = test_data_dir / ".001_emergency_stop"
        assert file_path.exists()

        # Check file contents
        with open(file_path) as f:
            data = json.load(f)

        assert data["account_id"] == 1
        assert data["symbol"] == "DOGEUSDT"
        assert data["reason"] == "Test emergency stop"
        assert "timestamp" in data

        # Check logger was called
        mock_logger.critical.assert_called_once()

    @patch('src.utils.timezone.now_helsinki')
    def test_create_with_additional_data(self, mock_now, manager, test_data_dir):
        """Test creating emergency stop file with additional data"""
        from datetime import datetime
        import pytz

        mock_time = datetime(2025, 1, 15, 10, 30, 0, tzinfo=pytz.timezone('Europe/Helsinki'))
        mock_now.return_value = mock_time

        # Create with additional data
        additional = {
            "mm_rate": 95.5,
            "balance": 50.25
        }

        manager.create(
            account_id=2,
            symbol="BTCUSDT",
            reason="High MM rate",
            additional_data=additional
        )

        # Check file contents
        file_path = test_data_dir / ".002_emergency_stop"
        with open(file_path) as f:
            data = json.load(f)

        assert data["mm_rate"] == 95.5
        assert data["balance"] == 50.25

    def test_validate_and_raise_no_file(self, manager):
        """Test validation when no emergency stop file exists"""
        # Should not raise any exception
        EmergencyStopManager.validate_and_raise(
            account_id=1,
            account_name="Test Account"
        )

    def test_validate_and_raise_with_file(self, manager, test_data_dir):
        """Test validation raises error when file exists"""
        # Create emergency stop file
        file_path = test_data_dir / ".001_emergency_stop"
        test_data = {
            "timestamp": "2025-01-15T10:30:00+02:00",
            "account_id": 1,
            "symbol": "DOGEUSDT",
            "reason": "Test reason"
        }
        file_path.write_text(json.dumps(test_data))

        # Should raise RuntimeError
        with pytest.raises(RuntimeError) as exc_info:
            EmergencyStopManager.validate_and_raise(
                account_id=1,
                account_name="Test Account"
            )

        error_msg = str(exc_info.value)
        assert "Account 001" in error_msg
        assert "Test Account" in error_msg
        assert "emergency stop flag" in error_msg
        assert "Test reason" in error_msg

    def test_validate_and_raise_corrupted_file(self, manager, test_data_dir):
        """Test validation with corrupted emergency stop file"""
        # Create corrupted file
        file_path = test_data_dir / ".001_emergency_stop"
        file_path.write_text("corrupted json{")

        # Should raise RuntimeError about corrupted file
        with pytest.raises(RuntimeError) as exc_info:
            EmergencyStopManager.validate_and_raise(
                account_id=1,
                account_name="Test Account"
            )

        error_msg = str(exc_info.value)
        assert "corrupted" in error_msg.lower()

    def test_remove_existing_file(self, manager, test_data_dir):
        """Test removing emergency stop file"""
        # Create emergency stop file
        file_path = test_data_dir / ".001_emergency_stop"
        file_path.write_text('{"reason": "test"}')

        assert file_path.exists()

        # Remove it
        EmergencyStopManager.remove(1)

        assert not file_path.exists()

    def test_remove_nonexistent_file(self, manager):
        """Test removing file that doesn't exist"""
        # Should not raise any exception
        EmergencyStopManager.remove(1)

    def test_multiple_account_files(self, manager, test_data_dir):
        """Test handling multiple account emergency stop files"""
        # Create files for multiple accounts
        for account_id in [1, 2, 5, 10]:
            file_path = test_data_dir / f".{account_id:03d}_emergency_stop"
            data = {"account_id": account_id, "reason": f"Test {account_id}"}
            file_path.write_text(json.dumps(data))

        # Check each exists
        assert EmergencyStopManager.exists(1) is True
        assert EmergencyStopManager.exists(2) is True
        assert EmergencyStopManager.exists(5) is True
        assert EmergencyStopManager.exists(10) is True
        assert EmergencyStopManager.exists(3) is False

        # Check data retrieval
        data1 = EmergencyStopManager.get_data(1)
        assert data1["account_id"] == 1

        data5 = EmergencyStopManager.get_data(5)
        assert data5["account_id"] == 5

    def test_file_path_format(self, test_data_dir, monkeypatch):
        """Test that file paths follow correct format"""
        monkeypatch.setattr(EmergencyStopManager, 'DATA_DIR', test_data_dir)

        # Files should be hidden (start with dot) and zero-padded
        path1 = EmergencyStopManager.get_file_path(1)
        assert path1.name == ".001_emergency_stop"

        path99 = EmergencyStopManager.get_file_path(99)
        assert path99.name == ".099_emergency_stop"

        path999 = EmergencyStopManager.get_file_path(999)
        assert path999.name == ".999_emergency_stop"

    def test_directory_creation(self, manager, test_data_dir):
        """Test that data directory is created if it doesn't exist"""
        # Remove data directory
        import shutil
        shutil.rmtree(test_data_dir)
        assert not test_data_dir.exists()

        # Create emergency stop (should create directory)
        manager.create(
            account_id=1,
            symbol="DOGEUSDT",
            reason="Test"
        )

        # Directory should be created
        assert test_data_dir.exists()
        assert (test_data_dir / ".001_emergency_stop").exists()

    def test_json_formatting(self, manager, test_data_dir):
        """Test that JSON file is properly formatted"""
        manager.create(
            account_id=1,
            symbol="DOGEUSDT",
            reason="Test reason"
        )

        file_path = test_data_dir / ".001_emergency_stop"

        # Read raw file content
        content = file_path.read_text()

        # Should be indented (indent=2)
        assert "  " in content  # Has indentation
        assert "\n" in content  # Has newlines

        # Should be valid JSON
        data = json.loads(content)
        assert data["symbol"] == "DOGEUSDT"

    def test_static_methods_work_without_instance(self, test_data_dir, monkeypatch):
        """Test that static methods work without creating instance"""
        monkeypatch.setattr(EmergencyStopManager, 'DATA_DIR', test_data_dir)

        # Should work without creating instance
        path = EmergencyStopManager.get_file_path(1)
        assert path is not None

        exists = EmergencyStopManager.exists(1)
        assert exists is False

        data = EmergencyStopManager.get_data(1)
        assert data is None
