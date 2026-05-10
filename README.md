# AI Influencer Pipeline

Fully automated faceless YouTube/TikTok content engine.
ElevenLabs + PlayHT + OpenAI + Fireworks + Groq + Bing.

## Architecture

```
Bing (trends) → Groq (research+hooks) → OpenAI (script) → ElevenLabs/PlayHT (voice)
                                       → OpenAI (SEO+affiliates)
                                       → OpenAI+Fireworks (thumbnail)
                                       → FastAPI (webhook server for n8n)
```

## Prerequisites

- Python 3.14+
- `ffmpeg` (for audio post-processing — `brew install ffmpeg`)

## Setup

```bash
cd ai-influencer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env
```

The source files must live under their expected package paths. Move them before running:

```bash
mkdir -p config pipeline prompts
touch config/__init__.py pipeline/__init__.py prompts/__init__.py
mv settings.py config/
mv models.py stage1_research.py stage2_script.py stage3_voice.py stage4_thumbnail.py stage5_monetize.py pipeline/
mv system_prompts.py prompts/
```

## Run pipeline once

```bash
python main.py --niche "personal finance" --tone authoritative
```

## Run batch (3 videos)

```bash
python main.py --batch 3 --niche "tech" --skip-thumbnail
```

## Start API server (for n8n)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

API docs: http://localhost:8000/docs

## n8n Integration

1. Copy `n8n/workflow.json`
2. In n8n: Import workflow → paste JSON
3. Set environment variables in n8n:
   - `PIPELINE_API_KEY` — matches your .env
   - `SLACK_CHANNEL_ID` — for notifications
   - `NOTIFY_EMAIL` — for email alerts
   - `CONTENT_SHEET_ID` — Google Sheet for video log
4. Activate the workflow — runs Mon/Wed/Fri at 9am
5. Or POST to the webhook URL to trigger manually

## Manual n8n trigger

```bash
curl -X POST https://fritzthatcat.app.n8n.cloud/webhook/ai-influencer-trigger \
  -H "Content-Type: application/json" \
  -d '{"niche": "crypto", "tone": "dramatic"}'
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| POST | /run | Async run (returns job_id immediately) |
| POST | /run/sync | Sync run (blocks, returns full package) |
| POST | /run/batch | Batch async run |
| GET | /jobs/{job_id} | Poll job status |
| GET | /jobs | List all jobs |

## Output structure

Each run produces a `video_id_package.json` in `./output/` containing:
- `research`: trending topic, hooks, selection rationale
- `script`: full marked-up script with broll/affiliate cues
- `voice_spec`: ElevenLabs/PlayHT configuration used
- `seo`: title, description, tags, chapters
- `affiliates`: product insertions with script lines
- `thumbnail_concepts`: 3 concepts with Fireworks prompts
- `audio_path`: path to post-processed MP3
- `thumbnail_path`: path to 1280x720 JPEG

## Pipeline stages

| Stage | File | Provider | Time |
|-------|------|----------|------|
| Research + hooks | stage1_research.py | Bing + Groq | ~5s |
| Script | stage2_script.py | OpenAI GPT-4o | ~20s |
| SEO + affiliates | stage5_monetize.py | OpenAI | ~10s |
| Voice | stage3_voice.py | ElevenLabs → PlayHT | ~60s |
| Thumbnail | stage4_thumbnail.py | OpenAI + Fireworks | ~30s |

Total: ~2 minutes per video end-to-end.

## Niche presets

Set `CHANNEL_NICHE` to any of these for optimized affiliate defaults:
- `personal finance` — Robinhood, Acorns, Personal Capital
- `tech` — Amazon, NordVPN, Skillshare
- `health` — AG1, Whoop, Calm
- `productivity` — Notion, Todoist, Grammarly
- `real estate` — Fundrise, Arrived Homes, Roofstock
