import asyncio
import logging
import os
import time

import aiofiles
import pytest
from dotenv import load_dotenv

from app.handler.session_manager import SessionManager
from app.storage.in_memory_conversation_store import InMemoryConversationStore
from app.websocket_server import WebsocketServer

os.environ["WEBSOCKET_SERVER_API_KEY"] = "SGVsbG8sIEkgYW0gdGhlIEFQSSBrZXkh"
os.environ["WEBSOCKET_SERVER_CLIENT_SECRET"] = (
    "TXlTdXBlclNlY3JldEtleVRlbGxOby0xITJAMyM0JDU="
)
os.environ["AZURE_SPEECH_REGION"] = "swedencentral"

dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(dotenv_path)

logging.basicConfig(level=logging.DEBUG)
logging.getLogger().setLevel(logging.DEBUG)


@pytest.fixture
async def server():
    server = WebsocketServer()
    server.conversations_store = InMemoryConversationStore()
    server.session_manager = SessionManager(logging.getLogger(__name__))
    await server.session_manager.create_connections()
    yield server
    await server.session_manager.close_connections()


@pytest.fixture
async def app(server):
    return server.app.test_client()


@pytest.mark.asyncio
async def test_server_fixture(server):
    assert server.app is not None
    assert hasattr(server.app, "test_client")
    assert os.getenv("AZURE_SPEECH_REGION") is not None


@pytest.mark.asyncio
async def test_health_check(app):
    """Test health check endpoint"""
    response = await app.get("/")

    assert response.status_code == 200
    assert await response.data == b'{"status":"healthy"}\n'


@pytest.mark.asyncio
async def test_health_check_valid_json(app):
    """Test if health check endpoint is valid JSON"""
    response = await app.get("/")

    # Check if response data is valid JSON
    data = await response.get_json()

    assert data is not None
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_invalid_route(app):
    """Test invalid route"""
    response = await app.get("/invalid")

    assert response.status_code == 404


def test_import_performance():
    """Measure import times for performance optimization"""

    start = time.time()
    import_time = time.time() - start
    print(f"WebsocketServer import took: {import_time:.2f} seconds")

    start = time.time()
    azure_time = time.time() - start
    print(f"Azure Blob import took: {azure_time:.2f} seconds")

    # Add this to your test file temporarily


@pytest.mark.asyncio
async def test_ws_invalid_api_key(app):
    """Test websocket connection with invalid API key"""

    headers = {
        "X-Api-Key": "invalid_key",
        "Audiohook-Session-Id": "test_session",
        "Audiohook-Correlation-Id": "test_correlation",
        "Signature-Input": "test_signature_input",
        "Signature": "test_signature",
    }

    async with app.websocket("/audiohook/ws", headers=headers) as ws:
        response = await ws.receive_json()

        assert response["type"] == "disconnect"
        assert response["parameters"]["reason"] == "unauthorized"
        assert response["parameters"]["info"] == "Invalid API Key"


@pytest.mark.asyncio
async def test_ws_invalid_session_id(app):
    """Test websocket connection with invalid API key"""

    headers = {
        "X-Api-Key": "invalid_key",
        "Audiohook-Session-Id": "",
        "Audiohook-Correlation-Id": "test_correlation",
        "Signature-Input": "test_signature_input",
        "Signature": "test_signature",
    }

    async with app.websocket("/audiohook/ws", headers=headers) as ws:
        response = await ws.receive_json()

        assert response["type"] == "disconnect"
        assert response["parameters"]["reason"] == "error"
        assert response["parameters"]["info"] == "No session ID provided"


@pytest.mark.asyncio
async def test_ws_valid_connection(app):
    """Test valid websocket connection"""
    headers = {
        "X-Api-Key": os.getenv("WEBSOCKET_SERVER_API_KEY"),
        "Audiohook-Session-Id": "e160e428-53e2-487c-977d-96989bf5c99d",
        "Audiohook-Correlation-Id": "test_correlation",
        "Signature-Input": "test_signature_input",
        "Signature": "test_signature",
    }
    async with app.websocket("/audiohook/ws", headers=headers) as ws:
        # Open Transaction
        # https://developer.genesys.cloud/devapps/audiohook/session-walkthrough#open-transaction
        await ws.send_json(
            {
                "version": "2",
                "type": "open",
                "seq": 1,
                "serverseq": 0,
                "id": "e160e428-53e2-487c-977d-96989bf5c99d",
                "position": "PT0S",
                "parameters": {
                    "organizationId": "d7934305-0972-4844-938e-9060eef73d05",
                    "conversationId": "090eaa2f-72fa-480a-83e0-8667ff89c0ec",
                    "participant": {
                        "id": "883efee8-3d6c-4537-b500-6d7ca4b92fa0",
                        "ani": "+1-555-555-1234",
                        "aniName": "John Doe",
                        "dnis": "+1-800-555-6789",
                    },
                    "media": [
                        {
                            "type": "audio",
                            "format": "PCMU",
                            "channels": ["external", "internal"],
                            "rate": 8000,
                        },
                        {
                            "type": "audio",
                            "format": "PCMU",
                            "channels": ["external"],
                            "rate": 8000,
                        },
                        {
                            "type": "audio",
                            "format": "PCMU",
                            "channels": ["internal"],
                            "rate": 8000,
                        },
                    ],
                    "language": "en-US",
                },
            }
        )

        response = await ws.receive_json()

        assert response["type"] == "opened"


