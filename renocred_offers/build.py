"""Offer Master build orchestrator.

Runs the Section 15 ingestion pipeline end to end:

    SOURCE DISCOVERY -> RAW OFFER -> EXTRACTION -> NORMALIZATION
      -> DEDUPLICATION -> ELIGIBILITY PARSING -> SOURCE VERIFICATION
      -> QUALITY SCORING -> OFFER MASTER

and emits ``renocred_offer_master.json`` in the same envelope shape as
``renocred_card_master.json`` so both masters can be consumed by identical
downstream code.
"""

import argparse
import datetime as dt
import os
from collections import Counter
from typing import Any

from extractflow.core.crawler import BaseCrawler
from extractflow.core.pipeline import Pipeline
from extractflow.exporters.csv_exporter import export_csv
from extractflow.exporters.json_exporter import export_json
from extractflow.utils.logger import get_logger
from extractflow.validators.quality_checker import check_quality
from extractflow.validators.schema_validator import validate_all
from renocred_offers import enrich_pdf
from renocred_offers.card_link import CardMaster
from renocred_offers.merchant_master import MerchantMaster
from renocred_offers.reconcile import reconcile
from renocred_offers.registry import build_adapter, load_sources
from renocred_offers.schema import (
    COMPLETENESS_FIELDS,
    DATASET_VERSION,
    INGESTION_SCHEMA,
    SCHEMA_VERSION,
)
from renocred_offers.verification import apply_verification, lifecycle

log = get_logger("renocred.build")

DYNAMIC_STRATEGIES = {"dynamic"}


def fetch_source(
    crawler: BaseCrawler, source: dict[str, Any], cache_dir: str | None = None
) -> tuple[str | None, str]:
    """Retrieve one source. Returns ``(html, method)``.

    Static retrieval is tried first even for sources marked ``dynamic``: many
    apparently JS-rendered pages ship their whole dataset inline, and a browser
    round-trip that is not needed is cost and flakiness for nothing.
    """
    url = source["url"]
    cache_path = os.path.join(cache_dir, f"{source['key']}.html") if cache_dir else None

    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            return fh.read(), "cache"

    result = crawler.fetch(url)
    if result.ok and result.text:
        if cache_path:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                fh.write(result.text)
        return result.text, "requests"

    if (
        source.get("strategy") in DYNAMIC_STRATEGIES
        or source.get("fallback_strategy") == "dynamic"
    ):
        html = _render(url, source)
        if html:
            if cache_path:
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as fh:
                    fh.write(html)
            return html, "playwright"

    log.error(
        "source %s unavailable: HTTP %s %s",
        source["key"],
        result.status,
        result.error or "",
    )
    return None, "failed"


def _render(url: str, source: dict[str, Any]) -> str | None:
    """Render a genuinely client-side page with ExtractFlow's DynamicScraper."""
    try:
        import asyncio

        from extractflow.core.scraper import DynamicScraper
    except ImportError:
        log.warning("playwright is not installed; cannot render %s", url)
        return None
    try:
        scraper = DynamicScraper(
            {
                "timeout": int(source.get("timeout", 45)) * 1000,
                "wait_for_selector": source.get("wait_for_selector"),
                "interactions": source.get("interactions", []),
            }
        )
        return asyncio.run(scraper.scrape(url))
    except Exception as exc:  # ScraperError and playwright launch failures alike
        log.warning("dynamic render failed for %s: %s", url, exc)
        return None


