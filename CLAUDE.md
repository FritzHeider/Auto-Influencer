# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Fully automated faceless YouTube/TikTok content engine. One `run_pipeline()` call produces a complete video package: researched topic, scripted content, voiceover audio, SEO metadata, affiliate insertions, thumbnail image, and assembled B-roll video — saved as `./output/{video_id}_package.json`.

## Commands

```bash
source venv/bin/activate

# Run pipeline (Python 3.14, venv/)
python main.py --niche "personal finance" --tone authoritative
python main.py --batch 3 --niche "tech" --skip-thumbnail --skip-video

# API server
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
# Interactive docs: http://localhost:8000/docs

# Tests
pytest test_pipeline.py -v
pytest test_pipeline.py::TestScriptMarkupStripping -v
pytest test_pipeline.py -k "test_elevenlabs" -v
```

`--skip-voice`, `--skip-thumbnail`, `--skip-video` bypass external API calls — use these for fast iteration. Tests mock all external calls (DuckDuckGo, Groq, OpenAI, ElevenLabs, fal.ai).

## Environment

Required `.env`: `OPENAI_API_KEY`, `GROQ_API_KEY`, `FAL_KEY`. Optional: `ELEVEN_API_KEY`, `N8N_WEBHOOK_URL`, `N8N_API_KEY`, `CHANNEL_NICHE`, `CHANNEL_TONE`, `CHANNEL_DEMOGRAPHIC`. Settings uses `extra="ignore"` so unrelated keys in a shared `.env` are safe.

## File structure

```
config/settings.py        pydantic-settings singleton; imported directly as `settings`
pipeline/models.py        all Pydantic models
pipeline/stage*.py        one file per pipeline stage (1–6)
prompts/system_prompts.py all LLM prompt templates ({placeholder} format, JSON-only responses)
main.py                   run_pipeline() / run_batch() + CLI
api.py                    FastAPI server with SQLite job store
dashboard.html            single-file browser UI
test_pipeline.py          pytest suite (pytest-asyncio)
workflow.json             importable n8n automation
```

## Pipeline architecture

`main.py:run_pipeline()` orchestrates six async stages, assembling a `VideoPackage` Pydantic model:

| Stage | File | Provider | Notes |
|-------|------|----------|-------|
| 1 Research | `stage1_research.py` | DuckDuckGo + Groq llama-3.3-70b (OpenAI fallback) | DDG queries run in parallel |
| 2 Script | `stage2_script.py` | OpenAI GPT-4o | Produces `[BROLL:]`, `[PAUSE]`, `[EMPHASIS]`, `[AFFILIATE:]` markup |
| 3 Voice | `stage3_voice.py` | OpenAI TTS-HD → ElevenLabs fallback | `strip_script_markup()` cleans cues; chunked at 4 000 chars; ffmpeg -14 LUFS |
| 4 Thumbnail | `stage4_thumbnail.py` | OpenAI (concepts) + fal.ai Flux | 3 concepts rendered in parallel; winner by `ctr_score`; model is overridable |
| 5 SEO + Affiliates | `stage5_monetize.py` | OpenAI | Run in parallel via `asyncio.gather` alongside stage 4 |
| 6 Video | `stage6_video.py` | fal.ai Kling v2 + ffmpeg | Plans ClipSlots, generates all in parallel, concat + audio mix |

Stages 4 and 5 run concurrently. Stage 6 runs after stage 3 (needs audio).

## Key design patterns

**Settings**: `config/settings.py` is a `pydantic_settings.BaseSettings` singleton. Import it as `from config.settings import settings` — never instantiate it again.

**Models**: `VideoPackage` is the top-level container. `ResearchResult → Script → VoiceSpec + SEOPackage + AffiliateInsertion[] + ThumbnailConcept[]` all compose into it. `Script.sections` are `ScriptSection` objects with `broll_cues[]` driving video generation.

**Prompts**: Every LLM call instructs the model to respond with JSON only; responses are parsed with `json.loads()` directly. All prompt strings live in `prompts/system_prompts.py`.

**Retries**: fal.ai and OpenAI calls use `tenacity` `@retry(stop_after_attempt(3), wait_exponential(...))`.

**Video slot planner** (`stage6_video.py`): `_plan()` produces a `ClipSlot` list (kind: `card` | `avatar` | `broll`). All slots fire in parallel via `asyncio.gather`. Cards are rendered with Pillow + ffmpeg; avatar clips use fal.ai SadTalker or Hallo (different param schemas); B-roll uses Kling v2 text-to-video.

