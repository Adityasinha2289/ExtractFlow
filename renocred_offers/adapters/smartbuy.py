"""HDFC SmartBuy terms adapter.

This surface is the GyFTR-powered voucher layer's legal page rather than an
offer grid, and it is the single richest *cap and exclusion* source researched:
it states per-card-family earn rates, monthly and daily caps, the cashback
settlement delay, and the Corporate/Commercial/Dealer/Distributor carve-out
that Section 27 item 7 names as the biggest eligibility risk in the whole
system.

Two integrity signals are detected and recorded rather than smoothed over:

* the page's own promotional banner still advertises a campaign that ended in
  2021, and
* it still states ``5X`` on Infinia, which the Master Plan documents as having
  been cut to ``3X`` effective 16 January 2026.

Both are surfaced as review flags on the affected records. Recording a stale
figure as VERIFIED is precisely the failure Section 27 item 1 warns about.
"""

import re
from typing import Any

from bs4 import BeautifulSoup

from renocred_offers import normalize as N
from renocred_offers.adapters.base import Adapter, describe
from renocred_offers.schema import evidence
from renocred_offers.taxonomy import CASHBACK, REDEEM_AUTOMATIC, REWARD_MULTIPLIER

#: Per-card-family caps stated in the "Daily and Monthly capping" table.
_CAP_ROW = re.compile(
    r"(Infinia|Diners\s*Black|Diners\s*Privilege|Regalia)\s+"
    r"([\d,]+)\s*(Reward\s*Points|Rs\.?)?\s+([\d,]+)\s*(Reward\s*Points|Rs\.?)?",
    re.I,
)

#: "upto 5X Reward points on HDFC Bank Infinia OR upto 3X Points on Diners Card"
_EARN_RULE = re.compile(
    r"up\s*to\s*(\d+(?:\.\d+)?)\s*([Xx%])\s*(?:Reward\s*)?[Pp]oints?\s*on\s*"
    r"(?:HDFC\s*Bank\s*)?([A-Za-z][\w\s]{2,28}?)(?=\s*(?:,|OR|or|\.|$))",
    re.I,
)

_CASHBACK_RULE = re.compile(
    r"([\d.]+)\s*%\s*platform\s*cashback\s*and\s*([\d.]+)\s*%\s*"
    r"(?:Bonanza\s*offer\s*)?cashback\s*on\s*(other\s*HDFC\s*Bank\s*Credit\s*cards)",
    re.I,
)

_EXCLUSION = re.compile(
    r"((?:Corporate|Commercial|Dealer|Distributor)[^.]{0,220}?"
    r"excluded from the promotion)",
    re.I,
)

_SETTLEMENT = re.compile(r"(?:within|posted within)\s*(\d+)\s*working\s*days", re.I)

_BANNER_CAMPAIGN = re.compile(
    r"(offers?\s*ends?\s*on\s*\d{1,2}\s*(?:st|nd|rd|th)?\s*[A-Za-z]+,?\s*\d{4})", re.I
)

#: Documented devaluation the plan cites (Business Standard, Jan 2026).
_DEVALUED = {"infinia": ("5X", "3X", "2026-01-16")}


