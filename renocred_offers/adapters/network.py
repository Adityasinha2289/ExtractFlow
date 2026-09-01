"""Network-program adapters (Visa, Mastercard).

Network offers are broad and India-wide but shallow on card specificity, so
these adapters deliberately populate ``network`` and leave ``card_ids`` null
rather than fanning the offer out across every card of that network in the Card
Master. Section 6 is explicit that network sources are authoritative only for
what they actually cover.
"""

import re
from typing import Any

from bs4 import BeautifulSoup

from renocred_offers import normalize as N
from renocred_offers.adapters.base import Adapter, describe
from renocred_offers.schema import evidence
from renocred_offers.taxonomy import REDEEM_ACTIVATION


class VisaWelcomeAdapter(Adapter):
    """Visa India's welcome-offer campaign page - one fixed-window campaign."""

    extraction_method = "requests+html-text"
    grantor_scope = "NETWORK"
    tier = "S"
    source_type = "NETWORK_PROGRAM"

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = N.clean_text(soup.get_text(" ", strip=True)) or ""
        if not text:
            return []

        title = N.clean_text(
            (
                soup.find("title").get_text()
                if soup.find("title")
                else "Visa Welcome Offer"
            )
        )
        record = self.new_record(
            source_ref="visa-welcome-offer",
            title=title or "Visa Welcome Offer",
            merchant_name=None,
            category=None,
        )
        record["merchant"]["merchant_name"] = None

        self.set_benefit(record, text[:600], snapshot=text[:600])
        valid_from, valid_until = N.parse_date_range(text)
        self.set_validity(record, valid_from, valid_until, snapshot=text[:400])

        # New-cardholder gating: stated outright where the copy says so,
        # otherwise inferred from the campaign name. A "welcome offer" is by
        # construction for new cardholders, but the inference is recorded as an
        # inference and flagged for review rather than presented as quoted fact.
        haystack = f"{title} {text}"
        stated = re.search(
            r"new\s*(?:card\s*holder|cardholder|customer|to\s*visa)", haystack, re.I
        )
        inferred = re.search(r"\bwelcome\s*offer\b", haystack, re.I)
        if stated or inferred:
            record["eligibility"]["new_customer_required"] = True
            record["evidence_data"].append(
                evidence(
                    "eligibility.new_customer_required",
                    True,
                    (
                        stated.group(0)
                        if stated
                        else (inferred.group(0) if inferred else "")
                    ),
                    self.url,
                    needs_review=not stated,
                    review_reason=(
                        None
                        if stated
                        else "inferred from the campaign being named a "
                        "welcome offer; the page "
                        "text does not state the new-cardholder condition verbatim"
                    ),
                )
            )

        self.set_redemption(
            record,
            redemption_url=self.url,
            redemption_type=REDEEM_ACTIVATION,
            activation_required=True,
        )
        record["eligibility"]["other_eligibility_rules"] = [
            "network-scope campaign; card-family eligibility is stated on the "
            "campaign page and is not enumerable from this surface",
        ]
        record["evidence_data"].append(
            evidence(
                "eligibility.card_ids",
                None,
                "network-level campaign; card list not enumerated by the source",
                self.url,
                needs_review=True,
                review_reason="card-family scope needs confirmation before VERIFIED",
            )
        )
        self.set_scope(record)
        record["identity"]["description"] = describe(record)
        return [record]


class PricelessAdapter(Adapter):
    """Mastercard Priceless Specials India.

    The card-family filter sits behind a card-binding dialog. Only the public
    teaser layer is read; no authentication is attempted, per Section 5's
    explicit constraint.
    """

    extraction_method = "playwright+html-grid"
    grantor_scope = "NETWORK"
    tier = "S"
    source_type = "NETWORK_PROGRAM"

    CANDIDATE_ROOTS = [
        "div.offer-item",
        "div.offer-card",
        "li.offer-item",
        "div.merchant-item",
        "a.offer-link",
        "div.card-item",
    ]

    def parse(self, payload: Any) -> list[dict]:
        html = payload if isinstance(payload, str) else getattr(payload, "text", "")
        soup = BeautifulSoup(html or "", "html.parser")
        nodes = []
        for selector in self.CANDIDATE_ROOTS:
            nodes = soup.select(selector)
            if nodes:
                break
        records = []
        for node in nodes:
            title = N.clean_text(node.get_text(" ", strip=True))
            if not title or len(title) < 8:
                continue
            record = self.new_record(
                source_ref=f"priceless::{title[:60]}",
                title=title[:160],
                merchant_name=None,
                category=None,
            )
            self.set_benefit(record, title, snapshot=title)
            self.set_redemption(record, redemption_url=self.url)
            self.set_scope(record)
            record["identity"]["description"] = describe(record)
            records.append(record)
        return records
