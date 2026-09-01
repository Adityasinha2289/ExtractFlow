# Configuration

ExtractFlow components take plain dictionaries, so configuration can come from
YAML, JSON, environment variables, or code.

## Crawler

```yaml
timeout: 40            # seconds per request
retries: 3             # attempts before giving up
default_delay: 1.5     # minimum seconds between requests to one host
respect_robots: true   # honour robots.txt (and its crawl-delay)
user_agent: "Mozilla/5.0 ..."
headers:
  Accept-Language: en-IN,en;q=0.9
```

`crawl-delay` from a host's `robots.txt` overrides `default_delay` when it is
larger. A `robots.txt` that returns HTML (an SPA serving `index.html` for every
path) is ignored rather than parsed as a rules file.

## Extraction mappings

A mapping is `{field: rule}`. Rule keys:

| Key | Meaning |
|---|---|
| `selector` | CSS selector. Omit to operate on the element itself. |
| `type` | `text`, `list`, `attribute`, `attr_list`, `html`, `exists`, `class_list`. Defaults to `text`. |
| `attribute` | Attribute name for `attribute` / `attr_list`. |
| `regex` | Applied to `text`; group 1 if present, else the whole match. |
| `default` | Returned when the selector matches nothing. |

```yaml
title:    {selector: "h3.product-heading", type: text}
percent:  {selector: ".discount", type: text, regex: "(\d+)%"}
outlets:  {selector: ".outlet a", type: attr_list, attribute: href}
in_stock: {selector: ".sold-out", type: exists}
```

Use `HTMLExtractor.extract_all(html, root_selector)` for repeating blocks. Each
record additionally carries `_root_classes`, because listing grids routinely
encode facets — category, region, availability — as CSS classes on the tile
rather than as visible text.

## Embedded JSON

```yaml
offers:  {variable: offerData, path: "offers.offer"}
props:   {script_id: __NEXT_DATA__, path: "props.pageProps"}
```

`variable` reads a `var NAME = {...}` assignment; `script_id` reads a
`<script id="..." type="application/json">` payload. `path` uses `a.b[0].c`
notation.

## Environment

| Variable | Effect |
|---|---|
| `EXTRACTFLOW_LOG_LEVEL` | Log level; default `INFO`. |
| `EXTRACTFLOW_CONCURRENCY` | Advisory worker count for callers that parallelise. |
