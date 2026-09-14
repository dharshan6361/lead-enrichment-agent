"""Hand-verified ground-truth facts for the assignment's 3 test domains.

This exists so extraction QUALITY can be measured, not just asserted. Facts
below were verified manually against each company's public site/LinkedIn as
of the README's writing - update them if the underlying pages change.

Each field is optional: if a fact isn't known/verifiable, leave it as None
and the checker will simply skip scoring that field for that domain.
"""
from __future__ import annotations

GROUND_TRUTH: dict[str, dict] = {
    "postman.com": {
        "expected_keywords_in_overview": ["API"],
        "expected_leadership_names": ["Abhinav Asthana"],
        "expected_email_domains": ["postman.com"],
    },
    "supabase.com": {
        "expected_keywords_in_overview": ["Postgres", "open-source", "open source"],
        "expected_leadership_names": ["Paul Copplestone"],
        "expected_email_domains": ["supabase.com", "supabase.io"],
    },
    "vapi.ai": {
        "expected_keywords_in_overview": ["voice"],
        "expected_leadership_names": [],  # not verified - left blank intentionally
        "expected_email_domains": [],
    },
}
