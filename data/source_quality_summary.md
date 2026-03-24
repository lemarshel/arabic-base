# Source Quality Summary

## Leipzig Corpora Collection
**Strengths:**
- Large, structured corpora across news, Wikipedia, web
- Consistent formatting, easy to parse
- Good for MSA frequency signals

**Weaknesses:**
- Token lists (not lemma lists) → require lemmatization
- Web corpus can include noise/dialectal material

## CAMeL Tools
**Strengths:**
- High‑quality MSA morphology + lemmatization
- Provides lemma, POS, root, diacritics

**Weaknesses:**
- Single‑token disambiguation lacks full context
- Some ambiguity remains for highly polysemous words

## MUSE Arabic‑English Dictionary
**Strengths:**
- Open bilingual lexicon
- Good baseline glosses for common words

**Weaknesses:**
- Sparse for function words and some MSA lemmas
- Often single‑word glosses only

## NLLB‑200 (MT fallback)
**Strengths:**
- Strong open multilingual model
- Better than lightweight MT on modern usage

**Weaknesses:**
- Single‑word translation can be noisy
- Needs human review for polysemous items

## OPUS / Subtitles (Planned)
**Strengths:**
- Great for everyday spoken usage

**Weaknesses:**
- More dialectal noise
- Needs heavy filtering for MSA focus

## Wiktionary / Lane / Quranic Corpus (Planned)
**Strengths:**
- High quality for semantic nuance and classical usage

**Weaknesses:**
- Manual review needed; not ideal for bulk automation