def extract(
    config: dict,
    merchants: MerchantMaster,
    cards: CardMaster,
    crawler: BaseCrawler,
    cache_dir: str | None,
) -> tuple[list[dict], list[dict]]:
    """Run every registered source's adapter. Returns ``(records, source_report)``."""
    records: list[dict] = []
    report: list[dict] = []

    for source in config.get("sources") or []:
        html, method = fetch_source(crawler, source, cache_dir)
        if html is None:
            report.append(
                {
                    **_source_row(source),
                    "status": "UNAVAILABLE",
                    "count": 0,
                    "method": method,
                }
            )
            continue
        adapter = build_adapter(source, merchants, cards)
        try:
            produced = adapter.parse(html)
        except Exception as exc:
            log.exception("adapter %s failed", source["key"])
            report.append(
                {
                    **_source_row(source),
                    "status": f"ERROR: {exc}",
                    "count": 0,
                    "method": method,
                }
            )
            continue

        # A page that fetched cleanly but yielded nothing is the signature of a
        # genuinely client-rendered source: the shell is 200 OK and empty. Fall
        # back to a browser only here, so the expensive path is reserved for the
        # cases that actually need it.
        wants_dynamic = (
            source.get("strategy") in DYNAMIC_STRATEGIES
            or source.get("fallback_strategy") == "dynamic"
        )
        if not produced and wants_dynamic and method != "playwright":
            log.info(
                "source %s returned 0 offers statically; rendering with playwright",
                source["key"],
            )
            rendered = _render(source["url"], source)
            if rendered:
                method = "playwright"
                produced = adapter.parse(rendered)
                if cache_dir:
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(
                        os.path.join(cache_dir, f"{source['key']}.html"),
                        "w",
                        encoding="utf-8",
                    ) as fh:
                        fh.write(rendered)

        if method == "playwright":
            adapter.extraction_method = (
                "playwright+" + adapter.extraction_method.split("+", 1)[-1]
            )
            for record in produced:
                record["extraction_method"] = adapter.extraction_method
        records.extend(produced)
        report.append(
            {
                **_source_row(source),
                "status": "OK",
                "count": len(produced),
                "method": method,
            }
        )
        log.info(
            "source %-24s -> %3d offer(s) via %s", source["key"], len(produced), method
        )

    for deferred in config.get("deferred") or []:
        report.append(
            {
                "key": deferred.get("key"),
                "name": deferred.get("name"),
                "url": deferred.get("url"),
                "tier": deferred.get("tier"),
                "role": "DEFERRED",
                "status": "DEFERRED",
                "count": 0,
                "method": None,
                "reason": deferred.get("reason"),
            }
        )
    return records, report


def _source_row(source: dict) -> dict:
    return {
        "key": source.get("key"),
        "name": source.get("name"),
        "url": source.get("url"),
        "tier": source.get("tier"),
        "role": source.get("role"),
        "source_type": source.get("source_type"),
        "vendor": source.get("vendor"),
        "issuer": source.get("issuer"),
        "network": source.get("network"),
    }


def finalise(records: list[dict], today: dt.date | None = None) -> list[dict]:
    """Lifecycle, verification, confidence -- applied uniformly to every record."""
    today = today or dt.date.today()
    for record in records:
        validity = record.get("validity") or {}
        record["lifecycle_status"] = lifecycle(
            validity.get("valid_from"), validity.get("valid_until"), today
        )
        validity["status"] = record["lifecycle_status"]
        apply_verification(record)
    return records


