"""
Pydantic models for Genesys AudioHook protocol and other application components.

These models represent the structure of messages exchanged between the client and server
in the Genesys AudioHook protocol as described in:
https://developer.genesys.cloud/devapps/audiohook/protocol-reference

Also includes other models used throughout the application.
"""

import asyncio
from typing import Any, Literal, Union

import azure.cognitiveservices.speech as speechsdk
from pydantic import BaseModel, ConfigDict, Field

from .enums import ClientMessageType, CloseReason, DisconnectReason, ServerMessageType


# Common message structure
class MessageBase(BaseModel):
    """Base model for all messages in the AudioHook protocol."""

    model_config = ConfigDict(populate_by_name=True)

    version: str
    id: str
    type: str
    seq: int
    parameters: dict[str, Any]


# Client message models
class ClientMessageBase(MessageBase):
    """Base model for client messages."""

    serverseq: int = 0
    position: str


# Server message models
class ServerMessageBase(MessageBase):
    """Base model for server messages."""

    clientseq: int
    position: str = ""  # Optional in some messages


# Message-specific models


# Media channel configuration
class MediaChannelInfo(BaseModel):
    """Information about media channels."""

    model_config = ConfigDict(populate_by_name=True)

    type: str
    codec: str = Field(alias="format")
    sample_rate: int = Field(alias="rate")
    channels: list[str]


# Participant information
class ParticipantInfo(BaseModel):
    """Information about a participant in a conversation."""

    model_config = ConfigDict(populate_by_name=True)

    ani: str
    ani_name: str = Field(alias="aniName")
    dnis: str


# Client message parameter models
class OpenMessageParameters(BaseModel):
    """Parameters for an 'open' message from client."""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    participant: ParticipantInfo
    media: list[MediaChannelInfo]


class PingMessageParameters(BaseModel):
    """Parameters for a 'ping' message from client."""

    model_config = ConfigDict(populate_by_name=True)

    rtt: str | None = None


class UpdateMessageParameters(BaseModel):
    """Parameters for an 'update' message from client."""

    model_config = ConfigDict(populate_by_name=True)

    language: str


class CloseMessageParameters(BaseModel):
    """Parameters for a 'close' message from client."""

    model_config = ConfigDict(populate_by_name=True)

    reason: CloseReason


# Server message parameter models
class DisconnectMessageParameters(BaseModel):
    """Parameters for a 'disconnect' message from server."""

    model_config = ConfigDict(populate_by_name=True)

    reason: DisconnectReason
    info: str


class OpenedMessageParameters(BaseModel):
    """Parameters for an 'opened' message from server."""

    model_config = ConfigDict(populate_by_name=True)

    start_paused: bool = Field(alias="startPaused")
    media: list[MediaChannelInfo]


class UpdatedMessageParameters(BaseModel):
    """Parameters for an 'updated' message from server."""

    model_config = ConfigDict(populate_by_name=True)

    # This could be extended with more fields as needed
    pass


# Specific client message types
class OpenMessage(ClientMessageBase):
    """
    Open message from client.

    The client initiates an open transaction by sending an 'open' message
    to provide session information and negotiate media format.
    """

    type: Literal[ClientMessageType.OPEN]
    parameters: OpenMessageParameters


class PingMessage(ClientMessageBase):
    """
    Ping message from client.

    The client sends ping messages to maintain connection health.
    """

    type: Literal[ClientMessageType.PING]
    parameters: PingMessageParameters


class UpdateMessage(ClientMessageBase):
    """
    Update message from client.

    The client sends update messages to change session properties like language.
    """

    type: Literal[ClientMessageType.UPDATE]
    parameters: UpdateMessageParameters


class CloseMessage(ClientMessageBase):
    """
    Close message from client.

    The client sends a close message to terminate the session.
    """

    type: Literal[ClientMessageType.CLOSE]
    parameters: CloseMessageParameters


# Specific server message types
class DisconnectMessage(ServerMessageBase):
    """
    Disconnect message from server.

    The server sends a disconnect message when terminating the connection abnormally.
    """

    type: Literal[ServerMessageType.DISCONNECT]
    parameters: DisconnectMessageParameters


class OpenedMessage(ServerMessageBase):
    """
    Opened message from server.

    The server responds to an open message with an opened message to complete
    the negotiation and allow the client to start sending audio.
    """

    type: Literal[ServerMessageType.OPENED]
    parameters: OpenedMessageParameters


class PongMessage(ServerMessageBase):
    """
    Pong message from server.

    The server responds to ping messages with pong messages.
    """

    type: Literal[ServerMessageType.PONG]
    parameters: dict[str, Any] = Field(default_factory=dict)


class ClosedMessage(ServerMessageBase):
    """
    Closed message from server.

    The server responds to close messages with closed messages.
    """

    type: Literal[ServerMessageType.CLOSED]
    parameters: dict[str, Any] = Field(default_factory=dict)


class UpdatedMessage(ServerMessageBase):
    """
    Updated message from server.

    The server responds to update messages with updated messages.
    """

    type: Literal[ServerMessageType.UPDATED]
    parameters: UpdatedMessageParameters


# Union types for all message types
ClientMessage = Union[OpenMessage, PingMessage, UpdateMessage, CloseMessage]
ServerMessage = Union[
    DisconnectMessage, OpenedMessage, PongMessage, ClosedMessage, UpdatedMessage
]


# Other application models


class TranscriptItem(BaseModel):
    """Pydantic model to store transcript items"""

    channel: int | None = None
    text: str
    start: str | None = None  # ISO 8601 duration string, e.g., "PT1.23S"
    end: str | None = None  # ISO 8601 duration string, e.g., "PT1.23S"


class Conversation(BaseModel):
    """Pydantic model to store conversation details"""

    model_config = ConfigDict(extra="ignore")

    id: str
    session_id: str
    active: bool = True
    ani: str
    ani_name: str
    dnis: str
    media: MediaChannelInfo  # dict[str, Any]
    position: str
    rtt: list[str] = Field(default_factory=list)
    transcript: list[TranscriptItem] = Field(default_factory=list)


class WebSocketSessionStorage(BaseModel):
    """Temporary in-memory storage for WebSocket session state"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client_seq: int = 0
    server_seq: int = 0
    conversation_id: str | None = None
    # Provider-specific speech session storage
    speech_session: Any | None = None


class Error(BaseModel):
    """Pydantic model to model Error response"""

    code: str
    message: str


class HealthCheckResponse(BaseModel):
    """Pydantic model to model Health Check response"""

    status: str
    error: Error | None = None


class ConversationsResponse(BaseModel):
    """Pydantic model to model Conversations response"""

    count: int
    conversations: list[Conversation] = Field(default_factory=list)


class AzureAISpeechSession(BaseModel):
    """Azure AI Speech session details"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    audio_buffer: speechsdk.audio.PushAudioInputStream
    raw_audio: bytearray
    media: dict[str, Any]
    recognize_task: asyncio.Task
