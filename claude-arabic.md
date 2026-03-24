# Arabic Vocabulary Project — Progress Log

## Project Goal
Build a comprehensive Arabic learning system similar to HSK Chinese base: full vocabulary list, root-family navigation, context texts, learner tracking, and future ML-ready data.

## Phase 0 — Project Setup & File Collection
- Date: 2026-03-24
- Status: COMPLETE
- Working repository: C:\Users\hp\arabic-base (git@github.com:lemarshel/arabic-base)
- Primary data source: words.js (4500 entries) — cleaned, normalized, and structured for app
- App entry point: index.html

## Phase 1 — Data Extraction & Root Analysis
- Status: COMPLETE
- Output: data/phase1_root_data.json
- Root index built from all words (3–4 consonant roots)
- POS tightened into: اسم / فعل / حرف (Arabic sentence parts)
- Analyzer: CAMeL Tools calima-msa-r13 (morphology DB)

## Phase 2 — Topic Division & Word Clustering
- Status: COMPLETE (auto-batched)
- Output: data/phase2_topics.json
- 40 topics × 100 words each (order-preserving frequency batches)

## Phase 3 — Context Texts
- Status: COMPLETE (auto-compiled)
- Output: data/phase3_stories.json
- 40 texts; each text assembled from the example sentences of its 100 words
- Each topic text contains Arabic, EN, RU lines

## Phase 4 — Learner Tracking System
- Status: COMPLETE (baseline)
- Output: data/phase4_learner_state.json
- LocalStorage logging added:
  - Learned/Familiar toggles
  - Search queries
  - Filter changes (tier, POS, letter)
  - Theme/palette/lang changes
  - Study/Quiz starts
- Event log storage key: arabic_events (latest 2000 entries)

## Phase 5 — Predictive Lexical Graph
- Status: NOT STARTED

## Phase 6 — Story Reader
- Status: NOT STARTED

## Phase 7 — Dashboard & Cross-linking
- Status: NOT STARTED
