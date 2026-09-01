"""Deduplication and conflict resolution (Master Plan Sections 17 and 13).

Two distinct duplicate problems need different logic:

* *Same offer, multiple source mentions* -- merge into one ``offer_id``, keeping
  every source in the evidence list and letting the higher-authority source win
  field precedence.
* *Genuinely similar-but-distinct offers* -- an issuer running a mass-market
  cashback and a premium-card-only accelerator at the same merchant
  concurrently. These must NOT merge, which is why ``card_ids`` is part of the
  match key. Collapsing them would silently tell a premium cardholder they only
  get the mass-market rate.

Conflict resolution follows Section 13's precedence: specificity and funding
entity first, recency only as a tiebreaker *within* a tier. True numeric
conflicts are never averaged and never resolved by picking the nicer number --
they go to CONFLICTED and out of ranking until a human rules.
"""

from typing import Any

from extractflow.utils.helpers import stable_hash
from extractflow.utils.logger import get_logger
from renocred_offers.taxonomy import TIER_RANK

log = get_logger("renocred.reconcile")

#: Fields on which a disagreement is *material* -- i.e. changes what a user
#: would actually receive. Cosmetic differences (title wording) are not.
MATERIAL_FIELDS = [
    "benefit.benefit_type",
    "benefit.discount_percentage",
    "benefit.flat_discount",
    "benefit.cashback",
    "benefit.reward_multiplier",
    "benefit.maximum_benefit",
    "eligibility.minimum_spend",
    "validity.valid_until",
    "redemption.coupon_code",
]


