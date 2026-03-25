"""
Arabic Base — Rebuild Pipeline (master list + translations + examples)
---------------------------------------------------------------------
Goal:
  - Replace weak translations and repetitive examples
  - Ensure full Arabic harakat (diacritics) on words + examples
  - Use higher-quality sources and structured audits

Primary source:
  - data/arabic_master_list.csv (lemma-based 4.5k list)

Secondary sources:
  - Kaikki.org Arabic Wiktionary dump (glosses + POS hints)
  - Tatoeba (Arabic examples + English translations, CC-BY)

Translation fallback:
  - OPUS-MT (ar→en, en→ru)
  - NLLB / M2M100 (optional, heavier)

Diacritics:
  - CAMeL Tools MLE disambiguator (calima-msa-r13)

Outputs:
  - words.js (AR_WORDS)
  - data/arabic_master_list_enriched.csv (optional)
"""

from __future__ import annotations

import csv
import json
import os
import re
import tarfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import requests
from tqdm import tqdm

# wordfreq (kept as optional fallback if deck missing)
from wordfreq import top_n_list, zipf_frequency

# CAMeL Tools
from camel_tools.data import CATALOGUE
from camel_tools.disambig.mle import MLEDisambiguator
from camel_tools.tokenizers.word import simple_word_tokenize
from camel_tools.utils.dediac import dediac_ar

# Transformers (NLLB / M2M100 / OPUS-MT)
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import torch
torch.set_num_threads(2)


BASE = Path(r"C:\Users\hp\arabic-base")
OUT_WORDS = BASE / "words.js"
SRC_DIR = BASE / "data_sources"
SRC_DIR.mkdir(parents=True, exist_ok=True)
MASTER_LIST_CSV = BASE / "data" / "arabic_master_list.csv"
MASTER_LIST_ENRICHED = BASE / "data" / "arabic_master_list_enriched.csv"

KAIKKI_JSONL = SRC_DIR / "kaikki_arabic.jsonl"
KAIKKI_URL = "https://kaikki.org/dictionary/Arabic/kaikki.org-dictionary-Arabic.jsonl"

TATOEBA_SENT = SRC_DIR / "sentences.csv"
TATOEBA_LINKS = SRC_DIR / "links.csv"
TATOEBA_SENT_TAR = SRC_DIR / "sentences.tar.bz2"
TATOEBA_LINKS_TAR = SRC_DIR / "links.tar.bz2"

CACHE_EXAMPLES = SRC_DIR / "tatoeba_examples.json"
CACHE_DIAC = SRC_DIR / "diac_cache.json"
CACHE_EN_RU = SRC_DIR / "en_ru_cache.json"
CACHE_AR_EN = SRC_DIR / "ar_en_cache.json"

# Legacy deck source (optional fallback)
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

# Short manual glosses for function words (override when present)
MANUAL_GLOSS_RAW = {
    "في": "in",
    "من": "from",
    "على": "on",
    "إلى": "to",
    "عن": "about",
    "مع": "with",
    "حتى": "until",
    "إلا": "except",
    "حيث": "where",
    "منذ": "since",
    "هذا": "this",
    "هذه": "this",
    "ذلك": "that",
    "تلك": "that",
    "الذي": "who/that",
    "التي": "who/that",
    "ما": "what/that",
    "لا": "no; not",
    "بل": "but; rather",
    "قد": "already; has",
    "كان": "was",
    "ليس": "is not",
    "لم": "did not",
    "لن": "will not",
    "سوف": "will",
    "ثم": "then",
    "إذا": "if; when",
    "أو": "or",
    "و": "and",
    "أم": "or (choice)",
    "كل": "all; every",
    "أي": "which; any",
    "هو": "he",
    "هي": "she",
    "هم": "they",
    "نحن": "we",
    "أنت": "you",
    "أنا": "I",
    "هنا": "here",
    "هناك": "there",
    "الآن": "now",
    "بعد": "after",
    "قبل": "before",
    "فوق": "above",
    "تحت": "under",
    "بين": "between",
    "عند": "at",
    "لدى": "at; with",
    "بدون": "without",
    "نعم": "yes",
    "ربما": "maybe",
    "لأن": "because",
    "لكن": "but",
    "إذ": "when; since",
    "كي": "so that",
    "لعل": "perhaps",
    "يا": "O! (vocative)",
    "ل": "to; for",
}
MANUAL_GLOSS = {normalize_ar(k): v for k, v in MANUAL_GLOSS_RAW.items()}

