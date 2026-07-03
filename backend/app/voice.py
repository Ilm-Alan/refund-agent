"""Voice I/O: speech-to-text in, text-to-speech out.

Voice is deliberately a thin layer over the same agent loop that serves the
chat: the transcript goes through run_turn with the same tools, the same
policy gate, and the same event bus, so a spoken request cannot reach any
code path a typed one could not.

The client targets the OpenAI audio API shape and takes no credential or
endpoint arguments; OPENAI_API_KEY and OPENAI_BASE_URL choose the provider
(any OpenAI-compatible endpoint works).
"""

from __future__ import annotations

import openai

from app.config import STT_MODEL, TTS_MODEL, TTS_VOICE

_client: openai.AsyncOpenAI | None = None


def get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI()
    return _client


def configured() -> bool:
    import os

    return bool(os.environ.get("OPENAI_API_KEY"))


async def transcribe(audio: bytes, filename: str) -> str:
    """Turn recorded audio into text."""
    result = await get_client().audio.transcriptions.create(
        model=STT_MODEL,
        file=(filename, audio),
    )
    return result.text.strip()


async def synthesize(text: str) -> bytes:
    """Turn the agent's reply into speech (mp3)."""
    response = await get_client().audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=text,
        response_format="mp3",
    )
    return response.content
