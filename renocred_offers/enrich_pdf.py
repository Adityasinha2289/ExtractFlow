"""Linked-eligibility-PDF enrichment (Master Plan Section 5 item 2, Section 21 item 2).

The plan's sharpest concrete finding is that issuer campaign offers routinely
link a separate "Outlet List" or "Model List" PDF that is the *actual*
eligibility scope, while the offer tile carries only a summary. A crawler that
reads the tile alone systematically over-generalises eligibility -- it will tell
a user an offer applies at a store or to a model where it does not.

So linked PDFs are treated as first-class ingestion targets: fetched, parsed,
and folded back into the offer's eligibility block. An offer whose linked PDF
could not be parsed keeps ``parsed: false`` and retains its review flag, rather
than quietly presenting the tile summary as though it were the full scope.
"""

import io
import re
import time
from typing import Any

from extractflow.core.crawler import BaseCrawler
from extractflow.utils.logger import get_logger
from renocred_offers import normalize as N
from renocred_offers.schema import evidence

log = get_logger("renocred.enrich_pdf")

#: Lines that are page furniture rather than outlet/model entries.
_NOISE_LINE = re.compile(
    r"^\s*(?:s\.?\s*no\.?|sr\.?\s*no\.?|page\s*\d+|terms|t\s*&\s*c|outlet\s*list|"
    r"store\s*list|model\s*list|annexure)\b",
    re.I,
)
_CITY_HINT = re.compile(r"\b(?:city|location|branch|store|outlet|address)\b", re.I)


def parse_pdf_text(data: bytes, max_pages: int = 12) -> str | None:
    """Extract text from a PDF. Returns None when it is unreadable or scanned."""
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf is not installed; linked PDFs cannot be parsed")
        return None
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
        text = "\n".join((page.extract_text() or "") for page in pages)
    except Exception as exc:
        log.warning("PDF parse failed: %s", exc)
        return None
    text = text.strip()
    # A scanned/image-only PDF yields almost nothing; do not pretend otherwise.
    return text if len(text) > 40 else None


