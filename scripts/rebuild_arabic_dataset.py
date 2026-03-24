"""
Arabic Base — Rebuild Pipeline (sources + translations + examples)
-----------------------------------------------------------------
Goal:
  - Replace weak translations and repetitive examples
  - Ensure full Arabic harakat (diacritics) on words + examples
  - Use higher-quality sources and structured audits

Primary source:
  - arabic_decks_arabic.json (high-quality word list with EN + examples)

Secondary sources (fallbacks):
  - Tatoeba (Arabic examples + English translations, CC-BY)
  - Cached RU/EN corrections from previous audits

Translation fallback:
  - OPUS-MT (Helsinki-NLP) English→Russian (only if cache missing)

Diacritics:
  - CAMeL Tools MLE disambiguator (calima-msa-r13)

Outputs:
  - words.js (AR_WORDS)
"""

from __future__ import annotations

import json
import os
import re
import tarfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import requests
from tqdm import tqdm

# wordfreq (kept as optional fallback if deck missing)
from wordfreq import top_n_list

# CAMeL Tools
from camel_tools.data import CATALOGUE
from camel_tools.disambig.mle import MLEDisambiguator
from camel_tools.tokenizers.word import simple_word_tokenize
from camel_tools.utils.dediac import dediac_ar

# Transformers (OPUS-MT)
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import torch
torch.set_num_threads(2)


BASE = Path(r"C:\Users\hp\arabic-base")
OUT_WORDS = BASE / "words.js"
SRC_DIR = BASE / "data_sources"
SRC_DIR.mkdir(parents=True, exist_ok=True)

TATOEBA_SENT = SRC_DIR / "sentences.csv"
TATOEBA_LINKS = SRC_DIR / "links.csv"
TATOEBA_SENT_TAR = SRC_DIR / "sentences.tar.bz2"
TATOEBA_LINKS_TAR = SRC_DIR / "links.tar.bz2"

CACHE_EXAMPLES = SRC_DIR / "tatoeba_examples.json"
CACHE_DIAC = SRC_DIR / "diac_cache.json"
CACHE_EN_RU = SRC_DIR / "en_ru_cache.json"
CACHE_AR_EN = SRC_DIR / "ar_en_cache.json"

# Primary deck source (preferred)
DECK_JSON = SRC_DIR / "arabic_decks_arabic.json"
DECK_JSON_FALLBACK = Path(r"C:\Users\hp\Downloads\arabic_decks_arabic.json")

# Optional caches from previous runs
CACHE_RU_WORD_FALLBACK = Path(r"C:\Users\hp\Downloads\OUR_ARABIC_v3\cache_ru.json")
CACHE_EN_WORD_FALLBACK = Path(r"C:\Users\hp\Downloads\OUR_ARABIC_v3\cache_en.json")
CACHE_RU_BY_EN_FALLBACK = Path(r"C:\Users\hp\Downloads\OUR_ARABIC_v3\cache_word_ru.json")
CACHE_EX_RU_FALLBACK = Path(r"C:\Users\hp\Downloads\OUR_ARABIC_v3\cache_example_ru.json")
CACHE_CHATGPT_FIX_FALLBACK = Path(r"C:\Users\hp\Downloads\OUR_ARABIC_v3\cache_chatgpt_fix.json")
BASE_WORDS_FALLBACK = Path(r"C:\Users\hp\Downloads\OUR_ARABIC_v3\words_raw.json")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
AR_RE = re.compile(r"[\u0621-\u064A]")
CYR_RE = re.compile(r"[\u0400-\u04FF]")
TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def normalize_ar(s: str) -> str:
    s = dediac_ar(s or "").strip()
    s = re.sub(r"[أإآٱ]", "ا", s)
    s = re.sub(r"ى", "ي", s)
    s = re.sub(r"ؤ", "و", s)
    s = re.sub(r"ئ", "ي", s)
    s = re.sub(r"ة", "ه", s)
    return re.sub(r"\s+", " ", s)

