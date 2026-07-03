"""Runtime configuration, loaded from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "8192"))
MAX_AGENT_STEPS = int(os.environ.get("MAX_AGENT_STEPS", "12"))

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CUSTOMERS_PATH = DATA_DIR / "customers.json"
POLICY_PATH = DATA_DIR / "refund_policy.md"
