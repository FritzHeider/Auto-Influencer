# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Fully automated faceless YouTube/TikTok content engine. One `run_pipeline()` call produces a complete video package: researched topic, scripted content, voiceover audio, SEO metadata, affiliate insertions, and thumbnail image — saved as a JSON package in `./output/`.

## Package structure

```
config/settings.py        pydantic-settings BaseSettings; singleton `settings` imported directly
pipeline/models.py        all Pydantic models
pipeline/stage*.py        one file per pipeline stage
prompts/system_prompts.py all LLM prompt templates
main.py                   CLI entry point + run_pipeline() / run_batch()
api.py                    FastAPI server
test_pipeline.py          pytest suite
```

## Running the pipeline

```bash
source venv/bin/activate
python main.py --niche "personal finance" --tone authoritative
python main.py --batch 3 --niche "tech" --skip-thumbnail
```

`--skip-voice` and `--skip-thumbnail` bypass TTS and image generation — useful for testing logic without API calls.

## API server (for n8n integration)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
# Docs at http://localhost:8000/docs
```

The API uses an in-memory job store (`jobs` dict in `api.py`) — jobs are lost on restart. The code comments note Redis as the production replacement.

## Tests

```bash
pytest test_pipeline.py -v
pytest test_pipeline.py::TestScriptMarkupStripping -v   # single class
pytest test_pipeline.py -k "test_elevenlabs" -v         # single test
```

Tests use `pytest-asyncio` for async stages. All external calls (Bing, Groq, OpenAI, ElevenLabs) are mocked via `unittest.mock`.

## Environment

Python 3.14 (`venv/`). Install with `pip install -r requirements.txt`.

Required `.env` keys: `OPENAI_API_KEY`, `GROQ_API_KEY`, `FAL_KEY`. Optional: `ELEVEN_API_KEY` (ElevenLabs TTS fallback), `N8N_WEBHOOK_URL`, `N8N_API_KEY`, `CHANNEL_NICHE`, `CHANNEL_TONE`, `CHANNEL_DEMOGRAPHIC`. See `.env.example` for the full list. Settings uses `extra="ignore"` so unrelated keys in a shared `.env` are safe.

## Architecture

**Data flow:** `main.py:run_pipeline()` orchestrates five async stages, assembling a `VideoPackage` Pydantic model that is saved as `./output/{video_id}_package.json`.

| Stage | File | Provider | Notes |
|-------|------|----------|-------|
| Research + hooks | `stage1_research.py` | DuckDuckGo → Groq (OpenAI fallback) | DDG queries run in parallel; Groq llama-3.3-70b for speed, falls back to GPT-4o |
| Script | `stage2_script.py` | OpenAI GPT-4o | Returns marked-up script with `[BROLL:]`, `[PAUSE]`, `[EMPHASIS]`, `[AFFILIATE:]` cues |
| SEO + affiliates | `stage5_monetize.py` | OpenAI | Run in parallel via `asyncio.gather` |
| Voice | `stage3_voice.py` | OpenAI TTS → ElevenLabs fallback | `strip_script_markup()` cleans cues; chunked at 4000 chars; ffmpeg normalizes to -14 LUFS |
| Thumbnail | `stage4_thumbnail.py` | OpenAI (concepts) + fal.ai Flux | Generates 3 concepts, renders all in parallel, winner picked by `ctr_score` |

**Key models** (`models.py`): `VideoPackage` is the top-level container. `ResearchResult` → `Script` → `VoiceSpec` + `SEOPackage` + `AffiliateInsertion[]` + `ThumbnailConcept[]` all compose into it.

**Settings** (`settings.py`): `pydantic_settings.BaseSettings` with `.env` file. Singleton `settings` imported directly — no dependency injection.

**Prompts** (`system_prompts.py`): All LLM prompts are string templates with `{placeholder}` format. Every prompt instructs the model to respond with JSON only — responses are parsed with `json.loads()` directly, no structured output library.

**API** (`api.py`): FastAPI with background tasks for async jobs. Auth is a single shared API key checked via `X-Api-Key` header. `/run/sync` blocks until done (designed for n8n Wait nodes).

## n8n workflow

`workflow.json` is the importable n8n automation. It triggers Mon/Wed/Fri at 9am and POSTs to the FastAPI webhook. The n8n workflow expects `PIPELINE_API_KEY`, `SLACK_CHANNEL_ID`, `NOTIFY_EMAIL`, and `CONTENT_SHEET_ID` set as n8n environment variables.
