import asyncio
import json
import os
from typing import Any

from azure.storage.blob.aio import BlobServiceClient
from quart import request

from ..enums import (
    AzureGenesysEvent,
    DisconnectReason,
    ServerMessageType,
)
from ..events.event_publisher import EventPublisher
from ..models import (
    ClientMessage,
    ClientMessageBase,
    CloseMessage,
    ConversationsResponse,
    DisconnectMessage,
    DisconnectMessageParameters,
    OpenMessage,
    PingMessage,
    ServerMessageBase,
    UpdateMessage,
    WebSocketSessionStorage,
)
from ..speech.azure_ai_speech_provider import AzureAISpeechProvider
from ..speech.azure_openai_gpt4o_transcriber import (
    AzureOpenAIGPT4oTranscriber,
)
from ..speech.speech_provider import SpeechProvider
from ..storage.base_conversation_store import ConversationStore
from ..storage.conversation_store import get_conversation_store
from ..utils.identity import get_azure_credential_async
from .health_handler import HealthHandler
from .media_handler import MediaHandler
from .message_handler import ClientMessageType, MessageHandler


class SessionManager:
    """
    Manages active WebSocket sessions and delegates logic to appropriate handlers.
    """

    def __init__(self, logger):
        self.logger = logger
        self.active_ws_sessions: dict[str, WebSocketSessionStorage] = {}

        # Shared resources — initialized in create_connections
        self.blob_service_client: BlobServiceClient | None = None
        self.conversations_store: ConversationStore | None = None
        self.event_publisher: EventPublisher | None = None
        self.speech_provider: SpeechProvider | None = None

        # Handlers — will be initialized after connections are created
        self.message_handler: MessageHandler | None = None
        self.health_handler: HealthHandler | None = None
        self.media_handler: MediaHandler | None = None

    # ========== CONNECTION LIFECYCLE ==========

    async def create_connections(self):
        """Create connections before serving"""
        if connection_string := os.getenv("AZURE_STORAGE_CONNECTION_STRING"):
            self.blob_service_client = BlobServiceClient.from_connection_string(
                connection_string
            )
        elif account_url := os.getenv("AZURE_STORAGE_ACCOUNT_URL"):
            self.blob_service_client = BlobServiceClient(
                account_url, credential=get_azure_credential_async()
            )

        self.conversations_store = get_conversation_store()

        if os.getenv("AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE") or os.getenv(
            "AZURE_EVENT_HUB_CONNECTION_STRING"
        ):
            self.event_publisher = EventPublisher()

        if selected_speech_provider := os.getenv("SPEECH_PROVIDER", "azure-ai-speech"):
            if selected_speech_provider == "azure-ai-speech":
                if os.getenv("AZURE_SPEECH_REGION") and (
                    os.getenv("AZURE_SPEECH_KEY")
                    or os.getenv("AZURE_SPEECH_RESOURCE_ID")
                ):
                    self.speech_provider = AzureAISpeechProvider(
                        self.conversations_store, self.send_event, self.logger
                    )
                else:
                    raise RuntimeError(
                        "Azure Speech configuration is required. Please set AZURE_SPEECH_REGION and either AZURE_SPEECH_KEY or AZURE_SPEECH_RESOURCE_ID."
                    )

            elif selected_speech_provider == "azure-openai-gpt4o-transcribe":
                if os.getenv("AZURE_OPENAI_ENDPOINT") and (
                    os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT")
                ):
                    self.speech_provider = AzureOpenAIGPT4oTranscriber(
                        self.conversations_store, self.send_event, self.logger
                    )
                else:
                    raise RuntimeError(
                        "Azure OpenAI Speech configuration is required. Please set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_MODEL_DEPLOYMENT."
                    )

        # Initialize handlers after resources are ready
        self.health_handler = HealthHandler(
            self.conversations_store,
            self.blob_service_client,
            self.event_publisher,
            self.logger,
        )
        self.message_handler = MessageHandler(
            self.speech_provider,
            self.conversations_store,
            self.send_event,
            self.remove_session,
            self.logger,
        )
        self.media_handler = MediaHandler(
            self.speech_provider, self.conversations_store, self.logger
        )

    async def close_connections(self):
        """Close connections after serving"""
        if self.blob_service_client:
            await self.blob_service_client.close()

        if self.event_publisher:
            await self.event_publisher.close()

        if self.conversations_store:
            await self.conversations_store.close()

        if self.speech_provider:
            await self.speech_provider.close()

    # ========== CONVERSATION VIEWER ==========

    async def get_conversations(self) -> Any:
        """
        Retrieve a list of conversations.
        """
        # TODO implement pagination
        active = request.args.get("active")
        if isinstance(active, str):
            active = {"true": True, "false": False}.get(active.lower())

        conversations = await self.conversations_store.list(active=active)

        return ConversationsResponse(
            count=len(conversations),
            conversations=conversations,
        ).model_dump(exclude_none=True), 200

    async def get_conversation(self, conversation_id) -> Any:
        """
        Retrieve a client session by its conversation ID.
        """
        conversation = await self.conversations_store.get(conversation_id)
        if conversation:
            return conversation.model_dump(exclude_none=True), 200
        return {
            "error": {
                "code": "unknown_conversation",
                "message": f"No conversation found for conversation ID '{conversation_id}'. Please verify the ID and try again.",
            }
        }, 404

    # ========== HEALTH CHECK ==========

    async def health_check(self):
        """Health check endpoint"""
        return await self.health_handler.health_check()

    # ========== AUDIOHOOK WEBSOCKET ROUTING ==========

    async def handle_websocket(self, websocket):
        """Handle the WebSocket lifecycle."""

        async def send_message_callback(
            type: ServerMessageType,
            client_message: dict,
            parameters: dict | None = None,
        ):
            if parameters is None:
                parameters = {}
            await self.send_message(websocket, type, client_message, parameters)

        async def close_websocket_callback():
            await websocket.close(1000)

        headers = websocket.headers
        remote = websocket.remote_addr
        session_id = headers["Audiohook-Session-Id"]

        if not session_id:
            return await self.disconnect(
                reason=DisconnectReason.ERROR,
                message="No session ID provided",
                code=1008,
                session_id=None,
                websocket=websocket,
            )

        if headers["X-Api-Key"] != os.getenv("WEBSOCKET_SERVER_API_KEY"):
            return await self.disconnect(
                reason=DisconnectReason.UNAUTHORIZED,
                message="Invalid API Key",
                code=3000,
                session_id=session_id,
                websocket=websocket,
            )

        await websocket.accept()
        websocket = (
            websocket._get_current_object()
        )  # real object rather than local proxy

        correlation_id = headers["Audiohook-Correlation-Id"]
        self.logger.info(f"[{session_id}] Accepted websocket connection from {remote}")
        self.logger.info(f"[{session_id}] Correlation ID: {correlation_id}")

        signature_input = headers["Signature-Input"]
        signature = headers["Signature"]
        client_secret = os.getenv("WEBSOCKET_SERVER_CLIENT_SECRET")

        # TODO implement signature validation
        if not signature_input and not signature and not client_secret:
            return await self.disconnect(
                reason=DisconnectReason.UNAUTHORIZED,
                message="Invalid signature",
                code=3000,
                session_id=session_id,
                websocket=websocket,
            )

        # Save new client in persistent storage
        self.active_ws_sessions[session_id] = WebSocketSessionStorage(
            send_message_callback=send_message_callback,
            close_websocket_callback=close_websocket_callback,
        )

        # Open the websocket connection and start receiving data (messages / audio)
        try:
            while True:
                data = await websocket.receive()

                if isinstance(data, str):
                    # Parse JSON to get message type, then validate with the appropriate model
                    json_data = json.loads(data)
                    message_type = json_data.get("type")
                    # Use match-case to select the appropriate message model
                    match message_type:
                        case ClientMessageType.OPEN:
                            client_message = OpenMessage.model_validate_json(data)
                        case ClientMessageType.PING:
                            client_message = PingMessage.model_validate_json(data)
                        case ClientMessageType.UPDATE:
                            client_message = UpdateMessage.model_validate_json(data)
                        case ClientMessageType.CLOSE:
                            client_message = CloseMessage.model_validate_json(data)
                        case _:
                            # Fallback for unknown message types
                            self.logger.warning(
                                f"[{session_id}] Unknown message type: {message_type}"
                            )
                            client_message = ClientMessageBase.model_validate_json(data)
                    self.logger.debug(f"[{session_id}] Received message: {data}")
                    await self.message_handler.handle_incoming_message(
                        client_message, self.active_ws_sessions[session_id]
                    )
                elif isinstance(data, bytes):
                    await self.media_handler.handle_bytes(
                        data, session_id, self.active_ws_sessions[session_id]
                    )
                else:
                    self.logger.debug(
                        f"[{session_id}] Received unknown data type: {type(data)}: {data}"
                    )
        except asyncio.CancelledError:
            self.logger.warning(
                f"[{session_id}] Websocket connection cancelled/disconnected."
            )

            # Note: AudioHook currently does not support re-establishing session connections.
            # Set the client session to inactive and remove the temporary client session
            if session_id in self.active_ws_sessions:
                ws_session = self.active_ws_sessions[session_id]
                await self.conversations_store.set_active(
                    ws_session.conversation_id, False
                )
                del self.active_ws_sessions[session_id]

    async def disconnect(
        self,
        reason: DisconnectReason,
        message: str,
        code: int,
        session_id: str | None,
        websocket=None,
    ):
        """
        Disconnect the websocket connection gracefully.

        Using sequence number 1 for the disconnect message as per the protocol specification,
        since the client did not send an open message.
        If session_id is not known yet, use the raw websocket object to send message.
        """
        session_id = session_id or "unknown"
        self.logger.warning(f"[{session_id}] {message}")
        # Create a disconnect message using the Pydantic model
        disconnect_params = DisconnectMessageParameters(reason=reason, info=message)
        disconnect_message = DisconnectMessage(
            version="2",
            type=ServerMessageType.DISCONNECT,
            seq=1,
            clientseq=1,
            id=session_id,
            parameters=disconnect_params,
        )

        if websocket:
            await websocket.send_json(disconnect_message.model_dump())
            return await websocket.close(code)

        if session_id in self.active_ws_sessions:
            ws_session = self.active_ws_sessions[session_id]
            await ws_session.websocket.send_json(disconnect_message.model_dump())
            return await ws_session.websocket.close(code)

        self.logger.warning(
            f"Cannot disconnect — no websocket found for session_id={session_id}"
        )

    # To avoid circular dependencies, implemented in session_manager and referenced by handlers via that shared instance.
    async def send_message(
        self,
        websocket,
        type: ServerMessageType,
        client_message: ClientMessage,
        parameters: dict | None = None,
    ):
        if parameters is None:
            parameters = {}
        """Send a message to the client."""
        session_id = client_message.id
        ws_session = self.active_ws_sessions[session_id]
        ws_session.server_seq += 1
        server_message = ServerMessageBase(
            version="2",
            type=type,
            seq=ws_session.server_seq,
            clientseq=client_message.seq,
            id=session_id,
            parameters=parameters,
        )
        self.logger.info(f"[{session_id}] Server sending message with type {type}.")
        self.logger.debug(server_message)
        try:
            await websocket.send_json(server_message.model_dump(exclude_none=True))
        except Exception as e:
            self.logger.error(f"Failed to send message for session {session_id}: {e}")

    async def send_event(
        self,
        event: AzureGenesysEvent,
        session_id: str,
        message: dict[str, Any],
        properties: dict[str, str] | None = None,
    ):
        """Send an JSON event to Azure Event Hub using the EventPublisher abstraction."""
        if properties is None:
            properties = {}
        if not self.event_publisher:
            self.logger.debug(f"[{session_id}] No Event Hub publisher configured.")
            return

        if session_id in self.active_ws_sessions:
            # Get the conversation ID from the active WebSocket session
            ws_session = self.active_ws_sessions[session_id]
            try:
                await self.event_publisher.send_event(
                    event_type=f"azure-genesys-audiohook.{event}",
                    conversation_id=ws_session.conversation_id,
                    message=message,
                    properties=properties,
                )
                self.logger.debug(f"[{session_id}] Sending event: {event} {message}")
            except Exception as e:
                self.logger.error(f"[{session_id}] Failed to send event: {e}")

    def remove_session(self, session_id: str):
        if session_id in self.active_ws_sessions:
            del self.active_ws_sessions[session_id]
            self.logger.info(f"Session {session_id} removed from active sessions.")
