import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv

from app.websocket_server import WebsocketServer

load_dotenv(find_dotenv())

LOGGER: logging.Logger = logging.getLogger(__name__)

def configure_logging() -> None:
    """Configure logging based on environment variables."""
    if os.getenv("DEBUG_MODE") == "true":
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logging.getLogger("azure.identity").setLevel(logging.WARNING)
        logging.getLogger("azure.core").setLevel(logging.WARNING)
        logging.getLogger("azure.eventhub").setLevel(logging.WARNING)
        logging.getLogger("azure.storage").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("hypercorn").setLevel(logging.WARNING)
        LOGGER.info("Starting server in debug mode")
    else:
        logging.basicConfig(level=logging.WARNING)


def create_server() -> WebsocketServer:
    """Create and configure the WebSocket server.

    Returns:
        WebsocketServer: Configured WebSocket server instance

    Raises:
        RuntimeError: If server configuration fails
    """
    try:
        server = WebsocketServer()
        LOGGER.info("WebSocket server created successfully")
        return server
    except Exception as e:
        LOGGER.error(f"Failed to create WebSocket server: {e}")
        raise RuntimeError(f"Server initialization failed: {e}") from e


def main() -> None:
    """Main entry point for the application."""
    try:
        configure_logging()
        server = create_server()
        LOGGER.info("Starting WebSocket server...")
        server.app.run()
    except Exception as e:
        LOGGER.error(f"Application failed to start: {e}")
        sys.exit(1)

# Initialize module-level variables for application context
configure_logging()
_server: WebsocketServer | None = None

# Run development server when running this script directly.
# For production it is recommended that Quart will be run using Hypercorn or an alternative ASGI server.
if __name__ == "__main__":
    main()
else:
    _server = create_server()
    app = _server.app
