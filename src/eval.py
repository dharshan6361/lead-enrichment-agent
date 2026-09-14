"""Score a generated output.json against hand-verified ground truth.

This is what proves extraction quality rather than just claiming it - run it
after `python -m src.main` and cite the numbers in your README/Loom.

Usage:
    python -m src.eval output/output.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .ground_truth import GROUND_TRUTH


def score_record(record: dict, truth: dict) -> dict:
    """Return per-field pass/fail plus notes for one domain's record."""
    result = {"domain": record["domain"], "checks": [], "score": None}
    checks = result["checks"]

    # 1. Overview should mention at least one expected keyword.
    expected_keywords = truth.get("expected_keywords_in_overview", [])
    if expected_keywords:
        overview = record.get("company_overview", "").lower()
        hit = any(kw.lower() in overview for kw in expected_keywords)
        checks.append(
            {
                "field": "company_overview",
                "pass": hit,
                "detail": f"expected one of {expected_keywords}",
            }
        )

    # 2. Leadership names should be found (case-insensitive substring match).
    expected_names = truth.get("expected_leadership_names", [])
    if expected_names:
        found_names = {m.get("name", "").lower() for m in record.get("leadership", [])}
        for name in expected_names:
            hit = any(name.lower() in fn for fn in found_names)
            checks.append(
                {"field": f"leadership:{name}", "pass": hit, "detail": "expected name found"}
            )

    # 3. Emails should come from an expected domain (catches hallucinated addresses).
    expected_email_domains = truth.get("expected_email_domains", [])
    if expected_email_domains:
        emails = record.get("contact_points", {}).get("emails", [])
        hit = any(any(d in e for d in expected_email_domains) for e in emails) if emails else False
        checks.append(
            {
                "field": "contact_points.emails",
                "pass": hit,
                "detail": f"expected domain in {expected_email_domains}, got {emails}",
            }
        )

    # 4. Confidence score should be internally consistent: no errors -> should
    #    not silently claim 0.0, and errors present -> should not claim 1.0.
    has_errors = bool(record.get("errors"))
    confidence = record.get("data_confidence_score", 0.0)
    consistent = not (has_errors and confidence > 0.8) and not (not has_errors and confidence == 0.0)
    checks.append(
        {
            "field": "data_confidence_score",
            "pass": consistent,
            "detail": f"score={confidence}, errors={has_errors}",
        }
    )

    scored = [c for c in checks if c["pass"] is not None]
    result["score"] = round(sum(c["pass"] for c in scored) / len(scored), 2) if scored else None
    return result


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/output.json")
    if not output_path.exists():
        print(f"No output file at {output_path}. Run `python -m src.main` first.")
        sys.exit(1)

    records = json.loads(output_path.read_text())
    report = []
    for record in records:
        truth = GROUND_TRUTH.get(record["domain"])
        if truth is None:
            print(f"(skipping {record['domain']}: no ground truth defined)")
            continue
        report.append(score_record(record, truth))

    print(json.dumps(report, indent=2))

    scored = [r["score"] for r in report if r["score"] is not None]
    if scored:
        overall = round(sum(scored) / len(scored), 2)
        print(f"\nOverall accuracy across {len(scored)} domain(s): {overall * 100:.0f}%")


if __name__ == "__main__":
    main()
