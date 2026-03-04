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

from config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_DELAY_SECONDS,
    OPENAI_API_KEY, OPENAI_MODEL,
)

logger = logging.getLogger(__name__)

# max_retries=0 — we handle all failures ourselves with an immediate OpenAI fallback
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)

# ── OpenAI fallback client (created lazily so the key is optional) ────────────
_openai_client = None

def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            return None
        import openai as _openai_module
        _openai_client = _openai_module.OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _analyze_with_openai(prompt: str) -> dict | None:
    """
    Fallback: send the same prompt to OpenAI GPT-4o-mini on any Anthropic
    failure (429, 529, network errors, etc.).
    Uses json_object response_format for reliability.
    Returns a parsed result dict, or None on failure.
    """
    client = _get_openai_client()
    if client is None:
        logger.warning("    OpenAI fallback not configured — set OPENAI_API_KEY in config.py")
        return None
    try:
        logger.info("    Trying OpenAI fallback (%s) …", OPENAI_MODEL)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=1024,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
        )
        raw    = response.choices[0].message.content.strip()
        result = json.loads(raw)
        result.setdefault("damages", {})
        logger.info("    OpenAI fallback succeeded")
        return result
    except Exception as exc:
        logger.error("    OpenAI fallback failed: %s", exc)
        return None

# Tracks when we last called the API so we only sleep the *remaining* gap,
# not the full CLAUDE_DELAY_SECONDS every time.
_last_call_time: float = 0.0

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM = """You are a legal research assistant for a Canadian plaintiff-side law firm.
Analyse court decisions and extract structured damages information.
The firm handles personal injury, long-term disability (LTD) insurance, and class actions.
Respond ONLY with valid JSON — no explanation, no markdown code fences."""

_USER_TEMPLATE = """Case title: {title}
Court: {court}
Province: {province}

---BEGIN DECISION---
{text}
---END DECISION---

Determine whether this decision is relevant to a plaintiff-side firm that handles:
  • Personal injury (PI) damages cases
  • Long-term disability (LTD) insurance disputes
  • Class actions involving personal injury, LTD, or product liability

Relevant case types:
  PI cases:
    • Motor vehicle accident (MVA)
    • Slip and fall / trip and fall
    • Other negligence-based personal injury

  LTD cases:
    • Insurer denied or terminated long-term disability benefits
    • Bad faith claim against disability insurer
    • Court assesses arrears of benefits, future benefits, or punitive damages

  Class actions:
    • Certification hearings or common issues trials
    • Aggregate damages awarded to a plaintiff class
    • Product liability, mass tort, or LTD class proceedings

IMPORTANT classification rule — case_type priority:
  • If the underlying injury arose from a MOTOR VEHICLE ACCIDENT, COLLISION,
    or any type of car/truck/motorcycle crash, ALWAYS classify as "MVA" —
    even if the case also involves an insurance dispute, accident benefits,
    disability claim, or bad faith against an insurer.
  • Only classify as "LTD" when the dispute is purely about long-term
    disability insurance benefits with NO underlying motor vehicle accident.
  • The presence of an insurer as a party does NOT make a case LTD.
    Most MVA cases involve insurance — they are still MVA cases.

Return ONLY this JSON object (fill null where information is not present):

{{
  "is_relevant": true | false,
  "case_type": "MVA" | "Slip and Fall" | "Trip and Fall" | "Other PI" | "LTD" | "Class Action" | null,
  "summary": "<plain-language summary of facts and outcome, 3 sentences MAX, or null>",
  "damages": {{
    "non_pecuniary":      "<PI only: dollar amount e.g. '$75,000', or null>",
    "general_damages":    "<use only if non-pecuniary not itemised separately, or null>",
    "past_income_loss":   "<PI: past income loss; LTD: past benefits denied — dollar amount or null>",
    "future_income_loss": "<PI: future income loss; LTD: future benefits at risk — dollar amount or null>",
    "cost_of_future_care":"<dollar amount or null>",
    "special_damages":    "<dollar amount or null>",
    "aggravated_punitive":"<dollar amount or null — especially relevant for LTD bad faith>",
    "total":              "<total damages or aggregate class award or null>"
  }},
  "notes": "<important caveats: e.g. liability split, contributory negligence, certification granted/denied, per-member range, appeal pending, costs awarded — or null>"
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

    # Adaptive delay — only sleep however much time is still needed since the
    # last API call.  PDF fetch + pre-filter already burned several seconds,
    # so this is often zero or just a few seconds.
    global _last_call_time
    elapsed = time.time() - _last_call_time
    wait    = max(0.0, CLAUDE_DELAY_SECONDS - elapsed)
    if wait > 0.5:
        logger.info("    Rate-limit pause: %.1fs", wait)
        time.sleep(wait)
    _last_call_time = time.time()

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
    except Exception as exc:
        # Any Anthropic failure (429, 529, network, etc.) → immediate OpenAI fallback
        logger.warning("    Anthropic failed for '%s': %s — switching to OpenAI …", title, exc)
        return _analyze_with_openai(prompt)