# Manual POS override for core particles/prepositions/conjunctions
MANUAL_POS_HARF = {normalize_ar(x) for x in [
    "في","من","على","إلى","عن","مع","حتى","إلا","حيث","منذ","لا","بل","قد","ليس",
    "لم","لن","سوف","ثم","إذا","أو","و","أم","لأن","لكن","إذ","كي","لعل","يا","ل",
    "بعد","قبل","فوق","تحت","بين","عند","لدى","بدون"
]}

# Manual examples for frequent function words (MSA-friendly)
MANUAL_EXAMPLES = {
    "في": ("أعيشُ في المدينة.", "I live in the city."),
    "من": ("أنا من كازاخستان.", "I am from Kazakhstan."),
    "على": ("الكتاب على الطاولة.", "The book is on the table."),
    "إلى": ("سأذهب إلى البيت.", "I will go home."),
    "عن": ("تحدّثنا عن العمل.", "We talked about work."),
    "مع": ("سأذهب مع صديقي.", "I will go with my friend."),
    "و": ("أنا وأنت أصدقاء.", "You and I are friends."),
    "أو": ("اشرب ماءً أو شايًا.", "Drink water or tea."),
    "لكن": ("أريد أن آتي، لكنني مشغول.", "I want to come, but I'm busy."),
    "لأن": ("أنا سعيد لأنك هنا.", "I'm happy because you are here."),
    "إذا": ("إذا تأخرتُ سأتصل.", "If I am late, I will call."),
    "لم": ("لم أفهم.", "I didn't understand."),
    "لن": ("لن أنسى.", "I will not forget."),
    "لا": ("لا تقلق.", "Don't worry."),
    "نعم": ("نعم، أفهم.", "Yes, I understand."),
    "هل": ("هل أنت بخير؟", "Are you okay?"),
    "هذا": ("هذا كتاب.", "This is a book."),
    "ذلك": ("ذلك بعيد.", "That is far."),
    "هناك": ("هناك مشكلة.", "There is a problem."),
    "هنا": ("أنا هنا.", "I am here."),
    "كل": ("كل يوم.", "Every day."),
    "أي": ("أي كتاب تريد؟", "Which book do you want?")
}

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
# Kaikki (Wiktionary) glossary
# ---------------------------------------------------------------------------
def ensure_kaikki():
    if not KAIKKI_JSONL.exists():
        download_file(KAIKKI_URL, KAIKKI_JSONL)


