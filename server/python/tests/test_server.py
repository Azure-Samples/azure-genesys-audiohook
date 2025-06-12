"""Tests for the server.py module."""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

import server


class TestConfigureLogging:
    """Test logging configuration functionality."""

    def test_configure_logging_debug_mode(self):
        """Test that debug mode is properly configured."""
        with patch.dict(os.environ, {"DEBUG_MODE": "true"}):
            with patch("logging.basicConfig") as mock_basic_config:
                with patch("logging.getLogger") as mock_get_logger:
                    mock_logger = MagicMock()
                    mock_get_logger.return_value = mock_logger

                    server.configure_logging()

                    mock_basic_config.assert_called_once_with(level=logging.DEBUG)
                    # Verify that various loggers are set to WARNING level
                    assert mock_logger.setLevel.call_count >= 7

    def test_configure_logging_production_mode(self):
        """Test that production mode is properly configured."""
        with patch.dict(os.environ, {"DEBUG_MODE": "false"}):
            with patch("logging.basicConfig") as mock_basic_config:
                server.configure_logging()
                mock_basic_config.assert_called_once_with(level=logging.WARNING)

    def test_configure_logging_no_debug_mode_env(self):
        """Test that missing DEBUG_MODE env var defaults to production."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("logging.basicConfig") as mock_basic_config:
                server.configure_logging()
                mock_basic_config.assert_called_once_with(level=logging.WARNING)


class TestCreateServer:
    """Test server creation functionality."""

    def test_create_server_success(self):
        """Test successful server creation."""
        with patch("server.WebsocketServer") as mock_server_class:
            mock_server = MagicMock()
            mock_server_class.return_value = mock_server

            result = server.create_server()

            assert result == mock_server
            mock_server_class.assert_called_once()

    def test_create_server_failure(self):
        """Test server creation failure handling."""
        with patch("server.WebsocketServer") as mock_server_class:
            mock_server_class.side_effect = Exception("Server creation failed")

            with pytest.raises(RuntimeError, match="Server initialization failed"):
                server.create_server()


class TestMain:
    """Test main function functionality."""

    def test_main_success(self):
        """Test successful main execution."""
        with patch("server.configure_logging") as mock_configure:
            with patch("server.create_server") as mock_create:
                mock_server = MagicMock()
                mock_create.return_value = mock_server

                server.main()

                mock_configure.assert_called_once()
                mock_create.assert_called_once()
                mock_server.app.run.assert_called_once()

    def test_main_failure(self):
        """Test main execution failure handling."""
        with patch("server.configure_logging") as mock_configure:
            with patch("server.create_server") as mock_create:
                mock_create.side_effect = Exception("Creation failed")

                with pytest.raises(SystemExit):
                    server.main()

                mock_configure.assert_called_once()
                mock_create.assert_called_once()


class TestModuleLevel:
    """Test module-level behavior."""

    def test_module_imports(self):
        """Test that all necessary imports are available."""
        # This test ensures that the module can be imported without errors
        assert hasattr(server, "configure_logging")
        assert hasattr(server, "create_server")
        assert hasattr(server, "main")
        assert hasattr(server, "LOGGER")

    def test_logger_is_configured(self):
        """Test that the module logger is properly configured."""
        assert isinstance(server.LOGGER, logging.Logger)
        assert server.LOGGER.name == "server"
