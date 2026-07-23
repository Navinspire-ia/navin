---
name: rag-knowledge-builder
description: Build and evaluate RAG corpora — ingest, chunk, embed, index, and spot-check retrieval quality. Use when creating knowledge bases for agents.
metadata: {"navin":{"emoji":"📚","category":"data"}}
---

# RAG Knowledge Builder

## Overview

Garbage in, garbage out. Clean sources beat clever chunkers.

## Workflow

1. Define the question types the RAG must answer.
2. Ingest sources (docs, `web-extractor` output, PDFs).
3. Chunk with structure awareness (headings > fixed blind windows).
4. Embed/index with the project’s vector store (note model + dims).
5. Evaluate with 10–20 gold questions; measure hit rate / faithfulness.
6. Fix gaps (missing docs, bad chunking) before tuning prompts.

## Rules

- Track provenance (source URL/path) on every chunk.
- Exclude secrets and credentials from the corpus.
