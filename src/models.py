"""Pydantic schemas describing the structured intelligence we extract per domain."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TeamMember(BaseModel):
    name: str = Field(description="Full name of the leadership/team member.")
    title: Optional[str] = Field(
        default=None, description="Role or job title, e.g. 'CEO & Co-Founder'."
    )
    linkedin_url: Optional[str] = Field(
        default=None, description="LinkedIn profile URL if discoverable on the page."
    )


class ContactPoints(BaseModel):
    emails: list[str] = Field(
        default_factory=list,
        description="Generic/public emails found on the site (contact@, sales@, support@, etc).",
    )
    other_contacts: list[str] = Field(
        default_factory=list,
        description="Other public contact channels found (contact form URL, phone, social handles).",
    )


class CompanyIntelligence(BaseModel):
    """The full structured record produced for a single company domain."""

    domain: str = Field(description="The input domain this record was built from.")
    company_overview: str = Field(
        description="A concise 2-sentence summary of what the company does."
    )
    target_audience: str = Field(
        description="Who the product/service is built for, i.e. their ICP."
    )
    contact_points: ContactPoints = Field(default_factory=ContactPoints)
    leadership: list[TeamMember] = Field(
        default_factory=list,
        description="Key leadership / team members found in page content.",
    )
    data_confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Estimated 0.0-1.0 score for completeness/quality of the extracted data.",
    )
    pages_crawled: list[str] = Field(
        default_factory=list, description="List of URLs actually fetched for this domain."
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal errors/warnings encountered while processing this domain.",
    )


# JSON schema handed to the LLM as a tool definition (Anthropic tool-use / structured output).
EXTRACTION_TOOL_SCHEMA = {
    "name": "record_company_intelligence",
    "description": "Record structured company intelligence extracted from crawled web pages.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_overview": {
                "type": "string",
                "description": "A concise 2-sentence summary of what the company does.",
            },
            "target_audience": {
                "type": "string",
                "description": "Who the product/service is built for (ICP).",
            },
            "contact_points": {
                "type": "object",
                "properties": {
                    "emails": {"type": "array", "items": {"type": "string"}},
                    "other_contacts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["emails", "other_contacts"],
            },
            "leadership": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "title": {"type": ["string", "null"]},
                        "linkedin_url": {"type": ["string", "null"]},
                    },
                    "required": ["name"],
                },
            },
            "data_confidence_score": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "0.0-1.0 estimate of extraction completeness/quality.",
            },
        },
        "required": [
            "company_overview",
            "target_audience",
            "contact_points",
            "leadership",
            "data_confidence_score",
        ],
    },
}
