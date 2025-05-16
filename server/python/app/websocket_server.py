import logging
from typing import ClassVar

from azure.storage.blob.aio import BlobServiceClient
from quart import Quart, send_from_directory, websocket

from .events.event_publisher import EventPublisher
from .handler.session_manager import SessionManager
from .models import (
    WebSocketSessionStorage,
)
from .speech.speech_provider import SpeechProvider
from .storage.base_conversation_store import ConversationStore
from .utils.auth import require_api_key


class WebsocketServer:
    """Websocket server class"""

    active_ws_sessions: ClassVar[dict[str, WebSocketSessionStorage]] = {}
    logger: logging.Logger = logging.getLogger(__name__)
    blob_service_client: BlobServiceClient | None = None
    conversations_store: ConversationStore | None = None
    event_publisher: EventPublisher | None = None
    speech_provider: SpeechProvider | None = None

    def __init__(self):
        """Initialize the server"""
        self.app = Quart(__name__, static_folder="static")
        self.logger = logging.getLogger(__name__)
        self.session_manager = SessionManager(self.logger)
        self.setup_routes()
        self.app.before_serving(self.session_manager.create_connections)
        self.app.after_serving(self.session_manager.close_connections)

    async def serve_view(self):
        return await send_from_directory(self.app.static_folder, "index.html")

    def setup_routes(self):
        """Setup the routes for the server"""

        @self.app.route("/")
        async def health_check():
            return await self.session_manager.health_check()

        @self.app.route("/api/conversations")
        @require_api_key
        async def get_conversations():
            return await self.session_manager.get_conversations()

        @self.app.route("/api/conversation/<conversation_id>")
        @require_api_key
        async def get_conversation(conversation_id):
            return await self.session_manager.get_conversation(conversation_id)

        @self.app.route("/viewconversations")
        @require_api_key
        async def serve_view():
            return await self.serve_view()

        @self.app.websocket("/audiohook/ws")
        async def ws():
            await self.session_manager.handle_websocket(websocket)
