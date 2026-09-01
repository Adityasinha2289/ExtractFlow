"""Shared adapter scaffolding.

An adapter's job is narrow: turn one source's raw shape into Offer Master
records with honest provenance. It does *not* decide verification status,
confidence or dedup - those are pipeline stages that run identically over every
source, so no adapter can accidentally grant its own source more trust than the
Section 6 tier framework allows.
"""

import datetime as dt
from typing import Any, Iterable
from urllib.parse import urljoin

from extractflow.utils.helpers import stable_hash
from renocred_offers import normalize as N
from renocred_offers.schema import (
    DEFAULT_CURRENCY,
    DEFAULT_TIMEZONE,
    empty_offer,
    evidence,
)
from renocred_offers.taxonomy import (
    CASHBACK,
    FLAT_DISCOUNT,
    PERCENTAGE_DISCOUNT,
    REDEEM_AUTOMATIC,
    REDEEM_PROMO_CODE,
    REWARD_MULTIPLIER,
    classify_benefit,
    infer_transaction_type,
    normalise_category,
)


class Adapter:
    """Base class. Subclasses implement :meth:`parse`."""

    key: str = ""
    name: str = ""
    tier: str = "S"
    source_type: str = "ISSUER_PORTAL"
    grantor_scope: str = "ISSUER"
    issuer: str | None = None
    network: str | None = None
    extraction_method: str = "requests"

    def __init__(self, source_config: dict[str, Any], merchant_master, card_master):
        self.config = source_config or {}
        self.merchants = merchant_master
        self.cards = card_master
        self.url = self.config.get("url", "")
        self.key = self.config.get("key", self.key)
        self.name = self.config.get("name", self.name)
        self.tier = self.config.get("tier", self.tier)
        self.source_type = self.config.get("source_type", self.source_type)
        self.issuer = self.config.get("issuer", self.issuer)
        self.network = self.config.get("network", self.network)

    # -- to implement -------------------------------------------------------
    def parse(self, payload: Any) -> list[dict]:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------
    def _now(self) -> str:
        return dt.datetime.now().isoformat(timespec="seconds")

    def new_record(
        self,
        *,
        source_ref: str,
        title: str,
        merchant_name: Any = None,
        category: Any = None,
        detail_url: str | None = None,
    ) -> dict:
        """Start a record with identity, merchant and provenance filled in."""
        record = empty_offer()
        now = self._now()

        offer_id = f"{self.key}__{stable_hash(self.key, source_ref, title, length=10)}"
        record["identity"].update(
            {
                "offer_id": offer_id,
                "canonical_offer_id": offer_id,
                "title": N.clean_text(title),
                "grantor_scope": (
                    "NETWORK"
                    if self.network and not self.issuer
                    else self.grantor_scope
                ),
                "grantor_name": self.issuer or self.network or self.name,
                "issuer": self.issuer,
                "network": self.network,
                "source_offer_ref": source_ref,
            }
        )

        merchant = self.merchants.resolve(
            merchant_name, category, issuer=self.issuer, source_url=self.url
        )
        if merchant:
            record["merchant"].update(
                {
                    "merchant_id": merchant["merchant_id"],
                    "merchant_name": merchant["merchant_name"],
                    "merchant_category": merchant["merchant_category"],
                    "merchant_aliases": merchant["aliases"] or None,
                }
            )
        elif category is not None:
            record["merchant"]["merchant_category"] = normalise_category(category)

        record["benefit"]["currency"] = DEFAULT_CURRENCY
        record["validity"]["timezone"] = DEFAULT_TIMEZONE
        record["eligibility"]["issuer"] = self.issuer
        record["eligibility"]["network"] = self.network

        record.update(
            {
                "source_name": self.name,
                "source_url": self.url,
                "source_type": self.source_type,
                "source_tier": self.tier,
                "detail_url": detail_url,
                "extracted_at": now,
                "last_verified_at": now,
                "extraction_method": self.extraction_method,
                "lifecycle_status": "UNKNOWN",
            }
        )
        return record

    def set_benefit(
        self, record: dict, text: Any, *, hint: str | None = None, snapshot: Any = None
    ) -> dict:
        """Populate the benefit block from source text, per the two-axis model.

        Only the fields relevant to the resolved mechanism are populated; the
        rest stay null. A percentage is never written into a cashback field and
        a reward multiplier is never flattened into a discount, because those
        collapses are what silently produce wrong "best offer" rankings.
        """
        blob = N.clean_text(text) or ""
        pct = N.parse_percentage(blob)
        rate_type = N.parse_rate_type(blob)
        cap = N.parse_maximum_benefit(blob)
        amounts = N.parse_all_money(blob)
        flat = amounts[0] if amounts else None

        benefit_type, nature = classify_benefit(
            blob,
            has_percentage=pct is not None,
            has_flat_amount=flat is not None,
            hint=hint,
        )
        block = record["benefit"]
        block["benefit_type"] = benefit_type
        block["benefit_nature"] = nature
        block["benefit_text"] = blob or None
        block["maximum_benefit"] = cap
        # "Up to 20%" is a ceiling, not a rate a user will receive.
        block["rate_type"] = rate_type

        if benefit_type == PERCENTAGE_DISCOUNT:
            block["discount_percentage"] = pct
            # "Up to Rs X" is the cap, not a flat discount - never both.
            if pct is None and flat is not None and cap is None:
                block["flat_discount"] = flat
        elif benefit_type == FLAT_DISCOUNT:
            block["flat_discount"] = flat if cap is None else None
        elif benefit_type == CASHBACK:
            block["cashback"] = pct if pct is not None else None
            if pct is None and flat is not None and cap is None:
                block["flat_discount"] = flat
        elif benefit_type == REWARD_MULTIPLIER:
            block["reward_multiplier"] = N.parse_reward_multiplier(blob)
        else:
            if pct is not None:
                block["discount_percentage"] = pct

        record["evidence_data"].append(
            evidence(
                "benefit",
                blob or None,
                snapshot if snapshot is not None else text,
                self.url,
            )
        )
        return record

    def set_validity(
        self, record: dict, valid_from: Any, valid_until: Any, snapshot: Any = None
    ) -> dict:
        record["validity"]["valid_from"] = valid_from
        record["validity"]["valid_until"] = valid_until
        if valid_from or valid_until:
            record["evidence_data"].append(
                evidence(
                    "validity", f"{valid_from} .. {valid_until}", snapshot, self.url
                )
            )
        return record

    def set_cards(
        self, record: dict, slugs: Iterable[str], snapshot: Any = None
    ) -> dict:
        """Resolve source card slugs through the Card Master relationship layer."""
        slugs = [s for s in (slugs or []) if s]
        if not slugs:
            return record
        link = self.cards.link(slugs, issuer=self.issuer)
        elig = record["eligibility"]
        elig["card_ids"] = link["card_ids"] or None
        elig["unmatched_card_slugs"] = link["unmatched_card_slugs"] or None
        elig["card_families"] = self.cards.families(link["card_ids"]) or None
        record["evidence_data"].append(
            evidence(
                "eligibility.card_ids",
                link["card_ids"],
                snapshot if snapshot is not None else ",".join(slugs),
                self.url,
                needs_review=bool(link["unmatched_card_slugs"]),
                review_reason=(
                    f"{len(link['unmatched_card_slugs'])} of {len(slugs)} "
                    "source card slugs "
                    "did not resolve against the Card Master"
                    if link["unmatched_card_slugs"]
                    else None
                ),
            )
        )
        return record

    def set_scope(self, record: dict) -> dict:
        """Write the Section 9 eligibility_scope conjunction."""
        elig = record["eligibility"]
        scope = {
            k: v
            for k, v in {
                "network": elig.get("network"),
                "issuer": elig.get("issuer"),
                "card_ids": elig.get("card_ids"),
                "card_families": elig.get("card_families"),
                "merchant_id": (record.get("merchant") or {}).get("merchant_id"),
                "student_gate": elig.get("student_required"),
                "new_customer_gate": elig.get("new_customer_required"),
                "minimum_spend": elig.get("minimum_spend"),
                "transaction_type": elig.get("transaction_type"),
                "location": elig.get("location"),
                "frequency_limit": elig.get("frequency_limit"),
            }.items()
            if v not in (None, [], {})
        }
        elig["eligibility_scope"] = scope or None
        return record

    def set_redemption(
        self,
        record: dict,
        *,
        coupon: Any = None,
        redemption_url: str | None = None,
        redemption_type: str | None = None,
        activation_required: Any = None,
    ) -> dict:
        block = record["redemption"]
        code = N.clean_text(coupon)
        block["coupon_code"] = code
        block["redemption_type"] = redemption_type or (
            REDEEM_PROMO_CODE if code else REDEEM_AUTOMATIC
        )
        block["redemption_url"] = redemption_url
        block["activation_required"] = activation_required
        return record

    def set_transaction(self, record: dict, text: Any, is_online: Any = None) -> dict:
        txn = infer_transaction_type(text, is_online)
        record["eligibility"]["transaction_type"] = txn
        if txn in {"ONLINE", "OFFLINE"}:
            record["eligibility"]["online_offline"] = txn
        return record

    def absolutise(self, href: Any, base: str | None = None) -> str | None:
        if not href or not isinstance(href, str):
            return None
        return urljoin(base or self.url, href.replace(" ", "%20"))


