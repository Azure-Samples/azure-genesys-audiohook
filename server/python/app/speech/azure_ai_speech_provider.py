import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import ClassVar, cast

import azure.cognitiveservices.speech as speechsdk

from app.language.agent_assist import AgentAssistant

from ..enums import AzureGenesysEvent, ServerMessageType
from ..models import (
    AzureAISpeechSession,
    MediaChannelInfo,
    SummaryItem,
    TranscriptItem,
    WebSocketSessionStorage,
)
from ..storage.base_conversation_store import ConversationStore
from ..utils.event_entity_builder import (
    build_agent_assist_entity,
    build_agent_assist_utterance,
    build_transcript_entity,
)
from ..utils.identity import get_speech_token
from .speech_provider import SpeechProvider


class AzureAISpeechProvider(SpeechProvider):
    """Azure AI Speech implementation of SpeechProvider."""

    supported_languages: ClassVar[list[str]] = []

    def __init__(
        self,
        conversations_store: ConversationStore,
        send_event_callback: Callable[..., Awaitable[None]],
        logger: logging.Logger,
    ) -> None:
        self.conversations_store = conversations_store
        self.send_event = send_event_callback
        self.logger = logger

        # Load configuration from environment
        self.region: str | None = os.getenv("AZURE_SPEECH_REGION")
        self.speech_key: str | None = os.getenv("AZURE_SPEECH_KEY")
        self.speech_resource_id: str | None = os.getenv("AZURE_SPEECH_RESOURCE_ID")
        languages = os.getenv("AZURE_SPEECH_LANGUAGES", "en-US")
        self.supported_languages = languages.split(",") if languages else ["en-US"]

    async def initialize_session(
        self,
        session_id: str,
        ws_session: WebSocketSessionStorage,
        media: MediaChannelInfo,
    ) -> None:
        """Prepare audio push stream and launch recognition task."""
        audio_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=media.rate,
            bits_per_sample=8,
            channels=len(media.channels),
            wave_stream_format=speechsdk.AudioStreamWaveFormat.MULAW,
        )
        stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)

        # Get the absolute path to the provider.py script's directory
        provider_script_dir = os.path.dirname(os.path.abspath(__file__))

        # Calculate the path to the config file based on the provider.py's directory
        config_path = os.path.join(provider_script_dir, "../language/config.yaml")
        assist = AgentAssistant(config_path)

        ws_session.speech_session = AzureAISpeechSession(
            audio_buffer=stream,
            raw_audio=bytearray(),
            media=media,
            recognize_task=asyncio.create_task(
                self._recognize_speech(session_id, ws_session)
            ),
            assist=assist,
            assist_futures=[],
        )

    async def handle_audio_frame(
        self,
        session_id: str,
        ws_session: WebSocketSessionStorage,
        media: MediaChannelInfo,
        data: bytes,
    ) -> None:
        """Feed incoming chunks into the push stream and raw buffer."""
        if ws_session.speech_session is None:
            self.logger.error(f"[{session_id}] Session not initialized.")
            return

        try:
            speech_session = cast(AzureAISpeechSession, ws_session.speech_session)
            speech_session.audio_buffer.write(data)
        except Exception as ex:
            self.logger.error(f"[{session_id}] Write error: {ex}")

    async def shutdown_session(
        self,
        session_id: str,
        ws_session: WebSocketSessionStorage,
        finalize_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Signal end of audio and await recognition finish."""
        if ws_session.speech_session is None:
            self.logger.error(f"[{session_id}] Session not initialized.")
            return

        try:
            speech_session = cast(AzureAISpeechSession, ws_session.speech_session)
            speech_session.audio_buffer.close()
        except Exception as ex:
            self.logger.warning(f"[{session_id}] Close error: {ex}")

        task = speech_session.recognize_task
        if task:
            try:
                await task
            except Exception as ex:
                self.logger.error(f"[{session_id}] Recognition error: {ex}")

        # Finalize now — clean up session after recognition ends
        if finalize_callback:
            await finalize_callback()

    async def close(self) -> None:
        """No global cleanup needed for Azure Speech."""
        return None

    async def _recognize_speech(
        self,
        session_id: str,
        ws_session: WebSocketSessionStorage,
    ) -> None:
        """
        Configure SpeechRecognizer, wire callbacks, and drive the
        continuous-recognition loop until the audio stream is closed.
        """

        speech_session = cast(AzureAISpeechSession, ws_session.speech_session)
        media = speech_session.media
        is_multichannel = bool((media.channels) and len(media.channels) > 1)

        region = self.region
        endpoint = None
        if is_multichannel and region:
            endpoint = (
                f"wss://{region}.stt.speech.microsoft.com"
                "/speech/recognition/conversation/cognitiveservices/v1?setfeature=multichannel2"
            )

        if self.speech_key:
            speech_config = speechsdk.SpeechConfig(
                subscription=self.speech_key,
                region=None if is_multichannel else region,
                endpoint=endpoint,
            )
        else:
            token = get_speech_token(self.speech_resource_id)
            speech_config = speechsdk.SpeechConfig(
                auth_token=token,
                region=None if is_multichannel else region,
                endpoint=endpoint,
            )

        if len(self.supported_languages) > 1:
            speech_config.speech_recognition_language = self.supported_languages[0]
            auto_detect = None
        else:
            auto_detect = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=self.supported_languages
            )
            speech_config.set_property(
                speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode,
                "Continuous",
            )

        speech_config.output_format = speechsdk.OutputFormat.Detailed
        speech_config.request_word_level_timestamps()
        speech_config.enable_audio_logging()
        speech_config.set_profanity(speechsdk.ProfanityOption.Masked)
        speech_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationStrategy, "Semantic"
        )

        audio_in = speechsdk.audio.AudioConfig(stream=speech_session.audio_buffer)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_in,
            auto_detect_source_language_config=auto_detect,
        )

        loop = asyncio.get_running_loop()
        done_event = asyncio.Event()

        recognizer.recognizing.connect(
            lambda evt: loop.call_soon_threadsafe(self._on_recognizing, session_id, evt)
        )
        recognizer.recognized.connect(
            lambda evt: loop.call_soon_threadsafe(
                self._on_recognized,
                session_id,
                ws_session,
                is_multichannel,
                loop,
                evt,
            )
        )
        recognizer.session_stopped.connect(
            lambda evt: loop.call_soon_threadsafe(
                self._on_session_stopped, session_id, ws_session, loop, done_event, evt
            )
        )

        self.logger.info(f"[{session_id}] Starting continuous recognition.")
        await asyncio.to_thread(recognizer.start_continuous_recognition_async().get)
        await done_event.wait()
        await asyncio.to_thread(recognizer.stop_continuous_recognition_async().get)
        self.logger.info(f"[{session_id}] Recognition stopped.")

        # Wait for final summary suggestion if there is
        await self._await_pending_assist(ws_session)
        await self._flush_summary(session_id, ws_session)

    def _on_recognizing(
        self, session_id: str, evt: speechsdk.SpeechRecognitionEventArgs
    ) -> None:
        """Log intermediate (partial) recognition results."""
        self.logger.info(f"[{session_id}] Recognizing: {evt.result.text}")

    def _on_recognized(
        self,
        session_id: str,
        ws_session: WebSocketSessionStorage,
        is_multichannel: bool,
        loop: asyncio.AbstractEventLoop,
        evt: speechsdk.SpeechRecognitionEventArgs,
    ) -> None:
        """Handle final recognition, update store, and emit partial transcript."""
        result = json.loads(evt.result.json)
        status = result.get("RecognitionStatus")

        if status == "InitialSilenceTimeout":
            self.logger.warning(f"[{session_id}] Initial silence timeout.")
            return

        def normalize_transcript_text(text: str) -> str:
            """Normalize transcript text by ensuring proper capitalization and punctuation."""
            if text and text[-1] not in ".!?":
                text = text[0].upper() + text[1:] + "."
            elif text and not text[0].isupper():
                text = text[0].upper() + text[1:]
            return text

        text = normalize_transcript_text(evt.result.text)

        offset = result.get("Offset", 0)
        duration = result.get("Duration", 0)
        start = f"PT{offset / 10_000_000:.2f}S"  # convert 100ns ticks to seconds
        end = f"PT{(offset + duration) / 10_000_000:.2f}S"
        words = result.get("NBest", [{}])[0].get("Words", [])

        channel = result.get("Channel") if is_multichannel else 1

        item = TranscriptItem(
            channel=channel,
            text=text,
            start=start,
            end=end,
        )

        transcript_entity = build_transcript_entity(
            channel_id="CUSTOMER",
            transcript_text=text,
            words=words,
            is_final=True,
            offset=offset,
            duration=duration,
        )

        async def _update() -> None:
            await self.conversations_store.append_transcript(
                ws_session.conversation_id, item
            )

        asyncio.run_coroutine_threadsafe(_update(), loop)
        asyncio.run_coroutine_threadsafe(
            self.send_event(
                event=AzureGenesysEvent.PARTIAL_TRANSCRIPT,
                session_id=session_id,
                message=item.model_dump(),
            ),
            loop,
        )

        asyncio.run_coroutine_threadsafe(
            ws_session.send_message_callback(
                type=ServerMessageType.EVENT,
                client_message={"id": session_id},
                parameters={"entities": [transcript_entity]},  # Client expect a list
            ),
            loop,
        )

        first_word = words[0] if words else {}
        speech_session = cast(AzureAISpeechSession, ws_session.speech_session)
        future = asyncio.create_task(
            self.handle_agent_assist(
                session_id,
                ws_session,
                text,
                first_word.get("Offset", offset),
                duration,
                end,
            )
        )
        speech_session.assist_futures.append(future)

    def _on_session_stopped(
        self,
        session_id: str,
        ws_session: WebSocketSessionStorage,
        loop: asyncio.AbstractEventLoop,
        done_event: asyncio.Event,
        evt: speechsdk.SessionEventArgs,
    ) -> None:
        """Signal that continuous recognition has finished."""
        self.logger.info(f"[{session_id}] Session stopped: {evt.session_id}")
        done_event.set()

    async def handle_agent_assist(
        self,
        session_id: str,
        ws_session: WebSocketSessionStorage,
        text: str,
        offset: int,
        duration: int,
        end: str,
        confidence: float = 0.85,
    ):
        speech_session = cast(AzureAISpeechSession, ws_session.speech_session)
        if not (speech_session and speech_session.assist):
            return

        summary = await speech_session.assist.on_transcription(text)
        if summary:
            summary_item = SummaryItem(text=summary.content, transcription_end=end)
            await self.conversations_store.append_summary(
                ws_session.conversation_id, summary_item
            )

            utterance = build_agent_assist_utterance(
                position=f"PT{offset / 10_000_000:.2f}S",
                text=summary.content,
                language="en-US",  # Optional: Make dynamic
                confidence=confidence,
                channel="CUSTOMER",
                is_final=True,
                duration=f"PT{duration / 10_000_000:.2f}S",
            )

            agent_assist_entity = build_agent_assist_entity(
                utterances=[utterance],
                suggestions=[],
            )

            try:
                await ws_session.send_message_callback(
                    type=ServerMessageType.EVENT,
                    client_message={"id": session_id},
                    parameters={"entities": [agent_assist_entity]},
                )
            except Exception as e:
                self.logger.warning(
                    f"[{session_id}] Failed to send assist message: {e}"
                )

    async def _flush_summary(
        self, session_id: str, ws_session: WebSocketSessionStorage
    ) -> None:
        speech_session = cast(AzureAISpeechSession, ws_session.speech_session)
        if hasattr(speech_session, "assist") and speech_session.assist:
            summary = await speech_session.assist.flush_summary()
            if summary:
                summary_item = SummaryItem(
                    text=summary.content,
                    transcription_end="end",
                )
                await self.conversations_store.append_summary(
                    ws_session.conversation_id, summary_item
                )

                utterance = build_agent_assist_utterance(
                    position=0,
                    text=summary.content,
                    language="en-US",  # update if needed
                    confidence=0.85,
                    channel="CUSTOMER",
                    is_final=True,
                    duration="PT1S",
                )

                entity = build_agent_assist_entity(
                    utterances=[utterance],
                    suggestions=[],
                )

                try:
                    await ws_session.send_message_callback(
                        type=ServerMessageType.EVENT,
                        client_message={"id": session_id},
                        parameters={"entities": [entity]},
                    )
                except Exception as e:
                    self.logger.warning(f"[{session_id}] Failed to send summary: {e}")

    async def _await_pending_assist(self, ws_session: WebSocketSessionStorage):
        speech_session = cast(AzureAISpeechSession, ws_session.speech_session)
        pending = speech_session.assist_futures
        if not pending:
            return

        self.logger.info(
            f"[{ws_session.conversation_id}] Awaiting {len(pending)} assist tasks."
        )
        for future in pending:
            try:
                await asyncio.wrap_future(future)
            except Exception as e:
                self.logger.warning(f"Assist future failed: {e}")
