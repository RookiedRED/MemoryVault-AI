# MemoryVault AI

A privacy-preserving personal AI memory vault. Store your documents, notes, and knowledge locally — then query them with a two-tier model pipeline that keeps sensitive data on-device while routing general questions to a cloud model only when safe to do so.

## How it works

Every query passes through a 10-step Guardian Pipeline:

1. **Retrieve** — semantic search over your local vault (BGE embeddings + sqlite-vec)
2. **Classify** — local Guardian model (Ollama) scores the privacy level of the query
3. **Route** — based on privacy level, the query goes local-only, guarded-online, or is blocked
4. **Sanitize** — PII and sensitive fields are redacted before anything leaves your machine
5. **Expert call** — sanitized payload sent to OpenAI (only for non-sensitive queries)
6. **Check** — Guardian scans the cloud response for any data leakage before showing it to you
7. **Finalize** — Guardian merges local context and cloud response into the final answer

Privacy levels: `PUBLIC` → `LOW_SENSITIVE` → `PRIVATE` → `HIGHLY_PRIVATE` → `SECRET`  
Anything `HIGHLY_PRIVATE` requires explicit approval before going online. `SECRET` is always blocked.

## Features

- **Local-first** — all vault data and embeddings stay on your machine
- **Prompt history log** — every Guardian and OpenAI call is logged to `logs/prompts.jsonl` (local only, never committed)
- **Relevance filtering** — vault chunks are only included in cloud prompts when cosine distance < threshold; irrelevant personal data is never sent
- **Mobile upload** — scan a QR code to upload files directly from your phone
- **Apple-style UI** — three-pane desktop layout with full mobile support

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally with a supported model (e.g. `qwen2.5:7b`)
- OpenAI API key (for cloud-assisted queries)

## Setup

```bash
# 1. Clone and create a virtual environment
git clone https://github.com/RookiedRED/MemoryVault-AI.git
cd MemoryVault-AI
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and fill in OPENAI_API_KEY, OLLAMA_MODEL, etc.

# 4. Pull your Ollama model
ollama pull qwen2.5:7b

# 5. Start the server
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## Environment variables

See `.env.example` for the full list. Key ones:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Your OpenAI API key |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Local Guardian model |
| `RETRIEVAL_DISTANCE_THRESHOLD` | `0.50` | Cosine distance cutoff for vault retrieval |
| `RETRIEVAL_TOP_K` | `3` | Max chunks retrieved per query |
| `EXPERT_MAX_CONTEXT_CHARS` | `4000` | Hard cap on context sent to cloud |

## Project structure

```
app/
  guardian/     # Pipeline, classifier, sanitizer, response checker
  expert/       # OpenAI client, rate limiter
  privacy/      # Taxonomy, policy, PII markers
  vault/        # Chunker, embedder, importer
  routes/       # FastAPI route handlers
  prompt_logger.py  # Local prompt history logger
static/         # Web UI (HTML/CSS/JS) + mobile upload page
tests/          # Pytest suite (157 tests)
logs/           # Prompt history — local only, gitignored
```

## Running tests

```bash
pytest tests/ -q
```
