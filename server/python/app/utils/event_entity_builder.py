from typing import Any, Literal, TypedDict
from uuid import uuid4


class EventEntityBase(TypedDict):
    type: str
    data: dict[str, Any]


class EventEntityAgentAssistSuggestionBase(TypedDict):
    type: str
    id: str
    confidence: float
    position: str | None


MediaChannel = Literal["CUSTOMER", "AGENT"]


def build_transcript_entity(
    channel_id: str,
    transcript_text: str,
    words: list[dict[str, Any]],
    is_final: bool,
    offset: int,
    duration: int,
    language: str = "en-US",
) -> EventEntityBase:
    token_data = []
    for word in words:
        token_data.append(
            {
                "type": "word",
                "value": word["Word"],
                "confidence": word.get("Confidence", 0.85),
                "offset": f"PT{word['Offset'] / 10_000_000:.2f}S",
                "duration": f"PT{word['Duration'] / 10_000_000:.2f}S",
                "language": language,
            }
        )

    transcript_data = {
        "id": str(uuid4()),
        "channelId": channel_id,
        "isFinal": is_final,
        "offset": f"PT{offset / 10_000_000:.2f}S",
        "duration": f"PT{duration / 10_000_000:.2f}S",
        "alternatives": [
            {
                "confidence": sum(w.get("Confidence", 0.85) for w in words)
                / len(words),
                "languages": [language],
                "interpretations": [
                    {
                        "type": "display",
                        "transcript": transcript_text,
                        "tokens": token_data,
                    }
                ],
            }
        ],
    }

    return {"type": "transcript", "data": transcript_data}


def build_agent_assist_entity(
    utterances: list[dict[str, Any]] | None = None,
    suggestions: list[dict[str, Any]] | None = None,
) -> EventEntityBase:
    if suggestions is None:
        suggestions = []
    if utterances is None:
        utterances = []
    return {
        "type": "agentassist",
        "data": {
            "id": str(uuid4()),
            "utterances": utterances or [],
            "suggestions": suggestions or [],
        },
    }


def build_agent_assist_utterance(
    position: str,
    text: str,
    language: str,
    confidence: float,
    channel: str,
    is_final: bool,
    duration: str = "PT1S",
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "position": position,
        "duration": duration,
        "text": text,
        "language": language,
        "confidence": confidence,
        "channel": channel,
        "isFinal": is_final,
    }


def build_faq_suggestion(
    question: str, answer: str, confidence: float, position: str = "PT0S"
) -> dict[str, Any]:
    base: EventEntityAgentAssistSuggestionBase = {
        "type": "faq",
        "id": str(uuid4()),
        "confidence": confidence,
        "position": position,
    }
    return {
        **base,
        "question": question,
        "answer": answer,
    }


def build_article_suggestion(
    title: str,
    excerpts: list[str],
    document_uri: str,
    confidence: float,
    metadata: dict[str, str] | None = None,
    position: str = "PT0S",
) -> dict[str, Any]:
    if metadata is None:
        metadata = {}
    base: EventEntityAgentAssistSuggestionBase = {
        "type": "article",
        "id": str(uuid4()),
        "confidence": confidence,
        "position": position,
    }
    return {
        **base,
        "title": title,
        "excerpts": excerpts,
        "documentUri": document_uri,
        "metadata": metadata or {},
    }
