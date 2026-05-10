# AI Influencer Pipeline

Fully automated faceless YouTube/TikTok content engine. One command produces a complete video package: researched topic, full script, voiceover audio, SEO metadata, affiliate insertions, and thumbnail image.

## Tech stack

```
DuckDuckGo (trends) → Groq llama-3.3-70b (research + hooks) → OpenAI GPT-4o (script)
                                                             → OpenAI GPT-4o (SEO + affiliates, parallel)
                                                             → OpenAI TTS tts-1-hd (voice) → ElevenLabs fallback
                                                             → OpenAI GPT-4o (thumbnail concepts) + fal.ai Flux (render)
                                                             → FastAPI (webhook server for n8n)
```

## Prerequisites

- Python 3.14+
- `ffmpeg` — required for audio normalization (`brew install ffmpeg`)

## Setup

```bash
git clone https://github.com/FritzHeider/Auto-Influencer.git
cd Auto-Influencer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env
```

## API keys

Edit `.env` with the following. See `.env.example` for the full template.

| Key | Required | Source |
|-----|----------|--------|
| `OPENAI_API_KEY` | Yes | platform.openai.com |
| `GROQ_API_KEY` | Yes | console.groq.com |
| `FAL_KEY` | Yes | fal.ai/dashboard/keys |
| `ELEVEN_API_KEY` | No | elevenlabs.io (TTS fallback only) |
| `N8N_API_KEY` | No | Required only for the API server |

Optional channel defaults (override with CLI flags):

```
CHANNEL_NICHE=personal finance
CHANNEL_TONE=authoritative
CHANNEL_DEMOGRAPHIC=25-45 year old professionals
```

## Run the pipeline

**Single video:**
```bash
source venv/bin/activate
python main.py --niche "personal finance" --tone authoritative
```

**Batch (3 videos in parallel):**
```bash
python main.py --batch 3 --niche "tech"
```

**Skip slow stages for logic testing:**
```bash
python main.py --niche "fitness" --skip-voice --skip-thumbnail
```

**All CLI flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--niche` | `CHANNEL_NICHE` env | Topic niche |
| `--tone` | `CHANNEL_TONE` env | Delivery tone |
| `--demographic` | `CHANNEL_DEMOGRAPHIC` env | Target audience |
| `--batch N` | 1 | Produce N videos in parallel |
| `--skip-voice` | off | Skip TTS generation |
| `--skip-thumbnail` | off | Skip image generation |

## Output

Each run saves `./output/{video_id}_package.json` containing:

```json
{
  "schema_version": "2",
  "video_id": "vid_20260510_...",
  "niche": "personal finance",
  "research": { "selected_topic": {...}, "hooks": [...], "winning_hook": "..." },
  "script": { "full_text": "...", "word_count": 1260, "estimated_duration_minutes": 10.5 },
  "voice_spec": { "provider": "openai", "voice_id": "onyx", ... },
  "seo": { "title": "...", "description": "...", "tags": [...], "chapters": [...] },
  "affiliates": [{ "product_name": "...", "script_line": "..." }],
  "thumbnail_concepts": [{ "concept_id": 1, "rendered_path": "...", "is_winner": true }, ...],
  "audio_path": "output/audio/{video_id}_final.mp3",
  "thumbnail_path": "output/thumbnails/{video_id}_thumbnail_1.jpg",
  "stage_timings": { "research": 5.1, "script": 18.4, "seo_affiliates": 6.2, "voice": 38.0, "thumbnail": 12.3 }
}
```

Audio files land in `./output/audio/`, thumbnails in `./output/thumbnails/`. Topic titles are tracked in `./output/used_topics.json` so consecutive runs don't repeat subjects.

## Pipeline stages

| Stage | File | Provider | ~Time |
|-------|------|----------|-------|
| Research + hooks | `stage1_research.py` | DuckDuckGo → Groq (OpenAI fallback) | 5s |
| Script | `stage2_script.py` | OpenAI GPT-4o | 20s |
| SEO + affiliates | `stage5_monetize.py` | OpenAI GPT-4o (parallel) | 8s |
| Voice | `stage3_voice.py` | OpenAI TTS → ElevenLabs fallback | 35–90s |
| Thumbnail | `stage4_thumbnail.py` | OpenAI GPT-4o concepts + fal.ai Flux render | 15s |

All LLM and image calls have tenacity retry (3 attempts, exponential backoff). Voice input is chunked at sentence boundaries to stay under OpenAI's 4096-char limit; chunks are generated in parallel and concatenated with ffmpeg. All 3 thumbnail concepts are rendered in parallel.

## Tests

```bash
pytest test_pipeline.py -v
pytest test_pipeline.py::TestScriptMarkupStripping -v   # single class
pytest test_pipeline.py -k "test_elevenlabs" -v         # single test
```

All external calls (DDG, Groq, OpenAI, ElevenLabs) are mocked — no API keys needed to run tests.

## API server (for n8n)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Interactive docs: `http://localhost:8000/docs`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Verify API keys live against OpenAI + Groq |
| `POST` | `/run` | Async run — returns `job_id` immediately |
| `POST` | `/run/sync` | Blocking run — returns full package (use for n8n Wait nodes) |
| `POST` | `/run/batch` | Batch async run of N videos |
| `GET` | `/jobs/{job_id}` | Poll job status |
| `GET` | `/jobs` | List all jobs (most recent first) |

Job state is persisted in `./output/jobs.db` (SQLite) — survives API restarts.

**Example `/run/sync` request:**
```bash
curl -X POST http://localhost:8000/run/sync \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your_api_key" \
  -d '{"niche": "fitness", "tone": "energetic"}'
```

**`/health` response:**
```json
{
  "status": "ok",
  "version": "1.0.0",
  "providers": {
    "openai": "ok",
    "groq": "ok",
    "fal": "ok",
    "elevenlabs": "not configured (optional)"
  }
}
```

Webhook callbacks (set `webhook_callback` in request body) retry up to 3 times with exponential backoff on failure.

## n8n integration

1. Import `workflow.json` into n8n
2. Set n8n environment variables:
   - `PIPELINE_API_KEY` — matches `N8N_API_KEY` in `.env`
   - `SLACK_CHANNEL_ID` — for notifications
   - `NOTIFY_EMAIL` — for email alerts
   - `CONTENT_SHEET_ID` — Google Sheet video log
3. Activate — triggers Mon/Wed/Fri at 9am, or POST to the webhook URL to trigger manually

## Affiliate niche presets

`DEFAULT_AFFILIATES` in `pipeline/stage2_script.py` covers 20 niches. Set `CHANNEL_NICHE` to any of the following for optimized product suggestions:

`personal finance` · `investing` · `real estate` · `tech` · `ai` · `productivity` · `business` · `entrepreneurship` · `health` · `fitness` · `mental health` · `cooking` · `travel` · `gaming` · `beauty` · `fashion` · `parenting` · `education` · `automotive` · `pets`

Unrecognized niches fall back to `["Amazon", "Skillshare", "NordVPN"]`.
