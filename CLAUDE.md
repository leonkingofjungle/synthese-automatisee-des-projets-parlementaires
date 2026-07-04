# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A two-piece pipeline that turns recent French parliamentary bills into plain-language
summaries for a static website:

1. `scripts/pdf_summarizer_mcp.py` — Python CLI that fetches recent bills from the
   public `parlement.tricoteuses.fr` API, gets the full text (AN opendata HTML mirror,
   falling back to the official PDF), summarizes each with Gemini into a structured
   JSON object (`categorie` / `accroche` / `points`), syncs the legislative status of
   every entry (`etat`/`statut` from `GET /dossiers/{uid}`), and writes the result to
   `front/resumes.json`. It is only the orchestrator; the LLM pieces live in sibling
   modules: `scripts/clients.py` (lazy Gemini + Claude clients, `.env` config),
   `scripts/summarizer.py` (Gemini prompt/schema, `CATEGORIES`, injection-guard
   sentinels), `scripts/judge.py` (judge prompt/schema, scoring, publication
   thresholds). `scripts/single_doc.py` runs the whole chain on one uid without
   writing `resumes.json` (debugging).
2. `front/` — a static site (vanilla JS ES modules, no build step, no backend) that
   reads `front/resumes.json`: `index.html` (home: search, "À la une", "En chiffres"
   stats block, 5 latest laws per category), `categorie.html?cat=slug` (all laws of
   one category), `loi.html?uid=…` (detail page). Shared rendering/mapping helpers
   live in `front/app.js`; all styling in `front/styles.css`.

There is no database and no cloud bucket in the current pipeline — the only external
dependencies are the tricoteuses API, assemblee-nationale.fr, the Gemini API, and the
Claude API (optional, used as LLM judge — `CLAUDE_API_KEY`).
`front/resumes.json` is the sole contract between the two pieces and is committed to
git (see `.gitignore`) so the front works without anyone running the script first.
Per-document fields: `uid`, `titre`, `date_depot`, `categorie`, `accroche`, `points`,
`link` (official text), `quality_score` (0–100 or null), `quality_flags`,
`dossier_uid`, `etat`, `statut`.

## Commands

```bash
pip install -r requirements.txt

# Generate/refresh front/resumes.json (requires GEMINI_API_KEY in .env)
python3 scripts/pdf_summarizer_mcp.py --limit 10
python3 scripts/pdf_summarizer_mcp.py --limit 10 --type PION --chambre AN  # defaults shown
python3 scripts/pdf_summarizer_mcp.py --force        # ignore cache, regenerate everything
python3 scripts/pdf_summarizer_mcp.py --workers 10   # more parallel network calls

# Refresh only the legislative statuses of cached entries (no LLM call, no key needed)
python3 scripts/pdf_summarizer_mcp.py --limit 0

# Serve the front — must be launched from front/, never from the repo root
cd front && python3 -m http.server 8000
# then open http://localhost:8000/
```

There is no test suite, linter, or build step in this repo.

## Architecture notes

- **Caching**: the script treats `front/resumes.json` as a cache keyed by document
  `uid`. Only uids not already present (with a non-empty `accroche`) are re-fetched
  and re-summarized; `--force` bypasses reuse for the current window only. Cached
  entries outside the current `--limit`/`--type`/`--chambre` window are always
  preserved; the file is re-written in full (atomically, via a `.tmp` +
  `os.replace`) ordered by `date_depot` descending.
- **LLM judge (quality score)**: after each Gemini summary, a Claude model
  (`JUDGE_MODEL` in `scripts/clients.py`, `CLAUDE_API_KEY` optional in `.env`)
  verifies each claim of the summary extractively against the source text
  (`ok`/`deforme`/`invente` + category/neutrality checks). A 0–100 `quality_score`
  is derived (ok=1.0, deforme=0.4, invente=0.0). Publication policy: score < 60 →
  **not published** (status `rejected`; a `rejets` counter persisted at the top
  level of `resumes.json` caps retries at `MAX_REJECT_ATTEMPTS=3`, after which the
  doc is abandoned); otherwise published with `quality_score`/`quality_flags`. The
  front shows the score on every law and an amber "Fiabilité" treatment when
  score < `QUALITY_BADGE_THRESHOLD` (85, in `front/app.js`) **or** `quality_flags`
  is non-empty (badge on rows via `renderQualityBadge`, detailed warn notice on
  `loi.html`); the sidebar "Les mieux vérifiées" lists the latest docs with
  score > 85. Guardrails: a judge response whose verdict count ≠ claim count (or
  with out-of-enum verdicts) is discarded (published unscored); texts longer than
  `JUDGE_TEXT_MAX_CHARS` (40k) are judged on the truncated prefix but never
  rejected (flag `texte_tronque`); an unreachable judge API disables the judge for
  the whole run after the first connection failure (`max_retries=0`, `_judge_down`
  flag); PDF-path docs are published with `quality_score: null`. A failed/rejected
  regeneration never resurrects the stale cached entry (`failed_uids` removed from
  the merge). Judge calls are serialized behind a lock.
- **Legislative status**: each entry carries `dossier_uid` plus the dossier's raw
  `etat`/`statut` (e.g. "En cours" / "1ère lecture en commission"). Statuses change
  over time, so `refresh_statuses` (in `pdf_summarizer_mcp.py`) re-fetches them on
  **every** run for **all** cached entries (one `GET /dossiers/{uid}` per unique
  dossier, parallel, no LLM; `--no-status-refresh` to skip; `--limit 0` = pure
  backfill). On network failure previous values are kept, never nulled. Convention:
  missing `dossier_uid` key = never resolved (retried next run); `null` = resolved,
  no dossier (not retried). The front maps raw labels to reader-friendly stages in
  `getStage`/`STAGES` (`front/app.js`) with a raw-label fallback for unknown
  vocabulary — status badges everywhere, plus the "Où en sont-elles ?" stats bars.
- **Text source priority**: for each document, `download_document_text` tries the AN
  opendata HTML mirror first (cheap, plain text, works for Assemblée nationale
  documents). If that 404s (e.g. Sénat documents, or missing mirrors), it falls back
  to sending the official PDF to Gemini multimodally (slower — server-side page
  rendering).
- **Parallelism**: only *new* documents are processed, via a `ThreadPoolExecutor`
  (`--workers`, default 5) — download + Gemini call per document are independent
  network operations.
- **Prompt/schema**: `SUMMARY_PROMPT_TEMPLATE` and `RESPONSE_SCHEMA` (in
  `scripts/summarizer.py`) define the contract with Gemini (structured JSON output,
  fixed `CATEGORIES` list, accroche 15–25 words, 3–4 points of 12–22 words each,
  one few-shot example). The front's category grouping depends on `categorie` being
  one of these exact values — if you change `CATEGORIES`, keep both in sync. Note:
  the template contains literal JSON braces, so `{titre}` is substituted with
  `.replace()`, not `.format()`.
- **Front reads only the static JSON file**: `front/index.html` fetches
  `resumes.json` via a relative path — it does *not* call the tricoteuses API
  directly. This means the front must be served from within `front/` (see the `cd
  front &&` command above); serving from the repo root would also expose `.env`.
- **`GEMINI_API_KEY`** is loaded from a `.env` file at the repo root (`.env.example`
  documents the required var). Clients are created lazily in `scripts/clients.py`
  (`lru_cache`), so the modules import fine without the key — it is only required
  when a Gemini call is actually made.
