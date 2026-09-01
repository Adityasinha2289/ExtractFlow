# ExtractFlow

A production-grade, configuration-driven Python framework for structured web data extraction.

## Features
- **Modular Architecture**: Pluggable components for crawling, scraping, parsing, and exporting.
- **Robust Validation**: Schema validation, duplicate detection and coverage reporting built-in.
- **Configurable**: Drive extraction pipelines via simple YAML/JSON configs.
- **Polite by default**: `robots.txt` is honoured, `crawl-delay` is respected, and requests are rate-limited per host.
- **Static-first**: Pages that ship their data inline are parsed without a browser; Playwright is reserved for genuinely client-rendered sources.

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium   # only for dynamic sources
```

## Quick start

```python
from extractflow.core.crawler import BaseCrawler
from extractflow.extractors.html import HTMLExtractor

crawler = BaseCrawler({"default_delay": 1.5})
page = crawler.fetch("https://example.com/offers")

mapping = {
    "title":    {"selector": "h3.title", "type": "text"},
    "discount": {"selector": ".discount", "type": "text", "regex": r"(\d+)%"},
    "link":     {"selector": "a", "type": "attribute", "attribute": "href"},
}
records = HTMLExtractor(mapping).extract_all(page.text, "div.offer-card")
```

## Components

| Layer | Module | Notes |
|---|---|---|
| Crawl | `core.crawler.BaseCrawler` | robots-aware, rate-limited, retrying. Never raises — failures come back on `FetchResult`. |
| Render | `core.scraper.DynamicScraper` | Playwright; waits for selectors, performs interactions. |
| Extract | `extractors.html` | Config-driven CSS/regex mapping; `extract_all` for repeating blocks. |
| | `extractors.embedded_json` | Pulls `var X = {...}` blobs and `__NEXT_DATA__` payloads out of "JS-rendered" pages. |
| | `extractors.tables` | Tables as `{headers, rows, records}`. |
| | `extractors.jsonld` | JSON-LD, `@graph`-flattened and `@type`-filtered. |
| | `extractors.api` | Normalises a Response / FetchResult / string into data. |
| Parse | `core.parser.RecordParser` | Declarative field transforms; a bad value never kills the batch. |
| Orchestrate | `core.pipeline.Pipeline` | Ordered named stages with per-stage timing and failure capture. |
| Validate | `validators.schema_validator` | Lightweight JSON-Schema subset; reports rather than raises. |
| | `validators.duplicate_detector` | Composite/callable keys, optional merge function. |
| | `validators.quality_checker` | Per-field coverage in `n/total` form, per-record completeness. |
| Export | `exporters.json_exporter` | Atomic writes; optional newline-delimited output. |
| | `exporters.csv_exporter` | Flattens nested records to dotted columns. |
| | `exporters.excel_exporter` | `.xlsx` via optional `openpyxl`. |

## Documentation

- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Examples](docs/examples.md)
- [Roadmap](docs/roadmap.md)

## Reference application

[`renocred_offers/`](docs/renocred-offer-pipeline.md) is a full application
built on the framework: a verification-first pipeline that extracts live Indian
credit-card offers from issuer portals, network programmes, infrastructure
vendors and aggregators, links them to a card master dataset, and emits a
scored, audit-trailed offer dataset.

```bash
python -m renocred_offers.build --cache output/snapshots   # build the dataset
python -m renocred_offers.viewer                            # browsable HTML page
```

## Tests

```bash
python -m pytest tests -q
```
