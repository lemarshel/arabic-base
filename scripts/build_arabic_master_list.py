# -*- coding: utf-8 -*-
"""
Arabic Master List Builder
- Uses Leipzig corpora (news, wikipedia, web) token frequency lists
- Lemmatizes with CAMeL Tools (MLE disambiguator)
- Deduplicates, scores, and ranks lemmas
- Outputs CSV/JSON with metadata + merged/rejected tables
"""
from __future__ import annotations

import csv
import json
import math
import re
import tarfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import os
import requests
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from camel_tools.disambig.mle import MLEDisambiguator
from camel_tools.utils.dediac import dediac_ar
from camel_tools.utils.charmap import CharMapper

BASE = Path(r"C:\Users\hp\arabic-base")
OUT_DIR = BASE / "data"
SRC_DIR = BASE / "data_sources"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SRC_DIR.mkdir(parents=True, exist_ok=True)

LEIPZIG_URLS = (
    "https://gist.githubusercontent.com/imvladikon/"
    "70c35d6b1fb83635a024751667be0112/raw/"
    "b91875489ccf7eb1ca8270e1138faf1db43952ec/leipzig_urls.json"
)

AR_RE = re.compile(r"[\u0621-\u064A]")
TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def normalize_ar(s: str) -> str:
    s = dediac_ar(s or "").strip()
    s = re.sub(r"[أإآٱ]", "ا", s)
    s = re.sub(r"ى", "ي", s)
    s = re.sub(r"ؤ", "و", s)
    s = re.sub(r"ئ", "ي", s)
    s = re.sub(r"ة", "ه", s)
    return re.sub(r"\s+", " ", s)


def is_arabic_word(s: str) -> bool:
    return bool(AR_RE.search(s or ""))


def clean_root(root: str) -> str:
    r = re.sub(r"[^\u0621-\u064A]", "", root or "")
    return r if len(r) in (3, 4) else ""

def limit_gloss(gloss: str, max_parts: int = 2) -> str:
    if not gloss:
        return ""
    g = gloss.replace("|", ";").replace(" / ", ";").replace("/", ";")
    parts = [p.strip() for p in re.split(r"[;,]", g) if p.strip()]
    if not parts:
        return gloss.strip()
    return "; ".join(parts[:max_parts])

def load_translator(engine: str = "opus"):
    if engine == "nllb":
        model_name = "facebook/nllb-200-distilled-600M"
    elif engine == "m2m100":
        model_name = "facebook/m2m100_418M"
    else:
        model_name = "Helsinki-NLP/opus-mt-ar-en"
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    return tok, model, model_name

def translate_batch(texts: List[str], tok, model, model_name: str) -> List[str]:
    if "nllb" in model_name:
        tok.src_lang = "arb_Arab"
        inputs = tok(texts, return_tensors="pt", padding=True, truncation=True)
        forced_bos = tok.convert_tokens_to_ids("eng_Latn")
        outputs = model.generate(**inputs, forced_bos_token_id=forced_bos, max_new_tokens=20)
    elif "m2m100" in model_name:
        tok.src_lang = "ar"
        inputs = tok(texts, return_tensors="pt", padding=True, truncation=True)
        forced_bos = tok.get_lang_id("en")
        outputs = model.generate(**inputs, forced_bos_token_id=forced_bos, max_new_tokens=20)
    else:
        inputs = tok(texts, return_tensors="pt", padding=True, truncation=True)
        outputs = model.generate(**inputs, max_new_tokens=20)
    return tok.batch_decode(outputs, skip_special_tokens=True)


def parse_size(size: str) -> int:
    size = (size or "").strip().upper()
    if size.endswith("K"):
        return int(float(size[:-1]) * 1_000)
    if size.endswith("M"):
        return int(float(size[:-1]) * 1_000_000)
    return int(size) if size.isdigit() else 0


def fetch_leipzig_urls() -> Dict[str, Dict]:
    return requests.get(LEIPZIG_URLS).json()


