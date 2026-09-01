# RenoCred Offer Intelligence Pipeline

An ExtractFlow application that builds `renocred_offer_master.json` — a
verification-first dataset of live Indian credit-card offers — implementing
`RenoCred_Offer_Intelligence_Master_Plan.md`.

The Offer Master is deliberately a **separate system** from the Card Master.
They are joined only through the explicit relationship layer in
`renocred_offers/card_link.py`, per Master Plan Section 27 item 10. Nothing in
this pipeline writes back into card records.

## Running it

```bash
pip install -r requirements.txt
python -m playwright install chromium        # only needed for dynamic sources

python -m renocred_offers.build \
    --card-master ../renocred_card_master.json \
    --sources     configs/renocred/sources.yaml \
    --out         output \
    --cache       output/snapshots \
    --max-pdfs    200
```

| Flag | Purpose |
|---|---|
| `--cache DIR` | Store raw source snapshots. These are the `evidence` corpus Section 14 needs for silent-change diffing, and they make re-runs offline. |
| `--no-pdfs` | Skip linked-eligibility-PDF enrichment (much faster; lower eligibility fidelity). |
| `--max-pdfs N` | Cap PDF fetches per run. |

### Outputs

| File | Contents |
|---|---|
| `renocred_offer_master.json` | The Offer Master — `{metadata, data}`, same envelope as the Card Master. |
| `renocred_merchant_master.json` | Merchant Master (Section 10) plus `partnership_gaps` research signals. |
| `extraction_report.json` | Per-source status, reconciliation stats, validation errors, PDF enrichment stats. |
| `renocred_offers.csv` | Flat projection — the input to the human-review queue. |
| `renocred_offer_ledger.html` | Standalone browsable page (see below). |

## Pipeline stages

Section 15's ingestion architecture, one stage per pipeline step:

```
extract → enrich_linked_pdfs → reconcile → verify_and_score → validate
```

1. **extract** — each registered source is fetched (robots-aware, rate-limited)
   and handed to its adapter. Static retrieval is always tried first; a browser
   is used only when a page fetches 200 OK and still yields zero offers, which
   is the actual signature of client-side rendering.
2. **enrich_linked_pdfs** — linked Outlet List and T&C PDFs are fetched and
   parsed, and their contents folded back into the offer's eligibility block.
3. **reconcile** — two-pass deduplication and Section 13 conflict resolution.
4. **verify_and_score** — lifecycle, verification state, confidence.
5. **validate** — the Phase 3C.2 acceptance gate.

## Module map

| Module | Master Plan section | Responsibility |
|---|---|---|
| `normalize.py` | 8 | Money, percentage, cap, floor and date parsing. Returns `None` rather than guessing. |
| `taxonomy.py` | 7 | The two-axis benefit model, source tiers, category mapping. |
| `schema.py` | 8 | Offer Master record shape, evidence items, completeness fields, ingestion schema. |
| `merchant_master.py` | 10 | Canonical merchants, alias matching, partnership gaps. |
| `card_link.py` | 9 | Read-only Card Master index; slug → `card_id` resolution. |
| `eligibility.py` | 11 | Ternary eligibility engine. |
| `verification.py` | 12, 14, 18 | State machine, freshness windows, confidence scoring. |
| `reconcile.py` | 13, 17 | Deduplication and conflict resolution. |
| `enrich_pdf.py` | 5, 21 | Linked eligibility PDF ingestion. |
| `registry.py` / `build.py` | 15 | Source registry and orchestration. |

## The rules that are enforced in code, not just documented

These are the plan's user-protecting invariants. Each is asserted by a test in
`tests/test_renocred_offers.py`.

**A B/C/D-tier source can never certify an offer.** Aggregators and forums are
discovery-grade. They can move an offer to `NEEDS_REVIEW` — i.e. queue it for
crawling — and nothing more. The tier lives on the source config and the check
lives centrally in `verification.resolve_state`, so no adapter can grant its
own source trust it has not earned.

**A true numeric conflict is never averaged.** When two same-tier sources
disagree materially, the offer goes to `CONFLICTED` and out of ranking until a
human rules. Never the mean, never the higher number, never the lower one — both
are equally likely to be the stale one. A *cross-tier* disagreement is not a
conflict: the higher-authority source simply wins, and a fresher low-tier source
never overrides a within-window high-tier one.

**A strict refinement is not a conflict.** "10%" and "10% up to ₹1,000" agree;
the second is merely more complete, so it auto-merges to the more specific value.

**A premium-card accelerator never merges into a mass-market offer.** `card_ids`
and the promo code are part of the dedup key. Without them, an Infinia-only
accelerator would silently collapse into the issuer's general offer at the same
merchant and premium cardholders would be shown the wrong number.

**Two rows from one source are two offers.** Only *byte-identical* rows are
deduplicated within a source; the card-aware match key applies across sources.
Collapsing sibling rows would delete real inventory and manufacture phantom
conflicts.

**Uncertainty excludes, it never defaults to eligible.** The eligibility engine
is ternary. `INSUFFICIENT_DATA` — an unresolved card slug, an unknown student
status, a location restriction against an unknown city, a Corporate-variant
exclusion the Card Master cannot express — keeps the offer out of the top
recommendation. It may be surfaced only with a "confirm with issuer" caveat.

**A ceiling is never scored as a rate.** "5% Instant Discount" is a rate the
user receives; "Up to 72% Off" is a bound most transactions never reach.
`benefit.rate_type` records which one the source stated — reusing the Card
Master's own `EXACT` / `UP_TO` vocabulary — and the scorer reports an `UP_TO`
offer as an upper bound with no point estimate, ranked below every offer with a
definite rate. Without this, a wallet holding an ordinary 5% offer and a
headline "up to 72%" offer is told to expect ₹28,800 on a ₹40,000 spend.

