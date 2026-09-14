"""Headless-browser crawling: fetch a homepage, discover relevant subpages, and
render JS-heavy content cleanly using Playwright.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Subpage slugs we actively look for, in priority order.
RELEVANT_SLUGS = [
    "about",
    "about-us",
    "company",
    "team",
    "our-team",
    "leadership",
    "contact",
    "contact-us",
    "pricing",
]

MAX_SUBPAGES = 5
NAV_TIMEOUT_MS = 15_000


@dataclass
class FetchedPage:
    url: str
    html: str
    status: int | None = None


@dataclass
class CrawlResult:
    domain: str
    pages: list[FetchedPage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return len(self.pages) > 0


def _same_site(base_domain: str, url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        return host == base_domain.lower().removeprefix("www.") or host.endswith(
            "." + base_domain.lower().removeprefix("www.")
        )
    except ValueError:
        return False


async def _safe_goto(page: Page, url: str) -> tuple[str | None, int | None]:
    """Navigate to a URL, tolerating timeouts / 404s / bot blocks. Returns (html, status)."""
    try:
        response = await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        # Give lazy/JS-rendered content a brief moment to settle.
        try:
            await page.wait_for_load_state("networkidle", timeout=4_000)
        except PlaywrightTimeoutError:
            pass  # Not all sites go idle (polling widgets, chat bots) - fine to proceed.

        status = response.status if response else None
        if status is not None and status >= 400:
            logger.warning("Non-OK status %s for %s", status, url)
            return None, status

        html = await page.content()
        return html, status
    except PlaywrightTimeoutError:
        logger.warning("Timeout loading %s", url)
        return None, None
    except Exception as exc:  # noqa: BLE001 - crawler must never crash the whole run
        logger.warning("Failed to load %s: %s", url, exc)
        return None, None


async def _discover_subpage_links(page: Page, base_url: str, base_domain: str) -> list[str]:
    """Pull same-site links off the homepage and rank ones matching relevant slugs."""
    try:
        hrefs: list[str] = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Link discovery failed for %s: %s", base_url, exc)
        return []

    candidates: dict[str, int] = {}
    for href in hrefs:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if not _same_site(base_domain, absolute):
            continue
        path = urlparse(absolute).path.lower().strip("/")
        for priority, slug in enumerate(RELEVANT_SLUGS):
            if slug in path:
                clean = absolute.split("#")[0].rstrip("/")
                candidates.setdefault(clean, priority)
                break

    ranked = sorted(candidates.items(), key=lambda kv: kv[1])
    return [url for url, _ in ranked[:MAX_SUBPAGES]]


async def crawl_domain(browser: Browser, domain: str) -> CrawlResult:
    """Fetch homepage + up to MAX_SUBPAGES relevant subpages for a single domain."""
    result = CrawlResult(domain=domain)
    homepage_url = f"https://{domain}"

    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 900},
    )
    page = await context.new_page()

    try:
        html, status = await _safe_goto(page, homepage_url)
        if html is None:
            result.errors.append(f"Homepage unreachable (status={status}) for {homepage_url}")
            # Try the bare http fallback once before giving up on this domain.
            html, status = await _safe_goto(page, f"http://{domain}")
            if html is None:
                return result

        result.pages.append(FetchedPage(url=page.url, html=html, status=status))

        subpage_urls = await _discover_subpage_links(page, page.url, domain)
        for url in subpage_urls:
            sub_html, sub_status = await _safe_goto(page, url)
            if sub_html is None:
                result.errors.append(f"Skipped unreachable subpage: {url}")
                continue
            result.pages.append(FetchedPage(url=page.url, html=sub_html, status=sub_status))

    finally:
        await context.close()

    return result
