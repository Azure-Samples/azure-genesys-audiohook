import asyncio
import logging

from typing import Any, Awaitable, Callable, cast

from ..enums import (
    AzureGenesysEvent,
    ClientMessageType,
    CloseReason,
    DisconnectReason,
    ServerMessageType,
)
from ..models import (
    Conversation,
    WebSocketSessionStorage,
)

from ..storage.base_conversation_store import ConversationStore
from ..speech.speech_provider import SpeechProvider

class MessageHandler:

    def __init__(
        self,
        speech_provider:SpeechProvider,
        conversations_store: ConversationStore,
        send_event_callback: Callable[..., Awaitable[None]],
        remove_session_callback:Callable[[str], None],
        logger: logging.Logger,
    ):
        self.speech_provider = speech_provider
        self.conversations_store = conversations_store
        self.send_event = send_event_callback
        self.remove_session= remove_session_callback
        self.logger = logger

    async def handle_incoming_message(self, message: dict, ws_session: WebSocketSessionStorage):
        """Handle incoming messages (JSON)."""
        session_id = message["id"]
        message_type = message["type"]

        # Validate sequence number
        if message["seq"] != ws_session.client_seq + 1:
            await self.disconnect(
                reason=DisconnectReason.ERROR,
                message=f"Sequence number mismatch: received {message['seq']}, expected {ws_session.client_seq + 1}",
                code=3000,
                session_id=session_id,
            )

        # Store new sequence number
        ws_session.client_seq = message["seq"]

        match message_type:
            case ClientMessageType.OPEN:
                await self.handle_open_message(message, ws_session)
            case ClientMessageType.PING:
                await self.handle_ping_message(message, ws_session)
            case ClientMessageType.UPDATE:
                await self.handle_update_message(message, ws_session)
            case ClientMessageType.CLOSE:
                await self.handle_close_message(message, ws_session)
            case _:
                self.logger.info(
                    f"[{session_id}] Unknown message type: {message['type']} : {message}"
                )

    async def handle_ping_message(self, message: dict, ws_session: WebSocketSessionStorage):
        """
        Handle a ping message from the client. Note that these ping/pong messages are a protocol feature distinct from the WebSocket
        ping/pong messages (which are not used).

        See https://developer.genesys.cloud/devapps/audiohook/protocol-reference#ping
        """
        await ws_session.send_message_callback(type=ServerMessageType.PONG, client_message=message)

        if message["parameters"].get("rtt"):
            await self.conversations_store.append_rtt(
                ws_session.conversation_id, message["parameters"]["rtt"]
            )

    async def handle_open_message(self, message: dict, ws_session: WebSocketSessionStorage):
        """
        Reply to an open message from the client. The server must respond to an open message with an "opened" message.

        Once the WebSocket connection has been established, the client initiates an open transaction. It provides the server with session information and negotiates the media format.
        The client will not send audio until the server completes the open transaction by responding with and "opened" message.

        See https://developer.genesys.cloud/devapps/audiohook/session-walkthrough#open-transaction
        """
        parameters = message["parameters"]
        conversation_id = parameters["conversationId"]
        ani = parameters["participant"]["ani"]
        ani_name = parameters["participant"]["aniName"]
        dnis = parameters["participant"]["dnis"]
        session_id = message["id"]
        media = parameters["media"]
        position = message["position"]

        # Store conversation_id in the temp session storage
        ws_session.conversation_id = conversation_id

        # Handle connection probe
        # See https://developer.genesys.cloud/devapps/audiohook/patterns-and-practices#connection-probe
        if conversation_id == "00000000-0000-0000-0000-000000000000":
            await self.handle_connection_probe(message, ws_session)
            return

        self.logger.info(
            f"[{session_id}] Session opened with conversation ID: {conversation_id}, ani: {ani}, ANI Name: {ani_name}, DNIS: {dnis}, Position: {position}"
        )
        self.logger.info(f"[{session_id}] Available media: {media}")

        # Select stereo media if available, otherwise fallback to the first media format
        selected_media = next(
            (
                m
                for m in media
                if len(m["channels"]) == 2
                and {"internal", "external"}.issubset(m["channels"])
            ),
            media[0],
        )

        # Save/update persistent state
        conversation = Conversation(
            id=conversation_id,
            session_id=session_id,
            ani=ani,
            ani_name=ani_name,
            dnis=dnis,
            media=selected_media,
            position=position,
        )
        await self.conversations_store.set(conversation)

        # Initialize speech session
        if self.speech_provider:
            await self.speech_provider.initialize_session(
                session_id, ws_session, selected_media
            )

        await ws_session.send_message_callback(
            type=ServerMessageType.OPENED,
            client_message=message,
            parameters={
                "startPaused": False,
                "media": [selected_media],
            },
        )

        self.logger.info(f"[{session_id}] Session opened with media: {selected_media}")

        asyncio.create_task(
            self.send_event(
                event=AzureGenesysEvent.SESSION_STARTED,
                session_id=session_id,
                message={
                    "ani": ani,
                    "ani-name": ani_name,
                    "conversation-id": conversation_id,
                    "dnis": dnis,
                    "media": selected_media,
                    "position": position,
                },
                properties={},
            )
        )

    async def handle_update_message(self, message: dict, ws_session: WebSocketSessionStorage):
        """Handle update message"""
        parameters = message["parameters"]
        language = parameters["language"]
        session_id = message["id"]

        self.logger.info(f"[{session_id}] Received update: language {language}")

    async def handle_close_message(self, message: dict, ws_session: WebSocketSessionStorage):
        """Handle close message"""
        session_id = message["id"]
        conversation_id = ws_session.conversation_id

        # Handle connection probe
        # See https://developer.genesys.cloud/devapps/audiohook/patterns-and-practices#connection-probe
        if conversation_id == "00000000-0000-0000-0000-000000000000":
            await ws_session.send_message_callback(
                type=ServerMessageType.CLOSED, client_message=message
            )

            self.remove_session(session_id)
            return
        
        # Close audio buffer (and recognition) if the session is ended
        if self.speech_provider:
            await self.speech_provider.shutdown_session(
                session_id,
                ws_session,
                finalize_callback=lambda: self.finalize_session(session_id, ws_session, message)
            )


    async def handle_connection_probe(self, message: dict, ws_session: WebSocketSessionStorage):
        """
        Handle connection probe

        To verify configuration settings before they are committed in the administration interface, the Genesys Cloud client attempts to establish a WebSocket connection to the configured URI followed by a synthetic AudioHook session.
        This connection probe and synthetic session helps flagging integration configuration issues and verify minimal server compliance without needing manual test calls.
        """
        session_id = message["id"]

        self.logger.info(
            f"[{session_id}] Connection probe. Conversation should not be logged and transcribed."
        )

        await ws_session.send_message_callback(
            type=ServerMessageType.OPENED,
            client_message=message,
            parameters={
                "startPaused": False,
                "media": [],
            },
        )

    async def finalize_session(self, session_id: str, ws_session: WebSocketSessionStorage, close_message: dict):
        conversation_id = ws_session.conversation_id

        conversation = await self.conversations_store.get(conversation_id)
        if conversation and conversation.media:
            transcript = [
                item.model_dump() if hasattr(item, "model_dump") else dict(item)
                for item in (conversation.transcript or [])
            ]
            await self.send_event(
                event=AzureGenesysEvent.TRANSCRIPT_AVAILABLE,
                session_id=session_id,
                message={"transcript": transcript},
            )

        await ws_session.send_message_callback(
            type=ServerMessageType.CLOSED,
            client_message=close_message,
        )

        await ws_session.close_websocket_callback()
        await self.conversations_store.set_active(conversation_id, False)
        self.remove_session(session_id)
