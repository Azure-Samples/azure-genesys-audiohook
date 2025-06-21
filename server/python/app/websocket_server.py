import logging
from quart import Quart, websocket, send_from_directory

from .handler.session_manager import SessionManager

from .utils.auth import require_api_key

class WebsocketServer:
    """Websocket server class. Routing and orchestration only."""

    def __init__(self):
        """Initialize the server"""
        self.app = Quart(__name__, static_folder='static')
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