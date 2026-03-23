"""
Parser for OMR cycling competition results.
Supports HTML (primary, most accurate) and PDF (fallback) sources.
Reads config.json for discipline definitions, parses all sources,
matches riders across disciplines, computes best-of scoring,
and outputs a unified data.json per year.
"""
from html.parser import HTMLParser
import pdfplumber
import json
import os
import re
import sys
import unicodedata

CATEGORIES = [
    "Ženske A", "Ženske B", "Ženske C", "Ženske D",
    "Amaterji",
    "Master A", "Master B", "Master C", "Master D",
    "Master E", "Master F", "Master G", "Master H",
    "Master I", "Master J"
]

SKIP_WORDS = {"Tekmovalec", "Vzpon", "Kolesarska", "Prijavim", "Kronometer",
              "Cestno", "VZPON", "memorial", "Maraton", "GRAN", "FONDO",
              "hitrostni", "Vožnja"}


# Club name aliases: map variant names to canonical name
CLUB_ALIASES = {
    "BAMBI": "ŠD BAM.BI",
    "BAM.BI": "ŠD BAM.BI",
    "B.V.G. Gulč": "B.V.G. GULČ",
    "Energija team": "ENERGIJATEAM.COM",
    "KD Rog": "KD ROG",
}


def normalize_club(club):
    """Normalize club name using alias map and case fixes."""
    if not club:
        return club
    club = club.strip()
    # Check exact match first
    if club in CLUB_ALIASES:
        return CLUB_ALIASES[club]
    # Check case-insensitive match
    for alias, canonical in CLUB_ALIASES.items():
        if club.upper() == alias.upper():
            return canonical
    return club


def normalize_name(name):
    """Normalize a name for matching: uppercase, strip accents, collapse whitespace."""
    name = name.strip().upper()
    name = re.sub(r'\s+', ' ', name)
    # Remove accents for fuzzy matching
    nfkd = unicodedata.normalize('NFKD', name)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def detect_category(line):
    for cat in CATEGORIES:
        if line == cat:
            return cat
    return None


def extract_race_info_from_header(header_line):
    """Extract race dates from the Tekmovalec header line."""
    dates = re.findall(r'\d{2}\.\d{2}\.', header_line)
    return dates


def skip_header_block(lines, start):
    """Skip past the race header block, return (next_line_index, num_races, race_dates)."""
    i = start + 1
    while i < len(lines):
        line = lines[i].strip()
        if "Tekmovalec" in line and "Licenca" in line:
            dates = extract_race_info_from_header(line)
            num_races = len(dates)
            return i + 1, num_races, dates
        i += 1
    return i, 0, []


def parse_score_line(line, num_races):
    """Parse a score line with dynamic race count.
    Format: 'rank. [license] score1 score2 ... scoreN total'
    Scores are typically 0-40 range. License is a long number (10+ digits) or alphanumeric.
    """
    match = re.match(r'^(\d+)\.\s*(.*)', line)
    if not match:
        return None

    rank = int(match.group(1))
    rest = match.group(2).strip()
    parts = rest.split()

    needed = num_races + 1  # scores + total

    if len(parts) < needed:
        return None

    # Strategy: work from the END of parts to find scores+total,
    # anything before that is license or club text
    # Take the last (num_races + 1) items as scores+total
    # Everything before is potential license/club
    tail = parts[-needed:]
    prefix = parts[:-needed]

    try:
        scores = [int(tail[j]) for j in range(num_races)]
        total = int(tail[num_races])
    except (ValueError, IndexError):
        # Fallback: try with no prefix (all parts are scores)
        if len(parts) == needed:
            try:
                scores = [int(parts[j]) for j in range(num_races)]
                total = int(parts[num_races])
                return {"rank": rank, "license": "", "scores": scores, "total": total}
            except (ValueError, IndexError):
                pass
        return None

    # Extract license from prefix
    license_num = ""
    if prefix:
        candidate = " ".join(prefix)
        # License is typically a long number or alphanumeric code
        if re.match(r'^[A-Za-z0-9]+$', candidate):
            license_num = candidate

    return {
        "rank": rank,
        "license": license_num,
        "scores": scores,
        "total": total
    }