def build_metadata(
    records: list[dict],
    sources: list[dict],
    merchants: MerchantMaster,
    cards: CardMaster,
    reconcile_report: dict,
    validation: dict,
    pdf_stats: dict,
) -> dict:
    """Metadata envelope mirroring ``renocred_card_master.json``."""
    now = dt.datetime.now().isoformat()
    quality = check_quality(records, COMPLETENESS_FIELDS)
    ok_sources = [s for s in sources if s.get("status") == "OK"]

    issuers = Counter(
        (r.get("identity") or {}).get("issuer")
        for r in records
        if (r.get("identity") or {}).get("issuer")
    )
    coverage_by_issuer = {
        issuer: {"extracted": count, "status": "SUCCESS"}
        for issuer, count in sorted(issuers.items())
    }
    networks = Counter(
        (r.get("identity") or {}).get("network")
        for r in records
        if (r.get("identity") or {}).get("network")
    )
    for network, count in networks.items():
        coverage_by_issuer[f"network:{network}"] = {
            "extracted": count,
            "status": "SUCCESS",
        }

    linked = sum(1 for r in records if (r.get("eligibility") or {}).get("card_ids"))
    with_docs = sum(
        1
        for r in records
        if (r.get("eligibility") or {}).get("linked_eligibility_documents")
    )

    return {
        "dataset_name": "renocred_offer_master",
        "dataset_version": DATASET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "source_count": len(ok_sources),
        "issuer_count": len(issuers),
        "offer_count": len(records),
        "merchant_count": len(merchants),
        "duplicate_count": reconcile_report.get("duplicate_count", 0),
        "invalid_count": validation.get("invalid_count", 0),
        "needs_review_count": sum(
            1 for r in records if r.get("data_quality") == "NEEDS_REVIEW"
        ),
        "conflicted_count": sum(
            1
            for r in records
            if (r.get("quality") or {}).get("verification_status") == "CONFLICTED"
        ),
        "coverage_by_issuer": coverage_by_issuer,
        "coverage_by_field": quality["coverage_by_field"],
        "coverage_by_source_tier": dict(Counter(r.get("source_tier") for r in records)),
        "verification_distribution": dict(
            Counter(
                (r.get("quality") or {}).get("verification_status") for r in records
            )
        ),
        "lifecycle_distribution": dict(
            Counter(r.get("lifecycle_status") for r in records)
        ),
        "benefit_type_distribution": dict(
            Counter((r.get("benefit") or {}).get("benefit_type") for r in records)
        ),
        "benefit_nature_distribution": dict(
            Counter((r.get("benefit") or {}).get("benefit_nature") for r in records)
        ),
        "confidence_distribution": dict(
            Counter(r.get("recommendation_confidence") for r in records)
        ),
        "card_linkage": {
            "offers_linked_to_card_master": linked,
            "offers_without_card_link": len(records) - linked,
            "card_master_version": cards.metadata.get("dataset_version"),
            "card_master_card_count": cards.metadata.get("card_count"),
        },
        "linked_eligibility_documents": {
            "offers_with_documents": with_docs,
            **pdf_stats,
        },
        "mean_completeness": quality["completeness"]["mean"],
        "last_verified_at": now,
        "phase": "3C.2_offer_ingestion",
        "extraction_framework": "ExtractFlow 0.1.0",
    }


def run(
    card_master_path: str,
    sources_path: str,
    out_dir: str,
    cache_dir: str | None = None,
    parse_pdfs: bool = True,
    max_pdfs: int = 40,
    pdf_seconds: float = 600.0,
) -> dict:
    """Execute the full build and write every artefact."""
    cards = CardMaster(card_master_path)
    merchants = MerchantMaster()
    config = load_sources(sources_path)
    crawler = BaseCrawler({**(config.get("defaults") or {})})

    state: dict[str, Any] = {}

    def stage_extract(_):
        records, report = extract(config, merchants, cards, crawler, cache_dir)
        state["source_report"] = report
        return records

    def stage_enrich(records):
        state["pdf_stats"] = (
            enrich_pdf.enrich(records, crawler, max_pdfs, pdf_seconds)
            if parse_pdfs
            else {
                "documents_seen": 0,
                "fetched": 0,
                "parsed": 0,
                "failed": 0,
                "too_large": 0,
                "skipped_over_budget": 0,
                "records_enriched": 0,
                "skipped": True,
            }
        )
        return records

    def stage_reconcile(records):
        merged, report = reconcile(records)
        state["reconcile_report"] = report
        return merged

    def stage_finalise(records):
        return finalise(records)

    def stage_validate(records):
        state["validation"] = validate_all(records, INGESTION_SCHEMA)
        return records

    pipeline = (
        Pipeline()
        .add("extract", stage_extract)
        .add("enrich_linked_pdfs", stage_enrich)
        .add("reconcile", stage_reconcile)
        .add("verify_and_score", stage_finalise)
        .add("validate", stage_validate)
    )
    result = pipeline.run()
    if not result.ok:
        raise RuntimeError(f"pipeline failed: {result.errors}")
    records = result.records

    metadata = build_metadata(
        records,
        state["source_report"],
        merchants,
        cards,
        state["reconcile_report"],
        state["validation"],
        state["pdf_stats"],
    )

    os.makedirs(out_dir, exist_ok=True)
    offer_master_path = os.path.join(out_dir, "renocred_offer_master.json")
    export_json({"metadata": metadata, "data": records}, offer_master_path)
    export_json(
        {
            "metadata": {
                "dataset_name": "renocred_merchant_master",
                "generated_at": metadata["generated_at"],
                "merchant_count": len(merchants),
            },
            "data": merchants.records(),
            "partnership_gaps": merchants.partnership_gaps(
                [
                    s.get("issuer")
                    for s in (config.get("sources") or [])
                    if s.get("issuer")
                ]
            ),
        },
        os.path.join(out_dir, "renocred_merchant_master.json"),
    )
    export_json(
        {
            "generated_at": metadata["generated_at"],
            "pipeline": result.summary(),
            "sources": state["source_report"],
            "reconciliation": state["reconcile_report"],
            "validation": {
                k: v for k, v in state["validation"].items() if k != "results"
            },
            "validation_errors": [
                {
                    "offer_id": (records[i].get("identity") or {}).get("offer_id"),
                    "errors": state["validation"]["results"][i]["errors"],
                }
                for i in state["validation"]["invalid_indexes"]
            ][:50],
            "linked_pdf_enrichment": state["pdf_stats"],
        },
        os.path.join(out_dir, "extraction_report.json"),
    )
    export_csv(_flat_rows(records), os.path.join(out_dir, "renocred_offers.csv"))

    log.info("pipeline: %s", result.summary())
    return {"metadata": metadata, "records": records, "report": state}


