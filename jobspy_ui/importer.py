"""Turn one of your existing scripts into a profile WITHOUT running it.

Uses `ast` only: finds a list literal assigned to a variable like `search_terms`, and the
keyword arguments of the first `scrape_jobs(...)` call that are plain literals. Anything
dynamic (e.g. `search_term=term`) is ignored. User code is never executed.
"""
from __future__ import annotations

import ast

from config import DEFAULT_CONFIG

KEY_MAP = {"site_name": "sites"}  # everything else shares its name with the config key
IGNORED = {"search_term", "verbose", "linkedin_fetch_description"}


def import_script(source: str) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Not valid Python: {exc.msg} (line {exc.lineno})")

    cfg: dict = {}
    warnings: list[str] = []

    # 1) the list of search terms: first assignment of a list of strings to a name containing "term"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any("term" in n.lower() for n in names):
                try:
                    vals = ast.literal_eval(node.value)
                except ValueError:
                    continue
                if vals and all(isinstance(v, str) for v in vals):
                    cfg["search_terms"] = list(vals)
                    break

    # 2) kwargs of the scrape_jobs call
    call = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Call)
         and (getattr(n.func, "id", None) == "scrape_jobs" or getattr(n.func, "attr", None) == "scrape_jobs")),
        None,
    )
    if call is None:
        warnings.append("No scrape_jobs(...) call found; only search terms were imported.")
    else:
        for kw in call.keywords:
            if kw.arg is None or kw.arg in IGNORED:
                continue
            key = KEY_MAP.get(kw.arg, kw.arg)
            if key not in DEFAULT_CONFIG:
                warnings.append(f"Ignored unsupported argument: {kw.arg}")
                continue
            try:
                cfg[key] = ast.literal_eval(kw.value)
            except ValueError:
                warnings.append(f"Skipped {kw.arg}: value isn't a plain literal")
        sv = next((k for k in call.keywords if k.arg == "search_term"), None)
        if sv is not None and "search_terms" not in cfg:
            try:
                cfg["search_terms"] = [ast.literal_eval(sv.value)]
            except ValueError:
                warnings.append("search_term isn't a literal and no list of terms was found")

    return {"config": cfg, "warnings": warnings}