def try_parse_single_line(line, num_races):
    """Try to parse a garbled single-line entry like:
    '1.P K A K S S A O R Č P A etra 10116494960 30 40 70'
    Extracts rank, optional license, scores, and total from the end.
    Name/club are unrecoverable from garbled text.
    """
    match = re.match(r'^(\d+)\.\s*(.*)', line)
    if not match:
        return None

    rank = int(match.group(1))
    rest = match.group(2).strip()
    parts = rest.split()

    needed = num_races + 1  # scores + total

    if len(parts) < needed:
        return None

    # Take last (num_races+1) items as scores+total
    tail = parts[-needed:]
    try:
        scores = [int(tail[j]) for j in range(num_races)]
        total = int(tail[num_races])
    except (ValueError, IndexError):
        return None

    # Everything before scores is garbled name+club+maybe license
    prefix = parts[:-needed]

    # Try to find license in prefix (long digit string)
    license_num = ""
    for p in prefix:
        if re.match(r'^\d{8,}$', p):
            license_num = p
            break

    # Try to extract name and club from prefix text
    text_before = " ".join(prefix)
    if license_num:
        text_before = text_before.replace(license_num, "").strip()

    # Try to parse name from prefix: look for "SURNAME Firstname" or "SURNAME-SURNAME Firstname"
    name_match = re.match(r'^([A-ZČŠŽĆĐ][A-ZČŠŽĆĐa-zčšžćđ\-]+(?:\s+[A-ZČŠŽĆĐa-zčšžćđ\-]+)*)', text_before)
    if name_match:
        name = name_match.group(1).strip()
        club_text = text_before[name_match.end():].strip()
    else:
        name = f"Rider #{rank} (garbled)"
        club_text = text_before

    return {
        "rank": rank,
        "name": name,
        "license": license_num,
        "club": club_text if club_text and not club_text.isdigit() else "",
        "scores": scores,
        "total": total
    }


def is_score_line(line):
    return bool(re.match(r'^\d+\.\s', line))


def is_name_line(line):
    """Check if line looks like a rider name (SURNAME Firstname)."""
    # Strip optional rank prefix
    cleaned = re.sub(r'^\d+\.', '', line).strip()
    if not cleaned:
        return False
    # Should start with uppercase letter, contain letters, minimal digits
    if any(cleaned == cat for cat in CATEGORIES):
        return False
    if any(w in cleaned for w in SKIP_WORDS):
        return False
    # Name pattern: at least one uppercase letter followed by lowercase, allowing hyphens
    if re.match(r'^[A-ZČŠŽĆĐ][A-ZČŠŽĆĐa-zčšžćđ\s\-]+$', cleaned):
        return True
    return False