def describe(record: dict) -> str | None:
    """RenoCred-authored one-line summary.

    Section 8 and Section 20 both require that RenoCred author its own
    description rather than republish bank or merchant copy verbatim, so this
    composes a sentence from the *structured* fields we extracted rather than
    copying source prose. The raw source text stays in ``benefit_text`` and in
    the evidence trail, where it belongs.
    """
    benefit = record.get("benefit") or {}
    merchant = (record.get("merchant") or {}).get("merchant_name")
    elig = record.get("eligibility") or {}
    grantor = (record.get("identity") or {}).get("grantor_name")

    if benefit.get("discount_percentage") is not None:
        head = f"{benefit['discount_percentage']:g}% discount"
    elif benefit.get("cashback") is not None:
        head = f"{benefit['cashback']:g}% cashback"
    elif benefit.get("reward_multiplier") is not None:
        head = f"{benefit['reward_multiplier']:g}X reward points"
    elif benefit.get("flat_discount") is not None:
        head = f"Rs {benefit['flat_discount']:,.0f} off"
    elif benefit.get("maximum_benefit") is not None:
        head = f"Up to Rs {benefit['maximum_benefit']:,.0f} benefit"
    else:
        nature = (benefit.get("benefit_nature") or "offer").lower()
        head = f"{nature.capitalize()} offer"

    parts = [head]
    if merchant:
        parts.append(f"at {merchant}")
    if grantor:
        parts.append(f"for {grantor} cardholders")
    if elig.get("minimum_spend") is not None:
        parts.append(f"on a minimum spend of Rs {elig['minimum_spend']:,.0f}")
    if benefit.get("maximum_benefit") is not None and "Up to" not in head:
        parts.append(f"capped at Rs {benefit['maximum_benefit']:,.0f}")
    until = (record.get("validity") or {}).get("valid_until")
    if until:
        parts.append(f"valid until {until}")
    return " ".join(parts) + "."
