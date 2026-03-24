# Arabic Master List — Methodology Summary

## Overview
This list is a **lemma‑based, MSA‑centered** vocabulary list compiled from multiple open corpora and lemmatized with CAMeL Tools. The goal is a **high‑payoff, learner‑useful** ranking rather than raw token frequency.

## Corpora (Primary)
- **Leipzig Corpora Collection (Arabic)** — news, Wikipedia, and web corpora. These are large, balanced sources with consistent formatting and token frequency lists.

## Morphology & Lemmatization
- **CAMeL Tools MLE Disambiguator** used to lemmatize surface tokens and recover lemma + POS + root.
- Orthographic normalization applied (hamza, alif/ya variants, ta marbuta normalization), then deduped by lemma.

## Candidate Generation
- Collected the **top 60k tokens per corpus** from each source.
- Lemmatized and merged into a single candidate pool.
- Stopped candidate generation at ~12k lemmas to avoid noise.

## Scoring & Ranking
Each lemma receives a usefulness score based on:
- **log frequency** (summed across sources)
- **dispersion** (how many corpora it appears in)
- **POS weight** (particles/verbs slightly boosted)

Final list is the **top 4,500** lemmas by usefulness score.

## Deduplication
- Tokens that collapse to the same lemma are merged into a single entry.
- Merged forms are stored in `merged_duplicates.csv`.

## Output
- Full list: `data/arabic_master_list.csv`
- JSON version: `data/arabic_master_list.json`
- Merged/Rejected tables: `data/merged_duplicates.csv`, `data/rejected_items.csv`
- Uncertain entries: `data/uncertain_items.csv`
