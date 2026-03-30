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

Legislation citator filter (API-only):
  When the CanLII API key is available, the caseCitator endpoint can tell us
  what legislation a case cites BEFORE downloading the PDF.  Cases citing
  criminal, family, tax, or immigration statutes are rejected.  Cases citing
  PI-relevant statutes (Highway Traffic Act, Occupiers' Liability, etc.)
  are fast-tracked.

Expected to eliminate ~75-85% of RSS entries (contract, criminal, family,
administrative, regulatory, IP, tax, etc.).
"""

import logging

logger = logging.getLogger(__name__)


# ── Legislation-based prefilter (uses caseCitator API) ────────────────────────
# Checked BEFORE downloading the PDF — lightweight API call replaces heavy fetch.

# If a case cites ANY of these → definitely not PI → skip PDF download
_LEGISLATION_EXCLUDE = [
    "criminal code",
    "divorce act",
    "income tax act",
    "excise tax act",
    "bankruptcy and insolvency act",
    "companies' creditors arrangement act",
    "immigration and refugee protection act",
    "citizenship act",
    "canada labour code",
    "labour relations act",
    "employment standards act",
    "workplace safety and insurance act",
    "workers compensation act",
    "workers' compensation act",
    "child, youth and family services act",
    "child and family services act",
    "youth criminal justice act",
    "controlled drugs and substances act",
    "cannabis act",
    "corrections and conditional release act",
    "extradition act",
    "securities act",
    "patent act",
    "copyright act",
    "trade-marks act",
    "trademarks act",
    "planning act",
    "residential tenancies act",
    "condominium act",
    "construction act",
    "municipal act",
]

# If a case cites ANY of these → strong PI signal → pass immediately
_LEGISLATION_PI = [
    "highway traffic act",
    "traffic safety act",
    "motor vehicle act",
    "occupiers' liability act",
    "occupiers liability act",
    "negligence act",
    "fatal accidents act",
    "family law act",            # Part V dependant claims
    "dog owners' liability act",
    "dog owners liability act",
    "consumer protection act",
    "insurance act",
    "insurance (vehicle) act",
    "automobile insurance act",
]

# Neutral — common in ALL civil litigation, don't indicate PI or non-PI.
# If a case ONLY cites these, it's inconclusive (could be PI).
_LEGISLATION_NEUTRAL = [
    "courts of justice act",
    "rules of civil procedure",
    "rules of court",
    "limitations act",
    "limitation of actions act",
    "evidence act",
    "canada evidence act",
    "interpretation act",
    "judicature act",
    "court of appeal act",
    "charter of rights",          # constitutional issues appear in any area
    "constitutional act",
    "official languages act",
    "crown liability",
    "proceedings against the crown",
    "judicial review procedure",
    "statutory powers procedure",
    "class proceedings act",      # could be PI class action
    "law and equity act",
    "court order enforcement act",
    "trustee act",
    "health care costs recovery act",  # PI-adjacent
]


def _matches_list(title: str, legislation_list: list[str]) -> str | None:
    """Return the first matching entry from the list, or None."""
    for entry in legislation_list:
        if entry in title:
            return entry
    return None


def prequalify_legislation(cited_titles: list[str]) -> tuple[bool | None, str]:
    """
    Filter based on legislation cited by the case (from caseCitator API).

    Logic:
      1. No citator data         → inconclusive (pass through)
      2. Cites EXCLUDE statute    → reject
      3. Cites PI statute         → fast-track
      4. ALL cited are NEUTRAL    → inconclusive (could be PI, proceed)
      5. Cites unknown statutes   → reject (specialized area, not PI)

    Returns
    -------
    (False, reason) → definitely not PI — skip PDF download
    (True,  reason) → strong PI signal — proceed
    (None,  reason) → inconclusive — fall through to title/keyword filters
    """
    if not cited_titles:
        return None, "no cited legislation data"

    # 1. Check for exclusion legislation
    for leg in cited_titles:
        match = _matches_list(leg, _LEGISLATION_EXCLUDE)
        if match:
            return False, f"cites excluded legislation: '{leg}'"

    # 2. Check for PI-relevant legislation
    for leg in cited_titles:
        match = _matches_list(leg, _LEGISLATION_PI)
        if match:
            return True, f"cites PI legislation: '{leg}'"

    # 3. Check if ALL cited statutes are neutral (common civil procedure)
    has_unknown = False
    unknown_examples = []
    for leg in cited_titles:
        if not _matches_list(leg, _LEGISLATION_NEUTRAL):
            has_unknown = True
            unknown_examples.append(leg)

    if not has_unknown:
        return None, f"cited {len(cited_titles)} neutral statute(s) only — inconclusive"

    # 4. Cites specialized (non-PI, non-neutral) statutes → reject
    preview = unknown_examples[0][:60] if unknown_examples else "?"
    if len(unknown_examples) > 1:
        return False, f"cites non-PI legislation: '{preview}' (+{len(unknown_examples)-1} more)"
    return False, f"cites non-PI legislation: '{preview}'"


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

    # Long-term disability (LTD) insurance — highly specific phrases
    "long-term disability",
    "long term disability",
    "ltd benefits",
    "disability insurer",
    "group disability",
    "own occupation",
    "disability policy",
    "disability insurance policy",
    "wrongful denial of benefits",
    "bad faith denial",

    # Class actions
    "class action",
    "class proceeding",
    "certification order",
    "representative plaintiff",
    "common issues trial",
    "class members",

    # Wrongful death / fatal injury claims
    "wrongful death",
    "fatal injuries",
    "fatal accident",
    "loss of dependency",
    "dependency claim",
    "fatal injury",
    "death of the plaintiff",
    "deceased plaintiff",

    # Medical malpractice / professional negligence
    "medical malpractice",
    "surgical error",
    "failure to diagnose",
    "misdiagnosis",
    "medical negligence",
    "hospital negligence",
    "nursing negligence",
    "informed consent",       # almost always medical context in PI

    # Product liability
    "product liability",
    "products liability",
    "defective product",
    "manufacturer's liability",
    "manufacturing defect",
    "design defect",

    # Dog / animal attacks
    "dog bite",
    "animal attack",

    # Common Canadian PI phrasing
    "motor vehicle",          # civil context covered — criminal caught by exclusions
    "injuries sustained",
    "sustained injuries",
    "plaintiff was injured",
    "injured in",
    "loss of enjoyment of life",
    "loss of amenities",
    "loss of capacity",
    "functional limitations",
    "pain, suffering",
    "general and special damages",
    "negligently caused",
    "the collision",
    "the accident",           # very common in PI decisions
]

# ── Tier 2: SUPPORTING keywords ───────────────────────────────────────────────
# These appear in many civil cases, not just PI.
# Require 1+ to pass (RSS summaries are short; Claude handles false positives).

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
    "disability benefits",
    "bad faith",
    "aggregate damages",
    "common issues",
]

# ── EXCLUSION keywords ────────────────────────────────────────────────────────
# Any one of these → hard reject, even if PI keywords are present.
# Covers criminal, family, tax, IP, immigration, labour, etc.

_EXCLUSION = [
    # Criminal law — only terms that CANNOT appear in a civil PI decision
    "criminal code",
    "the accused",
    "guilty plea",
    "not guilty",
    "criminal negligence causing",  # more specific than plain "criminal negligence"
    "manslaughter",
    "indictment",
    # NOTE: removed "criminal conviction" — a civil PI case against a drunk driver
    # may reference the defendant's conviction.  Also removed " sentence ",
    # "crown counsel", "crown attorney", " parole ",
    # "impaired driving", "dangerous driving" — these can all appear in civil PI
    # cases (e.g. plaintiff sues drunk driver; government as defendant).

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
# RSS summaries are short — even 1 supporting keyword in a civil case
# is enough reason to download the full text and let Claude decide.
_SUPPORTING_THRESHOLD = 1


# ─────────────────────────────────────────────────────────────────────────────
# Title-only quick excludes — checked BEFORE downloading the PDF
# ─────────────────────────────────────────────────────────────────────────────

_TITLE_EXCLUDES = [
    "r. v. ",          # Criminal — most common CanLII pattern
    " r v ",           # Criminal alternate
    "r. v ",
    "regina v. ",
    "the king v. ",
    "the queen v. ",
    "his majesty v. ",
    "her majesty v. ",
    " divorce",
    "child custody",
    "child support",
    "spousal support",
    "parenting order",
    "protection order",
    "restraining order",
    "adoption of",
    "bankruptcy of",
    "in the matter of the bankruptcy",
    "income tax appeal",
    "tax court",
    "immigration appeal",
    "refugee appeal",
    "labour arbitration",
    "grievance of",
    "judicial review of",
    "certiorari",
    "habeas corpus",
    # Ontario SABS / LAT — not tort damages
    " and licence appeal tribunal",
    " v. licence appeal tribunal",
    "statutory accident benefits",
    "sabs",
]


def prequalify_title(title: str) -> tuple[bool | None, str]:
    """
    Fast title-only check run BEFORE downloading the PDF.

    Returns
    -------
    (False, reason) → definitely not PI — skip, no download needed
    (None,  reason) → unclear from title alone — download PDF and run full check
    """
    t = title.lower()

    for kw in _TITLE_EXCLUDES:
        if kw in t:
            return False, f"title keyword: '{kw.strip()}'"

    return None, "title unclear — downloading PDF for full check"


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
        f"supporting keywords — likely not PI/LTD/class action"
    )
