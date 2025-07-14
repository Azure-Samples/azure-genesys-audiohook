import logging

from ..models import WebSocketSessionStorage
from ..speech.speech_provider import SpeechProvider
from ..storage.base_conversation_store import ConversationStore


class MediaHandler:
    """Manages audio and transcript connector"""

    def __init__(
        self,
        speech_provider: SpeechProvider,
        conversations_store: ConversationStore,
        logger: logging.Logger,
    ):
        if not speech_provider:
            logger.error("Speech provider is not configured.")
            raise ValueError("speech_provider must be provided.")

        self.conversations_store = conversations_store
        self.speech_provider = speech_provider
        self.logger = logger

    async def handle_bytes(
        self, data: bytes, session_id: str, ws_session: WebSocketSessionStorage
    ):
        """
        Handles audio stream in u-Law ("PCMU")

        The audio in the frames for PCMU are headerless and the samples of two-channel streams are interleaved. For example, a 100ms audio frame in the format negotiated in the above example (PCMU, two channels, 8000Hz sample rate) would comprise 1600 bytes and have the following layout:
        The number of samples per frame is variable and is up to the client. There is a tradeoff between higher latency (larger frames) and higher overhead (smaller frames). The client will guarantee that frames only contain whole samples for all channels (i.e. the bytes of individual samples will not be split across frames). The server must not make any assumptions about audio frame sizes and maintain a timeline of the audio stream by counting the samples.
        The position property in the message header represents the current position in the audio stream from the client's perspective when it sent the message. It is reported as time represented as ISO8601 Duration to avoid sample-rate dependence. It is computed as:

        position=\frac{samplesProcessed}{sampleRate}
        """

        if not self.speech_provider:
            self.logger.error(f"[{session_id}] No speech provider configured.")
            return

        conversation = await self.conversations_store.get(ws_session.conversation_id)
        media = conversation.media
        await self.speech_provider.handle_audio_frame(
            session_id, ws_session, media, data
        )
