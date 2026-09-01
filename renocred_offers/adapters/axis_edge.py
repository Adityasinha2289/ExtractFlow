"""Axis EDGE Rewards adapter.

The EDGE portal is a redemption *catalogue* rather than a discount grid: it
lists what points buy, not a percentage off a purchase. Those rows are ingested
as REWARD-nature records with an explicit points price, because Section 7's
whole argument is that reward economics cannot be flattened into a discount
percentage without a conversion step -- and the points price is exactly the
input that conversion needs.
"""

import re
from typing import Any

from extractflow.extractors.html import HTMLExtractor
from renocred_offers import normalize as N
from renocred_offers.adapters.base import Adapter, describe
from renocred_offers.schema import evidence
from renocred_offers.taxonomy import (
    NATURE_REWARD,
    PARTNER_LOYALTY_TRANSFER,
    REDEEM_ACTIVATION,
)

_POINTS = re.compile(r"([\d,]+)\s*(?:EDGE\s*)?(?:reward\s*)?points?", re.I)


class AxisEdgeAdapter(Adapter):
    """Parses the EDGE Rewards store product grid."""

    extraction_method = "requests+html-grid"
    grantor_scope = "ISSUER"
    tier = "S"
    source_type = "ISSUER_PORTAL"

    ROOT = "div.placeholder"
    MAPPING = {
        "heading": {"selector": "h3.product-heading", "type": "text"},
        "price": {"selector": ".product-price, .points, .price", "type": "text"},
        "link": {"selector": "a", "type": "attribute", "attribute": "href"},
        "image_alt": {
            "selector": "img.img-responsive",
            "type": "attribute",
            "attribute": "alt",
        },
    }

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        tiles = HTMLExtractor(self.MAPPING).extract_all(html, self.ROOT)
        records: list[dict] = []
        seen: set[str] = set()

        for tile in tiles:
            heading = N.clean_text(tile.get("heading")) or N.clean_text(
                tile.get("image_alt")
            )
            if not heading or len(heading) < 3 or heading.lower() in seen:
                continue
            seen.add(heading.lower())

            record = self.new_record(
                source_ref=f"edge::{heading[:60]}",
                title=f"Redeem EDGE Reward points for {heading}",
                merchant_name=heading,
                category=None,
                detail_url=self.absolutise(tile.get("link")),
            )
            blob = " ".join(x for x in (heading, N.clean_text(tile.get("price"))) if x)
            self.set_benefit(record, blob, hint=PARTNER_LOYALTY_TRANSFER, snapshot=blob)
            record["benefit"]["benefit_nature"] = NATURE_REWARD

            points = _POINTS.search(blob)
            if points:
                try:
                    record["eligibility"]["minimum_spend"] = None
                    record["eligibility"]["other_eligibility_rules"] = [
                        "redemption price: "
                        f"{float(points.group(1).replace(',', '')):,.0f} "
                        "EDGE Reward points"
                    ]
                except ValueError:
                    pass

            record["evidence_data"].append(
                evidence(
                    "benefit",
                    heading,
                    blob,
                    self.url,
                    needs_review=True,
                    review_reason=(
                        "reward-catalogue row: point value must be converted "
                        "before it is comparable with a cashback or discount "
                        "offer (Master Plan Section 7)"
                    ),
                )
            )
            self.set_redemption(
                record,
                redemption_url=record["detail_url"] or self.url,
                redemption_type=REDEEM_ACTIVATION,
                activation_required=True,
            )
            self.set_scope(record)
            record["identity"]["description"] = describe(record)
            records.append(record)
        return records
