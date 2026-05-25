#!/usr/bin/env python3
"""One-off cleanup: strip explicit / NSFW tags from danbooru_character.csv.

Reuses the EXCLUDED_WORDS blocklist + sanitize_tags() from
generate_dataset.py, so the stored metadata matches what the generator now
produces. The original CSV is preserved as danbooru_character.csv.bak.

After running this, re-run build_db.py so the database picks up the
cleaned tags:
    python webapp/build_db.py characters

Usage (run from the project folder, E:\\AnimaCharDb):
    python clean_explicit_tags.py
"""

import csv
import os
import shutil
import sys

from generate_dataset import sanitize_tags

CSV_PATH = os.environ.get("ANIMADEX_CSV", "danbooru_character.csv")


def _count(tag_string):
    return sum(1 for t in tag_string.split(",") if t.strip())


def main():
    if not os.path.isfile(CSV_PATH):
        sys.exit(f"CSV not found: {CSV_PATH}\nRun from the project folder.")

    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        fieldnames = csv.DictReader(f).fieldnames
    if not fieldnames or "core_tags" not in fieldnames:
        sys.exit("CSV has no 'core_tags' column -- nothing to do.")

    tmp = CSV_PATH + ".tmp"
    total = rows_changed = tags_removed = 0
    with open(CSV_PATH, encoding="utf-8", newline="") as src, \
         open(tmp, "w", encoding="utf-8", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        for row in csv.DictReader(src):
            total += 1
            original = row.get("core_tags") or ""
            cleaned = sanitize_tags(original)
            if cleaned != original:
                rows_changed += 1
                tags_removed += _count(original) - _count(cleaned)
                row["core_tags"] = cleaned
            writer.writerow(row)

    backup = CSV_PATH + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(CSV_PATH, backup)
        print(f"Original backed up -> {backup}")
    os.replace(tmp, CSV_PATH)

    print(f"Rows scanned     : {total}")
    print(f"Rows cleaned     : {rows_changed}")
    print(f"Explicit tags cut: {tags_removed}")
    print(f"\nDone -> {CSV_PATH}")
    print("Next:  python webapp/build_db.py characters")


if __name__ == "__main__":
    main()