**A cap is never invented.** If the source does not state a maximum,
`maximum_benefit` stays `null`. Likewise `validity` on a vendor row that states
no expiry. Null is honest; a fabricated zero or an assumed cap costs the user
money at the till.

**Descriptions are RenoCred-authored.** `identity.description` is composed from
the structured fields we extracted, not copied from bank or merchant prose
(Sections 8 and 20). The raw copy stays in `benefit_text` and in the evidence
trail, where it belongs.

## Adding a source

1. Add an entry to `configs/renocred/sources.yaml` with its `tier`, `role`
   (`TRUTH` or `DISCOVERY`), `source_type` and `adapter`.
2. If an existing adapter fits, you are done — the GyFTR adapter, for instance,
   covers every issuer on that vendor automatically.
3. Otherwise subclass `renocred_offers.adapters.base.Adapter` and implement
   `parse(html) -> list[dict]`, using the `set_benefit` / `set_validity` /
   `set_cards` / `set_scope` helpers so the record comes out schema-compliant.
4. Register the class in `renocred_offers/registry.py`.

An adapter's job is narrow: turn one source's raw shape into records with honest
provenance. It does not decide verification status, confidence or deduplication
— those stages run identically over every source by design.

## Known gaps

Recorded rather than hidden, because an omission that looks like an oversight is
worse than a documented one.

- **Legacy SBI card SKUs.** The SBI feed names 52 distinct card slugs; 16
  resolve against the 518-card Card Master. The remainder are retired or
  merged-bank SKUs (SBI Gold, SBI Platinum, LVB, OBC, Bhartiya Mahila Bank)
  that the Card Master does not carry. They are preserved verbatim in
  `unmatched_card_slugs` so eligibility is never *widened* by a failed lookup.
- **Consumer vs. corporate variants.** The Card Master's `personal_business`
  field is null throughout, so the Corporate/Commercial/Dealer/Distributor
  exclusion that appears in nearly every voucher T&C cannot be evaluated. This
  is the Phase 3C.4 gap the plan predicted; the engine returns
  `INSUFFICIENT_DATA` rather than assuming a consumer card.
- **SBI offer detail pages** are client-rendered from session state and expose
  no stable URL, so per-offer T&C beyond the tile copy is not ingested. Caps
  present in the tile text are parsed.
- **Deferred sources** are listed with reasons in the `deferred:` block of
  `sources.yaml` — HDFC SmartBuy (host unreachable), ICICI iShop, Visa Offers &
  Perks, Poshvine (login-gated), Mastercard India and RuPay (edge 403).

## Reading the dataset

```bash
python -m renocred_offers.viewer          # writes output/renocred_offer_ledger.html
```

One self-contained HTML file — open it straight from disk, no server. It embeds a
browsing projection of every record (the raw evidence snapshots are dropped; the
review reasons, conflicts, stated conditions and linked PDFs are kept).

It is built as an **audit surface, not a deals list**, because that is what the
dataset is: the headline figure is *live and verified*, not the record count, and
every row carries its tier, its confidence and when it was last checked. Filter by
verification state, window, source tier, issuer or benefit mechanism; open a row for
its full provenance. The Sources tab lists the deferred sources with their reasons,
and Data quality shows field coverage, the records that failed the ingestion gate,
and the merchant partnership gaps.

Regenerate it after any rebuild — it reads the built masters from `--out`, so it is
always consistent with the dataset rather than a stale snapshot of it.

## Matching a wallet against the dataset

`renocred_offers/query.py` demonstrates the matching layer answering the plan's
own worked question — "I am about to spend ₹8,000, which of my cards should I
use and what will I actually get?"

```bash
python -m renocred_offers.query \
    --cards sbi_sbi_prime_credit_card_v2 sbi_sbi_elite_credit_card_v2 \
    --spend 8000 --merchant Amazon
```

```
Wallet: 2 card(s) | spend Rs 8,000 at Amazon
Eligible & verified: 3 | needs confirmation: 5

BEST: 5% Instant Discount
  merchant   : Amazon
  net benefit: Rs 400.00  (5% of Rs 8,000)
  cards      : sbi_sbi_prime_credit_card_v2
  confidence : 0.87 | valid until 2026-09-30
```

Two properties are enforced rather than intended:

- Only `ELIGIBLE` **and** `VERIFIED` offers can be ranked. Anything
  `INSUFFICIENT_DATA` — or verified-but-not-`VERIFIED` — is reported in a
  separate `possible_confirm_with_issuer` bucket with its caveat attached, and
  can never become the answer.
- The ranking function receives only benefit and eligibility data. There is no
  code path by which a `partner`, `referral` or payout field could reach it, and
  `test_ranking_ignores_affiliate_fields` asserts that adding one leaves rank
  order and the stated basis byte-identical. Section 19 asks for provable
  independence rather than a stated intention; this is that proof.

Every result carries a `basis` string — `"5% of Rs 8,000"`, `"400 points (5X on
Rs 8,000) at Rs 0.25/point; requires a redemption step"` — because Phase 3C.6's
acceptance criterion is that a recommendation must be explainable, not merely
ranked. The point-to-rupee conversion is a named constant
(`DEFAULT_POINT_VALUE_INR`) rather than a magic number, so the assumption that
makes points comparable with cashback is visible and challengeable.
