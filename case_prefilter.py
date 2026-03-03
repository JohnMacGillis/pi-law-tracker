"""
case_prefilter.py
Keyword-based pre-qualification of court decisions.

Runs BEFORE the Claude API call to discard obvious non-PI cases at zero cost.

Two-tier matching strategy:
  Tier 1 — HIGH-CONFIDENCE keywords: finding just ONE of these almost
            certainly means it's a PI damages case.  Pass immediately.

  Tier 2 — SUPPORTING keywords: individually too broad, but 3+ together
            suggest PI.  Pass if threshold met.

  EXCLUSION keywords: any match → hard reject regardless of other keywords.

Expected to eliminate ~75-85% of RSS entries (contract, criminal, family,
administrative, regulatory, IP, tax, etc.).
"""

import logging

logger = logging.getLogger(__name__)


# ── Tier 1: HIGH-CONFIDENCE PI keywords ──────────────────────────────────────
# Finding ANY ONE of these → almost certainly a PI damages case.
# These terms rarely (if ever) appear in non-PI decisions.

_HIGH_CONFIDENCE = [
    # The defining Canadian PI damages category
    "non-pecuniary",
    "nonpecuniary",

    # PI-specific damages heads
    "pain and suffering",
    "cost of future care",
    "future care costs",
    "loss of housekeeping",
    "loss of competitive advantage",
    "in trust claim",

    # Accident types that are almost exclusively PI
    "slip and fall",
    "trip and fall",
    "motor vehicle accident",
    "motor vehicle collision",
    "automobile accident",
    " mva ",
    "(mva)",

    # Injury-specific clinical terms
    "whiplash",
    "soft tissue injury",
    "chronic pain syndrome",
    "traumatic brain injury",
    "acquired brain injury",
    "spinal cord injury",
    "disc herniation",
    "rotator cuff",

    # PI-specific legal phrases
    "personal injury damages",
    "quantum of damages",
    "assessment of damages",
    "occupiers' liability act",
    "occupiers liability act",
    "dog owners' liability",
    "dog owners liability",
    "icbc",
]

# ── Tier 2: SUPPORTING keywords ───────────────────────────────────────────────
# These appear in many civil cases, not just PI.
# Require 3+ to pass (individually too broad).

_SUPPORTING = [
    "general damages",
    "special damages",
    "pecuniary damages",
    "income loss",
    "loss of income",
    "loss of earnings",
    "future earnings",
    "aggravated damages",
    "punitive damages",
    "tort",
    "duty of care",
    "standard of care",
    "contributory negligence",
    "bodily injury",
    "personal injury",
    "plaintiff's injuries",
    "the plaintiff suffered",
    "injured plaintiff",
    "chronic pain",
    "physiotherapy",
    "medical expenses",
    "disability",
    "permanent impairment",
    "functional capacity",
    "accident benefits",
    "statutory accident benefits",
]

# ── EXCLUSION keywords ────────────────────────────────────────────────────────
# Any one of these → hard reject, even if PI keywords are present.
# Covers criminal, family, tax, IP, immigration, labour, etc.

_EXCLUSION = [
    # Criminal law
    "criminal code",
    "the accused",
    "guilty plea",
    "not guilty",
    "criminal negligence",
    "manslaughter",
    "impaired driving",
    "dangerous driving",
    " sentence ",
    "imprisonment",
    "probation order",
    " parole ",
    "crown counsel",
    "crown attorney",
    "indictment",
    "criminal conviction",

    # Family law
    "divorce act",
    "matrimonial property",
    "child custody",
    "child support",
    "spousal support",
    "parenting order",
    "adoption order",
    "child protection",
    "children's aid",

    # Tax
    "income tax act",
    "tax court of canada",
    "tax appeal",
    "excise tax act",
    "gst/hst",

    # Insolvency
    "bankruptcy and insolvency act",
    "companies' creditors arrangement",
    "trustee in bankruptcy",
    "proposal to creditors",

    # Regulatory / administrative
    "immigration and refugee",
    "refugee protection division",
    "deportation order",
    "labour relations board",
    "collective agreement",
    "grievance arbitration",
    "workers' compensation appeal",
    "workplace safety and insurance",
    "wsib appeal",

    # IP / commercial
    "patent infringement",
    "trademark infringement",
    "copyright infringement",
    "passing off",
    "securities commission",
    "securities act",

    # Municipal / land use
    "zoning bylaw",
    "official plan",
    "committee of adjustment",
    "land titles act",
    "land registry",
    "expropriation act",
]

# Thresholds
_SUPPORTING_THRESHOLD = 3   # need this many tier-2 keywords if no tier-1


# ─────────────────────────────────────────────────────────────────────────────

def prequalify(text: str, title: str = "") -> tuple[bool, str]:
    """
    Determine whether a case is likely a PI damages matter.

    Parameters
    ----------
    text  : Full case text (from PDF)
    title : Case title from RSS feed

    Returns
    -------
    (True,  reason_string)  → send to Claude
    (False, reason_string)  → skip — no API call
    """
    combined = (title + " " + text).lower()

    # ── Hard reject on exclusion keywords ────────────────────────────────────
    for kw in _EXCLUSION:
        if kw in combined:
            return False, f"exclusion keyword: '{kw}'"

    # ── Tier 1: one high-confidence match = definite pass ────────────────────
    for kw in _HIGH_CONFIDENCE:
        if kw in combined:
            return True, f"high-confidence keyword: '{kw}'"

    # ── Tier 2: need 3+ supporting keywords ──────────────────────────────────
    found = [kw for kw in _SUPPORTING if kw in combined]
    if len(found) >= _SUPPORTING_THRESHOLD:
        preview = ", ".join(f"'{k}'" for k in found[:4])
        return True, f"{len(found)} supporting keywords: {preview}"

    return False, (
        f"no high-confidence keyword; only {len(found)}/{_SUPPORTING_THRESHOLD} "
        f"supporting keywords — likely not PI"
    )
