"""SBI Card adapters - the deepest issuer source researched.

Two distinct surfaces:

* ``SbiCardOffersAdapter`` reads the inline ``offerData`` blob behind the main
  offers grid. It carries per-offer card-slug eligibility, real start/end dates
  and city scope.
* ``SbiCardFestiveAdapter`` reads the festive campaign microsite, whose tiles
  link an Outlet List and a T&C PDF. Section 5 item 2 is explicit that those
  linked PDFs -- not the tile summary -- are the actual eligibility scope, so
  they are captured as first-class ``linked_eligibility_documents`` rather than
  dropped.
"""

import re
from typing import Any

from extractflow.extractors.embedded_json import extract_assignment
from extractflow.extractors.html import HTMLExtractor
from renocred_offers import normalize as N
from renocred_offers.adapters.base import Adapter, describe
from renocred_offers.schema import evidence
from renocred_offers.taxonomy import EMI_OFFER, TXN_EMI


class SbiCardOffersAdapter(Adapter):
    """Parses ``var offerData={...}`` from the SBI Card offers page."""

    extraction_method = "requests+embedded-json"
    grantor_scope = "ISSUER"

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        blob = extract_assignment(html, "offerData")
        if not blob:
            return []
        offers = ((blob or {}).get("offers") or {}).get("offer") or []
        return [r for r in (self._one(o) for o in offers) if r]

    def _one(self, raw: dict) -> dict | None:
        source_ref = raw.get("offerId")
        if not source_ref:
            return None
        headline = N.clean_text(raw.get("text") or raw.get("discountBlock"))
        offer_type = raw.get("type") or ""
        brand = N.titleise(raw.get("brandName"))

        if not headline:
            # EMI-conversion tiles carry no discount copy at all: the mechanism
            # lives in `type` and the value is the EMI facility itself. These
            # are real offers, so synthesise a title from the structured fields
            # rather than dropping 17 valid records for having empty ad copy.
            facets = [f.strip() for f in offer_type.split(",") if f.strip()]
            if facets and brand:
                headline = f"{facets[0]} at {brand}"
            else:
                return None

        record = self.new_record(
            source_ref=source_ref,
            title=headline,
            merchant_name=raw.get("brandName"),
            category=raw.get("category"),
            detail_url=(
                "https://www.sbicard.com/en/personal/offers/offer-detail.page"
                f"?offerId={source_ref}"
            ),
        )

        # -- benefit -----------------------------------------------------
        # discountBlock and text are frequently identical; de-duplicate so the
        # stored benefit_text is not the same sentence twice.
        seen: set[str] = set()
        fragments = []
        for part in (raw.get("discountBlock"), raw.get("text"), raw.get("hoverText")):
            cleaned = N.clean_text(part)
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                fragments.append(cleaned)
        blob = " ".join(fragments) or headline
        # "Convert To EMI" lives in `type`, not in the discount copy.
        hint = EMI_OFFER if "emi" in offer_type.lower() else None
        self.set_benefit(record, blob, hint=hint, snapshot=raw.get("discountBlock"))

        minimum = N.parse_minimum_spend(blob)
        if minimum is not None:
            record["eligibility"]["minimum_spend"] = minimum

        # -- validity ----------------------------------------------------
        self.set_validity(
            record,
            N.parse_date(raw.get("startDate")),
            N.parse_date(raw.get("endDate")),
            snapshot=f"startDate={raw.get('startDate')} endDate={raw.get('endDate')}",
        )

        # -- eligibility -------------------------------------------------
        slugs = [
            s.strip() for s in re.split(r"[,;]", raw.get("card") or "") if s.strip()
        ]
        self.set_cards(record, slugs, snapshot=raw.get("card"))

        self.set_transaction(record, blob, raw.get("isOnline"))
        if "emi" in offer_type.lower():
            record["eligibility"]["transaction_type"] = TXN_EMI

        cities = [
            c
            for c in N.split_list((raw.get("city") or "").replace("\\n", ";"), ";")
            if c and c.lower() not in {"all", "national"}
        ]
        if cities:
            record["eligibility"]["location"] = cities
            record["evidence_data"].append(
                evidence(
                    "eligibility.location",
                    f"{len(cities)} cities",
                    raw.get("city"),
                    self.url,
                )
            )

        pin = N.clean_text(raw.get("pinCode"))
        if pin:
            record["eligibility"]["other_eligibility_rules"] = [f"pin codes: {pin}"]

        self.set_redemption(record, redemption_url=record["detail_url"])
        self.set_scope(record)
        record["identity"]["description"] = describe(record)
        return record