def build_kaikki_glosses(target_words: List[str]) -> Dict[str, Dict[str, str]]:
    """
    Build normalized-lemma → {gloss, pos, diac} mapping from Kaikki JSONL.
    Only extracts entries for target lemmas (efficient streaming).
    """
    ensure_kaikki()
    targets = {normalize_ar(w) for w in target_words if w}
    out: Dict[str, Dict[str, str]] = {}
    with open(KAIKKI_JSONL, encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except Exception:
                continue
            if data.get("lang") != "Arabic":
                continue
            word = (data.get("word") or "").strip()
            if not word:
                continue
            norm = normalize_ar(word)
            if norm not in targets:
                continue
            # collect glosses (English)
            glosses = []
            for s in data.get("senses", []) or []:
                for g in s.get("glosses", []) or []:
                    if g and g not in glosses:
                        glosses.append(g)
                for t in s.get("translations", []) or []:
                    if t.get("lang") == "en":
                        w = t.get("word") or ""
                        if w and w not in glosses:
                            glosses.append(w)
                if len(glosses) >= 4:
                    break
            gloss = limit_gloss("; ".join(glosses))
            pos = (data.get("pos") or "").strip().lower()
            diac = word if has_tashkeel(word) else ""
            existing = out.get(norm)
            if not existing:
                out[norm] = {"gloss": gloss, "pos": pos, "diac": diac}
            else:
                # fill missing fields only
                if not existing.get("gloss") and gloss:
                    existing["gloss"] = gloss
                if not existing.get("pos") and pos:
                    existing["pos"] = pos
                if not existing.get("diac") and diac:
                    existing["diac"] = diac
    return out


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
    force_examples = os.environ.get("AR_FORCE_EXAMPLES", "0").lower() in ("1","true","yes")
    if CACHE_EXAMPLES.exists() and not force_examples:
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

def load_nllb(model_name: str = "facebook/nllb-200-distilled-600M"):
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.eval()
    return tok, model

def load_m2m100(model_name: str = "facebook/m2m100_418M"):
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.eval()
    return tok, model

def translate_nllb(texts: List[str], tok, model, src_lang: str, tgt_lang: str, max_len=256) -> List[str]:
    if not texts:
        return []
    tok.src_lang = src_lang
    with torch.no_grad():
        enc = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_len)
        forced_bos = tok.convert_tokens_to_ids(tgt_lang)
        gen = model.generate(**enc, forced_bos_token_id=forced_bos, max_new_tokens=128)
    return tok.batch_decode(gen, skip_special_tokens=True)

def translate_m2m100(texts: List[str], tok, model, src_lang: str, tgt_lang: str, max_len=256) -> List[str]:
    if not texts:
        return []
    tok.src_lang = src_lang
    with torch.no_grad():
        enc = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_len)
        forced_bos = tok.get_lang_id(tgt_lang)
        gen = model.generate(**enc, forced_bos_token_id=forced_bos, max_new_tokens=128)
    return tok.batch_decode(gen, skip_special_tokens=True)


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
    if os.environ.get("AR_RESET_CACHE", "0").lower() in ("1","true","yes"):
        return {}
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


