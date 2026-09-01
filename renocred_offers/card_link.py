"""Card <-> Offer relationship layer (Master Plan Section 9).

This is the *only* seam between the Offer Master and the existing Card Master.
Per Section 27 item 10, the two datasets stay structurally separate systems;
nothing here writes back into card records.

The Card Master is loaded read-only and indexed three ways -- by
``official_url`` slug, by normalised card name, and by issuer+family tokens --
because issuer offer feeds identify cards by their own portal slugs, not by
RenoCred card ids.

Where a slug cannot be resolved, it is preserved verbatim in
``unmatched_card_slugs`` rather than dropped. Silently discarding an
unresolvable eligibility token would make an offer look *more* broadly
applicable than the source actually says, which is precisely the
over-inclusion failure Section 27 item 7 warns about.
"""

import json
import os
import re
from collections import defaultdict
from typing import Any, Iterable

from extractflow.utils.logger import get_logger

log = get_logger("renocred.card_link")

#: Tokens that carry no discriminating power in Indian card names.
_STOPWORDS = {
    "credit",
    "card",
    "cards",
    "the",
    "a",
    "an",
    "in",
    "association",
    "with",
    "apply",
    "for",
    "online",
    "bank",
    "co",
    "branded",
}


def _tokens(text: str) -> frozenset[str]:
    text = str(text or "").lower().replace("+", " plus ")
    words = re.split(r"[^a-z0-9]+", text)
    return frozenset(w for w in words if w and w not in _STOPWORDS)


class CardMaster:
    """Read-only index over ``renocred_card_master.json``."""

    def __init__(self, path: str):
        self.path = path
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.metadata: dict[str, Any] = payload.get("metadata", {})
        self.cards: list[dict[str, Any]] = payload.get("data", [])

        self._by_id: dict[str, dict] = {}
        self._by_slug: dict[str, set[str]] = defaultdict(set)
        self._by_tokens: dict[frozenset[str], set[str]] = defaultdict(set)
        self._by_issuer: dict[str, set[str]] = defaultdict(set)

        for card in self.cards:
            identity = card.get("identity") or {}
            card_id = identity.get("card_id")
            if not card_id:
                continue
            self._by_id[card_id] = card
            issuer = (identity.get("issuer") or "").strip()
            if issuer:
                self._by_issuer[issuer.lower()].add(card_id)

            url = identity.get("official_url") or ""
            if url:
                slug = os.path.basename(url.rstrip("/")).rsplit(".", 1)[0]
                if slug:
                    self._by_slug[slug.lower()].add(card_id)
                    self._by_tokens[_tokens(slug)].add(card_id)
            name = identity.get("card_name") or ""
            if name:
                self._by_tokens[_tokens(name)].add(card_id)

        log.info(
            "card master: %d cards, %d issuers, %d slug keys",
            len(self._by_id),
            len(self._by_issuer),
            len(self._by_slug),
        )

    # -- lookup -------------------------------------------------------------
    def get(self, card_id: str) -> dict | None:
        return self._by_id.get(card_id)

    def issuer_card_ids(self, issuer: str) -> list[str]:
        return sorted(self._by_issuer.get((issuer or "").lower(), ()))

    def resolve_slug(self, slug: str, issuer: str | None = None) -> list[str]:
        """Resolve one source card slug to zero or more RenoCred card ids.

        Exact slug match first, then an exact token-set match, then a
        containment match restricted to the issuer. Ambiguous containment
        matches (more than one candidate) are refused: a wrong card link is
        worse than an honest gap.
        """
        if not slug:
            return []
        key = slug.strip().lower()
        if key in self._by_slug:
            return sorted(self._by_slug[key])

        wanted = _tokens(slug)
        if not wanted:
            return []
        if wanted in self._by_tokens:
            return sorted(self._by_tokens[wanted])

        pool = self._by_issuer.get((issuer or "").lower()) if issuer else None
        candidates: set[str] = set()
        for tokens, ids in self._by_tokens.items():
            if not tokens or not wanted <= tokens:
                continue
            hits = ids & pool if pool else ids
            candidates |= hits
        return sorted(candidates) if len(candidates) == 1 else []

    # -- relationship resolution -------------------------------------------
    def link(self, slugs: Iterable[str], issuer: str | None = None) -> dict[str, Any]:
        """Resolve a source's card-slug list into the relationship structure."""
        matched: set[str] = set()
        unmatched: list[str] = []
        mapping: dict[str, list[str]] = {}

        for slug in slugs:
            slug = (slug or "").strip()
            if not slug:
                continue
            ids = self.resolve_slug(slug, issuer)
            if ids:
                matched.update(ids)
                mapping[slug] = ids
            elif slug not in unmatched:
                unmatched.append(slug)

        total = len(mapping) + len(unmatched)
        return {
            "card_ids": sorted(matched),
            "unmatched_card_slugs": unmatched,
            "slug_to_card_ids": mapping,
            "match_rate": round(len(mapping) / total, 4) if total else None,
        }

    def families(self, card_ids: Iterable[str]) -> list[str]:
        """Distinct product families behind a set of card ids."""
        out: set[str] = set()
        for card_id in card_ids:
            card = self._by_id.get(card_id)
            if not card:
                continue
            family = card.get("product_family_id") or (card.get("identity") or {}).get(
                "product_family"
            )
            if family:
                out.add(str(family))
        return sorted(out)

    def consumer_variant_known(self, card_id: str) -> bool | None:
        """Whether the Card Master can tell consumer from corporate/commercial.

        Returns ``None`` when it cannot -- the Phase 3C.4 gap flagged in
        Section 27 item 7. The Eligibility Engine turns that ``None`` into
        INSUFFICIENT_DATA rather than assuming a consumer card.
        """
        card = self._by_id.get(card_id)
        if not card:
            return None
        identity = card.get("identity") or {}
        declared = identity.get("personal_business")
        return (
            None
            if declared is None
            else str(declared).strip().lower() in {"personal", "consumer", "individual"}
        )
