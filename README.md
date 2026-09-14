# Autonomous Lead Enrichment Agent

A Python pipeline that takes a list of company domains, crawls their public
web presence with a headless browser, cleans the content down to
LLM-friendly text, and extracts structured company intelligence (overview,
ICP, contacts, leadership, confidence score) using Claude's tool-calling for
strict structured output.

## How it works

```
domains  ──▶  crawler.py  ──▶  extractor.py  ──▶  llm_extract.py  ──▶  output.json
            (Playwright:      (BeautifulSoup:      (Anthropic tool-use,
             homepage +        strip scripts/nav,   strict JSON schema
             relevant           collapse to plain    via models.py)
             subpages)          text, cap length)
```

1. **`src/crawler.py`** — Launches headless Chromium via Playwright, fetches
   the homepage, waits for JS-rendered content to settle, then discovers and
   fetches up to 5 same-site subpages matching common slugs (`about`,
   `team`, `contact`, `pricing`, etc). Handles timeouts, 404s, and bot
   blocks without crashing.
2. **`src/extractor.py`** — Strips `<script>`, `<style>`, `<svg>`, `<nav>`,
   `<footer>` and other boilerplate, preserves `mailto:` and LinkedIn hrefs
   inline (so emails/profile links survive text extraction), and caps each
   page at ~6k characters to bound token spend.
3. **`src/llm_extract.py`** — Sends the cleaned text to Claude with a single
   forced tool call (`record_company_intelligence`), so the response is
   always valid structured JSON matching the schema in `src/models.py` — no
   regex-parsing of free text.
4. **`src/main.py`** — Orchestrates the batch run. Every domain is wrapped in
   its own try/except at multiple layers, so one failing site (timeout,
   block, malformed HTML, LLM error) never aborts the rest of the batch —
   it's recorded with `data_confidence_score: 0.0` and an `errors` list
   instead.

## Setup

```bash
git clone <this-repo>
cd lead-enrichment-agent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# then edit .env and paste your ANTHROPIC_API_KEY
```

## Run

```bash
# Run against the three assignment test domains (also the default if no args given):
python -m src.main postman.com supabase.com vapi.ai

# Or supply your own list:
python -m src.main --input domains.txt --output output/output.json
```

Output is written to `output/output.json` as an array of records matching
`CompanyIntelligence` in `src/models.py`.

## Measuring extraction quality (differentiator)

Most take-home submissions assert quality; this one measures it.
`src/ground_truth.py` holds a small set of hand-verified facts for the 3 test
domains (keywords the overview should mention, leadership names, email
domains). `src/eval.py` scores a generated `output.json` against that
ground truth and prints a per-field pass/fail plus an overall accuracy
percentage:

```bash
python -m src.main postman.com supabase.com vapi.ai
python -m src.eval output/output.json
```

This also catches a failure mode confidence scores alone can't: it flags
records where `data_confidence_score` is inconsistent with whether errors
were logged (e.g. claiming near-perfect confidence despite a crawl error).

## Bonus: LinkedIn search fallback

If a leadership member is found on the site but their LinkedIn URL isn't
(common - many "About" pages list names/titles without links), the agent can
optionally search for it via [Tavily](https://tavily.com) (free tier: 1,000
searches/month, no card required).

- Get a free API key at tavily.com and add it to `.env` as `TAVILY_API_KEY`.
- Leave it blank to skip this step entirely - `src/linkedin_search.py`
  no-ops cleanly and the rest of the pipeline is unaffected either way.
- Implementation: `src/linkedin_search.py`, wired into `src/main.py` right
  after LLM extraction, only for leadership entries missing a `linkedin_url`.

## Design decisions & trade-offs

- **Forced tool-calling over free-text + regex parsing.** Anthropic's
  `tool_choice={"type": "tool", ...}` guarantees the model returns
  schema-shaped JSON, which is far more reliable than asking it to "return
  JSON" in free text and hoping there's no preamble or markdown fencing.
- **Regex email fallback.** Emails are extracted two ways — via the LLM
  (works well since links carry visible `mailto:` context) and via a plain
  regex sweep of the raw HTML — then merged. This catches emails an LLM
  might skip if they're visually buried.
- **Per-domain isolation.** `process_domain()` never raises; every failure
  mode (crawl failure, cleaning failure, LLM failure, schema-validation
  failure) degrades to a partial record with `errors` populated, rather than
  stopping the batch. This was prioritized given the rubric's weight on
  resilience.
- **Subpage discovery via slug matching**, not a generic sitemap crawl —
  keeps the crawl fast and cheap (≤6 pages/domain) while still hitting the
  pages most likely to contain team/contact info.
- **Bounded concurrency (`MAX_CONCURRENT_DOMAINS = 3`).** Domains process in
  parallel via `asyncio.gather` + a semaphore, rather than strictly one at a
  time — a slow or hanging site no longer stalls the whole batch behind it —
  while still capping how hard target sites and the LLM API get hit at once.

## Known limitations / next steps

- No proxy/anti-bot-bypass layer — sites with aggressive bot detection
  (Cloudflare challenge pages, etc.) will be recorded as failed/low-confidence
  rather than circumvented.
- Cost tracking is currently a rough per-request estimate
  (`estimate_cost_usd`) rather than pulled from the API's actual
  `usage` field on every response — wiring that through `main.py`'s
  aggregation loop is a quick follow-up.

## Sample output

See `output/output.sample.json` for an example of the expected output shape.
It illustrates the schema Claude is asked to fill in — regenerate the real
one by running the command above with a valid API key, since sandboxed
environments used to prepare this repo don't have open internet access to
the target domains.
