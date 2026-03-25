"""
Force diacritics (harakat) on all words + examples in words.js
Uses CAMeL Tools MLE disambiguator with cache.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict

from camel_tools.data import CATALOGUE
from camel_tools.disambig.mle import MLEDisambiguator
from camel_tools.tokenizers.word import simple_word_tokenize

BASE = Path(r"C:\Users\hp\arabic-base")
WORDS_JS = BASE / "words.js"
CACHE_DIAC = BASE / "data_sources" / "diac_cache.json"

TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def strip_tashkeel(s: str) -> str:
    return TASHKEEL_RE.sub("", s or "")


def ensure_diacritizer():
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
    text = " ".join(out)
    text = re.sub(r"\s+([،\.\!\?؟\:;])", r"\1", text)
    return text

def add_sukun_last(word: str) -> str:
    """Fallback: add sukun to the last Arabic letter if no diacritics exist."""
    if not word:
        return word
    if TASHKEEL_RE.search(word):
        return word
    # add sukun after the last Arabic letter
    chars = list(word)
    for i in range(len(chars)-1, -1, -1):
        if '\u0621' <= chars[i] <= '\u064A':
            return "".join(chars[:i+1]) + "\u0652" + "".join(chars[i+1:])
    return word


def load_cache() -> Dict[str, str]:
    if CACHE_DIAC.exists():
        try:
            return json.loads(CACHE_DIAC.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, str]):
    CACHE_DIAC.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_words():
    text = WORDS_JS.read_text(encoding="utf-8")
    prefix = "const AR_WORDS = "
    if text.startswith(prefix):
        text = text[len(prefix):]
    if text.endswith(";"):
        text = text[:-1]
    return json.loads(text)


def save_words(entries):
    WORDS_JS.write_text("const AR_WORDS = " + json.dumps(entries, ensure_ascii=False) + ";", encoding="utf-8")


def main():
    ensure_diacritizer()
    disambig = MLEDisambiguator.pretrained("calima-msa-r13")
    cache = load_cache()

    entries = load_words()
    for e in entries:
        for key in ("w", "xa"):
            val = (e.get(key) or "").strip()
            if not val:
                continue
            base = strip_tashkeel(val)
            if base in cache:
                cached = cache[base]
                if cached and TASHKEEL_RE.search(cached):
                    e[key] = cached
                    continue
            diac = diacritize_sentence(disambig, base)
            # If still no tashkeel for single word, try with a short context
            if key == "w" and not TASHKEEL_RE.search(diac or "") and base:
                # try context-based diacritization for names/OOV tokens
                for ctx_tpl in ("هذا {w}.", "اسمه {w}.", "مدينة {w}."):
                    ctx = ctx_tpl.format(w=base)
                    ctx_diac = diacritize_sentence(disambig, ctx)
                    parts = [p for p in ctx_diac.replace(".", "").split(" ") if p.strip()]
                    if len(parts) >= 2:
                        diac = parts[1]
                    if TASHKEEL_RE.search(diac or ""):
                        break
            if key == "w" and not TASHKEEL_RE.search(diac or "") and base:
                diac = add_sukun_last(base)
            cache[base] = diac
            e[key] = diac

    save_cache(cache)
    save_words(entries)
    print("Diacritics applied to words.js")


if __name__ == "__main__":
    main()
