"""Runtime configuration, loaded from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "8192"))
MAX_AGENT_STEPS = int(os.environ.get("MAX_AGENT_STEPS", "12"))

# Voice pipeline (optional; active when OPENAI_API_KEY is set).
STT_MODEL = os.environ.get("STT_MODEL", "gpt-4o-mini-transcribe")
TTS_MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "nova")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CUSTOMERS_PATH = DATA_DIR / "customers.json"
POLICY_PATH = DATA_DIR / "refund_policy.md"