def strip_tashkeel(s: str) -> str:
    return TASHKEEL_RE.sub("", s or "")


def normalize_token(s: str) -> str:
    return normalize_ar(s).replace(" ", "")


def is_arabic_word(w: str) -> bool:
    return bool(AR_RE.search(w))

def has_tashkeel(s: str) -> bool:
    return bool(TASHKEEL_RE.search(s or ""))

def limit_gloss(gloss: str, max_parts: int = 2) -> str:
    """Keep only the first 1–2 short translations."""
    if not gloss:
        return ""
    # normalize separators
    g = gloss.replace("|", ";").replace(" / ", ";").replace("/", ";")
    parts = [p.strip() for p in re.split(r"[;,]", g) if p.strip()]
    if not parts:
        return gloss.strip()
    return "; ".join(parts[:max_parts])

def clean_root(root: str) -> str:
    r = strip_tashkeel(root or "")
    r = re.sub(r"[^\u0621-\u064A]", "", r)
    return r if len(r) in (3,4) else ""

# Manual RU fixes for stubborn proper nouns / loanwords
RU_OVERRIDES = {
    "Sufi": "суфи",
    "pâté": "паштет",
    "pate": "паштет",
    "ember; live coal": "уголь; угольок",
    "commando; freedom fighter": "коммандос; боец сопротивления",
    "matters": "дела; вопросы",
    "sect; group": "секта; группа",
    "network; mesh": "сеть; сетка",
}

# Word-specific overrides (normalized Arabic -> RU)
RU_WORD_OVERRIDES = {
    "الامور": "дела; вопросы",
    "طائفة": "секта; группа",
    "شبك": "сеть; сетка",
}

# Word-level diacritics fixes
WORD_DIAC_OVERRIDES = {
    "آه": "آهٍ",  # interjection with tanwin
}

def load_json_any(paths: List[Path]) -> Dict:
    for p in paths:
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def strip_prefixes(tok: str) -> List[str]:
    """Return token variants with common clitic prefixes stripped."""
    variants = {tok}
    prefixes = ["وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ك", "ل"]
    for p in prefixes:
        if tok.startswith(p) and len(tok) > len(p) + 1:
            variants.add(tok[len(p):])
    return list(variants)


