# ─────────────────────────────────────────────────────────────────────────────
# courts.py  —  Courts to monitor (all levels except Small Claims)
#
# CanLII RSS feed format (updated 2026):
#   https://www.canlii.org/en/{province}/{db_id}/rss_new.xml
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
        "rss":       "https://www.canlii.org/en/ns/nssc/rss_new.xml",
    },
    {
        "province":  "NS",
        "db_id":     "nsca",
        "name":      "Nova Scotia Court of Appeal",
        "rss":       "https://www.canlii.org/en/ns/nsca/rss_new.xml",
    },

    # ── New Brunswick ─────────────────────────────────────────────────────────
    {
        "province":  "NB",
        "db_id":     "nbkb",
        "name":      "Court of King's Bench of New Brunswick",
        "rss":       "https://www.canlii.org/en/nb/nbkb/rss_new.xml",
    },
    {
        "province":  "NB",
        "db_id":     "nbca",
        "name":      "New Brunswick Court of Appeal",
        "rss":       "https://www.canlii.org/en/nb/nbca/rss_new.xml",
    },

    # ── Prince Edward Island ──────────────────────────────────────────────────
    {
        "province":  "PE",
        "db_id":     "pesctd",
        "name":      "Supreme Court of PEI – Trial Division",
        "rss":       "https://www.canlii.org/en/pe/pesctd/rss_new.xml",
    },
    {
        "province":  "PE",
        "db_id":     "pescad",
        "name":      "Supreme Court of PEI – Appeal Division",
        "rss":       "https://www.canlii.org/en/pe/pescad/rss_new.xml",
    },

    # ── Newfoundland & Labrador ───────────────────────────────────────────────
    {
        "province":  "NL",
        "db_id":     "nlsc",
        "name":      "Supreme Court of Newfoundland and Labrador",
        "rss":       "https://www.canlii.org/en/nl/nlsc/rss_new.xml",
    },
    {
        "province":  "NL",
        "db_id":     "nlca",
        "name":      "Court of Appeal of Newfoundland and Labrador",
        "rss":       "https://www.canlii.org/en/nl/nlca/rss_new.xml",
    },

    # ── Ontario ───────────────────────────────────────────────────────────────
    {
        "province":  "ON",
        "db_id":     "onsc",
        "name":      "Ontario Superior Court of Justice",
        "rss":       "https://www.canlii.org/en/on/onsc/rss_new.xml",
    },
    {
        "province":  "ON",
        "db_id":     "onca",
        "name":      "Court of Appeal for Ontario",
        "rss":       "https://www.canlii.org/en/on/onca/rss_new.xml",
    },
    {
        "province":  "ON",
        "db_id":     "onscdc",
        "name":      "Ontario Divisional Court",
        "rss":       "https://www.canlii.org/en/on/onscdc/rss_new.xml",
    },
]
