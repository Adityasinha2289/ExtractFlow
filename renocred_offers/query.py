"""Wallet matching demo (Master Plan Sections 11 and 24).

Answers the plan's own worked question -- "I am about to spend Rs 8,000 at
Nike, which of my cards should I use and what will I actually get?" -- against
the built Offer Master.

This is a demonstration of the matching layer, not the ranking engine itself.
Two properties matter and are enforced here:

* Only ELIGIBLE, positively-verified offers can be ranked. INSUFFICIENT_DATA
  offers are reported separately, with their caveat, and never as the answer.
* Ranking sees no affiliate, partner or referral field. Section 19 requires that
  monetisation cannot influence rank order, so the ranking function is given
  only benefit and eligibility data -- there is no code path by which a partner
  field could reach it.
"""

import argparse
import datetime as dt
import json
from typing import Any

from renocred_offers.eligibility import Wallet, evaluate
from renocred_offers.normalize import RATE_UP_TO
from renocred_offers.taxonomy import (
    CASHBACK,
    FLAT_DISCOUNT,
    NATURE_REWARD,
    PERCENTAGE_DISCOUNT,
    VOUCHER_ACCELERATOR,
)
from renocred_offers.verification import VERIFIED

#: Rupee value of one reward point. Deliberately a named, tunable constant
#: rather than a magic number: Section 7's whole argument is that points and
#: cashback are not comparable without an explicit conversion, so the
#: conversion has to be visible and challengeable.
DEFAULT_POINT_VALUE_INR = 0.25


def net_benefit(
    offer: dict, spend: float, point_value: float = DEFAULT_POINT_VALUE_INR
) -> dict[str, Any]:
    """Estimate an offer's rupee value on a transaction of ``spend``.

    Returns the amount *and* how it was derived, because Section 26's Phase
    3C.6 acceptance criterion is that a recommendation must be explainable --
    RenoCred has to be able to show which offer and which arithmetic produced
    the number, not just a ranked list.
    """
    benefit = offer.get("benefit") or {}
    elig = offer.get("eligibility") or {}
    cap = benefit.get("maximum_benefit")
    minimum = elig.get("minimum_spend")

    if minimum is not None and spend < minimum:
        return {
            "value": 0.0,
            "basis": f"spend below the Rs {minimum:,.0f} minimum",
            "applicable": False,
        }

    kind = benefit.get("benefit_type")
    value: float | None = None
    basis = ""

    if kind == PERCENTAGE_DISCOUNT and benefit.get("discount_percentage") is not None:
        rate = benefit["discount_percentage"]
        value = spend * rate / 100.0
        basis = f"{rate:g}% of Rs {spend:,.0f}"
    elif kind == CASHBACK and benefit.get("cashback") is not None:
        rate = benefit["cashback"]
        value = spend * rate / 100.0
        basis = f"{rate:g}% cashback on Rs {spend:,.0f}"
    elif kind == VOUCHER_ACCELERATOR and benefit.get("discount_percentage") is not None:
        rate = benefit["discount_percentage"]
        value = spend * rate / 100.0
        basis = f"{rate:g}% off the voucher face value"
    elif kind == FLAT_DISCOUNT and benefit.get("flat_discount") is not None:
        value = float(benefit["flat_discount"])
        basis = f"flat Rs {value:,.0f} off"
    elif benefit.get("reward_multiplier") is not None:
        # Points are converted explicitly, never silently flattened into a
        # percentage. A base earn of 1 point per Rs 100 is the Indian norm.
        multiplier = benefit["reward_multiplier"]
        points = (spend / 100.0) * multiplier
        value = points * point_value
        basis = (
            f"{points:,.0f} points ({multiplier:g}X on Rs {spend:,.0f}) "
            f"at Rs {point_value:.2f}/point"
        )
    elif cap is not None:
        # "Up to Rs X" with no rate: the cap is the only stated number, and it
        # is an upper bound, not an expectation. Report it as such.
        return {
            "value": None,
            "basis": f"source states only an upper bound of Rs {cap:,.0f}",
            "applicable": True,
            "upper_bound": cap,
        }

    if value is None:
        return {
            "value": None,
            "basis": "no computable benefit stated",
            "applicable": True,
        }

    if cap is not None and value > cap:
        value = float(cap)
        basis += f", capped at Rs {cap:,.0f}"

    if benefit.get("benefit_nature") == NATURE_REWARD or kind == VOUCHER_ACCELERATOR:
        basis += "; requires a redemption step"

    if benefit.get("rate_type") == RATE_UP_TO:
        # "Up to 20% off" is a ceiling most transactions never reach. Ranking it
        # as though it were a certain 20% is how an engine confidently promises
        # money the user does not get (Master Plan Section 27 item 5), so it is
        # reported as a bound and sorts below every offer with a definite rate.
        return {
            "value": None,
            "basis": f"upper bound only - source states {basis}",
            "applicable": True,
            "upper_bound": round(value, 2),
        }

    return {"value": round(value, 2), "basis": basis, "applicable": True}