def download_file(url: str, dst: Path):
    if dst.exists():
        return
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    with open(dst, "wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=dst.name) as pbar:
        for chunk in resp.iter_content(chunk_size=1 << 15):
            if chunk:
                f.write(chunk)
                pbar.update(len(chunk))


def extract_tar_bz2(tar_path: Path, out_dir: Path):
    with tarfile.open(tar_path, "r:bz2") as tar:
        tar.extractall(out_dir)


# ---------------------------------------------------------------------------
# Tatoeba Examples
# ---------------------------------------------------------------------------
def ensure_tatoeba():
    # Tatoeba exports
    download_file("https://downloads.tatoeba.org/exports/sentences.tar.bz2", TATOEBA_SENT_TAR)
    download_file("https://downloads.tatoeba.org/exports/links.tar.bz2", TATOEBA_LINKS_TAR)
    if not TATOEBA_SENT.exists():
        extract_tar_bz2(TATOEBA_SENT_TAR, SRC_DIR)
    if not TATOEBA_LINKS.exists():
        extract_tar_bz2(TATOEBA_LINKS_TAR, SRC_DIR)


def build_tatoeba_examples(target_words: List[str],
                           max_sentences: int = 120000,
                           per_word: int = 1) -> Dict[str, Tuple[str, str]]:
    """
    Build word -> (ar_sentence, en_sentence) mapping from Tatoeba.
    Only keeps at most `per_word` sentence per word.
    """
    if CACHE_EXAMPLES.exists():
        try:
            cached = json.loads(CACHE_EXAMPLES.read_text(encoding="utf-8"))
            if cached:
                return cached
        except Exception:
            pass

    targets = {normalize_token(w): w for w in target_words}
    wanted = set(targets.keys())

    ar_map: Dict[str, str] = {}
    word_to_arid: Dict[str, str] = {}
    ar_needed = set()

    # Pass 1: Arabic sentences (limited by Arabic count, not line count)
    ara_count = 0
    with open(TATOEBA_SENT, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            sid, lang, text = parts[0], parts[1], parts[2]
            if lang != "ara":
                continue
            ara_count += 1
            if ara_count > max_sentences:
                break
            tokens = [normalize_token(t) for t in simple_word_tokenize(text) if is_arabic_word(t)]
            uniq = set()
            for tok in tokens:
                for v in strip_prefixes(tok):
                    uniq.add(v)
            hit = False
            for tok in uniq:
                if tok in wanted and tok not in word_to_arid:
                    word_to_arid[tok] = sid
                    hit = True
            if hit:
                ar_map[sid] = text
                ar_needed.add(sid)
            if len(word_to_arid) >= int(len(wanted) * 0.85):
                # coverage good enough
                break

    # Pass 2: Links (find English ids)
    ar_to_en: Dict[str, str] = {}
    with open(TATOEBA_LINKS, encoding="utf-8") as f:
        for line in f:
            a, b = line.rstrip("\n").split("\t")
            if a in ar_needed:
                ar_to_en[a] = b
            elif b in ar_needed:
                ar_to_en[b] = a
            if len(ar_to_en) >= len(ar_needed):
                break

    # Pass 3: English sentences for mapped ids
    en_ids = set(ar_to_en.values())
    en_map: Dict[str, str] = {}
    with open(TATOEBA_SENT, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            sid, lang, text = parts[0], parts[1], parts[2]
            if lang == "eng" and sid in en_ids:
                en_map[sid] = text
                if len(en_map) >= len(en_ids):
                    break

    # Build word -> (ar, en)
    out: Dict[str, Tuple[str, str]] = {}
    for tok, ar_id in word_to_arid.items():
        ar_sent = ar_map.get(ar_id, "")
        en_id = ar_to_en.get(ar_id)
        en_sent = en_map.get(en_id, "") if en_id else ""
        if ar_sent and en_sent:
            out[targets[tok]] = (ar_sent, en_sent)

    CACHE_EXAMPLES.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Diacritizer (CAMeL Tools MLE)
# ---------------------------------------------------------------------------
def ensure_diacritizer():
    # Download disambiguator model if missing
    CATALOGUE.download_package("disambig-mle-calima-msa-r13", print_status=True)


def diacritize_sentence(disambig: MLEDisambiguator, sentence: str) -> str:
    tokens = simple_word_tokenize(sentence)
    dis = disambig.disambiguate(tokens)
    out = []
    for d in dis:
        if not d.analyses:
            out.append(d.word)
            continue
        out.append(d.analyses[0].diac)
    # Re-join tokens with spaces (simple heuristic)
    text = " ".join(out)
    text = re.sub(r"\s+([،\.\!\?؟\:;])", r"\1", text)
    return text


# ---------------------------------------------------------------------------
# Translation models (OPUS-MT)
# ---------------------------------------------------------------------------
def load_mt(model_name: str):
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.eval()
    return tok, model


def translate_batch(texts: List[str], tok, model, max_len=128) -> List[str]:
    if not texts:
        return []
    with torch.no_grad():
        enc = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_len)
        gen = model.generate(**enc, max_new_tokens=128)
        out = tok.batch_decode(gen, skip_special_tokens=True)
    return out

def translate_in_batches(texts: List[str], tok, model, batch_size=64, max_len=128) -> List[str]:
    out = []
    for i in tqdm(range(0, len(texts), batch_size), desc="translate", unit="batch"):
        chunk = texts[i:i+batch_size]
        out.extend(translate_batch(chunk, tok, model, max_len=max_len))
    return out


def load_cache(path: Path) -> Dict[str, str]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(path: Path, data: Dict[str, str]):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Deck loader (preferred source)
# ---------------------------------------------------------------------------
def load_deck_entries() -> List[Dict]:
    deck_path = DECK_JSON if DECK_JSON.exists() else DECK_JSON_FALLBACK
    if not deck_path.exists():
        return []
    data = json.loads(deck_path.read_text(encoding="utf-8"))
    out = []
    seen = set()
    for d in data:
        if not d.get("useful_for_flashcard", True):
            continue
        word = (d.get("diacritized_word") or "").strip()
        if not word or not is_arabic_word(word):
            continue
        # exclude multi-token phrases
        if len(simple_word_tokenize(word)) > 1:
            continue
        key = normalize_token(word)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    # lowest frequency rank first
    out.sort(key=lambda x: x.get("word_frequency", 10**9))
    return out


def load_base_entries() -> List[Dict]:
    """Load a stable 4.5k base list if present (words_raw.json)."""
    if BASE_WORDS_FALLBACK.exists():
        try:
            data = json.loads(BASE_WORDS_FALLBACK.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Main rebuild
# ---------------------------------------------------------------------------
def rebuild():
    # Config via environment variables (for incremental runs)
    target_n = int(os.environ.get("AR_TARGET", "4500"))
    max_sent = int(os.environ.get("AR_MAX_SENT", "50000"))
    stage = os.environ.get("AR_STAGE", "full").lower().strip()
    use_tatoeba = os.environ.get("AR_TATOEBA", "1").lower() not in ("0","false","no")
    if use_tatoeba:
        ensure_tatoeba()
    ensure_diacritizer()

    # 1) Load sources
    deck = load_deck_entries()  # enriched source with EN + examples
    deck_map = {normalize_token(d.get("diacritized_word","")): d for d in deck}

    base_entries = load_base_entries()
    if base_entries:
        base_entries = base_entries[:target_n]
    else:
        # Build a base list from deck or wordfreq if needed
        if deck:
            base_entries = []
            for i, d in enumerate(deck[:target_n]):
                base_entries.append({
                    "w": d.get("diacritized_word",""),
                    "r": d.get("root_word",""),
                    "pl": "",
                    "en": d.get("english_translation",""),
                    "ru": "",
                    "xa": d.get("example_sentence_native",""),
                    "xe": d.get("example_sentence_english",""),
                    "xr": "",
                    "tier": 1 + (i // max(1, target_n // 7)),
                    "level": 1 + (i // max(1, target_n // 7)),
                    "pos": d.get("pos","")
                })
        else:
            # Fallback to wordfreq if deck is missing
            words = top_n_list("ar", 6000)[:target_n]
            base_entries = [{
                "w": w, "r":"", "pl":"", "en":"", "ru":"",
                "xa":"", "xe":"", "xr":"",
                "tier": 1 + (i // max(1, target_n // 7)),
                "level": 1 + (i // max(1, target_n // 7)),
                "pos":""
            } for i, w in enumerate(words)]

    cleaned_words = [e.get("w","").strip() for e in base_entries]

    # 3) Build example map (word -> (ar_sentence, en_sentence))
    example_map = {}
    if use_tatoeba:
        example_map = build_tatoeba_examples(cleaned_words, max_sentences=max_sent)
        print(f"Examples cached: {len(example_map)}")
    if stage == "examples":
        return

    # 4) Load caches
    ru_by_word = load_json_any([SRC_DIR / "cache_ru.json", CACHE_RU_WORD_FALLBACK])
    en_by_word = load_json_any([SRC_DIR / "cache_en.json", CACHE_EN_WORD_FALLBACK])
    ru_by_en   = load_json_any([SRC_DIR / "cache_word_ru.json", CACHE_RU_BY_EN_FALLBACK])
    ru_by_ex   = load_json_any([SRC_DIR / "cache_example_ru.json", CACHE_EX_RU_FALLBACK])
    chatgpt_fix = load_json_any([SRC_DIR / "cache_chatgpt_fix.json", CACHE_CHATGPT_FIX_FALLBACK])

    # normalize cache keys
    ru_by_word_norm = {normalize_ar(k): v for k, v in ru_by_word.items()}
    en_by_word_norm = {normalize_ar(k): v for k, v in en_by_word.items()}

    # 5) Diacritizer
    disambig = MLEDisambiguator.pretrained("calima-msa-r13")
    cache_diac = load_cache(CACHE_DIAC)

    # 6) MT fallback (EN->RU) only if needed
    use_mt = os.environ.get("AR_USE_MT", "1").lower() not in ("0", "false", "no")
    tok_en_ru = model_en_ru = None

    def ensure_mt():
        nonlocal tok_en_ru, model_en_ru
        if tok_en_ru is None or model_en_ru is None:
            tok_en_ru, model_en_ru = load_mt("Helsinki-NLP/opus-mt-en-ru")

    def maybe_translate_en_ru(texts: List[str]) -> List[str]:
        if not texts:
            return []
        ensure_mt()
        return translate_in_batches(texts, tok_en_ru, model_en_ru, batch_size=32)

    def match_fix_key(word: str, root: str, pos: str) -> str:
        root_letters = normalize_ar(root)
        root_key = "-".join(list(root_letters)) if root_letters else ""
        return f"{normalize_ar(word)}|{root_key}|{pos}"

    # 7) Build entries
    entries = []
    used_examples = set()
    pending_word_en = set()
    pending_ex_en = set()

    for i, base in enumerate(base_entries):
        word = (base.get("w") or "").strip()
        if not word:
            continue
        base_norm = normalize_token(word)
        d = deck_map.get(base_norm, {})

        root = clean_root((base.get("r") or "").strip() or (d.get("root_word") or "").strip())
        pos_raw = (d.get("pos") or base.get("pos") or "").strip().lower()

        # Map POS to Arabic category
        if "فعل" in pos_raw or pos_raw.startswith("verb"):
            pos = "فعل"
        elif "حرف" in pos_raw or pos_raw in {"conjunction", "preposition", "particle", "interjection", "determiner"}:
            pos = "حرف"
        else:
            pos = "اسم"

        # English gloss
        en = limit_gloss(d.get("english_translation") or base.get("en") or "")
        if not en:
            en = limit_gloss(en_by_word_norm.get(normalize_ar(word), ""))

        # Example sentences
        ex_ar = (d.get("example_sentence_native") or base.get("xa") or "").strip()
        ex_en = (d.get("example_sentence_english") or base.get("xe") or "").strip()

        # Ensure example contains word; fallback to Tatoeba
        if ex_ar:
            if normalize_token(word) not in normalize_token(ex_ar):
                ex_ar = ""
                ex_en = ""
        if not ex_ar and word in example_map:
            ex_ar, ex_en = example_map[word]

        # Fallback template if still empty
        if not ex_ar:
            ex_ar = f"أُحِبُّ {word}."
            ex_en = f"I like {en or 'it'}."

        # De-duplicate example usage
        if ex_ar in used_examples and word in example_map:
            ex_ar, ex_en = example_map.get(word, (ex_ar, ex_en))
        used_examples.add(ex_ar)

        # Diacritize only if needed
        if not has_tashkeel(word):
            if word in cache_diac:
                word_diac = cache_diac[word]
            else:
                word_diac = diacritize_sentence(disambig, word)
                cache_diac[word] = word_diac
        else:
            word_diac = word
        # Manual diacritics override (rare)
        base_word_key = strip_tashkeel(word_diac)
        if base_word_key in WORD_DIAC_OVERRIDES:
            word_diac = WORD_DIAC_OVERRIDES[base_word_key]

        if not has_tashkeel(ex_ar):
            if ex_ar in cache_diac:
                ex_ar_diac = cache_diac[ex_ar]
            else:
                ex_ar_diac = diacritize_sentence(disambig, ex_ar)
                cache_diac[ex_ar] = ex_ar_diac
        else:
            ex_ar_diac = ex_ar

        # Russian translations
        norm_word = normalize_ar(word)
        ru = RU_WORD_OVERRIDES.get(norm_word, "") or ru_by_word_norm.get(norm_word, "")
        if not ru:
            ru = ru_by_en.get(en, "")
        # if non-cyrillic or junk, re-translate later
        if ru and (not CYR_RE.search(ru) or "<" in ru or "&lt" in ru):
            ru = ""
        if not ru and en:
            # manual overrides first
            if en in RU_OVERRIDES:
                ru = RU_OVERRIDES[en]
            else:
                pending_word_en.add(en)
        ru = limit_gloss(ru)

        ex_ru = ru_by_ex.get(ex_en, "")
        if ex_ru and (not CYR_RE.search(ex_ru) or "<" in ex_ru or "&lt" in ex_ru):
            ex_ru = ""
        if not ex_ru and ex_en:
            pending_ex_en.add(ex_en)

        # ChatGPT fixes (optional overrides)
        fix_key = match_fix_key(word, root, pos)
        if fix_key in chatgpt_fix:
            fix = chatgpt_fix[fix_key]
            en = fix.get("en", en) or en
            fix_ru = fix.get("ru", "")
            if fix_ru and CYR_RE.search(fix_ru):
                ru = fix_ru
            if fix.get("ex_ar"):
                ex_ar_diac = fix.get("ex_ar")
            if fix.get("ex_en"):
                ex_en = fix.get("ex_en")
            if fix.get("ex_ru"):
                ex_ru = fix.get("ex_ru")

        tier = base.get("tier") if isinstance(base.get("tier"), int) else 1 + (i // max(1, target_n // 7))
        level = base.get("level") if isinstance(base.get("level"), int) else tier
        entry = {
            "w": word_diac,
            "r": root,
            "pl": "",
            "en": en,
            "ru": ru,
            "xa": ex_ar_diac,
            "xe": ex_en,
            "xr": ex_ru,
            "tier": tier,
            "level": level,
            "pos": pos
        }
        entries.append(entry)

    # 8) MT fallback in batches (only for missing)
    if use_mt:
        if pending_word_en:
            missing = [t for t in pending_word_en if t not in ru_by_en]
            if missing:
                translated = maybe_translate_en_ru(missing)
                for src, tgt in zip(missing, translated):
                    ru_by_en[src] = tgt
        if pending_ex_en:
            missing_ex = [t for t in pending_ex_en if t not in ru_by_ex]
            if missing_ex:
                translated = maybe_translate_en_ru(missing_ex)
                for src, tgt in zip(missing_ex, translated):
                    ru_by_ex[src] = tgt

    # 9) Fill missing RU strings now that caches are updated
    for e in entries:
        if not (e.get("ru") or "").strip():
            en_val = e.get("en","")
            ru_val = RU_OVERRIDES.get(en_val, ru_by_en.get(en_val, ""))
            if ru_val and (not CYR_RE.search(ru_val) or "<" in ru_val or "&lt" in ru_val):
                ru_val = ""
            e["ru"] = limit_gloss(ru_val)
        if not (e.get("xr") or "").strip():
            e["xr"] = ru_by_ex.get(e.get("xe",""), "")

    save_cache(CACHE_EN_RU, ru_by_en)
    save_cache(CACHE_DIAC, cache_diac)

    # Save
    OUT_WORDS.write_text("const AR_WORDS = " + json.dumps(entries, ensure_ascii=False) + ";", encoding="utf-8")
    print("Rebuild complete. Words:", len(entries))


if __name__ == "__main__":
    rebuild()
