"""Connexions aux fournisseurs LLM : Gemini (résumé) et Claude (judge).

Les clients sont construits paresseusement : importer ce module ne requiert ni
GEMINI_API_KEY ni CLAUDE_API_KEY (testabilité, judge optionnel).
"""

import os
from functools import lru_cache

import anthropic
from dotenv import load_dotenv
from google import genai

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

GEMINI_MODEL = "gemini-2.5-flash"
JUDGE_MODEL = "claude-haiku-4-5"


@lru_cache(maxsize=1)
def gemini_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


@lru_cache(maxsize=1)
def judge_client() -> anthropic.Anthropic:
    # Pas de retry SDK : le judge est optionnel, une panne doit échouer vite.
    return anthropic.Anthropic(api_key=os.environ.get("CLAUDE_API_KEY", ""), max_retries=0)