## API

SQLite-backed job store at `./output/jobs.db` (persists across restarts). Auth via `X-Api-Key` header (`N8N_API_KEY`).

Key endpoints:
- `POST /run` — async job, returns `job_id` immediately
- `POST /run/sync` — blocks until done (n8n Wait node pattern)
- `POST /run/batch` — N videos in parallel; supports `topics: list[str]` for per-video topic overrides
- `GET /jobs/{job_id}` — poll status
- `POST /avatars` / `GET /avatars` / `DELETE /avatars/{id}` — avatar store management
- `POST /reference-media` (multipart) / `GET /reference-media` / `DELETE /reference-media/{id}` — reference image store
- `GET /packages/{video_id}` — full JSON package for a completed video
- `GET /files/{path}` — serve output files — path is relative to `./output/` (covers avatars, reference_media, video, audio, thumbnails)
- `POST /preview-prompt` — preview enriched B-roll prompt without generating

`PipelineRequest` advanced fields: `scene_style`, `thumbnail_model`, `avatar_id`, `avatar_model` (sadtalker/hallo), `voice_id`, `style_locked_broll`, `video_model` (fal model ID), `reference_image_id` (stored ref image for i2v), `use_native_audio` (bool), `transition` (cut|crossfade).

## fal.ai model IDs (verified)

All B-roll models are registered in `VIDEO_MODEL_REGISTRY` in `stage6_video.py` with their capability spec (`base_args`, `i2v_model`, `i2v_start_key`, `supports_audio`, `audio_key`, `retry_attempts`).

| Purpose | Model ID | Notes |
|---------|----------|-------|
| Thumbnail default | `fal-ai/flux/dev` | |
| Thumbnail pro | `fal-ai/flux-pro/v1.1` | |
| Thumbnail ultra | `fal-ai/flux-pro/v1.1-ultra` | |
| B-roll default | `fal-ai/kling-video/v2/master/text-to-video` | duration: "10" (string) |
| B-roll upgraded | `fal-ai/kling-video/v2.1/master/text-to-video` | duration: "10" (string) |
| B-roll pro+audio | `fal-ai/kling-video/v3/pro/text-to-video` | duration: 10 (int), audio_key: `generate_audio` |
| B-roll premium | `fal-ai/veo3` | Google, audio_key: `audio_enabled` (t2v) / `audio` (i2v) |
| B-roll fast | `fal-ai/luma-dream-machine/ray-2-flash` | no duration arg |
| B-roll budget | `fal-ai/ltx-video` | no duration arg |
| B-roll photorealistic | `fal-ai/minimax/video-01-live` | no duration arg |
| Avatar default | `fal-ai/sadtalker` | params: `source_image_url`, `driven_audio_url`, `still_mode`, `expression_scale`, `preprocess`, `enhancer` |
| Avatar alt | `fal-ai/hallo` | params: `source_image_url`, `audio_url` only |
| Avatar image gen | `fal-ai/flux/dev` | |

**Key i2v field differences**: Kling v2/v2.1 i2v uses `image_url`; Kling v3 i2v uses `start_image_url`; Veo3 i2v uses `image_url` but audio_key is `audio` (not `audio_enabled` like t2v). Always use the registry — never hardcode field names.

## Dashboard (`dashboard.html`)

Single-file browser UI. Communicates with `http://localhost:8000`. Features: dark/light/OLED theme cycle, 7 video model cards (Kling v2/v2.1/v3, Veo3, Luma, LTX, MiniMax), thumbnail model selector (3 Flux tiers), scene style, voice override, avatar store (generate/select/delete), reference image uploader (i2v), native AI audio toggle (Kling v3/Veo3), clip transition selector (cut/crossfade), cost estimator with >$20 confirmation guard, collapsible advanced settings, inline video player in job cards, thumbnail comparison, download links, script expander, webhook status.

`state.videoModel` is now correctly sent in the API payload as `video_model`. Native audio and crossfade are mutually exclusive (crossfade disabled when native audio on).

## n8n workflow

`workflow.json` triggers Mon/Wed/Fri at 9am and POSTs to the FastAPI server. Requires n8n env vars: `PIPELINE_API_KEY`, `SLACK_CHANNEL_ID`, `NOTIFY_EMAIL`, `CONTENT_SHEET_ID`.