def choose_corpora(urls: Dict[str, Dict]) -> Dict[str, Dict]:
    best: Dict[str, Dict] = {}
    for key, meta in urls.items():
        if meta.get("language_short") != "ara":
            continue
        if key.startswith("ara_news"):
            group = "news"
        elif key.startswith("ara_wikipedia"):
            group = "wikipedia"
        elif key.startswith("ara_web"):
            group = "web"
        else:
            continue
        sz = parse_size(meta.get("size", "0"))
        if group not in best or sz > best[group]["size_n"]:
            meta = dict(meta)
            meta["size_n"] = sz
            best[group] = meta
    return best


def download_and_extract(meta: Dict) -> Path:
    name = meta["data_id"]
    url = meta["url"]
    tar_path = SRC_DIR / f"{name}.tar.gz"
    if not tar_path.exists():
        print(f"Downloading {name}...")
        r = requests.get(url)
        r.raise_for_status()
        tar_path.write_bytes(r.content)
    out_dir = SRC_DIR / "leipzig" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    words_path = out_dir / f"{name}-words.txt"
    if not words_path.exists():
        with tarfile.open(tar_path, "r:gz") as tar:
            for m in tar.getmembers():
                if m.name.endswith("-words.txt"):
                    tar.extract(m, out_dir)
                    extracted = out_dir / m.name
                    extracted.rename(words_path)
                    break
    return words_path


def iter_wordlist(path: Path, max_tokens: int = 60000):
    seen = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if seen >= max_tokens:
                break
            parts = line.strip().split("\t")
            if len(parts) < 3:
                continue
            word, freq = parts[1], parts[2]
            if not is_arabic_word(word):
                continue
            try:
                freq = int(freq)
            except Exception:
                continue
            seen += 1
            yield word, freq