class SmartBuyTermsAdapter(Adapter):
    """Extracts per-card-family SmartBuy offers plus their caps and exclusions."""

    extraction_method = "requests+html-text"
    grantor_scope = "ISSUER"
    tier = "S"
    source_type = "ISSUER_PORTAL"

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = N.clean_text(soup.get_text(" ", strip=True)) or ""
        if not text:
            return []

        # The same carve-out appears twice with different capitalisation; keep
        # one copy so the rule list is not padded with a near-identical clause.
        exclusions: list[str] = []
        seen_exclusions: set[str] = set()
        for match in _EXCLUSION.finditer(text):
            clause = N.clean_text(match.group(1))
            if clause and clause.lower() not in seen_exclusions:
                seen_exclusions.add(clause.lower())
                exclusions.append(clause)
        exclusions = exclusions[:1]
        settlement = _SETTLEMENT.search(text)
        caps = self._caps(text)
        stale_banner = _BANNER_CAMPAIGN.search(text)

        records: list[dict] = []
        for family, rate, unit, snippet in self._earn_rules(text):
            records.append(
                self._build(
                    family,
                    rate,
                    unit,
                    snippet,
                    caps,
                    exclusions,
                    settlement,
                    stale_banner,
                    text,
                )
            )
        return [r for r in records if r]

    # -- parsing helpers ----------------------------------------------------
    def _caps(self, text: str) -> dict[str, dict[str, Any]]:
        caps: dict[str, dict[str, Any]] = {}
        for match in _CAP_ROW.finditer(text):
            family = re.sub(r"\s+", " ", match.group(1)).strip().lower()
            try:
                monthly = float(match.group(2).replace(",", ""))
                daily = float(match.group(4).replace(",", ""))
            except ValueError:
                continue
            unit = (
                "REWARD_POINTS"
                if (match.group(3) or "").lower().startswith("reward")
                else "INR"
            )
            caps.setdefault(family, {"monthly": monthly, "daily": daily, "unit": unit})
        # Cashback caps are stated in prose, not in the table.
        for family, amount in (
            re.findall(
                r"Rs\.?\s*([\d,]+)\s*on\s*(Regalia\s*Credit\s*Card|"
                r"Diners\s*Privilege\s*Credit\s*Card|"
                r"other\s*Credit\s*/?\s*Debit\s*/?\s*Prepaid\s*cards)",
                text,
                re.I,
            )[:0]
            or []
        ):
            pass
        for match in re.finditer(
            r"Rs\.?\s*([\d,]+)\s*on\s*((?:Regalia|Diners\s*Privilege)[\w\s]*Card|"
            r"other\s*Credit\s*/?\s*Debit\s*/?\s*Prepaid\s*cards)",
            text,
            re.I,
        ):
            key = re.sub(r"\s+", " ", match.group(2)).strip().lower()
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            caps.setdefault(key, {"monthly": value, "daily": None, "unit": "INR"})
        return caps

    def _earn_rules(self, text: str):
        seen: set[str] = set()
        for match in _EARN_RULE.finditer(text):
            family = re.sub(r"\s+", " ", match.group(3)).strip()
            if not family or family.lower() in seen or len(family) > 30:
                continue
            seen.add(family.lower())
            start = max(0, match.start() - 120)
            yield family, float(match.group(1)), match.group(2).upper(), text[
                start : match.end() + 160
            ]

        cashback = _CASHBACK_RULE.search(text)
        if cashback and "other hdfc bank credit cards" not in seen:
            total = float(cashback.group(1)) + float(cashback.group(2))
            start = max(0, cashback.start() - 120)
            yield (
                "other HDFC Bank Credit cards",
                total,
                "%",
                text[start : cashback.end() + 160],
            )

    # -- record construction ------------------------------------------------
    def _build(
        self,
        family,
        rate,
        unit,
        snippet,
        caps,
        exclusions,
        settlement,
        stale_banner,
        full_text,
    ) -> dict | None:
        is_points = unit == "X"
        headline = (
            f"Up to {rate:g}X reward points on SmartBuy for HDFC Bank {family}"
            if is_points
            else f"{rate:g}% cashback on SmartBuy for {family}"
        )

        record = self.new_record(
            source_ref=f"smartbuy::{family.lower()}",
            title=headline,
            merchant_name="HDFC SmartBuy",
            category="ecommerce",
        )
        self.set_benefit(
            record,
            headline,
            hint=REWARD_MULTIPLIER if is_points else CASHBACK,
            snapshot=snippet,
        )
        if is_points:
            record["benefit"]["reward_multiplier"] = rate
        else:
            record["benefit"]["cashback"] = rate

        # -- caps ---------------------------------------------------------
        cap = None
        for key, value in caps.items():
            if key in family.lower() or family.lower() in key:
                cap = value
                break
        if cap is None and "other" in family.lower():
            cap = caps.get("other credit / debit / prepaid cards") or caps.get(
                "other credit/ debit / prepaid cards"
            )
        if cap:
            rules = [
                f"monthly cap: {cap['monthly']:,.0f} {cap['unit']}",
            ]
            if cap.get("daily"):
                rules.append(f"daily cap: {cap['daily']:,.0f} {cap['unit']}")
            if cap["unit"] == "INR":
                record["benefit"]["maximum_benefit"] = cap["monthly"]
            record["eligibility"]["frequency_limit"] = "per calendar month"
            record["eligibility"]["other_eligibility_rules"] = rules
            record["evidence_data"].append(
                evidence(
                    "benefit.maximum_benefit",
                    cap["monthly"],
                    f"capping table row for {family}",
                    self.url,
                )
            )

        # -- exclusions ---------------------------------------------------
        rules = list(record["eligibility"].get("other_eligibility_rules") or [])
        rules.extend(exclusions)
        if settlement:
            rules.append(
                f"benefit is credited within {settlement.group(1)} working days of "
                "the transaction month end (settlement delay, not an instant discount)"
            )
        record["eligibility"]["other_eligibility_rules"] = rules or None
        if exclusions:
            record["evidence_data"].append(
                evidence(
                    "eligibility.other_eligibility_rules",
                    exclusions,
                    exclusions[0],
                    self.url,
                    needs_review=True,
                    review_reason=(
                        "Corporate/Commercial/Dealer/Distributor exclusion applies; "
                        "the Card Master cannot currently express this variant "
                        "distinction (Master Plan Section 27 item 7)"
                    ),
                )
            )

        # -- card linking --------------------------------------------------
        self.set_cards(record, [family, f"HDFC {family}"], snapshot=family)

        # -- integrity flags ------------------------------------------------
        review: list[str] = []
        if stale_banner:
            review.append(
                f"source page still advertises a campaign that ended "
                f"({stale_banner.group(1)}) - page freshness is not trustworthy"
            )
        devalued = _DEVALUED.get(family.lower())
        if devalued and is_points and f"{rate:g}X".upper() == devalued[0]:
            review.append(
                f"source states {devalued[0]} but this programme was reported cut to "
                f"{devalued[1]} effective {devalued[2]}; treat as CONFLICTED pending "
                "re-verification against HDFC's own T&C (Master Plan Section 14)"
            )
            record.setdefault("quality", {})["conflict_status"] = "CONFLICTED"
            record["quality"]["conflicts"] = [
                {
                    "field": "benefit.reward_multiplier",
                    "values": [
                        {
                            "value": rate,
                            "source": self.name,
                            "tier": self.tier,
                            "url": self.url,
                        },
                        {
                            "value": 3.0,
                            "source": "financial press devaluation report",
                            "tier": "B",
                            "url": "https://www.business-standard.com/"
                            "finance/personal-finance/hdfc-infinia-credit-"
                            "card-trims-voucher-returns-as-5x-points-"
                            "drop-to-3x-126011500373_1.html",
                        },
                    ],
                }
            ]
        if review:
            record.setdefault("quality", {})["review_reasons"] = review
            record["evidence_data"].append(
                evidence(
                    "quality.review_reasons",
                    review,
                    snippet,
                    self.url,
                    needs_review=True,
                    review_reason=review[0],
                )
            )

        self.set_redemption(
            record,
            redemption_url=self.url,
            redemption_type=REDEEM_AUTOMATIC,
            activation_required=False,
        )
        record["eligibility"]["transaction_type"] = "ONLINE"
        record["eligibility"]["online_offline"] = "ONLINE"
        self.set_scope(record)
        record["identity"]["description"] = describe(record)
        return record
