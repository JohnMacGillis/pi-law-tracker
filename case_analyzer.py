"""
case_analyzer.py
Sends case text to the Claude API and returns structured PI damages data.

The model is asked to:
  1. Determine whether the decision is a personal injury damages case.
  2. If yes, extract case type, a plain-language summary, and damages by category.

Response is always JSON so it can be stored directly in the CSV.
"""

import json
import logging
import time

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_DELAY_SECONDS

logger = logging.getLogger(__name__)

# max_retries=6 means the SDK will wait and retry automatically on 429s
# using exponential backoff (2s, 4s, 8s … up to ~64s between attempts)
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=6)

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM = """You are a legal research assistant for a Canadian personal injury law firm.
Analyse court decisions and extract structured damages information.
Respond ONLY with valid JSON — no explanation, no markdown code fences."""

_USER_TEMPLATE = """Case title: {title}
Court: {court}
Province: {province}

---BEGIN DECISION---
{text}
---END DECISION---

Determine whether this decision addresses the quantum or assessment of damages
in a personal injury matter. Relevant case types are:
  • Motor vehicle accident (MVA)
  • Slip and fall / trip and fall
  • Other negligence-based personal injury

Return ONLY this JSON object (fill null where information is not present):

{{
  "is_relevant": true | false,
  "case_type": "MVA" | "Slip and Fall" | "Trip and Fall" | "Other PI" | null,
  "summary": "<2-3 sentence plain-language summary of facts and outcome, or null>",
  "damages": {{
    "non_pecuniary":      "<dollar amount string e.g. '$75,000', or null>",
    "general_damages":    "<use only if non-pecuniary not itemised separately, or null>",
    "past_income_loss":   "<dollar amount or null>",
    "future_income_loss": "<dollar amount or null>",
    "cost_of_future_care":"<dollar amount or null>",
    "special_damages":    "<dollar amount or null>",
    "aggravated_punitive":"<dollar amount or null>",
    "total":              "<total damages awarded or null>"
  }},
  "notes": "<important caveats: e.g. liability split, contributory negligence reduction, appeal, costs awarded — or null>"
}}

If is_relevant is false, set all other fields to null.
"""

# ── Public function ───────────────────────────────────────────────────────────

def analyze_case(text: str, title: str, court: str = "", province: str = "") -> dict | None:
    """
    Send case text to Claude and return a parsed result dict, or None on failure.

    The returned dict has keys:
      is_relevant (bool), case_type, summary, damages (dict), notes
    """
    prompt = _USER_TEMPLATE.format(
        title=title,
        court=court,
        province=province,
        text=text,
    )

    # Polite delay to stay inside Claude API rate limits
    time.sleep(CLAUDE_DELAY_SECONDS)

    try:
        message = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Strip markdown fences if the model adds them despite the instruction
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 2 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        result = json.loads(raw)

        # Ensure damages sub-dict always exists
        result.setdefault("damages", {})
        return result

    except json.JSONDecodeError as exc:
        logger.error("JSON parse error for '%s': %s  |  raw=%s", title, exc, raw[:200])
        return None
    except anthropic.APIError as exc:
        logger.error("Anthropic API error for '%s': %s", title, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error analysing '%s': %s", title, exc)
        return None
