"""Aggregator adapters - DISCOVERY grade only.

Section 6 is unambiguous: B-tier aggregators are usable for seeding
NEEDS_REVIEW and never for VERIFIED; C-tier coupon sites contribute zero
confidence on their own and exist only to trigger a verification job against
the S-tier source. Nothing in this module can promote an offer past discovery -
the tier lives on the source config and the verification engine enforces the
rule centrally, so an adapter cannot grant its own source trust it has not
earned.
"""

import re
from typing import Any

from bs4 import BeautifulSoup

from extractflow.extractors.tables import extract_tables
from renocred_offers import normalize as N
from renocred_offers.adapters.base import Adapter, describe
from renocred_offers.schema import evidence

#: Header labels seen across BankBazaar / Paisabazaar offer tables.
_MERCHANT_HEADERS = re.compile(
    r"partner|merchant|brand|platform|website|offer\s*on", re.I
)
_OFFER_HEADERS = re.compile(r"offer|discount|benefit|deal|deals|deal\s*detail", re.I)
_VALIDITY_HEADERS = re.compile(r"valid|expiry|till|until|period", re.I)
_CODE_HEADERS = re.compile(r"code|coupon|promo", re.I)

_DISCOVERY_NOTE = (
    "discovery-grade source; terms must be re-verified against the issuer or "
    "network before this offer can be surfaced as verified (Master Plan Section 6)"
)


def _pick(row: dict, pattern: re.Pattern) -> str | None:
    for header, value in row.items():
        if header and pattern.search(str(header)):
            cleaned = N.clean_text(value)
            if cleaned:
                return cleaned
    return None


class AggregatorTableAdapter(Adapter):
    """Parses offer tables on BankBazaar / Paisabazaar-style pages."""

    extraction_method = "requests+html-tables"
    grantor_scope = "NETWORK"
    tier = "B"
    source_type = "AGGREGATOR"

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        records: list[dict] = []
        for table in extract_tables(html):
            headers = " ".join(str(h) for h in table.get("headers") or [])
            if not _OFFER_HEADERS.search(headers):
                continue
            for row in table.get("records") or []:
                record = self._one(row, table)
                if record:
                    records.append(record)
        return records

    def _one(self, row: dict, table: dict) -> dict | None:
        merchant = _pick(row, _MERCHANT_HEADERS)
        offer_text = _pick(row, _OFFER_HEADERS)
        if not merchant or not offer_text:
            return None

        record = self.new_record(
            source_ref=f"{self.key}::{merchant}::{offer_text[:40]}",
            title=f"{offer_text[:120]}",
            merchant_name=merchant,
            category=table.get("caption"),
        )
        self.set_benefit(record, offer_text, snapshot=offer_text)

        minimum = N.parse_minimum_spend(offer_text)
        if minimum is not None:
            record["eligibility"]["minimum_spend"] = minimum

        validity = _pick(row, _VALIDITY_HEADERS)
        if validity:
            valid_from, valid_until = N.parse_date_range(validity)
            # A bare date in a "valid till" column is an end date, not a start.
            if valid_from and not valid_until:
                valid_from, valid_until = None, valid_from
            self.set_validity(record, valid_from, valid_until, snapshot=validity)

        self.set_redemption(
            record, coupon=_pick(row, _CODE_HEADERS), redemption_url=self.url
        )
        self.set_transaction(record, offer_text)

        record["eligibility"]["other_eligibility_rules"] = [_DISCOVERY_NOTE]
        record["evidence_data"].append(
            evidence(
                "source_tier",
                self.tier,
                _DISCOVERY_NOTE,
                self.url,
                needs_review=True,
                review_reason=_DISCOVERY_NOTE,
            )
        )
        self.set_scope(record)
        record["identity"]["description"] = describe(record)
        return record


class GrabOnAdapter(Adapter):
    """Parses GrabOn coupon tiles. C-tier: noisy, duplicate-heavy, signal only."""

    extraction_method = "requests+html-grid"
    grantor_scope = "MERCHANT"
    tier = "C"
    source_type = "AGGREGATOR"

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        soup = BeautifulSoup(html or "", "html.parser")
        records: list[dict] = []
        seen: set[str] = set()

        for node in soup.select("div.gc-box, div.go-cpn-show"):
            title_node = node.select_one("p.title") or node.find(["h3", "h4", "p"])
            title = (
                N.clean_text(title_node.get_text(" ", strip=True))
                if title_node
                else None
            )
            if not title or len(title) < 8 or title.lower() in seen:
                continue
            seen.add(title.lower())

            code_node = node.select_one("span.coupon-code, .gc-code, .go-cpy")
            code = (
                N.clean_text(code_node.get_text(" ", strip=True)) if code_node else None
            )
            if code and (len(code) > 24 or " " in code):
                code = None  # tile text, not a real code

            record = self.new_record(
                source_ref=f"grabon::{title[:60]}",
                title=title[:140],
                merchant_name=self._merchant(title),
                category=None,
            )
            self.set_benefit(record, title, snapshot=title)
            self.set_redemption(record, coupon=code, redemption_url=self.url)
            record["eligibility"]["other_eligibility_rules"] = [_DISCOVERY_NOTE]
            record["evidence_data"].append(
                evidence(
                    "source_tier",
                    self.tier,
                    _DISCOVERY_NOTE,
                    self.url,
                    needs_review=True,
                    review_reason=_DISCOVERY_NOTE,
                )
            )
            self.set_scope(record)
            record["identity"]["description"] = describe(record)
            records.append(record)
        return records

    @staticmethod
    def _merchant(title: str) -> str | None:
        """Best-effort merchant from coupon copy ("... At Myntra", "On Ajio")."""
        match = re.search(r"\b(?:at|on|from)\s+([A-Z][\w&.\- ]{2,28})\s*$", title)
        if match:
            return match.group(1).strip()
        match = re.match(r"^([A-Z][\w&.\-]{2,20})\b", title)
        return match.group(1) if match else None
