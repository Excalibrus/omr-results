# OMR Cestno kolesarstvo - Results Parser & Viewer

Parses cycling competition results from PDF files and presents them on an interactive web page with filtering, multi-discipline scoring, and multi-year support.

## Structure

```
pdfs/
  2025/                          # PDFs organized by year
    Cestno kolesarstvo.pdf       # Climbs (vzponi)
    Cestno kolesarstvo (1).pdf   # Road races (cestne dirke)
    Cestno kolesarstvo (2).pdf   # Time trials (kronometer)
  2024/
    ...
data/                            # Generated JSON output
  2025.json
  years.json
config.json                      # Scoring rules & PDF mappings
parse_results.py                 # PDF parser
index.html                       # Web viewer
```

## Setup

```bash
pip install pdfplumber
```

## Usage

1. Place PDFs in `pdfs/<year>/`
2. Configure `config.json` (see below)
3. Run the parser:
   ```bash
   python parse_results.py
   ```
4. Serve and open `index.html`:
   ```bash
   python -m http.server 8080
   ```

## Configuration

`config.json` has two sections:

**disciplines** - global discipline definitions (same across all years):
```json
{
  "disciplines": [
    { "id": "climbs", "name": "Vzponi" },
    { "id": "road", "name": "Cestne dirke" },
    { "id": "tt", "name": "Kronometer" }
  ]
}
```

**years** - per-year settings for each discipline:
```json
{
  "years": {
    "2025": {
      "climbs": {
        "pdf": "Cestno kolesarstvo.pdf",
        "bestOf": 6,
        "bestOfGeneral": 3,
        "raceNames": ["Vzpon na Krvavec", "..."]
      }
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `pdf` | PDF filename inside `pdfs/<year>/` |
| `bestOf` | How many best races count for discipline standings |
| `bestOfGeneral` | How many best races count toward the general classification |
| `raceNames` | Display names for each race (order matches dates in PDF) |

**General classification** = sum of each discipline's `bestOfGeneral` top scores.

## Adding a new year

1. Create `pdfs/<year>/` and place the 3 PDF files inside
2. Add a `"<year>"` entry under `years` in `config.json` with PDF filenames, bestOf numbers, and race names
3. Run `python parse_results.py`

## Web viewer features

- **Year switcher** - pill buttons in the header
- **4 tabs** - General (combined standings), Vzponi, Cestne dirke, Kronometer
- **Filters** - search by name/club, filter by category, filter by club
- **Scoring info** - shows how many races count per tab
- **Best-of highlighting** - scores counting toward the total are highlighted brighter
- **Medals** - top 3 per category get gold/silver/bronze icons
