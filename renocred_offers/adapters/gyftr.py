"""GyFTR adapter - the Section 21 item 1 infra-vendor leverage point.

GyFTR is the shared voucher/redemption backend behind HDFC SmartBuy's voucher
tab, Axis GIFT EDGE and SBI Card's instant-voucher catalogue. To a search index
those are three unrelated pages; structurally they are one vendor template, so
*one* adapter covers every issuer that adopts the vendor -- and covers the next
one for free.

The vendor ships two builds. Server-rendered instances put the catalogue in
``__NEXT_DATA__``; client-rendered instances leave it to an API call. Both are
handled here, and both produce identical records.
"""

from typing import Any

from extractflow.extractors.embedded_json import extract_script_json
from extractflow.utils.helpers import deep_get
from renocred_offers import normalize as N
from renocred_offers.adapters.base import Adapter, describe
from renocred_offers.schema import evidence
from renocred_offers.taxonomy import REDEEM_VOUCHER, VOUCHER_ACCELERATOR

#: Catalogue lists inside the vendor's home payload, in priority order.
_CATALOGUE_KEYS = (
    "hot_deals",
    "popular_brands",
    "trending_brands",
    "all_brands",
    "brands",
)

#: Vendor offer_type codes.
_OFFER_TYPES = {"DIS": "discount", "CB": "cashback", "OFF": "offer"}


class GyftrAdapter(Adapter):
    """Parses a GyFTR-powered voucher catalogue."""

    extraction_method = "requests+next-data"
    grantor_scope = "ISSUER"
    tier = "A"
    source_type = "INFRA_VENDOR"

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        blob = extract_script_json(html, "__NEXT_DATA__")
        home = deep_get(blob, "props.pageProps.homeData") or {} if blob else {}

        brands: dict[Any, dict] = {}
        for key in _CATALOGUE_KEYS:
            for item in home.get(key) or []:
                if isinstance(item, dict) and item.get("brand_id") is not None:
                    brands.setdefault(item["brand_id"], item)

        if brands:
            return [r for r in (self._one(b) for b in brands.values()) if r]

        # Client-rendered vendor instance: the same catalogue, delivered as DOM
        # instead of as a server-side payload. Parsing it here is what makes one
        # vendor adapter cover every issuer on the vendor, whichever build they
        # were given (Master Plan Section 21 item 1).
        return self._parse_dom(html)

    def _parse_dom(self, html: str) -> list[dict]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
        records: list[dict] = []
        seen: set[str] = set()

        for tile in soup.select("div.productList1"):
            logo = tile.select_one("img[alt]")
            brand = N.clean_text(logo.get("alt")) if logo else None
            if not brand or brand.lower() in seen:
                continue

            off = tile.select_one("div.offRoung")
            discount = (
                N.parse_percentage(off.get_text(" ", strip=True)) if off else None
            )
            coins = tile.select_one("div.tag")
            coin_text = N.clean_text(coins.get_text(" ", strip=True)) if coins else None
            if discount is None and not coin_text:
                continue
            seen.add(brand.lower())

            face = tile.select_one("del")
            price = N.parse_money(face.get_text(" ", strip=True)) if face else None
            denominations = [
                N.parse_money(li.get_text(" ", strip=True))
                for li in tile.select("ul.denom_list_home li")
            ]
            denominations = [d for d in denominations if d]

            records.append(
                self._one(
                    {
                        "brand_id": tile.get("id") or brand,
                        "brand_name": brand,
                        "slug": None,
                        "discount_value": discount,
                        "offer_type": "DIS" if discount else None,
                        "earn_point_ratio": None,
                        "promocode": None,
                        "product_price": min(denominations) if denominations else price,
                        "updated": None,
                        "is_offer_active": 1,
                        "_coin_text": coin_text,
                        "_denominations": denominations,
                    }
                )
            )
        return [r for r in records if r]

    def _one(self, raw: dict) -> dict | None:
        brand = N.clean_text(raw.get("brand_name"))
        if not brand:
            return None

        discount = raw.get("discount_value")
        earn_ratio = raw.get("earn_point_ratio")
        coin_text = raw.get("_coin_text")
        # A vendor row with neither a discount, an earn ratio nor a coin accrual
        # is a catalogue listing, not an offer. Storing it would inflate offer
        # count with records carrying no benefit - the vanity metric Section 1
        # warns against.
        if not discount and not earn_ratio and not coin_text:
            return None

        offer_code = _OFFER_TYPES.get(raw.get("offer_type") or "", "offer")
        if discount:
            headline = f"{discount:g}% off {brand} gift vouchers"
        elif earn_ratio:
            headline = f"Earn {earn_ratio} points per Rs 100 on {brand} gift vouchers"
        else:
            headline = f"{coin_text} on {brand} gift vouchers"

        slug = raw.get("slug") or str(raw.get("brand_id"))
        record = self.new_record(
            source_ref=f"brand:{raw.get('brand_id')}",
            title=headline,
            merchant_name=brand,
            category=None,
            detail_url=self.absolutise(slug, self.url.rstrip("/") + "/"),
        )

        self.set_benefit(
            record,
            headline,
            hint=VOUCHER_ACCELERATOR,
            snapshot=f"discount_value={discount} offer_type={raw.get('offer_type')}",
        )
        if discount:
            record["benefit"]["discount_percentage"] = float(discount)
        if earn_ratio:
            record["benefit"][
                "reward_multiplier"
            ] = None  # earn ratio, not a multiplier
            record["eligibility"].setdefault("other_eligibility_rules", None)

        # Voucher denomination is the transaction floor for this offer.
        price = raw.get("product_price")
        if isinstance(price, (int, float)) and price > 0:
            record["eligibility"]["minimum_spend"] = float(price)

        self.set_redemption(
            record,
            coupon=raw.get("promocode"),
            redemption_url=record["detail_url"],
            redemption_type=REDEEM_VOUCHER,
            activation_required=False,
        )
        record["eligibility"]["transaction_type"] = "ONLINE"
        record["eligibility"]["online_offline"] = "ONLINE"

        # The vendor states no validity window on the catalogue row. Leaving
        # these null is correct: an invented expiry would be worse than none.
        active = raw.get("is_offer_active")
        record["evidence_data"].append(
            evidence(
                "validity",
                None,
                f"vendor row states no validity window (is_offer_active={active}, "
                f"updated={raw.get('updated')})",
                self.url,
                needs_review=True,
                review_reason=(
                    "GyFTR catalogue rows carry no expiry; validity must be confirmed "
                    "against the issuer T&C before this offer reaches VERIFIED "
                    "(Master Plan Section 6 - vendor and bank T&C can drift)"
                ),
            )
        )
        updated = N.parse_date(raw.get("updated"))
        if updated:
            record["validity"]["valid_from"] = updated

        rules = [
            f"vendor offer_type={offer_code}",
            "voucher-purchase accelerator; discount applies to the voucher, "
            "not to the merchant's own price",
        ]
        if coin_text:
            rules.append(f"vendor loyalty accrual: {coin_text}")
        denominations = raw.get("_denominations")
        if denominations:
            rules.append(
                "available denominations: "
                + ", ".join(f"Rs {d:,.0f}" for d in sorted(denominations))
            )
        record["eligibility"]["other_eligibility_rules"] = rules
        self.set_scope(record)
        record["identity"]["description"] = describe(record)
        return record
