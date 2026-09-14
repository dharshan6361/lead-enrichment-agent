"""Autonomous lead enrichment agent.

Usage:
    python -m src.main postman.com supabase.com vapi.ai
    python -m src.main --input domains.txt --output output/output.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from .crawler import crawl_domain
from .extractor import clean_html_to_text, extract_raw_emails
from .linkedin_search import enrich_missing_linkedin_urls, is_enabled as linkedin_search_enabled
from .llm_extract import LLMExtractionError, estimate_cost_usd, extract_company_intelligence
from .models import CompanyIntelligence

load_dotenv()  # populates os.environ from a local .env file, if present


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("lead_agent")

# How many domains to process in parallel. Kept deliberately low: each domain
# opens its own browser context, and we don't want to burst-hammer target
# sites or the Anthropic API when scaling past a handful of test domains.
MAX_CONCURRENT_DOMAINS = 3


async def process_domain(
    browser, client: Anthropic, domain: str
) -> tuple[CompanyIntelligence, float]:
    """Run the full pipeline for one domain. Never raises - always returns a record.

    Returns (record, cost_usd) - cost_usd is 0.0 if the LLM call never ran or failed.
    """
    logger.info("Processing %s", domain)
    errors: list[str] = []
    cost_usd = 0.0

    crawl_result = await crawl_domain(browser, domain)
    errors.extend(crawl_result.errors)

    if not crawl_result.succeeded:
        logger.warning("Crawl produced no pages for %s", domain)
        return (
            CompanyIntelligence(
                domain=domain,
                company_overview="Unable to retrieve content for this domain.",
                target_audience="Unknown - site unreachable.",
                data_confidence_score=0.0,
                pages_crawled=[],
                errors=errors or ["No pages could be fetched."],
            ),
            cost_usd,
        )

    page_texts: dict[str, str] = {}
    fallback_emails: set[str] = set()
    for fetched in crawl_result.pages:
        try:
            page_texts[fetched.url] = clean_html_to_text(fetched.html)
            fallback_emails.update(extract_raw_emails(fetched.html))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Failed to clean content from {fetched.url}: {exc}")

    try:
        extracted, input_tokens, output_tokens = extract_company_intelligence(
            client, domain, page_texts
        )
        cost_usd = estimate_cost_usd(input_tokens, output_tokens)
        logger.info(
            "%s: %d input tokens, %d output tokens, ~$%.5f",
            domain,
            input_tokens,
            output_tokens,
            cost_usd,
        )
    except LLMExtractionError as exc:
        logger.warning(str(exc))
        errors.append(str(exc))
        return (
            CompanyIntelligence(
                domain=domain,
                company_overview="LLM extraction failed; see errors for detail.",
                target_audience="Unknown - extraction failed.",
                data_confidence_score=0.0,
                pages_crawled=[p.url for p in crawl_result.pages],
                errors=errors,
            ),
            cost_usd,
        )

    # Merge regex-found emails the model may have missed, without duplicating.
    llm_emails = set(extracted.get("contact_points", {}).get("emails", []))
    merged_emails = sorted(llm_emails | fallback_emails)
    extracted.setdefault("contact_points", {})["emails"] = merged_emails

    # Bonus: fall back to a web search for any leadership LinkedIn URLs the
    # site itself didn't expose. No-op if TAVILY_API_KEY isn't configured.
    if extracted.get("leadership"):
        try:
            extracted["leadership"] = await enrich_missing_linkedin_urls(
                extracted["leadership"], domain
            )
        except Exception as exc:  # noqa: BLE001 - bonus feature must never break the batch
            errors.append(f"LinkedIn search enrichment failed: {exc}")

    try:
        record = CompanyIntelligence(
            domain=domain,
            pages_crawled=[p.url for p in crawl_result.pages],
            errors=errors,
            **extracted,
        )
    except Exception as exc:  # noqa: BLE001 - schema validation failure shouldn't crash the batch
        logger.warning("Validation failed for %s: %s", domain, exc)
        errors.append(f"Schema validation failed: {exc}")
        record = CompanyIntelligence(
            domain=domain,
            company_overview=str(extracted.get("company_overview", "N/A")),
            target_audience=str(extracted.get("target_audience", "N/A")),
            data_confidence_score=0.0,
            pages_crawled=[p.url for p in crawl_result.pages],
            errors=errors,
        )

    return record, cost_usd


async def run(domains: list[str], output_path: Path) -> None:
    client = Anthropic()  # reads ANTHROPIC_API_KEY from environment
    if linkedin_search_enabled():
        logger.info("TAVILY_API_KEY found - LinkedIn search fallback is ENABLED.")
    else:
        logger.info("TAVILY_API_KEY not set - LinkedIn search fallback is DISABLED (optional).")

    # Cap concurrent domain processing so we don't hammer target sites or the
    # LLM API in a tight burst - polite, and avoids tripping rate limits/bot
    # detection when scaling past a handful of test domains.
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOMAINS)
    results: list[dict] = []
    total_cost = 0.0
    start = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        async def guarded(domain: str) -> tuple[dict, float]:
            async with semaphore:
                try:
                    record, cost_usd = await process_domain(browser, client, domain)
                except Exception as exc:  # noqa: BLE001 - absolute last-resort guard per domain
                    logger.error("Unhandled failure for %s: %s", domain, exc)
                    record = CompanyIntelligence(
                        domain=domain,
                        company_overview="Processing failed unexpectedly.",
                        target_audience="Unknown",
                        data_confidence_score=0.0,
                        errors=[f"Unhandled exception: {exc}"],
                    )
                    cost_usd = 0.0
                return record.model_dump(), cost_usd

        try:
            # Domains run concurrently (bounded by MAX_CONCURRENT_DOMAINS) rather
            # than strictly sequentially - one slow/hanging site no longer stalls
            # the whole batch behind it.
            outcomes = await asyncio.gather(*(guarded(d) for d in domains))
            results = [record for record, _ in outcomes]
            total_cost = sum(cost for _, cost in outcomes)
        finally:
            await browser.close()

    elapsed = time.time() - start
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    logger.info("Done in %.1fs. Wrote %d records to %s", elapsed, len(results), output_path)
    logger.info("Estimated cumulative LLM cost: $%.4f (see README for methodology)", total_cost)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous lead enrichment agent.")
    parser.add_argument("domains", nargs="*", help="Company domains, e.g. postman.com")
    parser.add_argument("--input", type=Path, help="Path to a newline-delimited domains file.")
    parser.add_argument(
        "--output", type=Path, default=Path("output/output.json"), help="Output JSON path."
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    domains = list(args.domains)
    if args.input:
        domains.extend(
            line.strip() for line in args.input.read_text().splitlines() if line.strip()
        )
    if not domains:
        domains = ["postman.com", "supabase.com", "vapi.ai"]
        logger.info("No domains provided - defaulting to the assignment's test set: %s", domains)

    asyncio.run(run(domains, args.output))


if __name__ == "__main__":
    main()
