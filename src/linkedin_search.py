"""Bonus feature: search-engine fallback to find LinkedIn URLs for leadership
team members whose profile link wasn't discoverable directly on the company
site. Uses Tavily (https://tavily.com) - free tier covers this comfortably.

This module is fully optional. If TAVILY_API_KEY isn't set, callers should
skip it entirely; nothing else in the pipeline depends on it.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_tavily_client = None  # lazy-initialized singleton


def is_enabled() -> bool:
    """Whether a Tavily API key is configured."""
    return bool(os.environ.get("TAVILY_API_KEY"))


def _get_client():
    global _tavily_client
    if _tavily_client is None:
        from tavily import TavilyClient  # imported lazily so it's truly optional

        _tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily_client


def find_linkedin_url(person_name: str, company_domain: str) -> str | None:
    """Search for a person's LinkedIn profile URL given their name + company.

    Returns None on any failure (missing key, no results, API error) rather
    than raising - this is a bonus enrichment step and must never break the
    main pipeline.
    """
    if not person_name or not is_enabled():
        return None

    query = f'"{person_name}" {company_domain} site:linkedin.com/in'

    try:
        client = _get_client()
        response = client.search(query=query, max_results=3, search_depth="basic")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily search failed for %s (%s): %s", person_name, company_domain, exc)
        return None

    for result in response.get("results", []):
        url = result.get("url", "")
        if "linkedin.com/in/" in url:
            return url.split("?")[0]  # strip tracking params

    return None


async def enrich_missing_linkedin_urls(leadership: list[dict], company_domain: str) -> list[dict]:
    """Fill in linkedin_url for any leadership entries missing it.

    Runs synchronously under the hood (Tavily's SDK is sync); wrapped as an
    async function so it drops cleanly into main.py's async flow without a
    separate executor for this small, infrequent bonus call.
    """
    if not is_enabled():
        return leadership

    for member in leadership:
        if not member.get("linkedin_url") and member.get("name"):
            found = find_linkedin_url(member["name"], company_domain)
            if found:
                member["linkedin_url"] = found
                logger.info("Found LinkedIn URL for %s via search fallback", member["name"])

    return leadership
