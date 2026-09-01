# Architecture

ExtractFlow utilizes a Pipeline model: Crawler -> Scraper -> Parser -> Validator -> Exporter.

```
                 SOURCE CONFIG (YAML)
                         |
                         v
   +---------------------------------------------+
   |  BaseCrawler        robots.txt, rate limit,  |
   |                     retry, FetchResult       |
   +---------------------------------------------+
                         |
              static text | (200 OK but empty?)
                         |------------------------+
                         v                        v
                   EXTRACTORS              DynamicScraper
        html / embedded_json / tables      (Playwright render)
             jsonld / api                         |
                         |<-----------------------+
                         v
                     PARSER
             declarative field transforms
                         |
                         v
                    PIPELINE
        ordered named stages, per-stage timing,
        failure captured rather than raised
                         |
             +-----------+-----------+
             v                       v
        VALIDATORS               EXPORTERS
   schema / duplicates /      json / csv / excel
   quality coverage
```

## Design decisions

**Static-first retrieval.** A browser round-trip is expensive and flaky. Many
pages that appear to be single-page applications ship their entire dataset
inline in a `<script>` tag and only render client-side, so `embedded_json` is
tried before Playwright. A dynamic render is triggered only when a page fetches
`200 OK` and still yields zero records — the actual signature of client-side
rendering, as opposed to a page that is merely large.

**Failures are data, not exceptions.** `BaseCrawler.fetch` never raises; it
returns a `FetchResult` carrying the status, the error and the elapsed time. A
`Pipeline` stage that raises is recorded with its timing and aborts that run
without losing the stages that already succeeded. A `RecordParser` transform
that fails yields `None` for that field rather than discarding the record. One
malformed price string must never cost you an entire source.

**Validators report; callers decide.** `validate` returns
`{valid, errors, warnings}` instead of raising, because a batch of a few hundred
records will always contain a few bad ones and the right response is usually to
route them to review, not to abort.

**Null is not zero.** `quality_checker` counts `0` and `False` as populated
values and only `None`/empty containers as absent. Conflating "the rate is zero"
with "we could not find the rate" corrupts every coverage number downstream.

**Atomic writes.** Exporters write to a temp file and `os.replace` it into
position, so a reader never observes a half-written dataset.
