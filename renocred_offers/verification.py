"""Verification, freshness and confidence (Master Plan Sections 12, 14, 18).

Three rules from the plan are load-bearing here and are enforced in code, not
merely documented:

1. A D-tier signal can move an offer ``UNKNOWN -> NEEDS_REVIEW`` (i.e. queue it
   for crawling) but can never move anything to ``VERIFIED``.
2. An open conflict caps confidence regardless of every other input.
3. Confidence is a transparent weighted sum of four independent, auditable
   inputs -- not an opaque score that can drift.
"""

import datetime as dt
from typing import Any

from extractflow.utils.helpers import deep_get
from renocred_offers.schema import COMPLETENESS_FIELDS
from renocred_offers.taxonomy import TIER_RANK, TIER_A, TIER_B, TIER_S

# --------------------------------------------------------------------------
# Section 12 - verification states
# --------------------------------------------------------------------------
VERIFIED = "VERIFIED"
LIKELY_ACTIVE = "LIKELY_ACTIVE"
NEEDS_REVIEW = "NEEDS_REVIEW"
CONFLICTED = "CONFLICTED"
EXPIRED = "EXPIRED"
UNKNOWN = "UNKNOWN"

VERIFICATION_STATES = [
    VERIFIED,
    LIKELY_ACTIVE,
    NEEDS_REVIEW,
    CONFLICTED,
    EXPIRED,
    UNKNOWN,
]

#: Only S/A-tier sources can ever certify an offer.
CERTIFYING_TIERS = {TIER_S, TIER_A}

# --------------------------------------------------------------------------
# Section 14 - freshness windows, in days, by offer category volatility
# --------------------------------------------------------------------------
STALE_AFTER_DAYS: dict[str, int] = {
    "ISSUER_FLASH_DEAL": 1,
    "ISSUER_PORTAL": 7,
    "ISSUER_CAMPAIGN": 7,
    "INFRA_VENDOR": 7,
    "MERCHANT_PROMO": 3,
    "NETWORK_PROGRAM": 30,
    "CO_BRAND_PAGE": 30,
    "ENTITLEMENT": 90,
    "AGGREGATOR": 3,
    "COMMUNITY": 1,
}
DEFAULT_STALE_AFTER_DAYS = 7


def stale_after(
    last_verified: str | dt.datetime,
    source_type: str | None,
    benefit_nature: str | None = None,
) -> str:
    """The instant past which ``last_verified`` alone stops being sufficient."""
    if isinstance(last_verified, str):
        base = dt.datetime.fromisoformat(last_verified.replace("Z", "+00:00"))
    else:
        base = last_verified
    if benefit_nature == "ENTITLEMENT":
        days = STALE_AFTER_DAYS["ENTITLEMENT"]
    else:
        days = STALE_AFTER_DAYS.get(source_type or "", DEFAULT_STALE_AFTER_DAYS)
    return (base + dt.timedelta(days=days)).isoformat()


def lifecycle(
    valid_from: str | None, valid_until: str | None, today: dt.date | None = None
) -> str:
    """ACTIVE / SCHEDULED / EXPIRED / UNKNOWN from the validity window alone."""
    today = today or dt.date.today()
    start = dt.date.fromisoformat(valid_from) if valid_from else None
    end = dt.date.fromisoformat(valid_until) if valid_until else None
    if end and end < today:
        return "EXPIRED"
    if start and start > today:
        return "SCHEDULED"
    if start or end:
        return "ACTIVE"
    return "UNKNOWN"


# --------------------------------------------------------------------------
# Section 18 - confidence scoring
# --------------------------------------------------------------------------
#: Human-tunable weights. A weighted sum beats an opaque model at launch
#: because the team -- and eventually the user, via a "verified N days ago"
#: badge -- has to be able to explain why an offer is or is not trusted.
WEIGHTS = {"tier": 0.40, "recency": 0.30, "completeness": 0.20, "conflict": 0.10}

#: Ceiling applied when a conflict is open, whatever the other inputs say.
CONFLICT_CONFIDENCE_CAP = 0.35


def tier_score(source_tier: str | None) -> float:
    return round(TIER_RANK.get(source_tier or "", 0) / 5.0, 4)


