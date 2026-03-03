"""
case_prefilter.py
Keyword-based pre-qualification of court decisions.

Runs BEFORE the Claude API call to discard obvious non-PI cases at zero cost.
Expected to eliminate ~80% of RSS entries (contract, criminal, family, admin law).

Strategy:
  1. Hard-reject on strong exclusion keywords (criminal, family, tax, etc.)
  2. Count PI-positive keywords in the text
  3. Pass to Claude only if enough PI keywords are found

The filter errs on the side of inclusion — a few false positives (sent to
Claude unnecessarily) are fine; false negatives (real PI cases skipped) are not.
"""

import logging

logger = logging.getLogger(__name__)


# ── PI-positive keywords ───────────────────────────────────────────────────────
# Finding 2+ of these → likely worth sending to Claude

_PI_KEYWORDS = [
    # Core damages vocabulary
    "non-pecuniary",
    "nonpecuniary",
    "pain and suffering",
    "general damages",
    "special damages",
    "pecuniary damages",
    "future care",
    "cost of future care",
    "income loss",
    "loss of income",
    "loss of earning",
    "future earning",
    "aggravated damages",
    "punitive damages",
    "quantum of damages",
    "assessment of damages",
    "damages awarded",

    # Injury / accident types
    "personal injury",
    "bodily injury",
    "motor vehicle accident",
    "motor vehicle collision",
    " mva ",
    "slip and fall",
    "trip and fall",
    "occupiers' liability",
    "occupier's liability",
    "occupiers liability",
    "dog bite",
    "product liability",
    "medical malpractice",
    "medical negligence",
    "wrongful death",

    # Medical / clinical indicators
    "chronic pain",
    "soft tissue",
    "whiplash",
    "fracture",
    "traumatic brain injury",
    "spinal cord",
    "disc herniation",
    "physiotherapy",
    "medical expenses",
    "treatment costs",
    "functional capacity",
    "disability",
    "permanent impairment",

    # Legal concepts common in PI
    "tort",
    "duty of care",
    "standard of care",
    "contributory negligence",
    "vicarious liability",
    "plaintiff's injuries",

    # Insurance indicators
    "icbc",            # BC auto insurer
    "insurance adjuster",
    "accident benefit",
    "statutory accident",
]

# ── Strong exclusion keywords ─────────────────────────────────────────────────
# Any one of these → very unlikely to be a PI damages case → skip immediately

_EXCLUSION_KEYWORDS = [
    # Criminal law
    "criminal code",
    "criminal negligence",
    "the accused",
    "guilty plea",
    "not guilty",
    " sentence ",
    "imprisonment",
    "probation",
    " parole ",
    "crown counsel",
    "crown attorney",
    "indictment",
    "conviction",

    # Family law
    "divorce act",
    "matrimonial",
    "child custody",
    "child support",
    "spousal support",
    "parenting order",
    "adoption order",
    "child protection",

    # Tax / financial
    "income tax act",
    "tax court",
    "tax appeal",
    "excise tax",
    "hst appeal",
    "bankruptcy and insolvency",

    # Regulatory / administrative
    "immigration and refugee",
    "refugee protection",
    "deportation",
    "zoning bylaw",
    "municipal planning",
    "labour arbitration",
    "collective agreement",
    "grievance arbitration",

    # IP / commercial
    "intellectual property",
    "trademark infringement",
    "patent infringement",
    "copyright infringement",
    "securities commission",
]

# Number of PI keywords required to pass through to Claude
_THRESHOLD = 2


# ─────────────────────────────────────────────────────────────────────────────

def prequalify(text: str, title: str = "") -> tuple[bool, str]:
    """
    Determine whether a case is likely a PI damages matter worth analysing.

    Parameters
    ----------
    text  : Full case text (may be truncated)
    title : Case title from RSS feed (checked separately for extra signal)

    Returns
    -------
    (True,  reason_string)  → pass to Claude
    (False, reason_string)  → skip, no API call needed
    """
    combined = (title + " " + text).lower()

    # ── Hard reject on exclusion keywords ────────────────────────────────────
    for kw in _EXCLUSION_KEYWORDS:
        if kw in combined:
            return False, f"exclusion keyword matched: '{kw}'"

    # ── Count PI-positive keywords ────────────────────────────────────────────
    found = [kw for kw in _PI_KEYWORDS if kw in combined]

    if len(found) >= _THRESHOLD:
        preview = ", ".join(f"'{k}'" for k in found[:4])
        return True, f"{len(found)} PI keyword(s) found: {preview}"

    return False, (
        f"only {len(found)} PI keyword(s) found "
        f"(need {_THRESHOLD}) — likely not a PI damages case"
    )
