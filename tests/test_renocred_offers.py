"""Tests for the RenoCred offer pipeline.

Several of these encode Master Plan *policy* rather than mechanics -- that a
D-tier source can never certify an offer, that a true numeric conflict is never
averaged away, that a premium-card accelerator never merges into a mass-market
offer. Those are the rules that protect users from being told the wrong number,
so they are asserted by test, not left to code review.
"""

import copy
import datetime as dt

import pytest

from renocred_offers import normalize as N
from renocred_offers.eligibility import (
    ELIGIBLE,
    INSUFFICIENT_DATA,
    NOT_ELIGIBLE,
    Wallet,
    evaluate,
)
from renocred_offers.merchant_master import MerchantMaster, alias_key
from renocred_offers.reconcile import match_key, merge_pair, reconcile
from renocred_offers.schema import empty_offer
from renocred_offers.taxonomy import (
    NATURE_CASHBACK,
    NATURE_REWARD,
    REWARD_MULTIPLIER,
    classify_benefit,
    normalise_category,
)
from renocred_offers.verification import (
    CONFLICTED,
    EXPIRED,
    NEEDS_REVIEW,
    VERIFIED,
    apply_verification,
    confidence_band,
    lifecycle,
    resolve_state,
)


# ---------------------------------------------------------------- normalize
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Rs 1,000 per card", 1000.0),
        ("INR 15,000 Instant Discount", 15000.0),
        ("Rs.150", 150.0),
        ("Rs 2 Lakh", 200000.0),
        ("no amount here", None),
    ],
)
def test_parse_money(text, expected):
    assert N.parse_money(text) == expected


def test_percentage_above_100_is_refused():
    # A "150%" is a mis-parse of something else, not a real rate.
    assert N.parse_percentage("150% weird") is None
    assert N.parse_percentage("Up to 5% Extra Cashback") == 5.0


def test_maximum_benefit_only_when_stated():
    assert N.parse_maximum_benefit("10% up to Rs.150 Instant Discount") == 150.0
    # No cap in the copy means no cap in the record - never invent one.
    assert N.parse_maximum_benefit("5% Instant Discount") is None


def test_minimum_spend():
    assert N.parse_minimum_spend("Min. Trxn: Rs 2,000") == 2000.0
    assert N.parse_minimum_spend("Minimum Booking Value: Rs.500") == 500.0


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Validity: 1 Oct - 10 Oct 2025", ("2025-10-01", "2025-10-10")),
        ("Validity: 22 Sep - 03 Oct 2025", ("2025-09-22", "2025-10-03")),
        ("Offer valid till 31 st October 2026", (None, "2026-10-31")),
    ],
)
def test_parse_date_range(text, expected):
    assert N.parse_date_range(text) == expected


def test_date_range_spanning_year_boundary():
    # A start month later than the end month means the range crosses new year.
    assert N.parse_date_range("15 Dec - 10 Jan 2026") == ("2025-12-15", "2026-01-10")


# ----------------------------------------------------------------- taxonomy
def test_reward_multiplier_is_not_flattened_into_cashback():
    benefit, nature = classify_benefit("Up to 5X reward points on SmartBuy")
    assert benefit == REWARD_MULTIPLIER
    assert nature == NATURE_REWARD


def test_cashback_keeps_its_own_nature():
    _, nature = classify_benefit("Up to 5% Extra Cashback")
    assert nature == NATURE_CASHBACK


def test_category_normalisation():
    assert normalise_category("electronics-n-mobiles") == "ELECTRONICS"
    assert normalise_category("dept-stores-n-groceries") == "GROCERY"
    assert normalise_category(None) is None


# --------------------------------------------------------- merchant master
def test_distinct_merchant_surfaces_do_not_collapse():
    # Section 10: these are different eligibility surfaces, not one merchant.
    assert alias_key("Amazon") != alias_key("Amazon Fresh")
    assert alias_key("Reliance Smart") != alias_key("Reliance Digital")


def test_merchant_aliases_resolve_to_one_id():
    master = MerchantMaster()
    a = master.resolve("BigBasket Pvt Ltd", "grocery", "SBI Card")
    b = master.resolve("BigBasket", "grocery", "HDFC Bank")
    assert a["merchant_id"] == b["merchant_id"]
    assert len(master) == 1


def test_partnership_gap_is_a_research_signal():
    master = MerchantMaster()
    for issuer in ("SBI Card", "HDFC Bank"):
        master.resolve("Myntra", "fashion", issuer)
    gaps = master.partnership_gaps(["SBI Card", "HDFC Bank", "Axis Bank"])
    assert gaps and gaps[0]["unresearched_issuers"] == ["Axis Bank"]


