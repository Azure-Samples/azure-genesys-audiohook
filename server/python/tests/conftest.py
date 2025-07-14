import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True)
def mock_external_services():
    if os.getenv("CI") == "true":
        with (
            patch(
                "app.speech.azure_ai_speech_provider.AzureAISpeechProvider._recognize_speech",
                new_callable=AsyncMock,
            ) as mock_recognize,
            patch(
                "app.language.agent_assist.AgentAssistant.on_transcription",
                new_callable=AsyncMock,
            ) as mock_on_transcription,
            patch(
                "app.language.agent_assist.AgentAssistant.flush_summary",
                new_callable=AsyncMock,
            ) as mock_flush_summary,
        ):
            mock_recognize.return_value = None
            mock_on_transcription.return_value = type(
                "Summary", (), {"content": "mocked summary"}
            )()
            mock_flush_summary.return_value = type(
                "Summary", (), {"content": "mocked summary"}
            )()
            yield
    else:
        yield