def try_parse_rider(lines, idx, category, num_races):
    """Try to parse a rider entry starting at idx."""
    if idx >= len(lines):
        return None, 0

    line = lines[idx].strip()

    # Check if this is a name line
    # Strip optional rank prefix for name detection
    name_cleaned = re.sub(r'^\d+\.', '', line).strip()

    if not name_cleaned or not is_name_line(line):
        return None, 0

    name_text = name_cleaned

    # Look at the next line for the score line
    if idx + 1 >= len(lines):
        return None, 0

    next_line = lines[idx + 1].strip()

    # The score line pattern: "rank. [license] score1 ... scoreN total"
    scores_data = parse_score_line(next_line, num_races)

    if not scores_data:
        # Edge case: name might span two lines (e.g., "ALEGRO BAZNIK" then "2.Vesna ...")
        # Check if next_line starts with "rank." but also has text before scores
        # Try combining: the second part of name might be embedded in score line
        match2 = re.match(r'^(\d+)\.\s*([A-ZČŠŽĆĐa-zčšžćđ]+)\s+(.*)', next_line)
        if match2:
            # e.g., "2.Vesna 0 30 30 0 60" -> name part "Vesna", rest is scores
            extra_name = match2.group(2)
            rank = int(match2.group(1))
            rest_str = match2.group(3)
            # Try parsing the rest as license + scores
            parts = rest_str.split()
            if len(parts) >= num_races + 1:
                license_num = ""
                score_start = 0
                # Check for license
                if len(parts) > num_races + 1:
                    first = parts[0]
                    if re.match(r'^[A-Za-z0-9]+$', first) and (not first.isdigit() or len(first) > 3):
                        license_num = first
                        score_start = 1
                try:
                    score_parts = parts[score_start:]
                    scores = [int(score_parts[j]) for j in range(num_races)]
                    total = int(score_parts[num_races])
                    name_text = name_text + " " + extra_name
                    scores_data = {
                        "rank": rank,
                        "license": license_num,
                        "scores": scores,
                        "total": total
                    }
                except (ValueError, IndexError):
                    pass

        if not scores_data:
            return None, 0

    rank = scores_data["rank"]
    license_num = scores_data["license"]
    scores = scores_data["scores"]
    total = scores_data["total"]

    # The line after scores should be the club name
    club = ""
    lines_consumed = 2
    if idx + 2 < len(lines):
        club_line = lines[idx + 2].strip()
        if not is_score_line(club_line) and club_line not in CATEGORIES and \
           not any(w in club_line for w in SKIP_WORDS):
            club = club_line
            lines_consumed = 3

            # Handle multi-line club names
            if idx + 3 < len(lines):
                extra = lines[idx + 3].strip()
                if extra and not is_score_line(extra) and extra not in CATEGORIES and \
                   not any(w in extra for w in SKIP_WORDS) and \
                   not is_name_line(extra) and \
                   len(extra.split()) <= 2 and not any(c.isdigit() for c in extra):
                    club += " " + extra
                    lines_consumed = 4

    return {
        "rank": rank,
        "name": name_text,
        "license": license_num,
        "club": club,
        "category": category,
        "scores": scores,
        "total": total
    }, lines_consumed


class HTMLResultsParser(HTMLParser):
    """Parse rider results from the prijavim.se HTML export."""

    def __init__(self):
        super().__init__()
        self.riders = []
        self.race_dates = []
        self.race_names_from_header = []
        self.current_category = None
        self.in_td = False
        self.in_th = False
        self.in_h3 = False
        self.in_h1 = False
        self.current_row = []
        self.current_cell = ''
        self.in_row = False
        self.title = ''
        self.header_parsed = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'h1':
            self.in_h1 = True
            self.current_cell = ''
        elif tag == 'h3':
            self.in_h3 = True
            self.current_cell = ''
        elif tag == 'tr' and any('user_id' in str(a) for a in attrs):
            self.in_row = True
            self.current_row = []
            self.current_cell = ''
        elif tag == 'th' and not self.header_parsed:
            self.in_th = True
            self.current_cell = ''
        elif tag == 'td' and self.in_row:
            self.in_td = True
            self.current_cell = ''
        elif tag == 'br' and self.in_td:
            self.current_cell += '|'

    def handle_endtag(self, tag):
        if tag == 'h1':
            self.in_h1 = False
            self.title = self.current_cell.strip()
        elif tag == 'h3':
            self.in_h3 = False
            cat = self.current_cell.strip()
            if cat:
                self.current_category = cat
        elif tag == 'th' and self.in_th:
            self.in_th = False
            cell = self.current_cell.strip()
            # Detect date columns (dd.mm. format)
            date_match = re.match(r'^\s*(\d{2}\.\d{2}\.)\s*$', cell)
            if date_match:
                self.race_dates.append(date_match.group(1))
        elif tag == 'td' and self.in_row:
            self.in_td = False
            self.current_row.append(self.current_cell.strip())
        elif tag == 'tr':
            if self.in_row and self.current_row and self.current_category:
                self._process_row(self.current_row)
            self.in_row = False
            # Mark header as parsed after first category's header row
            if self.race_dates and not self.header_parsed:
                self.header_parsed = True

    def handle_data(self, data):
        if self.in_h1:
            self.current_cell += data
        elif self.in_h3:
            self.current_cell += data
        elif self.in_th:
            self.current_cell += data
        elif self.in_td:
            self.current_cell += data

    def _process_row(self, row):
        if len(row) < 4:
            return
        rank_str = row[0].replace('.', '').strip()
        if not rank_str.isdigit():
            return
        rank = int(rank_str)

        # Name|Club from second cell
        parts = row[1].split('|')
        name = parts[0].strip()
        club = parts[1].strip() if len(parts) > 1 else ''

        license_num = row[2].strip() if len(row) > 2 else ''

        # Scores + total
        score_cells = row[3:]
        scores = []
        for s in score_cells[:-1]:
            try:
                scores.append(int(s))
            except ValueError:
                scores.append(0)

        try:
            total = int(score_cells[-1]) if score_cells else 0
        except ValueError:
            total = sum(scores)

        self.riders.append({
            'rank': rank,
            'name': name,
            'license': license_num,
            'club': club,
            'category': self.current_category,
            'scores': scores,
            'total': total
        })