# ------------------------------------------------------------ verification
def _record(**overrides):
    record = empty_offer()
    record.update(
        {
            "source_tier": "S",
            "source_type": "ISSUER_PORTAL",
            "last_verified_at": dt.datetime.now().isoformat(),
            "lifecycle_status": "ACTIVE",
        }
    )
    record["quality"]["conflict_status"] = "NONE"
    record["quality"]["completeness"] = 0.9
    record.update(overrides)
    return record


def test_d_tier_source_can_never_certify():
    # The single most important rule in Section 12.
    for tier in ("B", "C", "D"):
        state = resolve_state(_record(source_tier=tier))
        assert state["verification_status"] != VERIFIED
        assert state["human_review_required"] is True


def test_expired_offer_is_terminal():
    assert (
        resolve_state(_record(lifecycle_status="EXPIRED"))["verification_status"]
        == EXPIRED
    )


def test_open_conflict_forces_conflicted_and_caps_confidence():
    record = _record()
    record["quality"]["conflict_status"] = "CONFLICTED"
    assert resolve_state(record)["verification_status"] == CONFLICTED
    apply_verification(record)
    assert record["quality"]["confidence"] <= 0.35


def test_incomplete_eligibility_blocks_verification():
    record = _record()
    record["quality"]["completeness"] = 0.2
    # completeness is recomputed by apply_verification, so assert via the state
    # machine directly with a deliberately sparse record.
    assert resolve_state(record)["verification_status"] == NEEDS_REVIEW


def test_lifecycle_from_validity_window():
    today = dt.date(2026, 9, 1)
    assert lifecycle("2026-08-01", "2026-09-30", today) == "ACTIVE"
    assert lifecycle("2025-10-01", "2025-10-10", today) == "EXPIRED"
    assert lifecycle("2026-12-01", "2026-12-31", today) == "SCHEDULED"
    assert lifecycle(None, None, today) == "UNKNOWN"


def test_confidence_bands():
    assert confidence_band(0.9) == "HIGH"
    assert confidence_band(0.6) == "MEDIUM"
    assert confidence_band(0.1) == "LOW"
    assert confidence_band(None) == "UNAVAILABLE"


def test_non_verified_offers_carry_an_exclusion_reason():
    record = apply_verification(_record(source_tier="C"))
    assert record["recommendation_exclusion_reason"]
    assert record["data_quality"] == "NEEDS_REVIEW"


# --------------------------------------------------------------- reconcile
def _offer(merchant, issuer, benefit_type, cards, tier="S", pct=None, source="s1"):
    record = _record(
        source_tier=tier, source_name=source, source_url=f"http://{source}"
    )
    record["identity"].update(
        {"offer_id": f"{source}-{merchant}-{pct}", "issuer": issuer}
    )
    record["merchant"]["merchant_id"] = merchant
    record["benefit"].update({"benefit_type": benefit_type, "discount_percentage": pct})
    record["eligibility"]["card_ids"] = cards
    return record


def test_premium_accelerator_does_not_merge_into_mass_market_offer():
    # Section 17: the card-aware key is what stops an Infinia-only accelerator
    # being collapsed into the issuer's general offer at the same merchant.
    mass = _offer("m_myntra", "HDFC Bank", "CASHBACK", ["hdfc_regalia"], pct=5)
    premium = _offer("m_myntra", "HDFC Bank", "CASHBACK", ["hdfc_infinia"], pct=10)
    assert match_key(mass) != match_key(premium)
    out, _ = reconcile([mass, premium])
    assert len(out) == 2


def test_same_source_rows_are_not_treated_as_duplicates():
    a = _offer(
        "m_x", None, "PERCENTAGE_DISCOUNT", None, tier="C", pct=10, source="grabon"
    )
    b = _offer(
        "m_x", None, "PERCENTAGE_DISCOUNT", None, tier="C", pct=20, source="grabon"
    )
    out, report = reconcile([a, b])
    assert len(out) == 2
    assert report["conflicted_count"] == 0


def test_cross_source_numeric_conflict_is_never_averaged():
    a = _offer("m_x", "HDFC Bank", "CASHBACK", ["c1"], tier="S", pct=10, source="bank")
    b = _offer(
        "m_x", "HDFC Bank", "CASHBACK", ["c1"], tier="S", pct=15, source="merchant"
    )
    merged = merge_pair(a, b)
    assert merged["quality"]["conflict_status"] == "CONFLICTED"
    # 12.5 would be the average; neither invented value may appear.
    assert merged["benefit"]["discount_percentage"] in (10, 15)


