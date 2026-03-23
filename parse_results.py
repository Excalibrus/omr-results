"""
Parser for OMR cycling competition results PDFs.
Reads config.json for discipline definitions, parses all PDFs,
matches riders across disciplines, computes best-of scoring,
and outputs a unified data.json.
"""
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
    # Name pattern: at least one uppercase letter followed by lowercase
    if re.match(r'^[A-ZČŠŽĆĐ][A-ZČŠŽĆĐa-zčšžćđ\s]+$', cleaned):
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


def parse_pdf(pdf_path):
    """Parse a cycling results PDF, auto-detecting race count."""
    with pdfplumber.open(pdf_path) as pdf:
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

        i += 1

    return results, title, all_race_dates, num_races


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
        pdf_path = f"pdfs/{year}/{disc_year['pdf']}"

        print(f"Parsing {disc['name']} from {pdf_path}...")
        try:
            riders, title, race_dates, num_races = parse_pdf(pdf_path)
        except FileNotFoundError:
            print(f"  WARNING: {pdf_path} not found, skipping")
            continue

        config_names = disc_year.get("raceNames", [])
        races = []
        for i, d in enumerate(race_dates):
            name = config_names[i] if i < len(config_names) else f"Race {i+1}"
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
    next_id = 0

    for disc_id, riders in all_parsed.items():
        disc_year = year_config[disc_id]
        disc_meta = next(d for d in disciplines_data if d["id"] == disc_id)

        for rider in riders:
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
                if rider["club"] and (not mr["club"] or len(rider["club"]) > len(mr["club"])):
                    mr["club"] = rider["club"]
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
                merged_riders.append(mr)

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

    # Build output
    data = {
        "disciplines": disciplines_data,
        "categories": CATEGORIES,
        "riders": merged_riders
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