def parse_html(html_path):
    """Parse cycling results from an HTML file exported from prijavim.se."""
    with open(html_path, encoding='utf-8') as f:
        html_content = f.read()

    parser = HTMLResultsParser()
    parser.feed(html_content)

    # Extract race names from the HTML header tooltips
    race_names = []
    # Look for show_me divs that contain race names
    for m in re.finditer(r'id="show_me\d+"[^>]*>(.*?)</div>', html_content):
        name = m.group(1).strip()
        # Remove year suffix
        name = re.sub(r'\s*20\d{2}\s*$', '', name).strip()
        race_names.append(name)
        if len(race_names) >= len(parser.race_dates):
            break

    # If we didn't find tooltip names, leave empty
    if len(race_names) != len(parser.race_dates):
        race_names = []

    title = parser.title
    num_races = len(parser.race_dates)

    print(f"  HTML: {len(parser.riders)} riders, {num_races} races")
    return parser.riders, title, parser.race_dates, num_races, race_names


def extract_race_names_from_table(pdf):
    """Extract race names using pdfplumber table extraction on the first page.
    Returns (dates, names) lists or ([], []) if extraction fails.
    """
    page = pdf.pages[0]
    tables = page.extract_tables()
    if not tables or len(tables[0]) < 2:
        return [], []

    header_row = tables[0][0]  # Race name cells
    date_row = tables[0][1]    # Tekmovalec, Licenca, dates..., Skupaj

    dates = []
    names = []
    for i, cell in enumerate(date_row):
        if cell and re.match(r'^\d{2}\.\d{2}\.$', cell.strip()):
            date = cell.strip()
            name = header_row[i] if i < len(header_row) and header_row[i] else ''
            # Clean up: remove newlines, year references, trailing junk
            name = name.replace('\n', ' ')
            name = re.sub(r'\s*20\d{2}\.{0,3}\s*', ' ', name)
            name = re.sub(r'\s+', ' ', name).strip()
            name = name.strip('- .')
            dates.append(date)
            names.append(name)

    return dates, names