def _entries(text: str, limit: int = 400) -> list[str]:
    """Candidate outlet/model entries, one per meaningful line."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = N.clean_text(raw)
        if not line or len(line) < 3 or len(line) > 160:
            continue
        if _NOISE_LINE.match(line):
            continue
        if line.lower() in seen:
            continue
        seen.add(line.lower())
        out.append(line)
        if len(out) >= limit:
            break
    return out


def enrich(
    records: list[dict],
    crawler: BaseCrawler | None = None,
    max_documents: int = 40,
    max_seconds: float = 600.0,
    max_bytes: int = 25 * 1024 * 1024,
) -> dict[str, Any]:
    """Fetch and parse each record's linked eligibility documents in place.

    ``max_seconds`` bounds the whole stage. A per-socket timeout does not bound
    a slow-dripping response, and one such document must not be able to stall an
    entire build; ``max_bytes`` likewise keeps a multi-hundred-page outlet list
    from dominating parse time.
    """
    crawler = crawler or BaseCrawler({"default_delay": 1.0, "timeout": 45})
    stats = {
        "documents_seen": 0,
        "fetched": 0,
        "parsed": 0,
        "failed": 0,
        "too_large": 0,
        "skipped_over_budget": 0,
        "records_enriched": 0,
    }
    cache: dict[str, str | None] = {}
    started = time.monotonic()

    for record in records:
        documents = (record.get("eligibility") or {}).get(
            "linked_eligibility_documents"
        )
        if not documents:
            continue
        enriched_any = False

        for document in documents:
            stats["documents_seen"] += 1
            url = document.get("url")
            if not url:
                continue
            if stats["fetched"] >= max_documents:
                document["parse_error"] = "skipped: per-run document cap reached"
                continue
            if time.monotonic() - started > max_seconds:
                # A single slow-dripping PDF can outlast any per-socket timeout.
                # The budget guarantees the build finishes; unfetched documents
                # keep parsed=false and their review flag, so the shortfall is
                # visible in the output rather than silently absorbed.
                document["parse_error"] = "skipped: enrichment time budget exhausted"
                stats["skipped_over_budget"] += 1
                continue

            if url in cache:
                text = cache[url]
            else:
                result = crawler.fetch(url)
                stats["fetched"] += 1
                if not result.ok or not result.content:
                    document["parse_error"] = result.error or f"HTTP {result.status}"
                    stats["failed"] += 1
                    cache[url] = None
                    continue
                if len(result.content) > max_bytes:
                    document["parse_error"] = (
                        f"skipped: {len(result.content):,} bytes exceeds the "
                        f"{max_bytes:,} byte parse limit"
                    )
                    stats["too_large"] += 1
                    cache[url] = None
                    continue
                text = parse_pdf_text(result.content)
                cache[url] = text

            if not text:
                document["parse_error"] = (
                    document.get("parse_error") or "no extractable text"
                )
                stats["failed"] += 1
                continue

            entries = _entries(text)
            document["parsed"] = True
            document["entry_count"] = len(entries)
            document["entries_sample"] = entries[:25]
            stats["parsed"] += 1
            enriched_any = True

            if document.get("document_type") == "OUTLET_LIST" and entries:
                # The outlet list is the real location scope. Replace the tile's
                # coarse region label rather than leaving a broader claim standing.
                elig = record["eligibility"]
                elig["other_eligibility_rules"] = list(
                    elig.get("other_eligibility_rules") or []
                ) + [
                    f"valid only at {len(entries)} listed outlets "
                    "(see linked Outlet List)"
                ]
                record["evidence_data"].append(
                    evidence(
                        "eligibility.location",
                        f"{len(entries)} listed outlets",
                        "; ".join(entries[:12]),
                        url,
                    )
                )
            elif document.get("document_type") == "TERMS_AND_CONDITIONS":
                clauses = _exclusion_clauses(text)
                if clauses:
                    elig = record["eligibility"]
                    elig["other_eligibility_rules"] = (
                        list(elig.get("other_eligibility_rules") or []) + clauses
                    )
                    record["evidence_data"].append(
                        evidence(
                            "eligibility.other_eligibility_rules",
                            clauses,
                            clauses[0],
                            url,
                        )
                    )
                cap = N.parse_maximum_benefit(text)
                if cap is not None and record["benefit"].get("maximum_benefit") is None:
                    record["benefit"]["maximum_benefit"] = cap
                    record["evidence_data"].append(
                        evidence(
                            "benefit.maximum_benefit",
                            cap,
                            "cap read from the linked T&C PDF",
                            url,
                        )
                    )

        if enriched_any:
            stats["records_enriched"] += 1
            # The review flag existed because the PDF was unparsed; clear it
            # only for records where every linked document actually parsed.
            if all(d.get("parsed") for d in documents):
                for item in record.get("evidence_data") or []:
                    if item.get("field") == "eligibility.linked_eligibility_documents":
                        item["needs_review"] = False
                        item["review_reason"] = (
                            f"linked eligibility PDF(s) parsed: "
                            f"{sum(d.get('entry_count') or 0 for d in documents)}"
                            " entries"
                        )

    log.info(
        "PDF enrichment: %d seen, %d fetched, %d parsed, %d failed, "
        "%d too large, %d over budget, %d record(s) enriched",
        stats["documents_seen"],
        stats["fetched"],
        stats["parsed"],
        stats["failed"],
        stats["too_large"],
        stats["skipped_over_budget"],
        stats["records_enriched"],
    )
    return stats


_EXCLUSION_PATTERNS = [
    re.compile(
        r"((?:Corporate|Commercial|Dealer|Distributor)[^.\n]{0,200}"
        r"(?:excluded|not\s*(?:eligible|applicable)))",
        re.I,
    ),
    re.compile(r"(not\s*(?:applicable|valid)\s*on[^.\n]{3,160})", re.I),
    re.compile(r"(offer\s*(?:is\s*)?not\s*valid[^.\n]{3,160})", re.I),
]


def _exclusion_clauses(text: str, limit: int = 6) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _EXCLUSION_PATTERNS:
        for match in pattern.finditer(text):
            clause = N.clean_text(match.group(1))
            if clause and clause.lower() not in seen and len(clause) > 12:
                seen.add(clause.lower())
                found.append(clause)
            if len(found) >= limit:
                return found
    return found
