# Examples

## 1. Repeating tiles in a grid

```python
from extractflow.extractors.html import HTMLExtractor

mapping = {
    "brand":      {"selector": "div.card-logo-block img", "type": "attribute", "attribute": "alt"},
    "discount":   {"selector": "div.discount-block", "type": "text"},
    "terms":      {"selector": "div.max-discount-block", "type": "list"},
    "outlet_pdf": {"selector": "div.avail-now-button-block a", "type": "attribute", "attribute": "href"},
}
tiles = HTMLExtractor(mapping).extract_all(html, "div.offer-card-block")

# Facets are often CSS classes on the tile, not text:
for tile in tiles:
    regions = [c for c in tile["_root_classes"] if c in {"north", "south", "east", "west"}]
```

## 2. A page whose data ships inline

Pages built on AEM/JSP frequently render client-side from an inline blob. There
is no need for a browser:

```python
from extractflow.extractors.embedded_json import extract_assignment

data = extract_assignment(html, "offerData")
offers = data["offers"]["offer"]        # the complete dataset, exactly as served
```

Next.js pages expose the equivalent through `__NEXT_DATA__`:

```python
from extractflow.extractors.embedded_json import extract_script_json
from extractflow.utils.helpers import deep_get

blob = extract_script_json(html)
brands = deep_get(blob, "props.pageProps.homeData.popular_brands") or []
```

## 3. Falling back to a browser only when needed

```python
import asyncio
from extractflow.core.scraper import DynamicScraper

records = parse(html)
if not records:                          # 200 OK but empty => truly client-rendered
    html = asyncio.run(
        DynamicScraper({"timeout": 60000, "wait_for_selector": "div.productList1"}).scrape(url)
    )
    records = parse(html)
```

## 4. Tables

```python
from extractflow.extractors.tables import extract_tables

for table in extract_tables(html):
    for row in table["records"]:
        print(row["Retail Partners"], row["Offers"], row["Coupon Code"])
```

## 5. Validation and coverage

```python
from extractflow.validators.schema_validator import validate_all
from extractflow.validators.quality_checker import check_quality

schema = {
    "required": ["id", "merchant.name"],
    "types":    {"discount_pct": "number"},
    "enums":    {"tier": ["S", "A", "B", "C", "D"]},
}
report = validate_all(records, schema)
print(report["invalid_count"], "invalid record(s)")

coverage = check_quality(records, ["merchant.name", "discount_pct", "valid_until"])
print(coverage["coverage_by_field"])     # {'merchant.name': '412/424', ...}
```

## 6. Deduplication with a merge rule

```python
from extractflow.validators.duplicate_detector import deduplicate

def keep_higher_authority(a, b):
    return a if a["tier"] <= b["tier"] else b

unique = deduplicate(records, ["merchant.id", "benefit.type"], merge=keep_higher_authority)
```

## 7. A complete pipeline

See [`renocred_offers/build.py`](../renocred_offers/build.py) for a production
example: eleven sources, adapter dispatch, PDF enrichment, reconciliation,
scoring and multi-format export, all composed as named `Pipeline` stages.
