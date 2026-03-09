# ─────────────────────────────────────────────────────────────────────────────
# courts.py  —  Courts to monitor (all levels except Small Claims)
#
# CanLII API uses db_id for the caseBrowse endpoint.
# RSS feeds are a fallback — format:
#   https://www.canlii.org/en/{province}/{db_id}/rss_new.xml
#
# NOTE: Some RSS db_ids differ from API db_ids (e.g. NL uses nlsc in RSS
# but nlsctd in the API). The API is preferred when CANLII_API_KEY is set.
# ─────────────────────────────────────────────────────────────────────────────

COURTS = [

    # ── Alberta ──────────────────────────────────────────────────────────────
    {
        "province":  "AB",
        "db_id":     "abqb",
        "name":      "Court of King's Bench of Alberta",
        "rss":       "https://www.canlii.org/en/ab/abqb/rss_new.xml",
    },
    {
        "province":  "AB",
        "db_id":     "abca",
        "name":      "Court of Appeal of Alberta",
        "rss":       "https://www.canlii.org/en/ab/abca/rss_new.xml",
    },

    # ── British Columbia ─────────────────────────────────────────────────────
    {
        "province":  "BC",
        "db_id":     "bcsc",
        "name":      "Supreme Court of British Columbia",
        "rss":       "https://www.canlii.org/en/bc/bcsc/rss_new.xml",
    },
    {
        "province":  "BC",
        "db_id":     "bcca",
        "name":      "Court of Appeal for British Columbia",
        "rss":       "https://www.canlii.org/en/bc/bcca/rss_new.xml",
    },

    # ── Manitoba ─────────────────────────────────────────────────────────────
    {
        "province":  "MB",
        "db_id":     "mbkb",
        "name":      "Court of King's Bench of Manitoba",
        "rss":       "https://www.canlii.org/en/mb/mbkb/rss_new.xml",
    },
    {
        "province":  "MB",
        "db_id":     "mbca",
        "name":      "Court of Appeal of Manitoba",
        "rss":       "https://www.canlii.org/en/mb/mbca/rss_new.xml",
    },

    # ── New Brunswick ────────────────────────────────────────────────────────
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

    # ── Newfoundland & Labrador ──────────────────────────────────────────────
    {
        "province":  "NL",
        "db_id":     "nlsctd",
        "name":      "Supreme Court of Newfoundland and Labrador",
        "rss":       "https://www.canlii.org/en/nl/nlsc/rss_new.xml",
    },
    {
        "province":  "NL",
        "db_id":     "nlca",
        "name":      "Court of Appeal of Newfoundland and Labrador",
        "rss":       "https://www.canlii.org/en/nl/nlca/rss_new.xml",
    },

    # ── Nova Scotia ──────────────────────────────────────────────────────────
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

    # ── Ontario ──────────────────────────────────────────────────────────────
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

    # ── Prince Edward Island ─────────────────────────────────────────────────
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

    # ── Quebec ───────────────────────────────────────────────────────────────
    {
        "province":  "QC",
        "db_id":     "qccs",
        "name":      "Quebec Superior Court",
        "rss":       "https://www.canlii.org/en/qc/qccs/rss_new.xml",
    },
    {
        "province":  "QC",
        "db_id":     "qcca",
        "name":      "Court of Appeal of Quebec",
        "rss":       "https://www.canlii.org/en/qc/qcca/rss_new.xml",
    },

    # ── Saskatchewan ─────────────────────────────────────────────────────────
    {
        "province":  "SK",
        "db_id":     "skkb",
        "name":      "Court of King's Bench for Saskatchewan",
        "rss":       "https://www.canlii.org/en/sk/skkb/rss_new.xml",
    },
    {
        "province":  "SK",
        "db_id":     "skca",
        "name":      "Court of Appeal for Saskatchewan",
        "rss":       "https://www.canlii.org/en/sk/skca/rss_new.xml",
    },

    # ── Federal ──────────────────────────────────────────────────────────────
    {
        "province":  "CA",
        "db_id":     "fct",
        "name":      "Federal Court",
        "rss":       "https://www.canlii.org/en/ca/fct/rss_new.xml",
    },
    {
        "province":  "CA",
        "db_id":     "fca",
        "name":      "Federal Court of Appeal",
        "rss":       "https://www.canlii.org/en/ca/fca/rss_new.xml",
    },
    {
        "province":  "CA",
        "db_id":     "csc-scc",
        "name":      "Supreme Court of Canada",
        "rss":       "https://www.canlii.org/en/ca/scc/rss_new.xml",
    },
]