def recency_score(
    last_verified: str | None, stale_at: str | None, now: dt.datetime | None = None
) -> float:
    """1.0 while fresh, decaying to 0.0 across a second staleness window."""
    if not last_verified or not stale_at:
        return 0.0
    now = now or dt.datetime.now()
    try:
        verified = dt.datetime.fromisoformat(last_verified.replace("Z", "+00:00"))
        stale = dt.datetime.fromisoformat(stale_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if verified.tzinfo:
        verified = verified.replace(tzinfo=None)
    if stale.tzinfo:
        stale = stale.replace(tzinfo=None)
    if now <= stale:
        return 1.0
    window = max((stale - verified).total_seconds(), 1.0)
    overdue = (now - stale).total_seconds()
    return round(max(0.0, 1.0 - overdue / window), 4)


def completeness_score(record: dict, fields: list[str] | None = None) -> float:
    fields = fields or COMPLETENESS_FIELDS
    filled = 0
    for path in fields:
        value = deep_get(record, path)
        if value is None:
            continue
        if isinstance(value, (str, list, dict, tuple)) and len(value) == 0:
            continue
        filled += 1
    return round(filled / len(fields), 4) if fields else 0.0


def score_confidence(record: dict, now: dt.datetime | None = None) -> dict[str, Any]:
    """Compute confidence from the four independent Section 18 inputs."""
    quality = record.get("quality") or {}
    inputs = {
        "tier": tier_score(record.get("source_tier")),
        "recency": recency_score(
            record.get("last_verified_at"), quality.get("stale_after"), now
        ),
        "completeness": completeness_score(record),
        "conflict": (
            0.0 if quality.get("conflict_status") not in (None, "NONE") else 1.0
        ),
    }
    score = sum(WEIGHTS[k] * v for k, v in inputs.items())

    if quality.get("conflict_status") not in (None, "NONE"):
        score = min(score, CONFLICT_CONFIDENCE_CAP)
    if record.get("lifecycle_status") == "EXPIRED":
        score = min(score, 0.20)

    score = round(max(0.0, min(1.0, score)), 4)
    return {"confidence": score, "inputs": inputs, "band": confidence_band(score)}


def confidence_band(score: float | None) -> str:
    """Bucket a score into the card master's confidence vocabulary."""
    if score is None:
        return "UNAVAILABLE"
    if score >= 0.75:
        return "HIGH"
    if score >= 0.50:
        return "MEDIUM"
    if score > 0.0:
        return "LOW"
    return "UNAVAILABLE"


# --------------------------------------------------------------------------
# Section 12 - the state machine
# --------------------------------------------------------------------------
def resolve_state(record: dict, now: dt.datetime | None = None) -> dict[str, Any]:
    """Determine an offer's verification state and whether a human must see it.

    Ordering matters: expiry and conflict are terminal for this pass and are
    tested before any freshness reasoning, because a stale-but-fresh-looking
    conflicted offer must never present as VERIFIED.
    """
    now = now or dt.datetime.now()
    quality = record.get("quality") or {}
    tier = record.get("source_tier")
    reasons: list[str] = list(quality.get("review_reasons") or [])

    life = record.get("lifecycle_status")
    if life == "EXPIRED":
        return {
            "verification_status": EXPIRED,
            "human_review_required": False,
            "review_reasons": reasons,
        }

    if quality.get("conflict_status") not in (None, "NONE"):
        reasons.append("open material conflict between sources (Section 13)")
        return {
            "verification_status": CONFLICTED,
            "human_review_required": True,
            "review_reasons": reasons,
        }

    if tier not in CERTIFYING_TIERS:
        # B/C-tier is discovery-grade and D-tier is signal-only. Neither can
        # certify; both merely queue the offer for S/A-tier re-verification.
        reasons.append(
            f"source tier {tier or 'unknown'} is discovery-grade, "
            "not verification-grade"
        )
        return {
            "verification_status": NEEDS_REVIEW if tier in {TIER_B, "C"} else UNKNOWN,
            "human_review_required": True,
            "review_reasons": reasons,
        }

    completeness = quality.get("completeness")
    if completeness is not None and completeness < 0.5:
        reasons.append(
            f"eligibility completeness {completeness:.0%} below 50% threshold"
        )
        return {
            "verification_status": NEEDS_REVIEW,
            "human_review_required": True,
            "review_reasons": reasons,
        }

    if not (record.get("eligibility") or {}).get("card_ids"):
        reasons.append("no source card slug resolved to a Card Master card id")

    stale_at = quality.get("stale_after")
    if stale_at:
        try:
            stale = dt.datetime.fromisoformat(stale_at.replace("Z", "+00:00"))
            if stale.tzinfo:
                stale = stale.replace(tzinfo=None)
            if now > stale:
                return {
                    "verification_status": LIKELY_ACTIVE,
                    "human_review_required": False,
                    "review_reasons": reasons
                    + ["past staleAfter window; awaiting re-crawl"],
                }
        except ValueError:
            pass

    # A-tier is treated as S for terms but flagged for a cap double-check,
    # because vendor and bank T&C can drift out of sync (Section 6).
    if (
        tier == TIER_A
        and (record.get("benefit") or {}).get("maximum_benefit") is not None
    ):
        reasons.append("A-tier vendor cap; double-check against issuer T&C (Section 6)")

    return {
        "verification_status": VERIFIED,
        "human_review_required": bool(reasons),
        "review_reasons": reasons,
    }


def apply_verification(record: dict, now: dt.datetime | None = None) -> dict:
    """Populate the whole quality block on a record, in dependency order."""
    quality = record.setdefault("quality", {})
    quality.setdefault("conflict_status", "NONE")
    quality["completeness"] = completeness_score(record)
    quality["stale_after"] = stale_after(
        record.get("last_verified_at") or dt.datetime.now().isoformat(),
        record.get("source_type"),
        (record.get("benefit") or {}).get("benefit_nature"),
    )

    state = resolve_state(record, now)
    quality["verification_status"] = state["verification_status"]
    quality["human_review_required"] = state["human_review_required"]
    quality["review_reasons"] = state["review_reasons"] or None

    scored = score_confidence(record, now)
    quality["confidence"] = scored["confidence"]
    quality["confidence_band"] = scored["band"]

    record["recommendation_confidence"] = scored["band"]
    record["data_quality"] = (
        "NEEDS_REVIEW" if state["human_review_required"] else "VALID"
    )

    # Section 11/19: anything not positively verified is withheld from the top
    # recommendation, and the reason is recorded so the exclusion is auditable.
    if state["verification_status"] != VERIFIED:
        record["recommendation_exclusion_reason"] = (
            f"verification_status={state['verification_status']}"
        )
    else:
        record["recommendation_exclusion_reason"] = None
    return record