def _get(record: dict, path: str) -> Any:
    node: Any = record
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set(record: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    node = record
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def match_key(record: dict) -> str:
    """Section 17's card-aware dedup key.

    ``card_ids`` is included deliberately: without it, a premium-card-only
    accelerator collapses into the issuer's mass-market offer at the same
    merchant, and the premium cardholder is told the wrong number. The promo
    code is included for the same reason -- two concurrent coupons at one
    merchant are two offers, not one offer seen twice.
    """
    identity = record.get("identity") or {}
    merchant = record.get("merchant") or {}
    benefit = record.get("benefit") or {}
    elig = record.get("eligibility") or {}
    cards = elig.get("card_ids") or elig.get("unmatched_card_slugs") or []
    return stable_hash(
        merchant.get("merchant_id"),
        identity.get("issuer") or identity.get("network"),
        benefit.get("benefit_type"),
        (record.get("validity") or {}).get("valid_until"),
        (record.get("redemption") or {}).get("coupon_code"),
        ",".join(sorted(str(c) for c in cards)),
        length=16,
    )


def exact_key(record: dict) -> str:
    """Within-source identity: same title and same benefit numbers."""
    benefit = record.get("benefit") or {}
    return stable_hash(
        record.get("source_url"),
        (record.get("identity") or {}).get("title"),
        benefit.get("discount_percentage"),
        benefit.get("flat_discount"),
        benefit.get("cashback"),
        benefit.get("maximum_benefit"),
        (record.get("redemption") or {}).get("coupon_code"),
        length=16,
    )


def _rank(record: dict) -> tuple[int, str]:
    return (
        TIER_RANK.get(record.get("source_tier") or "", 0),
        record.get("last_verified_at") or "",
    )


def is_refinement(low: Any, high: Any) -> bool:
    """True when ``high`` is strictly more specific than ``low``, not conflicting.

    Section 13 item 3: "10%" and "10% up to Rs 1,000" are not in conflict; the
    second is simply more complete, so auto-merge to the more specific record.
    """
    return low is None and high is not None


def detect_conflicts(primary: dict, other: dict) -> list[dict]:
    """Material disagreements between two records for the same offer."""
    conflicts = []
    for path in MATERIAL_FIELDS:
        a, b = _get(primary, path), _get(other, path)
        if a is None or b is None:
            continue
        if a == b:
            continue
        conflicts.append(
            {
                "field": path,
                "values": [
                    {
                        "value": a,
                        "source": primary.get("source_name"),
                        "tier": primary.get("source_tier"),
                        "url": primary.get("source_url"),
                    },
                    {
                        "value": b,
                        "source": other.get("source_name"),
                        "tier": other.get("source_tier"),
                        "url": other.get("source_url"),
                    },
                ],
            }
        )
    return conflicts


def merge_pair(primary: dict, other: dict) -> dict:
    """Fold ``other`` into ``primary``, applying Section 13's precedence."""
    winner, loser = (
        (primary, other) if _rank(primary) >= _rank(other) else (other, primary)
    )
    merged = dict(winner)

    conflicts = detect_conflicts(winner, loser)
    same_tier = winner.get("source_tier") == loser.get("source_tier")

    for path in MATERIAL_FIELDS:
        win_value, lose_value = _get(winner, path), _get(loser, path)
        # Auto-merge strict refinements: a null on the winner filled by the
        # loser is added information, not a disagreement.
        if is_refinement(win_value, lose_value):
            _set(merged, path, lose_value)

    # Union evidence so the audit trail keeps every source that saw this offer.
    evidence = list(winner.get("evidence_data") or [])
    seen = {(e.get("field"), e.get("source_url")) for e in evidence}
    for item in loser.get("evidence_data") or []:
        if (item.get("field"), item.get("source_url")) not in seen:
            evidence.append(item)
    merged["evidence_data"] = evidence

    contributing = list(
        dict.fromkeys(
            (winner.get("contributing_sources") or [winner.get("source_name")])
            + (loser.get("contributing_sources") or [loser.get("source_name")])
        )
    )
    merged["contributing_sources"] = [s for s in contributing if s]

    quality = dict(merged.get("quality") or {})
    if conflicts:
        # A fresher low-tier source never outranks a within-window high-tier
        # one, so only same-tier disagreement is a true conflict.
        if same_tier:
            quality["conflict_status"] = "CONFLICTED"
            quality["conflicts"] = conflicts
            log.warning(
                "material conflict on %s between %s and %s",
                [c["field"] for c in conflicts],
                winner.get("source_name"),
                loser.get("source_name"),
            )
        else:
            quality["conflict_status"] = "RESOLVED_BY_TIER"
            quality["conflicts"] = conflicts
    merged["quality"] = quality
    return merged


def reconcile(records: list[dict]) -> tuple[list[dict], dict[str, Any]]:
    """Deduplicate and reconcile a batch. Returns ``(records, report)``.

    Two passes, because the two duplicate problems in Section 17 are different:

    1. *Within one source*, only byte-identical rows are duplicates. A source
       that lists two offers is stating there are two offers -- collapsing them
       would delete real inventory and manufacture a phantom "conflict" between
       an offer and its sibling.
    2. *Across sources*, the card-aware match key applies, and disagreement
       between two independent sources is a genuine conflict worth surfacing.
    """
    # -- pass 1: exact duplicates within a single source -------------------
    exact: dict[str, dict] = {}
    exact_dropped = 0
    for record in records:
        key = exact_key(record)
        if key in exact:
            exact_dropped += 1
            continue
        exact[key] = record
    deduped = list(exact.values())

    # -- pass 2: cross-source reconciliation --------------------------------
    groups: dict[str, list[dict]] = {}
    for record in deduped:
        groups.setdefault(match_key(record), []).append(record)

    out: list[dict] = []
    merged_count = 0
    conflicted = 0

    for group in groups.values():
        if len(group) == 1:
            out.append(group[0])
            continue

        # Only records from *different* sources are candidate duplicates.
        by_source: dict[str, list[dict]] = {}
        for record in group:
            by_source.setdefault(record.get("source_url") or "", []).append(record)

        if len(by_source) == 1:
            out.extend(group)
            continue

        representatives = [
            sorted(v, key=_rank, reverse=True)[0] for v in by_source.values()
        ]
        leftovers = [
            r
            for v in by_source.values()
            for r in sorted(v, key=_rank, reverse=True)[1:]
        ]

        representatives.sort(key=_rank, reverse=True)
        current = representatives[0]
        for other in representatives[1:]:
            current = merge_pair(current, other)
            merged_count += 1
        if (current.get("quality") or {}).get("conflict_status") == "CONFLICTED":
            conflicted += 1
        out.append(current)
        out.extend(leftovers)

    report = {
        "input_count": len(records),
        "output_count": len(out),
        "exact_duplicate_count": exact_dropped,
        "duplicate_count": merged_count + exact_dropped,
        "cross_source_merged": merged_count,
        "conflicted_count": conflicted,
        "group_count": len(groups),
    }
    log.info(
        "reconcile: %d in -> %d out (%d merged, %d conflicted)",
        report["input_count"],
        report["output_count"],
        report["duplicate_count"],
        report["conflicted_count"],
    )
    return out, report
