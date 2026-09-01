# Changelog

## [0.1.0] - Initial Release
- Core extraction framework.
- HTML, JSON-LD, and API extractors.
- Embedded-JSON extractor for inline `var X = {...}` and `__NEXT_DATA__` payloads.
- Table extractor with header/record zipping.
- Polite `BaseCrawler`: robots.txt, crawl-delay, per-host rate limiting, retry.
- `Pipeline` with named stages, per-stage timing and failure capture.
- Schema validation, duplicate detection and quality/coverage reporting.
- JSON (incl. NDJSON), CSV and Excel exporters with atomic writes.
- RenoCred Offer Intelligence pipeline as a reference application.