def test_higher_tier_wins_over_fresher_lower_tier():
    high = _offer(
        "m_x", "HDFC Bank", "CASHBACK", ["c1"], tier="S", pct=10, source="bank"
    )
    low = _offer(
        "m_x", "HDFC Bank", "CASHBACK", ["c1"], tier="C", pct=15, source="coupon"
    )
    low["last_verified_at"] = "2099-01-01T00:00:00"
    merged = merge_pair(high, low)
    assert merged["benefit"]["discount_percentage"] == 10
    assert merged["quality"]["conflict_status"] == "RESOLVED_BY_TIER"


def test_strict_refinement_auto_merges():
    # "10%" and "10% capped at Rs 1,000" are not in conflict (Section 13 item 3).
    vague = _offer("m_x", "HDFC Bank", "CASHBACK", ["c1"], tier="S", pct=10, source="a")
    precise = _offer(
        "m_x", "HDFC Bank", "CASHBACK", ["c1"], tier="S", pct=10, source="b"
    )
    precise["benefit"]["maximum_benefit"] = 1000.0
    merged = merge_pair(vague, precise)
    assert merged["benefit"]["maximum_benefit"] == 1000.0
    assert merged["quality"].get("conflict_status") in (None, "NONE")


# -------------------------------------------------------------- eligibility
def _eligible_offer(**elig):
    record = empty_offer()
    record["identity"]["offer_id"] = "o1"
    record["eligibility"].update(elig)
    return record


def test_wallet_card_match_is_eligible():
    offer = _eligible_offer(card_ids=["sbi_prime", "sbi_elite"])
    result = evaluate(offer, Wallet(card_ids=["sbi_prime"]))
    assert result.status == ELIGIBLE
    assert result.matched_card_ids == ["sbi_prime"]


def test_no_card_match_is_not_eligible():
    offer = _eligible_offer(card_ids=["sbi_prime"])
    assert evaluate(offer, Wallet(card_ids=["hdfc_regalia"])).status == NOT_ELIGIBLE


def test_unresolved_source_slugs_yield_insufficient_data_not_eligible():
    # Section 11: uncertainty must never silently resolve to ELIGIBLE.
    offer = _eligible_offer(
        card_ids=["sbi_prime"], unmatched_card_slugs=["sbi-gold-card"]
    )
    result = evaluate(offer, Wallet(card_ids=["hdfc_regalia"]))
    assert result.status == INSUFFICIENT_DATA
    assert result.recommendable is False
    assert result.show_with_caveat is True


def test_expired_offer_is_not_eligible():
    offer = _eligible_offer(card_ids=["c1"])
    offer["validity"]["valid_until"] = "2025-01-01"
    assert evaluate(offer, Wallet(card_ids=["c1"])).status == NOT_ELIGIBLE


def test_unknown_student_status_is_insufficient_not_eligible():
    offer = _eligible_offer(card_ids=["c1"], student_required=True)
    result = evaluate(offer, Wallet(card_ids=["c1"], is_student=None))
    assert result.status == INSUFFICIENT_DATA
    assert result.recommendable is False


def test_location_restriction_without_known_city_is_insufficient():
    offer = _eligible_offer(card_ids=["c1"], location=["MUMBAI", "PUNE"])
    result = evaluate(offer, Wallet(card_ids=["c1"], city=None))
    assert result.status == INSUFFICIENT_DATA


def test_corporate_variant_unknown_blocks_when_offer_excludes_corporate():
    class StubCardMaster:
        @staticmethod
        def consumer_variant_known(_card_id):
            return None  # the Card Master gap flagged in Section 27 item 7

    offer = _eligible_offer(
        card_ids=["c1"],
        other_eligibility_rules=["Corporate Cards, Commercial Cards ... are excluded"],
    )
    result = evaluate(offer, Wallet(card_ids=["c1"]), card_master=StubCardMaster())
    assert result.status == INSUFFICIENT_DATA


# ------------------------------------------------------------------- query
from renocred_offers.query import net_benefit, rank  # noqa: E402


def _benefit_offer(benefit_type, nature="DISCOUNT", **fields):
    record = empty_offer()
    record["identity"]["offer_id"] = "o"
    record["benefit"].update(
        {"benefit_type": benefit_type, "benefit_nature": nature, **fields}
    )
    return record


def test_percentage_benefit_respects_the_cap():
    offer = _benefit_offer(
        "PERCENTAGE_DISCOUNT", discount_percentage=10, maximum_benefit=500.0
    )
    out = net_benefit(offer, spend=8000)
    assert out["value"] == 500.0  # not 800 - the cap binds
    assert "capped" in out["basis"]


def test_benefit_is_zero_below_the_minimum_spend():
    offer = _benefit_offer("PERCENTAGE_DISCOUNT", discount_percentage=10)
    offer["eligibility"]["minimum_spend"] = 10000.0
    out = net_benefit(offer, spend=8000)
    assert out["value"] == 0.0 and out["applicable"] is False


