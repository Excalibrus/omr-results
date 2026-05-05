# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install pdfplumber          # only runtime dep
python parse_results.py         # parse all years -> data/<year>.json + data/years.json
python parse_results.py <config> <output_dir>   # both args optional
python build.py                 # parse + copy index.html and data/ into dist/
python -m http.server 8080      # serve index.html locally
```

There are no tests, linters, or package manifest. The viewer is a single-file static page (`index.html`) consuming the JSON output via `fetch`, so any HTTP server works for local dev — opening the file via `file://` will not, because of the `fetch` calls.

## Architecture

### Pipeline
`config.json` -> `parse_results.py` -> `data/<year>.json` (one per year) + `data/years.json` (index) -> `index.html` (client-side rendering). `build.py` is just a deploy bundler that runs the parser and copies `index.html` + `data/` into `dist/`.

### Source resolution (parse_results.py `process_year`)
For each (year, discipline), inputs are tried in this priority order, falling through on failure:
1. `url` field in config (HTML fetched from prijavim.se)
2. local `.html` file under `pdfs/<year>/` (matched by name or discipline keyword)
3. PDF at `pdfs/<year>/<pdf>`

HTML is preferred because PDFs are lossy — pdfplumber occasionally garbles rows (e.g. 2023 TT), which is why `try_parse_single_line` exists as a fallback that recovers rank/scores/total from a single mangled line, accepting that the name may be unreadable.

### Cross-discipline rider merging
A rider appears in up to 3 separate result sets (climbs/road/tt). They are merged into one record by matching keys generated in `get_rider_key`:
- `(category, license)` if a license number is present, else
- `(category, normalized_name)` — uppercase, accent-stripped, whitespace-collapsed

Matching is **always scoped to category** — same name in different categories stays distinct. Club name is then resolved per-rider by picking the most common club across the rider's disciplines; ties are broken by the club with more total riders globally (`global_club_sizes`). Club aliases in `CLUB_ALIASES` normalize known variants (e.g. `BAMBI` -> `ŠD BAM.BI`) before counting.

### Two scoring totals per discipline
Each discipline result carries both `bestOf` (top-N races counting toward the discipline standings) and `bestOfGeneral` (top-N counting toward the overall classification). The general total = sum of each discipline's `bestOfGeneralTotal`. The parser also emits `bestOfIndices` / `bestOfGeneralIndices` so the UI can highlight which races counted.

### Ranks
Ranks are recomputed in Python (not taken from the source PDFs/HTML) for both general and per-discipline standings, per category, with tied totals sharing a rank.

### Club standings
Built by flattening all races from all disciplines, sorting by date (`dd.mm.` parsed as month, day), and summing each rider's per-race scores into their club's `raceScores` array. The flat `clubRaces` list in the output preserves this date-sorted ordering for the UI.

## Config (`config.json`)

`disciplines` is global (3 entries: climbs/road/tt with stable IDs). `years.<year>.<discipline>` carries the per-year settings: `pdf` (filename inside `pdfs/<year>/`), `bestOf`, `bestOfGeneral`, optional `url`, optional `html`, optional `raceNames` (overrides names extracted from the source). Adding a new year means dropping PDFs/HTML into `pdfs/<year>/` and adding a `<year>` block under `years` — no code changes.

## Repo conventions

- `pdfs/` and `data/` are gitignored. The committed `dist/` checkout is the published static site.
- Categories (`CATEGORIES` in parse_results.py) and `SKIP_WORDS` are tuned to the Slovenian PDFs; changes there directly affect what the parser will accept as a category header vs. noise.
