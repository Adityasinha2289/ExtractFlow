"""Build the standalone Offer Ledger page from a built Offer Master.

Produces one self-contained HTML file with the browsing projection embedded, so
it opens straight from disk with no server, no build step and no network beyond
the webfont link. It is deliberately a separate command from ``build``: the
dataset is the deliverable, and the viewer is one way of reading it.

The projection drops the raw evidence snapshots -- up to 2 KB apiece and of no
use to someone scanning -- while keeping every field that makes a record's trust
state legible: its review reasons, conflicts, stated conditions and linked
eligibility documents.
"""

import argparse
import json
import os
from typing import Any

from extractflow.utils.filesystem import atomic_write
from extractflow.utils.logger import get_logger

log = get_logger("renocred.viewer")

TEMPLATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "templates", "ledger.html"
)
PLACEHOLDER = "__DATA__"


def project(record: dict) -> dict[str, Any]:
    """One Offer Master record reduced to what the page actually renders."""
    ident, ben = record["identity"], record["benefit"]
    elig, val = record["eligibility"], record["validity"]
    qual, red = record["quality"], record["redemption"]
    merchant = record.get("merchant") or {}
    locations = elig.get("location") or []

    return {
        "id": ident["offer_id"],
        "title": ident["title"],
        "desc": ident["description"],
        "issuer": ident["issuer"],
        "network": ident["network"],
        "grantor": ident["grantor_scope"],
        "merchant": merchant.get("merchant_name"),
        "cat": merchant.get("merchant_category"),
        "btype": ben["benefit_type"],
        "bnature": ben["benefit_nature"],
        "pct": ben["discount_percentage"],
        "cb": ben["cashback"],
        "mult": ben["reward_multiplier"],
        "flat": ben["flat_discount"],
        "cap": ben["maximum_benefit"],
        "rate_type": ben.get("rate_type"),
        "btext": ben["benefit_text"],
        "min": elig.get("minimum_spend"),
        "txn": elig.get("transaction_type"),
        "cards": elig.get("card_ids") or [],
        "unmatched": elig.get("unmatched_card_slugs") or [],
        "loc": locations[:6],
        "loc_n": len(locations),
        "rules": [str(r)[:400] for r in (elig.get("other_eligibility_rules") or [])],
        "docs": [
            {
                "t": d.get("document_type"),
                "u": d.get("url"),
                "p": bool(d.get("parsed")),
                "n": d.get("entry_count"),
            }
            for d in (elig.get("linked_eligibility_documents") or [])
        ],
        "from": val["valid_from"],
        "until": val["valid_until"],
        "life": record["lifecycle_status"],
        "vs": qual["verification_status"],
        "conf": qual["confidence"],
        "band": qual["confidence_band"],
        "compl": qual["completeness"],
        "review": qual.get("review_reasons") or [],
        "conflicts": qual.get("conflicts") or [],
        "code": red.get("coupon_code"),
        "rtype": red.get("redemption_type"),
        "src": record["source_name"],
        "tier": record["source_tier"],
        "stype": record["source_type"],
        "url": record["source_url"],
        "durl": record.get("detail_url"),
        "method": record.get("extraction_method"),
        "verified_at": (record.get("last_verified_at") or "")[:10],
        "ev_n": len(record.get("evidence_data") or []),
        "dq": record.get("data_quality"),
    }


def build_payload(offer_master: dict, merchant_master: dict, report: dict) -> dict:
    return {
        "meta": offer_master["metadata"],
        "offers": [project(r) for r in offer_master["data"]],
        "sources": report.get("sources", []),
        "reconciliation": report.get("reconciliation", {}),
        "validation_errors": report.get("validation_errors", []),
        "gaps": merchant_master.get("partnership_gaps", []),
        "merchant_count": merchant_master["metadata"]["merchant_count"],
    }


def render(payload: dict, template_path: str = TEMPLATE) -> str:
    with open(template_path, encoding="utf-8") as fh:
        html = fh.read()
    if PLACEHOLDER not in html:
        raise ValueError(f"template {template_path} has no {PLACEHOLDER} placeholder")

    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Escape so no value in the data can terminate the <script> element early.
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return html.replace(PLACEHOLDER, blob)


def run(out_dir: str, template_path: str = TEMPLATE) -> str:
    offer_master = _load(os.path.join(out_dir, "renocred_offer_master.json"))
    merchant_master = _load(os.path.join(out_dir, "renocred_merchant_master.json"))
    report = _load(os.path.join(out_dir, "extraction_report.json"), default={})

    payload = build_payload(offer_master, merchant_master, report)
    path = os.path.join(out_dir, "renocred_offer_ledger.html")
    atomic_write(path, render(payload, template_path))
    log.info(
        "wrote %s (%d offers, %s bytes)",
        path,
        len(payload["offers"]),
        f"{os.path.getsize(path):,}",
    )
    return path


def _load(path: str, default: Any = None) -> Any:
    if default is not None and not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description="Build the standalone Offer Ledger page from a built Offer Master"
    )
    parser.add_argument("--out", default=os.path.join(here, "output"))
    parser.add_argument("--template", default=TEMPLATE)
    args = parser.parse_args()

    path = run(args.out, args.template)
    print(f"Offer Ledger written to {path}\nOpen it directly in a browser.")


if __name__ == "__main__":
    main()
