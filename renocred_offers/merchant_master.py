"""Merchant Master (Master Plan Section 10).

Merchant identity is itself ambiguous in raw source data: "Amazon", "Amazon
Pay" and "Amazon Fresh" are functionally different eligibility surfaces even
though a naive text match collapses them. This module keeps a canonical
``merchant_id`` per merchant with an alias list for fuzzy matching, and -- per
Section 10 -- records which issuers each merchant is known to partner with,
because a *gap* in that matrix is itself a prioritised research signal rather
than evidence that no offer exists.
"""

import re
from collections import defaultdict
from typing import Any, Iterable

from extractflow.utils.helpers import slugify
from renocred_offers.normalize import clean_text, titleise
from renocred_offers.taxonomy import normalise_category

#: Merchants that must never be alias-merged despite sharing a name stem,
#: because they are genuinely distinct eligibility surfaces.
DISTINCT_SURFACES = {
    "amazon",
    "amazon pay",
    "amazon fresh",
    "amazon business",
    "flipkart",
    "flipkart health",
    "myntra",
    "ajio",
    "tata cliq",
    "tata cliq luxury",
    "tata neu",
    "reliance digital",
    "reliance smart",
    "reliance trends",
    "jiomart",
}

#: Noise words stripped before alias comparison.
_NOISE = re.compile(
    r"\b(?:pvt|private|ltd|limited|india|indian|online|store|stores|shop|shopping"
    r"|gift|vouchers?|e-?voucher|gift\s*cards?|offers?|deals?|com|in)\b",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9]+")


def alias_key(name: Any) -> str | None:
    """Comparison key for merchant names.

    Case, punctuation, and boilerplate ("Pvt Ltd", "Gift Vouchers") are removed;
    word order is preserved because "Reliance Smart" and "Smart Reliance" are
    not automatically the same merchant.
    """
    text = clean_text(name)
    if not text:
        return None
    lowered = text.lower()
    if lowered in DISTINCT_SURFACES:
        return _PUNCT.sub(" ", lowered).strip()
    stripped = _NOISE.sub(" ", lowered)
    key = _PUNCT.sub(" ", stripped).strip()
    return key or _PUNCT.sub(" ", lowered).strip() or None


class MerchantMaster:
    """In-memory Merchant Master built up as offers are ingested."""

    def __init__(self) -> None:
        self._by_key: dict[str, str] = {}
        self._records: dict[str, dict[str, Any]] = {}
        self._partnerships: dict[str, set[str]] = defaultdict(set)

    # -- resolution ---------------------------------------------------------
    def resolve(
        self,
        name: Any,
        category: Any = None,
        issuer: str | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a raw merchant name to a canonical record, creating it if new."""
        key = alias_key(name)
        display = titleise(name)
        if not key or not display:
            return None

        merchant_id = self._by_key.get(key)
        if merchant_id is None:
            merchant_id = f"m_{slugify(key)}"
            # Guard against two different keys slugging to the same id.
            suffix = 2
            while (
                merchant_id in self._records
                and self._records[merchant_id]["alias_key"] != key
            ):
                merchant_id = f"m_{slugify(key)}_{suffix}"
                suffix += 1
            self._by_key[key] = merchant_id
            self._records[merchant_id] = {
                "merchant_id": merchant_id,
                "merchant_name": display,
                "alias_key": key,
                "merchant_category": normalise_category(category),
                "merchant_subcategory": None,
                "aliases": [],
                "known_bank_partnerships": [],
                "source_urls": [],
            }

        record = self._records[merchant_id]
        raw = clean_text(name)
        if raw and raw not in record["aliases"]:
            record["aliases"].append(raw)
        if record["merchant_category"] is None and category is not None:
            record["merchant_category"] = normalise_category(category)
        if issuer:
            self._partnerships[merchant_id].add(issuer)
        if source_url and source_url not in record["source_urls"]:
            record["source_urls"].append(source_url)
        return record

    # -- export -------------------------------------------------------------
    def records(self) -> list[dict[str, Any]]:
        out = []
        for merchant_id, record in sorted(self._records.items()):
            item = dict(record)
            item["known_bank_partnerships"] = sorted(
                self._partnerships.get(merchant_id, ())
            )
            item["aliases"] = sorted(set(item["aliases"]))
            out.append(item)
        return out

    def partnership_gaps(
        self, issuers: Iterable[str], min_partners: int = 2
    ) -> list[dict]:
        """Merchants partnered with several issuers but not all of them.

        Section 10's discovery signal: an absent issuer for a merchant that
        several peers already partner with is a research gap to prioritise, not
        proof that no such offer exists.
        """
        all_issuers = set(issuers)
        gaps = []
        for merchant_id, partners in self._partnerships.items():
            if len(partners) >= min_partners:
                missing = sorted(all_issuers - partners)
                if missing:
                    gaps.append(
                        {
                            "merchant_id": merchant_id,
                            "merchant_name": self._records[merchant_id][
                                "merchant_name"
                            ],
                            "known_partners": sorted(partners),
                            "unresearched_issuers": missing,
                        }
                    )
        return sorted(gaps, key=lambda g: -len(g["known_partners"]))

    def __len__(self) -> int:
        return len(self._records)