def _flat_rows(records: list[dict]) -> list[dict]:
    """A reviewer-friendly flat projection - the human-review queue's input."""
    rows = []
    for record in records:
        identity, benefit = record.get("identity") or {}, record.get("benefit") or {}
        elig, validity = record.get("eligibility") or {}, record.get("validity") or {}
        quality = record.get("quality") or {}
        rows.append(
            {
                "offer_id": identity.get("offer_id"),
                "title": identity.get("title"),
                "issuer": identity.get("issuer"),
                "network": identity.get("network"),
                "merchant": (record.get("merchant") or {}).get("merchant_name"),
                "category": (record.get("merchant") or {}).get("merchant_category"),
                "benefit_type": benefit.get("benefit_type"),
                "benefit_nature": benefit.get("benefit_nature"),
                "discount_pct": benefit.get("discount_percentage"),
                "cashback_pct": benefit.get("cashback"),
                "reward_multiplier": benefit.get("reward_multiplier"),
                "flat_discount": benefit.get("flat_discount"),
                "max_benefit": benefit.get("maximum_benefit"),
                "min_spend": elig.get("minimum_spend"),
                "card_ids_count": len(elig.get("card_ids") or []),
                "unmatched_slugs": len(elig.get("unmatched_card_slugs") or []),
                "valid_from": validity.get("valid_from"),
                "valid_until": validity.get("valid_until"),
                "lifecycle": record.get("lifecycle_status"),
                "verification": quality.get("verification_status"),
                "confidence": quality.get("confidence"),
                "completeness": quality.get("completeness"),
                "needs_review": quality.get("human_review_required"),
                "source": record.get("source_name"),
                "tier": record.get("source_tier"),
                "source_url": record.get("source_url"),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the RenoCred Offer Master")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument(
        "--card-master",
        default=os.path.join(os.path.dirname(here), "renocred_card_master.json"),
    )
    parser.add_argument(
        "--sources", default=os.path.join(here, "configs", "renocred", "sources.yaml")
    )
    parser.add_argument("--out", default=os.path.join(here, "output"))
    parser.add_argument(
        "--cache",
        default=None,
        help="directory for raw source snapshots (evidence + re-runs)",
    )
    parser.add_argument(
        "--no-pdfs", action="store_true", help="skip linked eligibility PDF enrichment"
    )
    parser.add_argument("--max-pdfs", type=int, default=40)
    parser.add_argument(
        "--pdf-seconds",
        type=float,
        default=600.0,
        help="wall-clock budget for the linked-PDF enrichment stage",
    )
    args = parser.parse_args()

    result = run(
        args.card_master,
        args.sources,
        args.out,
        args.cache,
        parse_pdfs=not args.no_pdfs,
        max_pdfs=args.max_pdfs,
        pdf_seconds=args.pdf_seconds,
    )
    meta = result["metadata"]
    print(
        f"\nOffer Master built: {meta['offer_count']} offers from "
        f"{meta['source_count']} live sources, {meta['merchant_count']} merchants"
    )
    print(f"  verification : {meta['verification_distribution']}")
    print(f"  lifecycle    : {meta['lifecycle_distribution']}")
    print(f"  confidence   : {meta['confidence_distribution']}")


if __name__ == "__main__":
    main()
