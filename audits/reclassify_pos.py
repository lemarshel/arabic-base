"""
Arabic Base — POS reclassification & root normalization
-------------------------------------------------------
Uses CAMeL Tools (calima-msa-r13) to tighten POS labels
into the 3-category Arabic grammar system:
  - اسم (noun family)
  - فعل (verb family)
  - حرف (particles: prep/conj/det/etc)

Also fills root letters from the analyzer when available
and valid (3–4 Arabic consonants).

Writes back to words.js to keep the app in sync.
"""

import json
import re
from pathlib import Path

from camel_tools.data import CATALOGUE
from camel_tools.morphology.database import MorphologyDB
from camel_tools.morphology.analyzer import Analyzer
from camel_tools.utils.dediac import dediac_ar

BASE = Path(r"C:\Users\hp\arabic-base")
WORDS = BASE / "words.js"

AR_RE = re.compile(r"^[\u0621-\u064A]{3,4}$")


def ensure_morph_db():
    """Download morphology DB if missing."""
    db_entry = CATALOGUE.components["MorphologyDB"].datasets["calima-msa-r13"]
    db_path = Path(db_entry.path) / "morphology.db"
    if not db_path.exists():
        print("Downloading CAMeL Tools morphology DB...")
        CATALOGUE.download_package("morphology-db-msa-r13", print_status=True)
    return db_path


def map_pos(pos_raw: str) -> str:
    """Map CAMeL POS tag into اسم/فعل/حرف."""
    pos = (pos_raw or "").lower()
    if pos.startswith("verb"):
        return "فعل"
    # particles: prepositions, conjunctions, determiners, etc.
    if pos.startswith("part") or pos in {
        "prep", "conj", "det", "interj", "punc", "abbrev"
    }:
        return "حرف"
    # everything else defaults to "اسم"
    return "اسم"


def load_words():
    raw = WORDS.read_text(encoding="utf-8")
    data = raw.split("=", 1)[1].strip().rstrip(";")
    return json.loads(data)


def save_words(arr):
    out = "const AR_WORDS = " + json.dumps(arr, ensure_ascii=False) + ";"
    WORDS.write_text(out, encoding="utf-8")


def main():
    ensure_morph_db()
    db = MorphologyDB.builtin_db("calima-msa-r13")
    analyzer = Analyzer(db)

    words = load_words()
    changed_pos = 0
    updated_root = 0
    skipped = 0

    for w in words:
        ar = (w.get("w") or "").strip()
        if not ar:
            continue
        # Skip multi-token entries (phrases)
        if " " in ar or "ـ" in ar or "/" in ar:
            skipped += 1
            continue

        ana = analyzer.analyze(dediac_ar(ar))
        if not ana:
            continue

        best = ana[0]
        new_pos = map_pos(best.get("pos"))
        if new_pos and new_pos != w.get("pos"):
            w["pos"] = new_pos
            changed_pos += 1

        # Root update (3–4 consonants only)
        root = best.get("root")
        if root and AR_RE.match(root):
            if w.get("r") != root:
                w["r"] = root
                updated_root += 1

    save_words(words)
    print(f"POS updated: {changed_pos}")
    print(f"Root updated: {updated_root}")
    print(f"Skipped (phrases): {skipped}")


if __name__ == "__main__":
    main()
