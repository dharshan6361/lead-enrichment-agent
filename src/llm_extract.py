"""LLM-backed structured extraction using Anthropic's tool-calling for a
strict JSON schema (no free-text parsing / no regex on model output).
"""
from __future__ import annotations

import logging
import os

from anthropic import Anthropic

from .models import EXTRACTION_TOOL_SCHEMA

logger = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = (
    "You are a B2B research analyst. You will be given cleaned text scraped "
    "from a company's public website (homepage plus a few subpages). Extract "
    "only what is explicitly supported by the provided text - do not invent "
    "names, emails, or facts. If leadership or contact info isn't present in "
    "the text, return empty lists rather than guessing. Call the "
    "record_company_intelligence tool exactly once with your findings."
)


class LLMExtractionError(RuntimeError):
    """Raised when the model fails to return a usable structured result."""


def build_user_prompt(domain: str, page_texts: dict[str, str]) -> str:
    sections = []
    for url, text in page_texts.items():
        sections.append(f"### Source: {url}\n{text}")
    joined = "\n\n".join(sections)
    return (
        f"Company domain: {domain}\n\n"
        f"Below is cleaned text from {len(page_texts)} page(s) of this company's "
        f"website. Extract structured company intelligence from it.\n\n{joined}"
    )


def extract_company_intelligence(
    client: Anthropic, domain: str, page_texts: dict[str, str]
) -> tuple[dict, int, int]:
    """Call the LLM with strict tool-calling and return (parsed_input, input_tokens, output_tokens).

    Raises LLMExtractionError on any failure so the caller can apply fallback
    logic without crashing the whole batch run.
    """
    if not page_texts:
        raise LLMExtractionError(f"No page text available for {domain}, skipping LLM call.")

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "record_company_intelligence"},
            messages=[{"role": "user", "content": build_user_prompt(domain, page_texts)}],
        )
    except Exception as exc:  # noqa: BLE001 - network/API errors must not crash the batch
        raise LLMExtractionError(f"Anthropic API call failed for {domain}: {exc}") from exc

    input_tokens = getattr(response.usage, "input_tokens", 0)
    output_tokens = getattr(response.usage, "output_tokens", 0)

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_company_intelligence":
            return block.input, input_tokens, output_tokens

    raise LLMExtractionError(f"Model did not return a tool_use block for {domain}.")


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Rough Sonnet-tier pricing estimate for cost-tracking/logging purposes."""
    input_rate_per_mtok = 3.0
    output_rate_per_mtok = 15.0
    return (input_tokens / 1_000_000) * input_rate_per_mtok + (
        output_tokens / 1_000_000
    ) * output_rate_per_mtok