def parse_pdf(pdf_path):
    """Parse a cycling results PDF, auto-detecting race count."""
    with pdfplumber.open(pdf_path) as pdf:
        # Extract race names from table structure
        table_dates, table_names = extract_race_names_from_table(pdf)

        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    # Detect title (second line usually)
    text_lines = full_text.split("\n")
    title = ""
    for tl in text_lines[:5]:
        tl = tl.strip()
        if tl.startswith("OMR"):
            title = tl
            break

    lines = full_text.split("\n")
    results = []
    current_category = None
    num_races = 0
    race_dates = []
    all_race_dates = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        cat = detect_category(line)
        if cat:
            current_category = cat
            i_next, nr, dates = skip_header_block(lines, i)
            if nr > 0:
                num_races = nr
                race_dates = dates
                if not all_race_dates:
                    all_race_dates = dates
            i = i_next
            continue

        if current_category and num_races > 0:
            rider, advance = try_parse_rider(lines, i, current_category, num_races)
            if rider:
                results.append(rider)
                i += advance
                continue

            # Fallback: try parsing garbled single-line entries
            # (e.g., 2023 TT where name+club+scores are on one line)
            if re.match(r'^\d+\.', line):
                single = try_parse_single_line(line, num_races)
                if single:
                    single["category"] = current_category
                    results.append(single)
                    i += 1
                    continue

        i += 1

    # Use table-extracted names, falling back to dates-only
    race_names = table_names if table_names else []

    return results, title, all_race_dates, num_races, race_names


def best_of_sum(scores, n):
    """Return sum of the top n scores."""
    sorted_scores = sorted(scores, reverse=True)
    return sum(sorted_scores[:n])


def best_of_indices(scores, n):
    """Return indices of the top n scores (for highlighting in UI)."""
    indexed = [(s, i) for i, s in enumerate(scores)]
    indexed.sort(key=lambda x: -x[0])
    return [i for s, i in indexed[:n] if s > 0]


def get_rider_key(rider):
    """Generate matching keys for a rider."""
    keys = []
    if rider["license"]:
        keys.append(("lic", rider["category"], rider["license"]))
    keys.append(("name", rider["category"], normalize_name(rider["name"])))
    return keys


