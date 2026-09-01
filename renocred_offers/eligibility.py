"""Eligibility Engine (Master Plan Section 11).

Returns a *ternary* result, not a boolean. The third state carries the weight:
RenoCred frequently cannot tell from card data alone whether a user's card is a
Corporate/Commercial variant -- excluded from nearly every consumer voucher
promotion researched -- or was issued without a joining fee. Section 11 is
explicit that silently defaulting INSUFFICIENT_DATA to ELIGIBLE is how a
recommendation engine starts costing users money, so the policy here is encoded
as a hard default: uncertainty excludes an offer from the top recommendation,
and may only ever be surfaced as "possibly eligible -- confirm with issuer".
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class Wallet:
    """A user's held/considered cards plus the few non-sensitive attributes
    that legitimately gate offers. Nothing more sensitive is modelled."""

    card_ids: list[str] = field(default_factory=list)
    is_student: bool | None = None
    is_new_customer: bool | None = None
    city: str | None = None
    networks: list[str] = field(default_factory=list)
    issuers: list[str] = field(default_factory=list)


@dataclass
class EligibilityResult:
    status: str
    reasons: list[str] = field(default_factory=list)
    matched_card_ids: list[str] = field(default_factory=list)
    #: True when the offer may be shown, but only with a confirm-with-issuer
    #: caveat and never as the top recommendation.
    show_with_caveat: bool = False

    @property
    def recommendable(self) -> bool:
        return self.status == ELIGIBLE


def _norm(values: Iterable[str] | None) -> set[str]:
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


def evaluate(
    offer: dict, wallet: Wallet, today: dt.date | None = None, card_master: Any = None
) -> EligibilityResult:
    """Evaluate one offer against one wallet."""
    today = today or dt.date.today()
    elig = offer.get("eligibility") or {}
    validity = offer.get("validity") or {}
    reasons: list[str] = []

    # -- validity window -------------------------------------------------
    until = validity.get("valid_until")
    if until:
        try:
            if dt.date.fromisoformat(until) < today:
                return EligibilityResult(NOT_ELIGIBLE, [f"offer expired on {until}"])
        except ValueError:
            reasons.append(f"unparseable valid_until {until!r}")
    start = validity.get("valid_from")
    if start:
        try:
            if dt.date.fromisoformat(start) > today:
                return EligibilityResult(NOT_ELIGIBLE, [f"offer starts on {start}"])
        except ValueError:
            reasons.append(f"unparseable valid_from {start!r}")

    # -- card-level scope ------------------------------------------------
    offer_cards = _norm(elig.get("card_ids"))
    wallet_cards = _norm(wallet.card_ids)
    matched = sorted(offer_cards & wallet_cards)

    if offer_cards:
        if matched:
            reasons.append(
                f"{len(matched)} wallet card(s) named in the offer's card list"
            )
        else:
            unmatched = elig.get("unmatched_card_slugs") or []
            if unmatched:
                # The offer names cards this pipeline could not resolve, so a
                # non-match is not proof of ineligibility.
                return EligibilityResult(
                    INSUFFICIENT_DATA,
                    [
                        f"no wallet card matched, but {len(unmatched)} "
                        "source card slug(s) "
                        "are unresolved against the Card Master"
                    ],
                    show_with_caveat=True,
                )
            return EligibilityResult(
                NOT_ELIGIBLE, ["no wallet card is in the offer's card list"]
            )

    # -- issuer / network scope (when no explicit card list) --------------
    if not offer_cards:
        issuer = elig.get("issuer")
        network = elig.get("network")
        if issuer and _norm([issuer]) - _norm(wallet.issuers):
            if not wallet.issuers:
                return EligibilityResult(
                    INSUFFICIENT_DATA,
                    [f"offer is scoped to issuer {issuer}; wallet issuers unknown"],
                    show_with_caveat=True,
                )
            return EligibilityResult(NOT_ELIGIBLE, [f"wallet holds no {issuer} card"])
        if network and _norm([network]) - _norm(wallet.networks):
            if not wallet.networks:
                return EligibilityResult(
                    INSUFFICIENT_DATA,
                    [f"offer is scoped to network {network}; wallet networks unknown"],
                    show_with_caveat=True,
                )
            return EligibilityResult(NOT_ELIGIBLE, [f"wallet holds no {network} card"])

    # -- corporate / commercial variant exclusion ------------------------
    # Nearly every voucher-accelerator T&C researched excludes Corporate,
    # Commercial, Dealer and Distributor cards. If the Card Master cannot
    # express that distinction, we must not assume a consumer card.
    if card_master is not None and matched:
        unknown_variant = [
            cid for cid in matched if card_master.consumer_variant_known(cid) is None
        ]
        if unknown_variant and _excludes_corporate(offer):
            return EligibilityResult(
                INSUFFICIENT_DATA,
                [
                    "offer excludes Corporate/Commercial variants and the Card Master "
                    "does not record consumer-vs-corporate for "
                    f"{len(unknown_variant)} matched card(s)"
                ],
                matched_card_ids=matched,
                show_with_caveat=True,
            )

    # -- gate flags -------------------------------------------------------
    if elig.get("student_required") is True and wallet.is_student is not True:
        if wallet.is_student is None:
            return EligibilityResult(
                INSUFFICIENT_DATA,
                ["offer requires student status; wallet does not state it"],
                matched_card_ids=matched,
                show_with_caveat=True,
            )
        return EligibilityResult(
            NOT_ELIGIBLE, ["offer requires student status"], matched_card_ids=matched
        )

    if elig.get("new_customer_required") is True and wallet.is_new_customer is not True:
        if wallet.is_new_customer is None:
            return EligibilityResult(
                INSUFFICIENT_DATA,
                ["offer is new-cardholder-only; wallet does not state it"],
                matched_card_ids=matched,
                show_with_caveat=True,
            )
        return EligibilityResult(
            NOT_ELIGIBLE, ["offer is new-cardholder-only"], matched_card_ids=matched
        )

    # -- location ---------------------------------------------------------
    locations = _norm(elig.get("location"))
    if locations and "national" not in locations and "all" not in locations:
        if wallet.city is None:
            return EligibilityResult(
                INSUFFICIENT_DATA,
                [
                    f"offer is restricted to {len(locations)} location(s); "
                    "wallet city unknown"
                ],
                matched_card_ids=matched,
                show_with_caveat=True,
            )
        if wallet.city.strip().lower() not in locations:
            return EligibilityResult(
                NOT_ELIGIBLE,
                [f"offer not available in {wallet.city}"],
                matched_card_ids=matched,
            )
        reasons.append(f"available in {wallet.city}")

    return EligibilityResult(
        ELIGIBLE,
        reasons or ["all stated constraints satisfied"],
        matched_card_ids=matched,
    )


def _excludes_corporate(offer: dict) -> bool:
    rules = (offer.get("eligibility") or {}).get("other_eligibility_rules") or []
    blob = " ".join(str(r) for r in rules).lower()
    return any(w in blob for w in ("corporate", "commercial", "dealer", "distributor"))


def match_wallet(
    offers: list[dict],
    wallet: Wallet,
    today: dt.date | None = None,
    card_master: Any = None,
) -> dict[str, list[dict]]:
    """Partition an offer set for one wallet.

    ``eligible`` is the only bucket the ranking engine may draw its top
    recommendation from; ``possible`` exists purely so the UI can offer a
    clearly-caveated "confirm with issuer" row.
    """
    buckets: dict[str, list[dict]] = {
        "eligible": [],
        "possible": [],
        "not_eligible": [],
    }
    for offer in offers:
        result = evaluate(offer, wallet, today, card_master)
        item = {
            "offer_id": (offer.get("identity") or {}).get("offer_id"),
            "title": (offer.get("identity") or {}).get("title"),
            "status": result.status,
            "reasons": result.reasons,
            "matched_card_ids": result.matched_card_ids,
        }
        if result.status == ELIGIBLE:
            buckets["eligible"].append(item)
        elif result.show_with_caveat:
            buckets["possible"].append(item)
        else:
            buckets["not_eligible"].append(item)
    return buckets
