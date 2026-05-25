# RoboModal Studio

Cinematic episodic video pipeline. One command produces a complete episode package: story brief, full script, voiceover audio, episode metadata, cover art, and assembled B-roll video.

## Tech stack

```
Groq llama-3.3-70b (story brief + episode concept)
  → OpenAI GPT-4o (cinematic episode script)
  → OpenAI GPT-4o (episode metadata, parallel)
  → OpenAI TTS tts-1-hd (narrator voice) → ElevenLabs fallback
  → OpenAI GPT-4o (cover art concepts) + fal.ai Flux (render)
  → fal.ai Kling v2 (B-roll video) + ffmpeg (assembly)
  → FastAPI (API server + SQLite job store)
```

## Prerequisites

- Python 3.14+
- `ffmpeg` — required for audio normalization and video assembly (`brew install ffmpeg`)

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

| Key | Required | Source |
|-----|----------|--------|
| `OPENAI_API_KEY` | Yes | platform.openai.com |
| `GROQ_API_KEY` | Yes | console.groq.com |
| `FAL_KEY` | Yes | fal.ai/dashboard/keys |
| `ELEVEN_API_KEY` | No | elevenlabs.io (TTS fallback) |
| `N8N_API_KEY` | No | Required only for the API server |

Optional series defaults (override with CLI flags):

```
DEFAULT_GENRE=drama
DEFAULT_TONE=cinematic
```

## Run the pipeline

**Single episode:**
```bash
source venv/bin/activate
python main.py --genre "thriller" --tone "dark" --series "Night City" --episode 1
```

**Batch (3 episodes in parallel):**
```bash
python main.py --batch 3 --genre "sci-fi" --series "Stellar Drift"
```

**Skip slow stages for fast iteration:**
```bash
python main.py --genre "fantasy" --series "The Iron Crown" --skip-voice --skip-thumbnail --skip-video
```

**All CLI flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--genre` | `DEFAULT_GENRE` env | Story genre (thriller, sci-fi, drama, fantasy…) |
| `--tone` | `DEFAULT_TONE` env | Narrative tone (dark, cinematic, epic, tense…) |
| `--series` | `""` | Series title for continuity |
| `--episode` | `1` | Episode number |
| `--prompt` | `""` | Creative direction for this episode |
| `--batch N` | `1` | Produce N consecutive episodes in parallel |
| `--skip-voice` | off | Skip TTS generation |
| `--skip-thumbnail` | off | Skip cover art generation |
| `--skip-video` | off | Skip B-roll video generation |

## Output

Each run saves `./output/{video_id}_package.json`:

```json
{
  "schema_version": "3",
  "video_id": "ep_20260524_143022_a1b2c3",
  "series_id": "ser_abc123",
  "series_title": "Night City",
  "episode_number": 1,
  "genre": "thriller",
  "episode_brief": {
    "episode_concept": "...",
    "opening_hook": "...",
    "key_beats": ["beat1", "beat2", "beat3", "beat4", "beat5"],
    "themes": ["corruption", "loyalty"],
    "cliffhanger": "..."
  },
  "script": {
    "episode_title": "The Envelope",
    "full_text": "...",
    "word_count": 1540,
    "estimated_duration_minutes": 10.5,
    "sections": [{ "label": "COLD_OPEN", "broll_cues": [...], "shot_types": [...] }]
  },
  "voice_spec": { "provider": "openai", "voice_id": "echo", "speed": 0.92 },
  "metadata": { "title": "...", "description": "...", "tags": [...], "chapters": [...] },
  "winning_thumbnail": { "concept_id": 1, "rendered_path": "...", "is_winner": true },
  "audio_path": "output/audio/{video_id}_final.mp3",
  "thumbnail_path": "output/thumbnails/{video_id}_thumbnail_1.jpg",
  "video_path": "output/video/{video_id}.mp4",
  "stage_timings": { "story_brief": 4.2, "script": 19.1, "metadata_cover": 7.8, "voice": 42.0, "video": 95.3 }
}
```

## Pipeline stages

| Stage | File | Provider | ~Time |
|-------|------|----------|-------|
| 1 — Story brief | `stage1_research.py` | Groq llama-3.3-70b (OpenAI fallback) | 4s |
| 2 — Episode script | `stage2_script.py` | OpenAI GPT-4o | 20s |
| 3 — Narrator voice | `stage3_voice.py` | OpenAI TTS-HD → ElevenLabs fallback | 35–90s |
| 4 — Cover art | `stage4_thumbnail.py` | OpenAI GPT-4o concepts + fal.ai Flux render | 15s |
| 5 — Episode metadata | `stage5_metadata.py` | OpenAI GPT-4o (parallel with stage 4) | 8s |
| 6 — Video assembly | `stage6_video.py` | fal.ai Kling v2 + ffmpeg | 90–300s |

Stages 4 and 5 run concurrently. Stage 6 runs after stage 3 (needs audio). All LLM and image calls retry 3× with exponential backoff. Voice is chunked at sentence boundaries (4 000 char limit) and concatenated with ffmpeg.

## Script markup

The script stage emits cinematic markup that drives downstream stages:

| Tag | Purpose | Handled by |
|-----|---------|------------|
| `[BROLL: description]` | B-roll clip prompt for video gen | stage6, stripped for TTS |
| `[SHOT: type]` | Camera direction (WIDE / CLOSE-UP / AERIAL…) | stage6, stripped for TTS |
| `[MOOD: emotion]` | Score/atmosphere cue | stage6, stripped for TTS |
| `[PAUSE]` | Dramatic silence → converted to `...` for TTS | stage3 |
| `[EMPHASIS]` | Narrator stress cue → removed for TTS | stage3 |

## Tests

```bash
pytest test_pipeline.py -v
pytest test_pipeline.py::TestScriptMarkupStripping -v
pytest test_pipeline.py -k "test_elevenlabs" -v
```

All external calls (Groq, OpenAI, ElevenLabs, fal.ai) are mocked — no API keys needed.

## API server

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Interactive docs: `http://localhost:8000/docs`

### Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Live API key check (OpenAI + Groq + fal) |
| `POST` | `/run` | Async episode run — returns `job_id` immediately |
| `POST` | `/run/sync` | Blocking run — returns full result (n8n Wait node pattern) |
| `POST` | `/run/batch` | Batch async run of N episodes |
| `GET` | `/jobs/{job_id}` | Poll job status |
| `GET` | `/jobs` | List all jobs (most recent first) |
| `POST` | `/series` | Create a series for multi-episode continuity |
| `GET` | `/series/{id}/episodes` | List all episodes in a series |
| `POST` | `/avatars` | Generate and store a narrator avatar image |
| `POST` | `/reference-media` | Upload a reference image for image-to-video |
| `POST` | `/characters/generate` | Generate a character portrait with full profile |
| `GET` | `/packages/{video_id}` | Retrieve the full JSON package for a completed episode |
| `GET` | `/files/{path}` | Serve output files (audio, video, thumbnails) |

Job state persists in `./output/jobs.db` (SQLite) — survives API restarts.

**Example `/run` request:**
```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your_api_key" \
  -d '{
    "series_title": "Night City",
    "genre": "thriller",
    "tone": "dark",
    "episode_number": 1,
    "story_prompt": "A detective receives an anonymous tip that unravels a city-wide conspiracy"
  }'
```

**`PipelineRequest` advanced fields:**

| Field | Description |
|-------|-------------|
| `scene_style` | Visual style prefix applied to all B-roll prompts |
| `cinematic_style` | Override for series visual style |
| `thumbnail_model` | `fal-ai/flux/dev` / `flux-pro/v1.1` / `flux-pro/v1.1-ultra` |
| `video_model` | Any model ID from the `VIDEO_MODEL_REGISTRY` in `stage6_video.py` |
| `avatar_id` | Stored avatar for talking-head clips |
| `avatar_model` | `sadtalker` or `hallo` |
| `voice_id` | OpenAI voice name or ElevenLabs UUID override |
| `reference_image_id` | Stored reference image for image-to-video |
| `use_native_audio` | Enable AI-generated audio (Kling v3 / Veo3) |
| `transition` | `cut` (default) or `crossfade` — mutually exclusive with `use_native_audio` |

## n8n integration

1. Import `workflow.json` into n8n
2. Set n8n environment variables:
   - `PIPELINE_API_KEY` — matches `N8N_API_KEY` in `.env`
   - `SLACK_CHANNEL_ID` — for notifications
   - `NOTIFY_EMAIL` — for email alerts
   - `CONTENT_SHEET_ID` — Google Sheet episode log
3. Activate — triggers Mon/Wed/Fri at 9am, or POST to the webhook URL to trigger manually