def process_year(year, year_config, disciplines, output_path):
    """Process all disciplines for a single year and write output JSON."""
    print(f"\n{'='*50}")
    print(f"Processing year {year}")
    print(f"{'='*50}")

    disciplines_data = []
    all_parsed = {}

    for disc in disciplines:
        disc_id = disc["id"]
        if disc_id not in year_config:
            print(f"  Skipping {disc['name']} (not configured for {year})")
            continue

        disc_year = year_config[disc_id]

        # Try HTML first (more accurate), fall back to PDF
        html_file = disc_year.get("html", "")
        pdf_path = f"pdfs/{year}/{disc_year['pdf']}"
        if not html_file:
            # Auto-detect: look for .html files in the year folder
            year_dir = f"pdfs/{year}"
            pdf_base = os.path.splitext(disc_year['pdf'])[0]
            # Try exact match first, then prefix match
            for candidate_name in [f"{pdf_base}.html"]:
                if os.path.exists(os.path.join(year_dir, candidate_name)):
                    html_file = candidate_name
                    break
            if not html_file:
                # Try matching by disc_id (e.g., "climb.html" for disc "climbs")
                for f_name in os.listdir(year_dir) if os.path.isdir(year_dir) else []:
                    if f_name.endswith('.html') and disc_id.rstrip('s') in f_name.lower():
                        html_file = f_name
                        break
        html_path = f"pdfs/{year}/{html_file}" if html_file else ""

        riders = None
        if html_path and os.path.exists(html_path):
            print(f"Parsing {disc['name']} from {html_path}...")
            try:
                riders, title, race_dates, num_races, extracted_names = parse_html(html_path)
            except Exception as e:
                print(f"  WARNING: HTML parse failed ({e}), falling back to PDF")
                riders = None

        if riders is None:
            print(f"Parsing {disc['name']} from {pdf_path}...")
            try:
                riders, title, race_dates, num_races, extracted_names = parse_pdf(pdf_path)
            except FileNotFoundError:
                print(f"  WARNING: {pdf_path} not found, skipping")
                continue

        # Config race names override extracted ones if provided
        config_names = disc_year.get("raceNames", [])
        races = []
        for i, d in enumerate(race_dates):
            if config_names and i < len(config_names) and config_names[i]:
                name = config_names[i]
            elif i < len(extracted_names) and extracted_names[i]:
                name = extracted_names[i]
            else:
                name = f"Race {i+1}"
            races.append({"date": d, "name": name})

        disciplines_data.append({
            "id": disc_id,
            "name": disc["name"],
            "title": title,
            "bestOf": disc_year["bestOf"],
            "bestOfGeneral": disc_year["bestOfGeneral"],
            "races": races,
            "numRaces": num_races
        })

        all_parsed[disc_id] = riders

        cats = {}
        for r in riders:
            cats.setdefault(r["category"], []).append(r)
        print(f"  {len(riders)} riders across {len(cats)} categories")

    if not all_parsed:
        print(f"  No data found for {year}, skipping output")
        return False

    # Merge riders across disciplines
    key_to_merged_id = {}
    merged_riders = []
    rider_club_counts = {}  # id -> {club: count} for resolving best club
    next_id = 0

    for disc_id, riders in all_parsed.items():
        disc_year = year_config[disc_id]
        disc_meta = next(d for d in disciplines_data if d["id"] == disc_id)

        for rider in riders:
            rider["club"] = normalize_club(rider["club"])
            keys = get_rider_key(rider)

            existing_id = None
            for k in keys:
                if k in key_to_merged_id:
                    existing_id = key_to_merged_id[k]
                    break

            if existing_id is not None:
                mr = merged_riders[existing_id]
                if rider["license"] and not mr["license"]:
                    mr["license"] = rider["license"]
            else:
                existing_id = next_id
                next_id += 1
                mr = {
                    "id": existing_id,
                    "name": rider["name"],
                    "license": rider["license"],
                    "club": rider["club"],
                    "category": rider["category"],
                    "disciplines": {}
                }
                rider_club_counts[existing_id] = {}
                merged_riders.append(mr)

            # Track club appearances to pick the most common one
            if rider["club"]:
                counts = rider_club_counts[existing_id]
                counts[rider["club"]] = counts.get(rider["club"], 0) + 1

            for k in keys:
                key_to_merged_id[k] = existing_id

            best_of_n = disc_year["bestOf"]
            best_of_gen = disc_year["bestOfGeneral"]
            mr["disciplines"][disc_id] = {
                "scores": rider["scores"],
                "total": rider["total"],
                "bestOfTotal": best_of_sum(rider["scores"], best_of_n),
                "bestOfGeneralTotal": best_of_sum(rider["scores"], best_of_gen),
                "bestOfIndices": best_of_indices(rider["scores"], best_of_n),
                "bestOfGeneralIndices": best_of_indices(rider["scores"], best_of_gen),
                "rank": rider["rank"]
            }

    # Resolve club: each rider gets the most common club name across disciplines
    # On tie, prefer the club with the most total riders (more established club)
    global_club_sizes = {}
    for mr in merged_riders:
        counts = rider_club_counts.get(mr["id"], {})
        for club in counts:
            global_club_sizes[club] = global_club_sizes.get(club, 0) + 1

    for mr in merged_riders:
        counts = rider_club_counts.get(mr["id"], {})
        if counts:
            max_count = max(counts.values())
            # Get all clubs with the max count (tie candidates)
            candidates = [c for c, n in counts.items() if n == max_count]
            if len(candidates) == 1:
                mr["club"] = candidates[0]
            else:
                # Tie: prefer the club with more total riders globally
                mr["club"] = max(candidates, key=lambda c: global_club_sizes.get(c, 0))

    # Compute general classification
    for mr in merged_riders:
        general_total = 0
        for disc in disciplines:
            disc_id = disc["id"]
            if disc_id in mr["disciplines"]:
                general_total += mr["disciplines"][disc_id]["bestOfGeneralTotal"]
        mr["general"] = {"total": general_total, "rank": 0}

    # Compute general ranks per category
    for cat in CATEGORIES:
        cat_riders = [r for r in merged_riders if r["category"] == cat and r["general"]["total"] > 0]
        cat_riders.sort(key=lambda r: -r["general"]["total"])
        current_rank = 0
        prev_total = None
        for i, r in enumerate(cat_riders):
            if r["general"]["total"] != prev_total:
                current_rank = i + 1
            r["general"]["rank"] = current_rank
            prev_total = r["general"]["total"]

    # Compute discipline ranks per category
    for disc_meta in disciplines_data:
        disc_id = disc_meta["id"]
        for cat in CATEGORIES:
            cat_riders = [r for r in merged_riders
                          if r["category"] == cat and disc_id in r["disciplines"]]
            cat_riders.sort(key=lambda r: -r["disciplines"][disc_id]["bestOfTotal"])
            current_rank = 0
            prev_total = None
            for i, r in enumerate(cat_riders):
                t = r["disciplines"][disc_id]["bestOfTotal"]
                if t != prev_total:
                    current_rank = i + 1
                r["disciplines"][disc_id]["rank"] = current_rank
                prev_total = t

    # Build flat list of all races sorted by date (for club standings)
    all_races = []
    for disc_meta in disciplines_data:
        disc_id = disc_meta["id"]
        for i, race in enumerate(disc_meta["races"]):
            all_races.append({
                "disc_id": disc_id,
                "disc_name": disc_meta["name"],
                "race_index": i,
                "date": race["date"],
                "name": race["name"]
            })
    # Sort by date (dd.mm. format -> parse to comparable)
    def date_sort_key(r):
        parts = r["date"].replace(".", "").strip()
        if len(parts) == 4:
            return (int(parts[2:4]), int(parts[0:2]))  # month, day
        return (0, 0)
    all_races.sort(key=date_sort_key)

    # Compute club standings with flat race scores
    clubs = {}
    for mr in merged_riders:
        club = mr["club"]
        if not club:
            continue
        if club not in clubs:
            clubs[club] = {
                "name": club,
                "total": 0,
                "riderCount": 0,
                "raceScores": [0] * len(all_races),
                "riders": []
            }
        rider_all_total = 0
        for disc_id, dd in mr["disciplines"].items():
            rider_all_total += dd["total"]
            for j, s in enumerate(dd["scores"]):
                # Find the flat index for this disc+race
                for fi, fr in enumerate(all_races):
                    if fr["disc_id"] == disc_id and fr["race_index"] == j:
                        clubs[club]["raceScores"][fi] += s
                        break
        clubs[club]["total"] += rider_all_total
        clubs[club]["riderCount"] += 1
        clubs[club]["riders"].append({
            "name": mr["name"],
            "category": mr["category"],
            "total": rider_all_total
        })

    # Sort clubs by total, assign ranks
    club_list = sorted(clubs.values(), key=lambda c: -c["total"])
    current_rank = 0
    prev_total = None
    for i, c in enumerate(club_list):
        if c["total"] != prev_total:
            current_rank = i + 1
        c["rank"] = current_rank
        prev_total = c["total"]
        # Sort riders within club by contribution
        c["riders"].sort(key=lambda r: -r["total"])

    # Build club races list for UI (date-sorted, no disc_id internals)
    club_races = [{"date": r["date"], "name": r["name"], "discipline": r["disc_name"]} for r in all_races]

    # Build output
    data = {
        "disciplines": disciplines_data,
        "categories": CATEGORIES,
        "riders": merged_riders,
        "clubs": club_list,
        "clubRaces": club_races
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Merged: {len(merged_riders)} unique riders -> {output_path}")
    return True


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "data"

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    disciplines = config["disciplines"]
    years_config = config["years"]
    processed_years = []

    for year in sorted(years_config.keys(), reverse=True):
        output_path = os.path.join(output_dir, f"{year}.json")
        success = process_year(year, years_config[year], disciplines, output_path)
        if success:
            processed_years.append(year)

    # Write years index
    processed_years.sort(reverse=True)
    os.makedirs(output_dir, exist_ok=True)
    years_index_path = os.path.join(output_dir, "years.json")
    with open(years_index_path, "w", encoding="utf-8") as f:
        json.dump(processed_years, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"Done! Processed years: {', '.join(processed_years)}")
    print(f"Years index: {years_index_path}")


if __name__ == "__main__":
    main()