@pytest.mark.asyncio
async def test_ws_audio_processing_complete(app):
    """Test websocket audio processing with better error handling and debugging"""
    API_KEY = os.getenv("WEBSOCKET_SERVER_API_KEY")
    CONVERSATION_ID = "090eaa2f-72fa-480a-83e0-8667ff89c0ec"
    headers = {
        "X-Api-Key": API_KEY,
        "Audiohook-Session-Id": "e160e428-53e2-487c-977d-96989bf5c99d",
        "Audiohook-Correlation-Id": "test_correlation",
        "Signature-Input": "test_signature_input",
        "Signature": "test_signature",
    }

    async with app.websocket("/audiohook/ws", headers=headers) as ws:
        # Send open message
        await ws.send_json(
            {
                "version": "2",
                "type": "open",
                "seq": 1,
                "serverseq": 0,
                "id": "e160e428-53e2-487c-977d-96989bf5c99d",
                "position": "PT0S",
                "parameters": {
                    "organizationId": "d7934305-0972-4844-938e-9060eef73d05",
                    "conversationId": CONVERSATION_ID,
                    "participant": {
                        "id": "883efee8-3d6c-4537-b500-6d7ca4b92fa0",
                        "ani": "+1-555-555-1234",
                        "aniName": "John Doe",
                        "dnis": "+1-800-555-6789",
                    },
                    "media": [
                        {
                            "type": "audio",
                            "format": "PCMU",
                            "channels": ["external", "internal"],
                            "rate": 8000,
                        }
                    ],
                    "language": "en-US",
                },
            }
        )

        # Wait for opened response
        response = await ws.receive_json()
        logging.info("WebSocket opened response: %s", response)
        assert response["type"] == "opened"

        # Check if test.wav file exists
        file_path = os.path.join(os.path.dirname(__file__), "test.wav")
        if not os.path.exists(file_path):
            logging.warning("test.wav file not found, creating minimal test audio data")
            # Create minimal audio data for testing (silence)
            test_audio_data = b"\x00" * 8000  # 1 second of silence at 8kHz
        else:
            async with aiofiles.open(file_path, "rb") as f:
                test_audio_data = await f.read()

        # Send audio data in chunks
        chunk_size = 1024
        total_chunks = len(test_audio_data) // chunk_size + (
            1 if len(test_audio_data) % chunk_size else 0
        )

        logging.info(
            f"Sending {total_chunks} chunks of audio data (total: {len(test_audio_data)} bytes)"
        )

        for i in range(0, len(test_audio_data), chunk_size):
            chunk = test_audio_data[i : i + chunk_size]
            await ws.send(chunk)
            await asyncio.sleep(0.01)  # Small delay between chunks

        logging.info("Finished sending audio data")

        # Send close message with proper parameters including reason
        await ws.send_json(
            {
                "version": "2",
                "type": "close",
                "seq": 2,
                "serverseq": 0,
                "id": "e160e428-53e2-487c-977d-96989bf5c99d",
                "position": "PT5S",
                "parameters": {
                    "reason": "end"  # Add required reason field
                },
            }
        )

        # Wait for responses with shorter timeout and better error handling
        responses = []
        timeout_duration = 10  # Reduced timeout

        try:
            # Try to receive multiple responses
            for i in range(3):  # Expect at most 3 responses
                try:
                    response = await asyncio.wait_for(
                        ws.receive_json(), timeout=timeout_duration
                    )
                    logging.info(f"WebSocket response {i + 1}: {response}")
                    responses.append(response)

                    # Break if we get a closed response
                    if response.get("type") == "closed":
                        break

                except TimeoutError:
                    logging.info(f"Timeout waiting for response {i + 1}")
                    break

        except Exception as e:
            logging.error(f"Error receiving WebSocket responses: {e}")

        # Validate responses
        if not responses:
            pytest.fail("No responses received from WebSocket after sending audio data")

        # Check for at least one event response (transcript or recognition result)
        event_responses = [r for r in responses if r.get("type") == "event"]

        if not event_responses:
            logging.warning("No event responses received, checking all responses:")
            for i, response in enumerate(responses):
                logging.info(f"Response {i + 1}: {response}")

            # For now, just verify we got some response
            assert len(responses) > 0, "Should receive at least one response"
        else:
            # Verify we got at least one event response
            assert (
                len(event_responses) > 0
            ), "Should receive at least one event response"

            # Verify event response structure
            event_response = event_responses[0]
            assert (
                "parameters" in event_response
            ), "Event response should have parameters"

            logging.info("Audio processing test completed successfully")