def rank(
    offers: list[dict],
    wallet: Wallet,
    spend: float,
    merchant: str | None = None,
    today: dt.date | None = None,
    card_master: Any = None,
    point_value: float = DEFAULT_POINT_VALUE_INR,
) -> dict[str, Any]:
    """Rank eligible offers by net verified benefit."""
    today = today or dt.date.today()
    ranked: list[dict] = []
    possible: list[dict] = []

    for offer in offers:
        if merchant:
            name = ((offer.get("merchant") or {}).get("merchant_name") or "").lower()
            if merchant.lower() not in name:
                continue

        result = evaluate(offer, wallet, today, card_master)
        status = (offer.get("quality") or {}).get("verification_status")
        row = {
            "offer_id": (offer.get("identity") or {}).get("offer_id"),
            "title": (offer.get("identity") or {}).get("title"),
            "merchant": (offer.get("merchant") or {}).get("merchant_name"),
            "issuer": (offer.get("identity") or {}).get("issuer"),
            "cards": result.matched_card_ids,
            "verification": status,
            "confidence": (offer.get("quality") or {}).get("confidence"),
            "valid_until": (offer.get("validity") or {}).get("valid_until"),
            **net_benefit(offer, spend, point_value),
        }

        if result.status == "ELIGIBLE" and status == VERIFIED:
            ranked.append(row)
        elif result.show_with_caveat or status != VERIFIED:
            row["caveat"] = "; ".join(result.reasons) or f"verification_status={status}"
            possible.append(row)

    # Definite values first, best first. Ceiling-only offers ("up to 20%") sort
    # below every offer with a rate a user will actually receive, and among
    # themselves by their bound - a bound is weaker evidence, not a bigger prize.
    ranked.sort(
        key=lambda r: (
            r["value"] is None,
            -(r["value"] if r["value"] is not None else (r.get("upper_bound") or 0)),
        )
    )
    return {
        "spend": spend,
        "merchant": merchant,
        "best": ranked[0] if ranked else None,
        "eligible": ranked,
        "possible_confirm_with_issuer": possible[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match a wallet against the Offer Master"
    )
    parser.add_argument("--offers", default="output/renocred_offer_master.json")
    parser.add_argument("--cards", nargs="*", default=[], help="card_ids held")
    parser.add_argument("--spend", type=float, default=8000.0)
    parser.add_argument("--merchant", default=None)
    parser.add_argument("--city", default=None)
    parser.add_argument("--point-value", type=float, default=DEFAULT_POINT_VALUE_INR)
    args = parser.parse_args()

    with open(args.offers, encoding="utf-8") as fh:
        offers = json.load(fh)["data"]

    wallet = Wallet(
        card_ids=args.cards,
        city=args.city,
        issuers=sorted({c.split("_")[0] for c in args.cards}),
    )
    result = rank(
        offers, wallet, args.spend, args.merchant, point_value=args.point_value
    )

    print(
        f"\nWallet: {len(args.cards)} card(s) | spend Rs {args.spend:,.0f}"
        + (f" at {args.merchant}" if args.merchant else "")
    )
    print(
        f"Eligible & verified: {len(result['eligible'])} | "
        f"needs confirmation: {len(result['possible_confirm_with_issuer'])}\n"
    )

    if result["best"]:
        best = result["best"]
        if best["value"] is not None:
            amount = f"Rs {best['value']:,.2f}"
        elif best.get("upper_bound") is not None:
            amount = f"at most Rs {best['upper_bound']:,.0f}"
        else:
            amount = "value not computable"
        print(f"BEST: {best['title']}")
        print(f"  merchant   : {best['merchant']}")
        print(f"  net benefit: {amount}  ({best['basis']})")
        print(f"  cards      : {', '.join(best['cards']) or 'issuer-wide'}")
        print(
            f"  confidence : {best['confidence']} | valid until {best['valid_until']}"
        )
    else:
        print("No eligible verified offer for this wallet and context.")

    for row in result["eligible"][1:6]:
        if row["value"] is not None:
            amount = f"Rs {row['value']:,.2f}"
        elif row.get("upper_bound") is not None:
            amount = f"<= Rs {row['upper_bound']:,.0f}"
        else:
            amount = "-"
        print(f"   - {amount:>14}  {row['title'][:64]}")


if __name__ == "__main__":
    main()