def main():
    urls = fetch_leipzig_urls()
    corpora = choose_corpora(urls)
    print("Selected corpora:", corpora)

    disambig = MLEDisambiguator.pretrained()
    ar2bw = CharMapper.builtin_mapper("ar2bw")

    TARGET_LEMMAS = 12000
    lemma_stats: Dict[str, Dict] = {}
    merged: List[Dict] = []
    rejected: List[Dict] = []

    for group, meta in corpora.items():
        words_path = download_and_extract(meta)
        for word, freq in iter_wordlist(words_path):
            if len(lemma_stats) >= TARGET_LEMMAS:
                break
            analyses = disambig.disambiguate([word])[0].analyses
            if not analyses:
                continue
            best = analyses[0].analysis
            lemma = best.get("lemma") or best.get("lex") or word
            pos = best.get("pos") or ""
            root = clean_root(best.get("root", ""))
            lemma_norm = normalize_ar(lemma)
            if not lemma_norm:
                continue

            entry = lemma_stats.get(lemma_norm)
            if not entry:
                lemma_stats[lemma_norm] = {
                    "lemma": lemma,
                    "pos": pos,
                    "root": root,
                    "forms": set([word]),
                    "freqs": defaultdict(int),
                    "sources": set([group]),
                }
            else:
                entry["forms"].add(word)
                entry["sources"].add(group)
                if word != entry["lemma"]:
                    merged.append({"lemma": entry["lemma"], "variant": word, "reason": "same lemma"})
            lemma_stats[lemma_norm]["freqs"][group] += freq

    total_sources = max(1, len(corpora))
    candidates = []
    for lemma_norm, data in lemma_stats.items():
        total_freq = sum(data["freqs"].values())
        dispersion = len(data["sources"]) / total_sources
        pos = data["pos"]
        pos_weight = 1.0
        if pos in ("part", "prep", "conj", "prn", "det", "intj"):
            pos_weight = 1.2
        elif pos.startswith("verb") or pos == "verb":
            pos_weight = 1.1
        elif pos.startswith("noun"):
            pos_weight = 1.0
        elif pos.startswith("adj"):
            pos_weight = 0.95
        freq_score = math.log(total_freq + 1)
        usefulness = freq_score * pos_weight + dispersion * 2.0

        if data["freqs"].get("web", 0) > data["freqs"].get("news", 0) + data["freqs"].get("wikipedia", 0):
            register = "everyday"
        elif data["freqs"].get("news", 0) + data["freqs"].get("wikipedia", 0) >= data["freqs"].get("web", 0):
            register = "media"
        else:
            register = "mixed"

        candidates.append({
            "lemma": data["lemma"],
            "lemma_norm": lemma_norm,
            "pos": pos,
            "root": data["root"],
            "forms": sorted(data["forms"]),
            "total_freq": total_freq,
            "dispersion": dispersion,
            "usefulness": usefulness,
            "register": register,
            "sources": sorted(list(data["sources"]))
        })

    candidates.sort(key=lambda x: x["usefulness"], reverse=True)
    final = []
    for cand in candidates:
        if len(final) >= 4500:
            break
        if not is_arabic_word(cand["lemma"]):
            rejected.append({"item": cand["lemma"], "reason": "non-arabic"})
            continue
        final.append(cand)

    rows = []
    # Translation settings
    do_translate = os.environ.get("AR_TRANSLATE", "0") == "1"
    engine = os.environ.get("AR_MT_ENGINE", "opus")
    trans_cache_path = SRC_DIR / "arabic_master_translations.json"
    trans_cache = {}
    if trans_cache_path.exists():
        trans_cache = json.loads(trans_cache_path.read_text(encoding="utf-8"))
    tok = model = model_name = None
    if do_translate:
        tok, model, model_name = load_translator(engine)
        missing = [c["lemma"] for c in final if c["lemma"] not in trans_cache]
        batch_size = 32
        for i in range(0, len(missing), batch_size):
            batch = missing[i:i + batch_size]
            outs = translate_batch(batch, tok, model, model_name)
            for src, out in zip(batch, outs):
                trans_cache[src] = limit_gloss(out, 2)
    uncertain = []
    for idx, c in enumerate(final, start=1):
        gloss = trans_cache.get(c["lemma"], "")
        # canonical form rule (rough, based on POS)
        pos = c["pos"]
        if pos.startswith("verb") or pos == "verb":
            rule = "verb: 3ms past lemma"
        elif pos.startswith("noun") or pos == "noun":
            rule = "noun: singular lemma"
        elif pos.startswith("adj") or pos == "adj":
            rule = "adj: masc singular lemma"
        else:
            rule = "particle/base"
        if len(gloss.split()) > 3:
            uncertain.append({"lemma": c["lemma"], "reason": "long gloss", "gloss": gloss})

        rows.append({
            "rank": idx,
            "arabic_lemma": c["lemma"],
            "transliteration": ar2bw.map_string(c["lemma"]),
            "english_core_gloss": gloss,
            "part_of_speech": c["pos"],
            "canonical_form_rule_used": rule,
            "root_if_relevant": c["root"],
            "estimated_frequency_tier": "T1" if idx <= 1000 else "T2" if idx <= 2500 else "T3" if idx <= 4000 else "T4",
            "dispersion_score": round(c["dispersion"], 3),
            "usefulness_score": round(c["usefulness"], 3),
            "register_label": c["register"],
            "keep_reason": "high_frequency + dispersion",
            "merged_forms_or_variants": "; ".join(c["forms"][:5]) + (" ..." if len(c["forms"]) > 5 else ""),
            "notes": ""
        })

    csv_path = OUT_DIR / "arabic_master_list.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path = OUT_DIR / "arabic_master_list.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(OUT_DIR / "merged_duplicates.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["lemma", "variant", "reason"])
        w.writeheader(); w.writerows(merged)

    with open(OUT_DIR / "rejected_items.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item", "reason"])
        w.writeheader(); w.writerows(rejected)

    if do_translate:
        trans_cache_path.write_text(json.dumps(trans_cache, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(OUT_DIR / "uncertain_items.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["lemma", "reason", "gloss"])
        w.writeheader(); w.writerows(uncertain)

    print("Wrote", csv_path)


if __name__ == "__main__":
    main()
