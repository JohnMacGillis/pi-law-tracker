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
The firm handles personal injury damages, occupiers' liability, MVA liability,
and long-term disability (LTD) / private insurance disputes.
Respond ONLY with valid JSON — no explanation, no markdown code fences."""

_USER_TEMPLATE = """Case title: {title}
Court: {court}
Province: {province}

---BEGIN DECISION---
{text}
---END DECISION---

Determine whether this decision is relevant to a plaintiff-side firm.

RELEVANT case types (classify as one of these or mark not relevant):

  "MVA Damages" — A tort or accident benefits claim arising from a motor vehicle
    collision where the court is ASSESSING OR AWARDING DAMAGES (non-pecuniary,
    income loss, future care, etc.). The lawsuit must be about the collision itself.

  "MVA Liability" — A motor vehicle accident case where the court is deciding
    FAULT, liability, or contributory negligence — not just damages.

  "Occupiers Liability" — A claim under occupiers' liability legislation or
    common-law duty of care owed by a property owner/occupier (slip and fall,
    trip and fall, dangerous premises, inadequate maintenance, ice/snow, etc.).

  "LTD" — A dispute about long-term disability insurance benefits, private
    disability insurance, or any claim for disability benefits under an insurance
    contract (denial, termination, bad faith, arrears). Even if the underlying
    disability was caused by an MVA, if the LAWSUIT is against the insurer over
    the disability policy, classify as "LTD".

  "Other PI" — Any other negligence-based personal injury case that awards
    damages (medical malpractice, product liability, dog bite, assault, etc.)
    but does NOT fit the above categories.

NOT RELEVANT — mark is_relevant: false for ALL of the following:
  • Ontario SABS (Statutory Accident Benefits Schedule) disputes — these are
    LAT/FSCO proceedings about accident benefits entitlement, not tort damages
  • Costs decisions — cases that are ONLY about legal costs/tariffs, not
    substantive damages
  • Criminal cases, family law, immigration, labour/employment, tax,
    administrative/regulatory proceedings
  • Cases where an MVA is mentioned only as background/history but the actual
    dispute is about something unrelated
  • Workers' compensation, CPP disability, EI appeals
  • Certification motions or class actions (tracked separately)
  • Procedural motions (discovery, adjournments, case management) with no
    substantive damages assessment

IMPORTANT classification rules:
  • Classify based on what the LAWSUIT IS ACTUALLY ABOUT, not background facts.
  • If a motor vehicle accident is mentioned as historical context but the lawsuit
    is about something else (LTD benefits, CPP, a later slip and fall), do NOT
    classify as MVA.
  • A case can involve both liability and damages — if it does, use the primary
    focus. If liability is contested AND damages are assessed, prefer
    "MVA Damages" (it's the more useful classification).

Return ONLY this JSON object (fill null where information is not present):

{{
  "is_relevant": true | false,
  "case_type": "MVA Damages" | "MVA Liability" | "Occupiers Liability" | "LTD" | "Other PI" | null,
  "summary": "<plain-language summary of facts and outcome, 3 sentences MAX, or null>",
  "damages": {{
    "non_pecuniary":      "<dollar amount e.g. '$75,000', or null>",
    "general_damages":    "<use only if non-pecuniary not itemised separately, or null>",
    "past_income_loss":   "<PI: past income loss; LTD: past benefits denied — dollar amount or null>",
    "future_income_loss": "<PI: future income loss; LTD: future benefits at risk — dollar amount or null>",
    "cost_of_future_care":"<dollar amount or null>",
    "special_damages":    "<dollar amount or null>",
    "aggravated_punitive":"<dollar amount or null — especially relevant for LTD bad faith>",
    "total":              "<total damages or aggregate class award or null>"
  }},
  "notes": "<important caveats ONLY — e.g. liability split, contributory negligence, appeal pending. Do NOT repeat dollar amounts already listed in the damages fields above. Keep brief or null.>"
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