class SbiCardFestiveAdapter(Adapter):
    """Parses the festive campaign microsite tile grid."""

    extraction_method = "requests+html-grid"
    grantor_scope = "ISSUER"

    ROOT = "div.sdo-card-block"
    MAPPING = {
        "brand": {
            "selector": "div.card-logo-block img",
            "type": "attribute",
            "attribute": "alt",
        },
        "brand_src": {
            "selector": "div.card-logo-block img",
            "type": "attribute",
            "attribute": "src",
        },
        "discount": {"selector": "div.sdo-card-discount-block", "type": "text"},
        "terms": {"selector": "div.sdo-max-discount-block", "type": "list"},
        "outlet_pdf": {
            "selector": "div.avail-now-button-block a",
            "type": "attribute",
            "attribute": "href",
        },
        "tnc_pdf": {
            "selector": "div.card-bottom-links-wrap a",
            "type": "attribute",
            "attribute": "href",
        },
    }

    #: Tile CSS classes double as facets - these are layout, not meaning.
    _LAYOUT_CLASSES = re.compile(r"^(?:sdo-card-block|col-|position-|text-|-\d)")
    _REGIONS = {"north", "south", "east", "west", "national", "regional"}

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        tiles = HTMLExtractor(self.MAPPING).extract_all(html, self.ROOT)
        return [r for r in (self._one(t) for t in tiles) if r]

    def _one(self, tile: dict) -> dict | None:
        brand = N.clean_text(tile.get("brand"))
        discount = N.clean_text(tile.get("discount"))
        if not brand or not discount:
            return None

        classes = [
            c
            for c in (tile.get("_root_classes") or [])
            if not self._LAYOUT_CLASSES.match(c)
        ]
        regions = sorted({c for c in classes if c in self._REGIONS} - {"regional"})
        categories = [c for c in classes if c not in self._REGIONS]

        record = self.new_record(
            source_ref=f"festive::{brand}",
            title=f"{discount} at {brand}",
            merchant_name=brand,
            category=categories[0] if categories else None,
        )

        terms_text = " | ".join(tile.get("terms") or [])
        self.set_benefit(record, f"{discount} {terms_text}", snapshot=discount)

        minimum = N.parse_minimum_spend(terms_text)
        if minimum is not None:
            record["eligibility"]["minimum_spend"] = minimum

        validity_line = next(
            (t for t in (tile.get("terms") or []) if "validity" in t.lower()),
            terms_text,
        )
        valid_from, valid_until = N.parse_date_range(validity_line)
        self.set_validity(record, valid_from, valid_until, snapshot=validity_line)

        if regions:
            record["eligibility"]["location"] = [r.upper() for r in regions]

        # Section 5 item 2: the linked PDFs are the real eligibility scope.
        documents = []
        for label, href in (
            ("OUTLET_LIST", tile.get("outlet_pdf")),
            ("TERMS_AND_CONDITIONS", tile.get("tnc_pdf")),
        ):
            url = self.absolutise(href)
            if url:
                documents.append({"document_type": label, "url": url, "parsed": False})
        if documents:
            record["eligibility"]["linked_eligibility_documents"] = documents
            record["evidence_data"].append(
                evidence(
                    "eligibility.linked_eligibility_documents",
                    [d["url"] for d in documents],
                    f"{len(documents)} linked eligibility PDF(s) on the offer tile",
                    self.url,
                    needs_review=True,
                    review_reason=(
                        "linked Outlet/T&C PDF is the authoritative eligibility scope "
                        "and has not been parsed - tile summary alone over-generalises "
                        "(Master Plan Section 5 item 2)"
                    ),
                )
            )

        # The offer is issuer-wide across SBI consumer cards; the microsite does
        # not enumerate card slugs, so card_ids stays null rather than being
        # guessed at from the issuer's full card list.
        self.set_transaction(record, f"{discount} {terms_text}")
        self.set_redemption(record)
        self.set_scope(record)
        record["identity"]["description"] = describe(record)
        return record
