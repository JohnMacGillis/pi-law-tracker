# ─────────────────────────────────────────────────────────────────────────────
# courts.py  —  Courts to monitor (all levels except Small Claims)
#
# CanLII RSS feed format:
#   https://www.canlii.org/en/{province}/{db_id}/nav/date/rss.xml
#
# NOTE: If a feed returns a 404, the db_id may have changed on CanLII.
# Verify at: https://www.canlii.org/en/  (browse by province → court)
# ─────────────────────────────────────────────────────────────────────────────

COURTS = [

    # ── Nova Scotia ───────────────────────────────────────────────────────────
    {
        "province":  "NS",
        "db_id":     "nssc",
        "name":      "Nova Scotia Supreme Court",
        "rss":       "https://www.canlii.org/en/ns/nssc/nav/date/rss.xml",
    },
    {
        "province":  "NS",
        "db_id":     "nsca",
        "name":      "Nova Scotia Court of Appeal",
        "rss":       "https://www.canlii.org/en/ns/nsca/nav/date/rss.xml",
    },

    # ── New Brunswick ─────────────────────────────────────────────────────────
    # NB renamed Queen's Bench → King's Bench in 2022.
    # CanLII may still use "nbqb" — check and update db_id/rss if needed.
    {
        "province":  "NB",
        "db_id":     "nbkb",
        "name":      "Court of King's Bench of New Brunswick",
        "rss":       "https://www.canlii.org/en/nb/nbkb/nav/date/rss.xml",
    },
    {
        "province":  "NB",
        "db_id":     "nbca",
        "name":      "New Brunswick Court of Appeal",
        "rss":       "https://www.canlii.org/en/nb/nbca/nav/date/rss.xml",
    },

    # ── Prince Edward Island ──────────────────────────────────────────────────
    {
        "province":  "PE",
        "db_id":     "pesctd",
        "name":      "Supreme Court of PEI – Trial Division",
        "rss":       "https://www.canlii.org/en/pe/pesctd/nav/date/rss.xml",
    },
    {
        "province":  "PE",
        "db_id":     "pescad",
        "name":      "Supreme Court of PEI – Appeal Division",
        "rss":       "https://www.canlii.org/en/pe/pescad/nav/date/rss.xml",
    },

    # ── Newfoundland & Labrador ───────────────────────────────────────────────
    {
        "province":  "NL",
        "db_id":     "nlsc",
        "name":      "Supreme Court of Newfoundland and Labrador",
        "rss":       "https://www.canlii.org/en/nl/nlsc/nav/date/rss.xml",
    },
    {
        "province":  "NL",
        "db_id":     "nlca",
        "name":      "Court of Appeal of Newfoundland and Labrador",
        "rss":       "https://www.canlii.org/en/nl/nlca/nav/date/rss.xml",
    },

    # ── Ontario ───────────────────────────────────────────────────────────────
    {
        "province":  "ON",
        "db_id":     "onsc",
        "name":      "Ontario Superior Court of Justice",
        "rss":       "https://www.canlii.org/en/on/onsc/nav/date/rss.xml",
    },
    {
        "province":  "ON",
        "db_id":     "onca",
        "name":      "Court of Appeal for Ontario",
        "rss":       "https://www.canlii.org/en/on/onca/nav/date/rss.xml",
    },
    {
        "province":  "ON",
        "db_id":     "onscdc",
        "name":      "Ontario Divisional Court",
        "rss":       "https://www.canlii.org/en/on/onscdc/nav/date/rss.xml",
    },
]
