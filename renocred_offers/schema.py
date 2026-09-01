"""Offer Master record schema (Master Plan Section 8).

The block layout, the null-not-fabricated-zero convention, the ``evidence_data``
item shape and the ``data_quality`` / ``lifecycle_status`` /
``recommendation_confidence`` trailer fields are all deliberately identical to
``renocred_card_master.json`` so that the two masters can be loaded, validated
and joined by the same downstream code -- while remaining, per Section 27 item
10, two structurally separate systems linked only through the explicit
relationship layer in :mod:`renocred_offers.card_link`.
"""

from typing import Any

SCHEMA_VERSION = "1.0.0"
DATASET_VERSION = "1.0.0"
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_CURRENCY = "INR"


def empty_offer() -> dict[str, Any]:
    """A fully-formed Offer Master record with every field present and null.

    Materialising the whole shape up front is what makes ``coverage_by_field``
    meaningful: a missing field and a null field must not be distinguishable
    downstream, or coverage silently over-reports.
    """
    return {
        # -- Identity ---------------------------------------------------
        "identity": {
            "offer_id": None,
            "canonical_offer_id": None,
            "title": None,
            "description": None,
            "grantor_scope": None,
            "grantor_name": None,
            "issuer": None,
            "network": None,
            "source_offer_ref": None,
        },
        # -- Merchant ---------------------------------------------------
        "merchant": {
            "merchant_id": None,
            "merchant_name": None,
            "merchant_category": None,
            "merchant_subcategory": None,
            "merchant_aliases": None,
        },
        # -- Benefit (two-axis taxonomy, Section 7) ---------------------
        "benefit": {
            "benefit_type": None,
            "benefit_nature": None,
            "discount_percentage": None,
            "rate_type": None,
            "flat_discount": None,
            "cashback": None,
            "reward_multiplier": None,
            "maximum_benefit": None,
            "currency": None,
            "benefit_text": None,
        },
        # -- Eligibility (Section 9/11) ---------------------------------
        "eligibility": {
            "eligibility_scope": None,
            "network": None,
            "issuer": None,
            "card_ids": None,
            "card_families": None,
            "unmatched_card_slugs": None,
            "student_required": None,
            "new_customer_required": None,
            "minimum_spend": None,
            "maximum_spend": None,
            "transaction_type": None,
            "online_offline": None,
            "location": None,
            "age_restrictions": None,
            "frequency_limit": None,
            "other_eligibility_rules": None,
            "linked_eligibility_documents": None,
        },
        # -- Validity ---------------------------------------------------
        "validity": {
            "valid_from": None,
            "valid_until": None,
            "timezone": None,
            "status": None,
        },
        # -- Redemption -------------------------------------------------
        "redemption": {
            "redemption_type": None,
            "coupon_code": None,
            "activation_required": None,
            "redemption_url": None,
            "referral_url": None,
            "application_url": None,
            "instructions": None,
        },
        # -- Quality (Sections 12, 13, 14, 18) --------------------------
        "quality": {
            "verification_status": None,
            "confidence": None,
            "confidence_band": None,
            "completeness": None,
            "stale_after": None,
            "conflict_status": None,
            "conflicts": None,
            "verification_method": None,
            "human_review_required": None,
            "review_reasons": None,
        },
        # -- Analytics --------------------------------------------------
        "analytics": {
            "impressions": None,
            "clicks": None,
            "applications": None,
            "redemptions": None,
        },
        # -- Provenance trailer (mirrors card master) -------------------
        "source_name": None,
        "source_url": None,
        "source_type": None,
        "source_tier": None,
        "detail_url": None,
        "extracted_at": None,
        "last_verified_at": None,
        "extraction_method": None,
        "evidence_data": [],
        "data_quality": None,
        "lifecycle_status": None,
        "recommendation_confidence": None,
        "recommendation_exclusion_reason": None,
    }


def evidence(
    field: str,
    value: Any,
    snapshot: Any,
    source_url: str | None,
    needs_review: bool = False,
    review_reason: str | None = None,
) -> dict:
    """One evidence item, shaped exactly like the card master's.

    ``snapshot`` is the raw source text the value was read out of. Section 14
    depends on this: a diff against the stored snapshot is what distinguishes
    "terms silently changed" from "offer removed".
    """
    text = (
        snapshot
        if isinstance(snapshot, str)
        else ("" if snapshot is None else str(snapshot))
    )
    return {
        "field": field,
        "value": value,
        "evidence": text[:2000],
        "source_url": source_url,
        "needs_review": bool(needs_review),
        "review_reason": review_reason,
    }


#: Fields whose population is what ``completeness`` in Section 18 measures.
#: Weighted toward the eligibility block, because eligibility mismatch is the
#: single biggest way RenoCred could mislead a user (Section 8, Section 27).
COMPLETENESS_FIELDS: list[str] = [
    "identity.title",
    "identity.grantor_scope",
    "merchant.merchant_id",
    "merchant.merchant_name",
    "merchant.merchant_category",
    "benefit.benefit_type",
    "benefit.benefit_nature",
    "benefit.maximum_benefit",
    "eligibility.issuer",
    "eligibility.card_ids",
    "eligibility.minimum_spend",
    "eligibility.transaction_type",
    "eligibility.location",
    "validity.valid_from",
    "validity.valid_until",
    "redemption.redemption_type",
    "source_url",
    "source_tier",
]

#: The Phase 3C.2 acceptance gate: nothing reaches storage without these.
INGESTION_SCHEMA: dict[str, Any] = {
    "required": [
        "identity.offer_id",
        "identity.title",
        "merchant.merchant_id",
        "benefit.benefit_type",
        "benefit.benefit_nature",
        "source_url",
        "source_tier",
        "evidence_data",
    ],
    "non_empty": ["evidence_data"],
    "types": {
        "benefit.discount_percentage": "number",
        "benefit.flat_discount": "number",
        "benefit.cashback": "number",
        "benefit.reward_multiplier": "number",
        "benefit.maximum_benefit": "number",
        "eligibility.minimum_spend": "number",
        "eligibility.card_ids": "array",
        "quality.confidence": "number",
        "evidence_data": "array",
    },
    "enums": {
        "source_tier": ["S", "A", "B", "C", "D"],
        "identity.grantor_scope": ["NETWORK", "ISSUER", "MERCHANT"],
        "quality.verification_status": [
            "VERIFIED",
            "LIKELY_ACTIVE",
            "NEEDS_REVIEW",
            "CONFLICTED",
            "EXPIRED",
            "UNKNOWN",
        ],
        "benefit.rate_type": ["EXACT", "UP_TO"],
        "data_quality": ["VALID", "NEEDS_REVIEW"],
        "lifecycle_status": ["ACTIVE", "SCHEDULED", "EXPIRED", "UNKNOWN"],
        "recommendation_confidence": ["HIGH", "MEDIUM", "LOW", "UNAVAILABLE"],
    },
    "recommended": [
        "validity.valid_until",
        "benefit.maximum_benefit",
        "eligibility.card_ids",
    ],
}