def load_master_list(target_n: int) -> List[Dict]:
    """Load the 4.5k master list (lemma-based) from CSV."""
    if not MASTER_LIST_CSV.exists():
        return []
    rows = []
    with open(MASTER_LIST_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    rows = rows[:target_n] if target_n else rows
    out = []
    for i, r in enumerate(rows):
        word = (r.get("arabic_lemma") or "").strip()
        if not word or not is_arabic_word(word):
            continue
        out.append({
            "w": word,
            "r": (r.get("root_if_relevant") or "").strip(),
            "pl": "",
            "en": (r.get("english_core_gloss") or "").strip(),
            "ru": "",
            "xa": "",
            "xe": "",
            "xr": "",
            "tier": 1 + (i // max(1, target_n // 7)),
            "level": 1 + (i // max(1, target_n // 7)),
            "pos": (r.get("part_of_speech") or "").strip()
        })
    return out


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

    # 1) Load sources (master list preferred)
    base_entries = load_master_list(target_n)
    if not base_entries:
        base_entries = load_base_entries()
    if not base_entries:
        # Fallback to wordfreq if everything else is missing
        words = top_n_list("ar", 6000)[:target_n]
        base_entries = [{
            "w": w, "r":"", "pl":"", "en":"", "ru":"",
            "xa":"", "xe":"", "xr":"",
            "tier": 1 + (i // max(1, target_n // 7)),
            "level": 1 + (i // max(1, target_n // 7)),
            "pos":""
        } for i, w in enumerate(words)]

    cleaned_words = [e.get("w","").strip() for e in base_entries]
    master_norms = {normalize_ar(w) for w in cleaned_words}

    # 2) Kaikki glosses (primary glossary)
    kaik_map = build_kaikki_glosses(cleaned_words)

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
    mt_engine = os.environ.get("AR_MT_ENGINE", "AUTO").lower().strip()
    nllb_tok = nllb_model = None
    m2m_tok = m2m_model = None
    opus_ar_en_tok = opus_ar_en_model = None
    opus_en_ru_tok = opus_en_ru_model = None

    def ensure_engine(engine: str):
        nonlocal nllb_tok, nllb_model, m2m_tok, m2m_model, opus_ar_en_tok, opus_ar_en_model, opus_en_ru_tok, opus_en_ru_model
        if engine == "nllb":
            if nllb_tok is None or nllb_model is None:
                nllb_tok, nllb_model = load_nllb()
        elif engine == "m2m100":
            if m2m_tok is None or m2m_model is None:
                m2m_tok, m2m_model = load_m2m100()
        else:
            # OPUS models for specific directions
            if opus_ar_en_tok is None or opus_ar_en_model is None:
                opus_ar_en_tok, opus_ar_en_model = load_mt("Helsinki-NLP/opus-mt-ar-en")
            if opus_en_ru_tok is None or opus_en_ru_model is None:
                opus_en_ru_tok, opus_en_ru_model = load_mt("Helsinki-NLP/opus-mt-en-ru")

    def translate_texts(texts: List[str], src: str, tgt: str) -> List[str]:
        if not texts:
            return []
        engine = mt_engine
        if engine == "auto":
            # Prefer NLLB, fallback to M2M100, then OPUS
            try:
                ensure_engine("nllb")
                return translate_nllb(texts, nllb_tok, nllb_model, src, tgt)
            except Exception:
                try:
                    ensure_engine("m2m100")
                    return translate_m2m100(texts, m2m_tok, m2m_model, src.split("_")[0], tgt.split("_")[0])
                except Exception:
                    ensure_engine("opus")
                    # OPUS fallback by direction
                    if src.startswith("arb") and tgt.startswith("eng"):
                        return translate_in_batches(texts, opus_ar_en_tok, opus_ar_en_model, batch_size=32)
                    if src.startswith("eng") and tgt.startswith("rus"):
                        return translate_in_batches(texts, opus_en_ru_tok, opus_en_ru_model, batch_size=32)
                    return translate_in_batches(texts, opus_en_ru_tok, opus_en_ru_model, batch_size=32)
        elif engine == "nllb":
            ensure_engine("nllb")
            return translate_nllb(texts, nllb_tok, nllb_model, src, tgt)
        elif engine == "m2m100":
            ensure_engine("m2m100")
            return translate_m2m100(texts, m2m_tok, m2m_model, src.split("_")[0], tgt.split("_")[0])
        else:
            ensure_engine("opus")
            if src.startswith("arb") and tgt.startswith("eng"):
                return translate_in_batches(texts, opus_ar_en_tok, opus_ar_en_model, batch_size=32)
            if src.startswith("eng") and tgt.startswith("rus"):
                return translate_in_batches(texts, opus_en_ru_tok, opus_en_ru_model, batch_size=32)
            return translate_in_batches(texts, opus_en_ru_tok, opus_en_ru_model, batch_size=32)

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
        km = kaik_map.get(normalize_ar(word), {})

        root = clean_root((base.get("r") or "").strip())
        pos_raw = (km.get("pos") or base.get("pos") or "").strip().lower()

        # Map POS to Arabic category
        norm_word = normalize_ar(word)
        if norm_word in MANUAL_POS_HARF:
            pos = "حرف"
        elif "فعل" in pos_raw or pos_raw.startswith("verb"):
            pos = "فعل"
        elif pos_raw in {"conjunction", "conj", "preposition", "prep", "adp", "particle", "part",
                         "interjection", "det", "determiner"}:
            pos = "حرف"
        elif "حرف" in pos_raw:
            pos = "حرف"
        else:
            pos = "اسم"

        # English gloss (manual → Kaikki → master list → cache)
        manual_gloss = MANUAL_GLOSS.get(normalize_ar(word), "")
        en = manual_gloss or limit_gloss(km.get("gloss") or "")
        if not en:
            en = limit_gloss(base.get("en") or "")
        if not en:
            en = limit_gloss(en_by_word_norm.get(normalize_ar(word), ""))

        # Example sentences
        ex_ar = (base.get("xa") or "").strip()
        ex_en = (base.get("xe") or "").strip()
        manual_ex = MANUAL_EXAMPLES.get(normalize_ar(word))
        if manual_ex:
            ex_ar, ex_en = manual_ex

        # Ensure example contains word; fallback to Tatoeba
        if ex_ar:
            if normalize_token(word) not in normalize_token(ex_ar):
                ex_ar = ""
                ex_en = ""
        if not ex_ar and word in example_map:
            ex_ar, ex_en = example_map[word]

        # Fallback template if still empty (simple, readable, and word-containing)
        if not ex_ar:
            if pos == "فعل":
                ex_ar = f"هُوَ {word}."
                ex_en = f"He {en or 'did it'}."
            elif pos == "اسم":
                ex_ar = f"هذا {word}."
                ex_en = f"This is {en or 'a thing'}."
            else:
                ex_ar = f"هذه جملة فيها {word}."
                ex_en = f"This sentence contains {en or 'the word'}."
        # If English example missing, translate from Arabic
        if not ex_en and use_mt:
            try:
                ex_en = translate_texts([ex_ar], "arb_Arab", "eng_Latn")[0]
            except Exception:
                ex_en = ""

        # De-duplicate example usage
        if ex_ar in used_examples and word in example_map:
            ex_ar, ex_en = example_map.get(word, (ex_ar, ex_en))
        used_examples.add(ex_ar)

        # Prefer Kaikki diacritized form if available
        if km.get("diac"):
            word = km["diac"]

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
                translated = translate_texts(missing, "eng_Latn", "rus_Cyrl")
                for src, tgt in zip(missing, translated):
                    ru_by_en[src] = tgt
        if pending_ex_en:
            missing_ex = [t for t in pending_ex_en if t not in ru_by_ex]
            if missing_ex:
                translated = translate_texts(missing_ex, "eng_Latn", "rus_Cyrl")
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

    # 10) Re-assign levels by English difficulty (easiest -> hardest)
    def english_score(en_text: str) -> float:
        if not en_text:
            return 0.0
        first = re.split(r"[;,(\\[]", en_text)[0].strip().lower()
        first = re.sub(r"^(to|a|an|the)\\s+", "", first)
        token = re.split(r"\\s+", first)[0]
        return zipf_frequency(token, "en")

    scored = sorted(entries, key=lambda e: english_score(e.get("en","")), reverse=True)
    n = len(scored)
    if n:
        bucket = max(1, n // 7)
        for idx, e in enumerate(scored):
            level = min(7, 1 + (idx // bucket))
            e["tier"] = level
            e["level"] = level

    # Save
    OUT_WORDS.write_text("const AR_WORDS = " + json.dumps(entries, ensure_ascii=False) + ";", encoding="utf-8")
    print("Rebuild complete. Words:", len(entries))

    # Save enriched master list (optional, for audits)
    if base_entries and MASTER_LIST_CSV.exists():
        try:
            with open(MASTER_LIST_ENRICHED, "w", encoding="utf-8", newline="") as f:
                fieldnames = [
                    "rank","arabic_lemma","english_core_gloss","part_of_speech","root_if_relevant",
                    "estimated_frequency_tier","register_label"
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for idx, e in enumerate(entries, start=1):
                    writer.writerow({
                        "rank": idx,
                        "arabic_lemma": e.get("w",""),
                        "english_core_gloss": e.get("en",""),
                        "part_of_speech": e.get("pos",""),
                        "root_if_relevant": e.get("r",""),
                        "estimated_frequency_tier": f"T{e.get('tier',1)}",
                        "register_label": ""
                    })
        except Exception as e:
            print("Could not write enriched master list:", e)


if __name__ == "__main__":
    rebuild()
