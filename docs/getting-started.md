# Getting Started

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium   # only needed for dynamic sources
```

## Your first extraction

```python
from extractflow.core.crawler import BaseCrawler
from extractflow.extractors.html import HTMLExtractor
from extractflow.exporters.json_exporter import export_json

crawler = BaseCrawler({"default_delay": 1.5})
page = crawler.fetch("https://example.com/deals")
if not page.ok:
    raise SystemExit(f"fetch failed: {page.status} {page.error}")

mapping = {
    "brand":    {"selector": ".brand img", "type": "attribute", "attribute": "alt"},
    "headline": {"selector": ".headline", "type": "text"},
    "terms":    {"selector": ".terms", "type": "list"},
}
records = HTMLExtractor(mapping).extract_all(page.text, "div.deal-card")
export_json(records, "output/deals.json")
```

## When the page looks empty

If the HTML has no content, check for an inline data blob before reaching for a
browser — it is faster, exact, and far less brittle:

```python
from extractflow.extractors.embedded_json import extract_assignment, extract_script_json

data = extract_assignment(page.text, "offerData")     # var offerData = {...}
data = data or extract_script_json(page.text)         # <script id="__NEXT_DATA__">
```

Only if both come back empty is the page genuinely client-rendered:

```python
import asyncio
from extractflow.core.scraper import DynamicScraper

html = asyncio.run(
    DynamicScraper({"timeout": 60000, "wait_for_selector": "div.deal-card"}).scrape(url)
)
```

## Composing a pipeline

```python
from extractflow.core.pipeline import Pipeline

result = (
    Pipeline()
    .add("fetch",    lambda _: crawler.fetch(url).text)
    .add("extract",  lambda html: HTMLExtractor(mapping).extract_all(html, "div.deal-card"))
    .add("validate", lambda rows: rows)
    .run()
)
print(result.summary())   # fetch=ok(...) -> extract=ok(42 in 31ms) -> ...
```

Next: [configuration](configuration.md), [examples](examples.md), and the
[RenoCred reference application](renocred-offer-pipeline.md).
