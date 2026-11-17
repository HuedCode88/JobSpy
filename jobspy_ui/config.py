"""Search-profile config: defaults, valid values, validation.

A "profile" is the UI's version of one of your scripts. Its `config` is a plain
dict that maps almost 1:1 onto scrape_jobs() kwargs, plus a list of search_terms
(your scripts loop over a list of terms; so does the runner).
"""
from __future__ import annotations

SITES = [
    {"id": "indeed", "label": "Indeed", "default": True, "note": "Most reliable, little rate limiting"},
    {"id": "linkedin", "label": "LinkedIn", "default": True, "note": "Rate-limits fast; proxies recommended"},
    {"id": "zip_recruiter", "label": "ZipRecruiter", "default": True, "note": "US/Canada only"},
    {"id": "glassdoor", "label": "Glassdoor", "default": True, "note": "Rate-limits after ~30 requests/IP"},
    {"id": "google", "label": "Google Jobs", "default": False, "note": "Currently unavailable upstream"},
    {"id": "bayt", "label": "Bayt", "default": False, "note": "Middle East"},
    {"id": "naukri", "label": "Naukri", "default": False, "note": "India"},
    {"id": "bdjobs", "label": "BDJobs", "default": False, "note": "Bangladesh"},
]
VALID_SITES = [s["id"] for s in SITES]
JOB_TYPES = ["fulltime", "parttime", "internship", "contract"]
DESCRIPTION_FORMATS = ["markdown", "html", "plain"]

DEFAULT_CONFIG = {
    "search_terms": [],
    "sites": ["indeed", "linkedin", "zip_recruiter", "glassdoor"],
    "location": "",
    "country_indeed": "USA",
    "results_wanted": 20,       # per site, per search term
    "hours_old": 24,            # None = no age filter
    "distance": 50,
    "job_type": None,           # one of JOB_TYPES or None
    "is_remote": False,
    "easy_apply": None,         # True or None (None = don't filter)
    "fetch_description": True,
    "description_format": "markdown",
    "enforce_annual_salary": False,
    "proxies": [],              # ["user:pass@host:port", "localhost"]
    "only_new": False,          # export only jobs not seen in earlier runs of this profile
}


def _int(value, name, lo, hi, allow_none=False):
    if value in (None, "", 0) and allow_none:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number")
    if not lo <= n <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return n


def validate_config(raw: dict) -> dict:
    """Return a cleaned config (all keys present) or raise ValueError with a user-facing message."""
    cfg = {**DEFAULT_CONFIG, **{k: v for k, v in (raw or {}).items() if k in DEFAULT_CONFIG}}

    terms = cfg["search_terms"]
    if isinstance(terms, str):
        terms = terms.splitlines()
    terms = [t.strip() for t in terms if isinstance(t, str) and t.strip()]
    seen, unique = set(), []
    for t in terms:  # de-dupe terms, keep order
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    if not unique:
        raise ValueError("Add at least one search term")
    if len(unique) > 50:
        raise ValueError("Max 50 search terms per profile")
    cfg["search_terms"] = unique

    sites = [s for s in cfg["sites"] if s in VALID_SITES]
    if not sites:
        raise ValueError("Pick at least one job board")
    cfg["sites"] = sites

    cfg["location"] = (cfg["location"] or "").strip()
    cfg["country_indeed"] = (cfg["country_indeed"] or "USA").strip()
    cfg["results_wanted"] = _int(cfg["results_wanted"], "Results wanted", 1, 1000)
    cfg["hours_old"] = _int(cfg["hours_old"], "Hours old", 1, 24 * 365, allow_none=True)
    cfg["distance"] = _int(cfg["distance"], "Distance", 1, 500)

    if cfg["job_type"] in ("", None):
        cfg["job_type"] = None
    elif cfg["job_type"] not in JOB_TYPES:
        raise ValueError(f"Job type must be one of {', '.join(JOB_TYPES)}")

    if cfg["description_format"] not in DESCRIPTION_FORMATS:
        raise ValueError("Invalid description format")

    for key in ("is_remote", "fetch_description", "enforce_annual_salary", "only_new"):
        cfg[key] = bool(cfg[key])
    cfg["easy_apply"] = True if cfg["easy_apply"] else None

    proxies = cfg["proxies"]
    if isinstance(proxies, str):
        proxies = proxies.splitlines()
    cfg["proxies"] = [p.strip() for p in proxies if isinstance(p, str) and p.strip()]
    return cfg