def test_reward_points_are_converted_explicitly_not_flattened():
    # Section 7: points must go through a stated conversion, and the basis has
    # to say so, or a points offer silently out-ranks a better cashback one.
    offer = _benefit_offer(
        "REWARD_MULTIPLIER", nature=NATURE_REWARD, reward_multiplier=5
    )
    out = net_benefit(offer, spend=8000, point_value=0.25)
    assert out["value"] == 100.0  # 8000/100 * 5 points * Rs 0.25
    assert "point" in out["basis"] and "redemption step" in out["basis"]


def test_up_to_rate_is_a_ceiling_not_an_expected_value():
    # "Up to 72% Off" must never be scored as a certain 72%. Doing so is how a
    # recommendation confidently promises money the user never receives.
    offer = _benefit_offer(
        "PERCENTAGE_DISCOUNT", discount_percentage=72, rate_type="UP_TO"
    )
    out = net_benefit(offer, spend=40000)
    assert out["value"] is None
    assert out["upper_bound"] == 28800.0
    assert "upper bound only" in out["basis"]


def test_exact_rate_is_scored_as_an_expected_value():
    offer = _benefit_offer(
        "PERCENTAGE_DISCOUNT", discount_percentage=5, rate_type="EXACT"
    )
    assert net_benefit(offer, spend=8000)["value"] == 400.0


def test_definite_rate_outranks_a_larger_ceiling():
    exact = _benefit_offer(
        "PERCENTAGE_DISCOUNT", discount_percentage=5, rate_type="EXACT"
    )
    exact["eligibility"]["card_ids"] = ["c1"]
    exact["quality"]["verification_status"] = VERIFIED

    ceiling = _benefit_offer(
        "PERCENTAGE_DISCOUNT", discount_percentage=72, rate_type="UP_TO"
    )
    ceiling["eligibility"]["card_ids"] = ["c1"]
    ceiling["quality"]["verification_status"] = VERIFIED

    out = rank([ceiling, exact], Wallet(card_ids=["c1"]), spend=8000)
    assert out["best"]["value"] == 400.0  # the certain Rs 400, not "up to Rs 5,760"


def test_rate_type_detection():
    assert N.parse_rate_type("5% Instant Discount") == "EXACT"
    assert N.parse_rate_type("Up to 20% Instant Discount") == "UP_TO"
    # A definite rate that merely carries a rupee cap is still an exact rate.
    assert N.parse_rate_type("10% up to Rs.150 Instant Discount") == "EXACT"


def test_upper_bound_only_offer_reports_no_point_estimate():
    # "Up to Rs 15,000" with no rate is a bound, not an expected value.
    offer = _benefit_offer("FLAT_DISCOUNT", maximum_benefit=15000.0)
    out = net_benefit(offer, spend=8000)
    assert out["value"] is None
    assert out["upper_bound"] == 15000.0


def test_only_verified_and_eligible_offers_are_ranked():
    verified = _benefit_offer("CASHBACK", nature=NATURE_CASHBACK, cashback=5)
    verified["eligibility"]["card_ids"] = ["c1"]
    verified["quality"]["verification_status"] = VERIFIED
    verified["merchant"]["merchant_name"] = "Nike"

    unverified = _benefit_offer("CASHBACK", nature=NATURE_CASHBACK, cashback=50)
    unverified["eligibility"]["card_ids"] = ["c1"]
    unverified["quality"]["verification_status"] = NEEDS_REVIEW
    unverified["merchant"]["merchant_name"] = "Nike"

    out = rank(
        [verified, unverified], Wallet(card_ids=["c1"]), spend=8000, merchant="Nike"
    )
    # The richer-looking but unverified offer must not become the answer.
    assert len(out["eligible"]) == 1
    assert out["best"]["value"] == 400.0
    assert len(out["possible_confirm_with_issuer"]) == 1


def test_ranking_ignores_affiliate_fields():
    # Section 19 / Phase 3C.7: rank order must be provably independent of
    # monetisation data, verified by test rather than by policy alone.
    base = _benefit_offer("CASHBACK", nature=NATURE_CASHBACK, cashback=5)
    base["eligibility"]["card_ids"] = ["c1"]
    base["quality"]["verification_status"] = VERIFIED

    monetised = copy.deepcopy(base)
    monetised["referral"] = {"partner": "BigAffiliate", "payout_inr": 5000}
    monetised["redemption"]["referral_url"] = "https://aff.example/?clickId=abc"

    plain_rank = rank([base], Wallet(card_ids=["c1"]), spend=8000)
    monetised_rank = rank([monetised], Wallet(card_ids=["c1"]), spend=8000)
    assert plain_rank["best"]["value"] == monetised_rank["best"]["value"]
    assert plain_rank["eligible"][0]["basis"] == monetised_rank["eligible"][0]["basis"]
