# Roadmap

## Shipped (0.1.0)

- Polite crawler: `robots.txt`, `crawl-delay`, per-host rate limiting, retry with backoff.
- Extractors: config-driven HTML (single and repeating blocks), embedded JSON
  (`var X = {...}` and `__NEXT_DATA__`), tables, JSON-LD, API payloads.
- Playwright rendering for genuinely client-side sources.
- `Pipeline` with named stages, per-stage timing and failure capture.
- Validators: schema, duplicate detection, quality/coverage.
- Exporters: JSON (incl. NDJSON), CSV, Excel; all atomic.
- RenoCred Offer Intelligence reference application.

## Next

- **Concurrency.** `EXTRACTFLOW_CONCURRENCY` is currently advisory. A bounded
  per-host worker pool would cut wall-clock on multi-source runs substantially,
  and matters most for the linked-PDF stage, which is entirely IO-bound.
- **Snapshot diffing.** Raw snapshots are already stored; a content-hash diff
  between runs would detect silent term changes, which timestamps alone cannot.
- **PDF tables.** `pypdf` yields text; outlet and model lists are often laid out
  as tables where column structure carries meaning.
- **Incremental runs.** Re-crawl only sources past their staleness window rather
  than the whole registry.
- **Pluggable adapter discovery** via entry points, so adapters can ship in
  separate packages.

## Not planned

- Authentication, CAPTCHA solving, or paywall bypass. Login-gated content is out
  of scope by design; only publicly observable surfaces are read.
