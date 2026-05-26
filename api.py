import json
import logging
import mimetypes
import os
import sqlite3
import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import fal_client
import httpx
from fastapi import FastAPI, BackgroundTasks, File, Form, HTTPException, Header, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, model_validator

from config.settings import settings
from main import run_pipeline, run_batch

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_DB_PATH = Path(settings.output_dir) / "jobs.db"


def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                job_id   TEXT PRIMARY KEY,
                data     TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS series (
                series_id  TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )


def set_job(job_id: str, data: dict) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs (job_id, data) VALUES (?, ?)",
            (job_id, json.dumps(data)),
        )


def get_job(job_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT data FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def all_jobs() -> dict[str, dict]:
    with _db() as conn:
        rows = conn.execute("SELECT job_id, data FROM jobs ORDER BY created_at DESC").fetchall()
    return {row["job_id"]: json.loads(row["data"]) for row in rows}


# ── Series DB helpers ─────────────────────────────────────────────────────────

def set_series_db(series_id: str, data: dict) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO series (series_id, data) VALUES (?, ?)",
            (series_id, json.dumps(data, default=str)),
        )


def get_series_db(series_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT data FROM series WHERE series_id = ?", (series_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def all_series_db() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT data FROM series ORDER BY created_at DESC").fetchall()
    return [json.loads(r["data"]) for r in rows]


def delete_series_db(series_id: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM series WHERE series_id = ?", (series_id,))
    return cur.rowcount > 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    logger.info(f"RoboModal Studio API starting (job store: {_DB_PATH})")
    yield
    logger.info("RoboModal Studio API shutting down")


app = FastAPI(
    title="RoboModal Studio API",
    description="Cinematic episodic video pipeline",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard.html", include_in_schema=False)
async def serve_dashboard():
    if not _DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return FileResponse(_DASHBOARD_PATH, media_type="text/html")


class PipelineRequest(BaseModel):
    # Episode identity
    series_id: Optional[str] = None
    series_title: str = ""
    episode_number: int = 1
    genre: Optional[str] = None
    tone: Optional[str] = None
    # Creative direction
    story_prompt: str = ""
    previously_on: str = ""
    world_notes: str = ""
    narration_style: str = "third_person"
    visual_style: str = ""
    color_grade: str = ""
    music_mood: str = "neutral"
    themes: list[str] = []
    character_ids: list[str] = []
    # Pipeline toggles
    skip_voice: bool = False
    skip_thumbnail: bool = False
    skip_video: bool = False
    webhook_callback: Optional[str] = None
    # Advanced video/media options
    scene_style: Optional[str] = None
    thumbnail_model: Optional[str] = None
    avatar_id: Optional[str] = None
    avatar_model: Optional[str] = None
    voice_id: Optional[str] = None
    style_locked_broll: bool = False
    video_model: Optional[str] = None
    reference_image_id: Optional[str] = None
    use_native_audio: bool = False
    transition: str = "cut"
    card_style: str = "pillow"
    cinematic_style: Optional[str] = None
    reference_all_clips: bool = False

    @model_validator(mode="after")
    def check_audio_transition_compat(self) -> "PipelineRequest":
        if self.use_native_audio and self.transition == "crossfade":
            raise ValueError("use_native_audio and transition='crossfade' are mutually exclusive")
        return self


class BatchRequest(BaseModel):
    count: int = 3
    genre: Optional[str] = None
    tone: Optional[str] = None
    series_id: Optional[str] = None
    series_title: str = ""
    episode_number: int = 1
    story_prompts: list[str] = []
    skip_voice: bool = False
    skip_thumbnail: bool = False
    skip_video: bool = False


class AvatarRequest(BaseModel):
    genre: str
    style: Optional[str] = None


class SeriesRequest(BaseModel):
    title: str
    genre: str = "drama"
    tone: str = "cinematic"
    logline: str = ""
    world_notes: str = ""
    visual_style: str = ""
    color_grade: str = ""


def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    if settings.n8n_api_key and x_api_key != settings.n8n_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def _fire_webhook(url: str, payload: dict, max_attempts: int = 3) -> None:
    delay = 2.0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                logger.info(f"Webhook delivered on attempt {attempt}: {url}")
                return
            except Exception as e:
                if attempt == max_attempts:
                    logger.error(f"Webhook failed after {max_attempts} attempts: {e}")
                else:
                    logger.warning(f"Webhook attempt {attempt} failed, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    delay *= 2


def _resolve_characters(character_ids: list[str]) -> list[dict]:
    """Load character data from the reference media index for given IDs."""
    if not character_ids:
        return []
    index = _load_ref_media_index()
    chars = {e["ref_id"]: e for e in index if e.get("media_type") == "character"}
    return [chars[cid] for cid in character_ids if cid in chars]


async def run_pipeline_job(job_id: str, request: PipelineRequest):
    """Background job runner with SQLite-backed status tracking."""
    set_job(job_id, {
        "status": "running", "video_id": None, "error": None,
        "series_id": request.series_id, "series_title": request.series_title,
        "episode_number": request.episode_number,
        "webhook_callback": request.webhook_callback, "webhook_status": None,
    })

    # Resolve series context if series_id provided
    series_data = {}
    if request.series_id:
        series_data = get_series_db(request.series_id) or {}

    characters = _resolve_characters(request.character_ids)

    try:
        package = await run_pipeline(
            genre=request.genre or series_data.get("genre"),
            tone=request.tone or series_data.get("tone"),
            series_id=request.series_id,
            series_title=request.series_title or series_data.get("title", ""),
            episode_number=request.episode_number,
            story_prompt=request.story_prompt,
            previously_on=request.previously_on or series_data.get("last_cliffhanger", ""),
            world_notes=request.world_notes or series_data.get("world_notes", ""),
            characters=characters,
            narration_style=request.narration_style,
            visual_style=request.visual_style or series_data.get("visual_style", ""),
            color_grade=request.color_grade or series_data.get("color_grade", ""),
            music_mood=request.music_mood,
            themes=request.themes,
            skip_voice=request.skip_voice,
            skip_thumbnail=request.skip_thumbnail,
            skip_video=request.skip_video,
            scene_style=request.scene_style,
            thumbnail_model=request.thumbnail_model,
            avatar_id=request.avatar_id,
            avatar_model=request.avatar_model,
            voice_id=request.voice_id,
            style_locked_broll=request.style_locked_broll,
            video_model=request.video_model,
            reference_image_id=request.reference_image_id,
            use_native_audio=request.use_native_audio,
            transition=request.transition,
            card_style=request.card_style,
            cinematic_style=request.cinematic_style,
            reference_all_clips=request.reference_all_clips,
        )

        # Update series last_cliffhanger and episode_count
        if request.series_id and series_data:
            series_data["last_cliffhanger"] = package.episode_brief.cliffhanger
            series_data["episode_count"] = series_data.get("episode_count", 0) + 1
            set_series_db(request.series_id, series_data)

        result = {
            "status": "complete",
            "video_id": package.video_id,
            "series_id": package.series_id,
            "series_title": package.series_title,
            "episode_number": package.episode_number,
            "episode_title": package.script.episode_title,
            "synopsis": package.metadata.synopsis,
            "title": package.metadata.title,
            "cliffhanger": package.episode_brief.cliffhanger,
            "word_count": package.script.word_count,
            "duration_min": package.script.estimated_duration_minutes,
            "audio_path": package.audio_path,
            "thumbnail_path": package.thumbnail_path,
            "video_path": package.video_path,
            "tags": package.metadata.tags,
            "stage_timings": package.stage_timings,
            "full_script": package.script.full_text,
            "description": package.metadata.description,
            "thumbnail_paths": [c.rendered_path for c in package.thumbnail_concepts if c.rendered_path],
            "webhook_callback": request.webhook_callback,
            "webhook_status": None,
        }

        if request.webhook_callback:
            try:
                await _fire_webhook(request.webhook_callback, result)
                result["webhook_status"] = "delivered"
            except Exception:
                result["webhook_status"] = "failed"

        set_job(job_id, result)

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        set_job(job_id, {
            "status": "failed", "error": str(e),
            "webhook_callback": request.webhook_callback,
            "webhook_status": None,
        })


@app.get("/health")
async def health():
    from openai import AsyncOpenAI
    from groq import AsyncGroq

    async def check_openai() -> str:
        try:
            await AsyncOpenAI(api_key=settings.openai_api_key).models.list()
            return "ok"
        except Exception as e:
            return f"error: {e}"

    async def check_groq() -> str:
        try:
            await AsyncGroq(api_key=settings.groq_api_key).models.list()
            return "ok"
        except Exception as e:
            return f"error: {e}"

    openai_status, groq_status = await asyncio.gather(check_openai(), check_groq())
    fal_status = "ok" if settings.fal_key else "missing"

    all_ok = all(s == "ok" for s in [openai_status, groq_status, fal_status])
    return {
        "status": "ok" if all_ok else "degraded",
        "version": "2.0.0",
        "providers": {
            "openai": openai_status,
            "groq": groq_status,
            "fal": fal_status,
            "elevenlabs": "configured" if settings.elevenlabs_api_key else "not configured (optional)",
        },
    }


@app.post("/run")
async def trigger_pipeline(
    request: PipelineRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None),
):
    """Trigger a single episode pipeline run. Returns job_id immediately."""
    verify_api_key(x_api_key)
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    set_job(job_id, {"status": "queued"})
    background_tasks.add_task(run_pipeline_job, job_id, request)
    return {"job_id": job_id, "status": "queued"}


@app.post("/run/sync")
async def trigger_pipeline_sync(
    request: PipelineRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """Synchronous episode pipeline — blocks until complete."""
    verify_api_key(x_api_key)
    series_data = {}
    if request.series_id:
        series_data = get_series_db(request.series_id) or {}
    characters = _resolve_characters(request.character_ids)
    try:
        package = await run_pipeline(
            genre=request.genre or series_data.get("genre"),
            tone=request.tone or series_data.get("tone"),
            series_id=request.series_id,
            series_title=request.series_title or series_data.get("title", ""),
            episode_number=request.episode_number,
            story_prompt=request.story_prompt,
            previously_on=request.previously_on or series_data.get("last_cliffhanger", ""),
            world_notes=request.world_notes or series_data.get("world_notes", ""),
            characters=characters,
            narration_style=request.narration_style,
            visual_style=request.visual_style or series_data.get("visual_style", ""),
            skip_voice=request.skip_voice,
            skip_thumbnail=request.skip_thumbnail,
            skip_video=request.skip_video,
        )
        return {
            "status": "complete",
            "video_id": package.video_id,
            "episode_title": package.script.episode_title,
            "episode_number": package.episode_number,
            "synopsis": package.metadata.synopsis,
            "cliffhanger": package.episode_brief.cliffhanger,
            "word_count": package.script.word_count,
            "duration_min": package.script.estimated_duration_minutes,
            "audio_path": package.audio_path,
            "thumbnail_path": package.thumbnail_path,
            "full_script": package.script.full_text,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run/batch")
async def trigger_batch(
    request: BatchRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None),
):
    """Trigger a batch run of N episodes."""
    verify_api_key(x_api_key)
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    prompts = request.story_prompts

    async def run_batch_job():
        set_job(batch_id, {"status": "running", "count": request.count, "completed": 0})
        sem = asyncio.Semaphore(3)

        async def _run_one(index: int):
            async with sem:
                story_prompt = prompts[index] if index < len(prompts) else ""
                try:
                    return await run_pipeline(
                        genre=request.genre,
                        tone=request.tone,
                        series_id=request.series_id,
                        series_title=request.series_title,
                        episode_number=request.episode_number + index,
                        story_prompt=story_prompt,
                        skip_voice=request.skip_voice,
                        skip_thumbnail=request.skip_thumbnail,
                        skip_video=request.skip_video,
                    )
                except Exception as e:
                    logger.error(f"Batch episode {index+1} failed: {e}")
                    return None

        results = await asyncio.gather(*[_run_one(i) for i in range(request.count)])
        packages = [r for r in results if r is not None]
        set_job(batch_id, {
            "status": "complete",
            "count": request.count,
            "completed": len(packages),
            "video_ids": [p.video_id for p in packages],
            "titles": [p.metadata.title for p in packages],
        })

    background_tasks.add_task(run_batch_job)
    return {"batch_id": batch_id, "status": "queued", "count": request.count}


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    data = get_job(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return data


@app.get("/jobs")
async def list_jobs_endpoint():
    return {"jobs": all_jobs()}


# ── Series endpoints ──────────────────────────────────────────────────────────

@app.post("/series")
async def create_series(
    request: SeriesRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """Create a new story series."""
    verify_api_key(x_api_key)
    series_id = f"ser_{uuid.uuid4().hex[:10]}"
    data = {
        "series_id": series_id,
        "title": request.title,
        "genre": request.genre,
        "tone": request.tone,
        "logline": request.logline,
        "world_notes": request.world_notes,
        "visual_style": request.visual_style,
        "color_grade": request.color_grade,
        "character_ids": [],
        "episode_count": 0,
        "last_cliffhanger": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    set_series_db(series_id, data)
    return data


@app.get("/series")
async def list_series():
    """List all series."""
    return {"series": all_series_db()}


@app.get("/series/{series_id}")
async def get_series(series_id: str):
    """Get a specific series."""
    data = get_series_db(series_id)
    if not data:
        raise HTTPException(status_code=404, detail="Series not found")
    return data


@app.put("/series/{series_id}")
async def update_series(
    series_id: str,
    request: SeriesRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """Update a series."""
    verify_api_key(x_api_key)
    existing = get_series_db(series_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Series not found")
    existing.update({
        "title": request.title,
        "genre": request.genre,
        "tone": request.tone,
        "logline": request.logline,
        "world_notes": request.world_notes,
        "visual_style": request.visual_style,
        "color_grade": request.color_grade,
    })
    set_series_db(series_id, existing)
    return existing


@app.delete("/series/{series_id}")
async def delete_series(series_id: str, x_api_key: Optional[str] = Header(default=None)):
    """Delete a series."""
    verify_api_key(x_api_key)
    if not delete_series_db(series_id):
        raise HTTPException(status_code=404, detail="Series not found")
    return {"deleted": series_id}


def _scan_series_episodes(series_id: str) -> list[dict]:
    output_dir = Path(settings.output_dir)
    episodes = []
    for pkg_file in sorted(output_dir.glob("ep_*_package.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(pkg_file.read_text())
            if data.get("series_id") == series_id:
                script = data.get("script") or {}
                metadata = data.get("metadata") or {}
                brief = data.get("episode_brief") or {}
                episodes.append({
                    "video_id": data.get("video_id"),
                    "episode_number": data.get("episode_number", 1),
                    "episode_title": script.get("episode_title", ""),
                    "synopsis": metadata.get("synopsis", ""),
                    "cliffhanger": brief.get("cliffhanger", ""),
                    "duration_min": script.get("estimated_duration_minutes"),
                    "audio_path": data.get("audio_path"),
                    "video_path": data.get("video_path"),
                    "thumbnail_path": data.get("thumbnail_path"),
                    "created_at": data.get("created_at"),
                })
        except Exception:
            continue
    episodes.sort(key=lambda e: e.get("episode_number", 0))
    return episodes


@app.get("/series/{series_id}/episodes")
async def list_series_episodes(series_id: str):
    """List all completed episodes for a series from filesystem scan."""
    episodes = await asyncio.to_thread(_scan_series_episodes, series_id)
    return {"series_id": series_id, "episodes": episodes}


# ── Avatar endpoints ──────────────────────────────────────────────────────────

def _avatar_index_path() -> Path:
    return Path(settings.avatar_store_dir) / "index.json"


def _load_avatar_index() -> list[dict]:
    p = _avatar_index_path()
    if p.exists():
        return json.loads(p.read_text())
    return []


def _save_avatar_index(entries: list[dict]) -> None:
    p = _avatar_index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2))


@app.post("/avatars")
async def generate_avatar(
    request: AvatarRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """Generate and store a narrator/presenter avatar image."""
    verify_api_key(x_api_key)
    fal_client.api_key = settings.fal_key
    avatar_id = uuid.uuid4().hex[:10]
    store_dir = Path(settings.avatar_store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)

    style_desc = f", {request.style} style" if request.style else ""
    try:
        result = await fal_client.run_async(
            "fal-ai/flux/dev",
            arguments={
                "prompt": (
                    f"cinematic {request.genre} story narrator, looking directly at camera, "
                    f"dramatic lighting, moody studio background with soft bokeh{style_desc}, "
                    "intense focused expression, portrait photograph, sharp focus, 4K"
                ),
                "image_size": {"width": 512, "height": 512},
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": 1,
                "output_format": "jpeg",
                "enable_safety_checker": False,
            },
        )
        image_url = result["images"][0]["url"]
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            avatar_path = store_dir / f"{avatar_id}.jpg"
            avatar_path.write_bytes(resp.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Avatar generation failed: {e}")

    entry = {
        "avatar_id": avatar_id,
        "genre": request.genre,
        "style": request.style,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "path": str(avatar_path),
    }
    index = _load_avatar_index()
    index.append(entry)
    _save_avatar_index(index)
    return entry


@app.get("/avatars")
async def list_avatars():
    return {"avatars": _load_avatar_index()}


@app.delete("/avatars/{avatar_id}")
async def delete_avatar(avatar_id: str, x_api_key: Optional[str] = Header(default=None)):
    verify_api_key(x_api_key)
    index = _load_avatar_index()
    new_index = [e for e in index if e["avatar_id"] != avatar_id]
    if len(new_index) == len(index):
        raise HTTPException(status_code=404, detail="Avatar not found")
    Path(settings.avatar_store_dir, f"{avatar_id}.jpg").unlink(missing_ok=True)
    _save_avatar_index(new_index)
    return {"deleted": avatar_id}


# ── Reference media endpoints ─────────────────────────────────────────────────

def _ref_media_index_path() -> Path:
    return Path(settings.reference_media_dir) / "index.json"


def _load_ref_media_index() -> list[dict]:
    p = _ref_media_index_path()
    if p.exists():
        return json.loads(p.read_text())
    return []


def _save_ref_media_index(entries: list[dict]) -> None:
    p = _ref_media_index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2))


@app.post("/reference-media")
async def upload_reference_media(
    file: UploadFile = File(...),
    media_type: str = Form(default="image"),
    x_api_key: Optional[str] = Header(default=None),
):
    verify_api_key(x_api_key)
    store_dir = Path(settings.reference_media_dir)
    store_dir.mkdir(parents=True, exist_ok=True)

    ref_id = uuid.uuid4().hex[:10]
    content = await file.read()

    ext = mimetypes.guess_extension(file.content_type or "application/octet-stream") or ""
    ext = ext.replace(".jpe", ".jpg").replace(".jfif", ".jpg")
    if not ext or ext == ".bin":
        orig_ext = Path(file.filename or "").suffix
        ext = orig_ext if orig_ext else ".bin"

    dest = store_dir / f"{ref_id}{ext}"
    dest.write_bytes(content)

    entry = {
        "ref_id": ref_id,
        "filename": file.filename,
        "media_type": media_type,
        "mime_type": file.content_type,
        "path": str(dest),
        "size_bytes": len(content),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    index = _load_ref_media_index()
    index.append(entry)
    _save_ref_media_index(index)
    return entry


@app.get("/reference-media")
async def list_reference_media():
    return {"items": _load_ref_media_index()}


@app.delete("/reference-media/{ref_id}")
async def delete_reference_media(ref_id: str, x_api_key: Optional[str] = Header(default=None)):
    verify_api_key(x_api_key)
    index = _load_ref_media_index()
    entry = next((e for e in index if e["ref_id"] == ref_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Reference media not found")
    Path(entry["path"]).unlink(missing_ok=True)
    _save_ref_media_index([e for e in index if e["ref_id"] != ref_id])
    return {"deleted": ref_id}


# ── Character endpoints ───────────────────────────────────────────────────────

@app.post("/characters/generate")
async def generate_character(
    prompt: str = Form(...),
    name: str = Form(default=""),
    role: str = Form(default="supporting"),
    description: str = Form(default=""),
    backstory: str = Form(default=""),
    voice_id: str = Form(default=""),
    x_api_key: Optional[str] = Header(default=None),
):
    """Generate a character image with Flux and store with full profile."""
    verify_api_key(x_api_key)
    fal_client.api_key = settings.fal_key
    ref_id = uuid.uuid4().hex[:10]
    store_dir = Path(settings.reference_media_dir)
    store_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = await fal_client.run_async(
            "fal-ai/flux/dev",
            arguments={
                "prompt": prompt,
                "image_size": {"width": 512, "height": 512},
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": 1,
                "output_format": "jpeg",
                "enable_safety_checker": False,
            },
        )
        image_url = result["images"][0]["url"]
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            char_path = store_dir / f"{ref_id}.jpg"
            char_path.write_bytes(resp.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Character generation failed: {e}")

    entry = {
        "ref_id": ref_id,
        "filename": f"{ref_id}.jpg",
        "media_type": "character",
        "mime_type": "image/jpeg",
        "path": str(char_path),
        "size_bytes": char_path.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "name": name or "",
        "role": role,
        "description": description,
        "backstory": backstory,
        "voice_id": voice_id,
        "prompt": prompt,
    }
    index = _load_ref_media_index()
    index.append(entry)
    _save_ref_media_index(index)
    return entry


@app.get("/characters")
async def list_characters():
    """List all stored character profiles."""
    index = _load_ref_media_index()
    chars = [e for e in index if e.get("media_type") == "character"]
    return {"characters": chars}


@app.put("/characters/{char_id}")
async def update_character(
    char_id: str,
    name: str = Form(default=""),
    role: str = Form(default="supporting"),
    description: str = Form(default=""),
    backstory: str = Form(default=""),
    voice_id: str = Form(default=""),
    x_api_key: Optional[str] = Header(default=None),
):
    """Update a character's profile fields."""
    verify_api_key(x_api_key)
    index = _load_ref_media_index()
    entry = next((e for e in index if e["ref_id"] == char_id and e.get("media_type") == "character"), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Character not found")
    entry.update({"name": name, "role": role, "description": description, "backstory": backstory, "voice_id": voice_id})
    _save_ref_media_index(index)
    return entry


@app.delete("/characters/{char_id}")
async def delete_character(char_id: str, x_api_key: Optional[str] = Header(default=None)):
    verify_api_key(x_api_key)
    index = _load_ref_media_index()
    entry = next((e for e in index if e["ref_id"] == char_id), None)
    if not entry or entry.get("media_type") != "character":
        raise HTTPException(status_code=404, detail="Character not found")
    Path(entry["path"]).unlink(missing_ok=True)
    _save_ref_media_index([e for e in index if e["ref_id"] != char_id])
    return {"deleted": char_id}


# ── Package / file serving endpoints ─────────────────────────────────────────

@app.get("/jobs/{job_id}/clips")
async def get_job_clips(job_id: str):
    """Return available clip files for a job — works during and after generation."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    video_dir = Path(settings.video_dir)
    clips = []

    # Work directory (in-progress clips): video_dir/{video_id}/c*.mp4
    video_id = job.get("video_id") or job_id
    work_dir = video_dir / video_id
    if work_dir.is_dir():
        for f in sorted(work_dir.glob("c*.mp4")):
            rel = f"video/{video_id}/{f.name}"
            clips.append({"path": rel, "name": f.name, "kind": "broll", "size": f.stat().st_size})

    # Final assembled video
    final = video_dir / f"{video_id}_final.mp4"
    if final.exists():
        clips.append({"path": f"video/{video_id}_final.mp4", "name": "final.mp4", "kind": "final", "size": final.stat().st_size})

    return {
        "job_id": job_id,
        "video_id": video_id,
        "status": job.get("status"),
        "clips": clips,
        "episode_title": job.get("episode_title"),
        "series_title": job.get("series_title"),
        "episode_number": job.get("episode_number"),
        "thumbnail_path": job.get("thumbnail_path"),
        "thumbnail_paths": job.get("thumbnail_paths") or [],
        "duration_min": job.get("duration_min"),
        "video_path": job.get("video_path"),
        "audio_path": job.get("audio_path"),
        "synopsis": job.get("synopsis"),
        "cliffhanger": job.get("cliffhanger"),
        "tags": job.get("tags") or [],
    }


@app.get("/packages/{video_id}")
async def get_package(video_id: str):
    package_path = Path(settings.output_dir) / f"{video_id}_package.json"
    if not package_path.exists():
        raise HTTPException(status_code=404, detail="Package not found")
    return json.loads(package_path.read_text())


@app.get("/files/{path:path}")
async def serve_output_file(path: str):
    output_root = Path(settings.output_dir).resolve()
    file_path = (output_root / path).resolve()
    try:
        file_path.relative_to(output_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(str(file_path), media_type=media_type or "application/octet-stream")


# ── Prompt preview endpoint ───────────────────────────────────────────────────

class PromptPreviewRequest(BaseModel):
    cue: str
    topic: str = "general"
    scene_style: Optional[str] = None
    style_locked: bool = False


@app.post("/preview-prompt")
async def preview_prompt(request: PromptPreviewRequest):
    from pipeline.stage6_video import _enrich_prompt
    prompts = []
    for v in range(4):
        prompts.append(_enrich_prompt(
            request.cue, request.topic, request.cue,
            variant=v, scene_style=request.scene_style, style_locked=request.style_locked,
        ))
        if request.style_locked:
            break
    return {"cue": request.cue, "scene_style": request.scene_style, "prompts": prompts}


# ── Projects (filesystem scan) ────────────────────────────────────────────────

def _scan_projects() -> list[dict]:
    output_dir = Path(settings.output_dir)
    projects = []
    for pkg_file in sorted(output_dir.glob("ep_*_package.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(pkg_file.read_text())
            script = data.get("script") or {}
            metadata = data.get("metadata") or {}
            brief = data.get("episode_brief") or {}
            thumb_paths = [
                c.get("rendered_path") for c in (data.get("thumbnail_concepts") or [])
                if isinstance(c, dict) and c.get("rendered_path")
            ]
            projects.append({
                "video_id": data.get("video_id", ""),
                "series_id": data.get("series_id"),
                "series_title": data.get("series_title", ""),
                "episode_number": data.get("episode_number", 1),
                "episode_title": script.get("episode_title", ""),
                "genre": data.get("genre", ""),
                "synopsis": metadata.get("synopsis", ""),
                "cliffhanger": brief.get("cliffhanger", ""),
                "title": metadata.get("title", ""),
                "created_at": data.get("created_at", ""),
                "duration_min": script.get("estimated_duration_minutes"),
                "word_count": script.get("word_count"),
                "tags": metadata.get("tags", []),
                "description": metadata.get("description", ""),
                "audio_path": data.get("audio_path"),
                "video_path": data.get("video_path"),
                "thumbnail_path": data.get("thumbnail_path"),
                "thumbnail_paths": thumb_paths,
            })
        except Exception as e:
            logger.warning(f"Could not parse {pkg_file.name}: {e}")
    return projects


@app.get("/projects")
async def list_projects():
    """List all completed episode packages from filesystem scan."""
    projects = await asyncio.to_thread(_scan_projects)
    return {"projects": projects}


# ── Background music ──────────────────────────────────────────────────────────

_BG_MUSIC_DIR = Path(settings.output_dir) / "bg_music"


def _bg_music_index_path() -> Path:
    return _BG_MUSIC_DIR / "index.json"


def _load_bg_music_index() -> list[dict]:
    p = _bg_music_index_path()
    return json.loads(p.read_text()) if p.exists() else []


def _save_bg_music_index(entries: list[dict]) -> None:
    _bg_music_index_path().parent.mkdir(parents=True, exist_ok=True)
    _bg_music_index_path().write_text(json.dumps(entries, indent=2))


@app.post("/bg-music")
async def upload_bg_music(
    file: UploadFile = File(...),
    name: str = Form(default=""),
    x_api_key: Optional[str] = Header(default=None),
):
    verify_api_key(x_api_key)
    _BG_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    music_id = uuid.uuid4().hex[:10]
    ext = Path(file.filename or "").suffix or ".mp3"
    dest = _BG_MUSIC_DIR / f"{music_id}{ext}"
    content = await file.read()
    dest.write_bytes(content)
    entry = {
        "music_id": music_id,
        "name": name or file.filename or music_id,
        "filename": file.filename,
        "path": str(dest),
        "size_bytes": len(content),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    index = _load_bg_music_index()
    index.append(entry)
    _save_bg_music_index(index)
    return entry


@app.get("/bg-music")
async def list_bg_music():
    return {"tracks": _load_bg_music_index()}


@app.delete("/bg-music/{music_id}")
async def delete_bg_music(music_id: str, x_api_key: Optional[str] = Header(default=None)):
    verify_api_key(x_api_key)
    index = _load_bg_music_index()
    entry = next((e for e in index if e["music_id"] == music_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Track not found")
    Path(entry["path"]).unlink(missing_ok=True)
    _save_bg_music_index([e for e in index if e["music_id"] != music_id])
    return {"deleted": music_id}


# ── Episode assembly ──────────────────────────────────────────────────────────

class EpisodeAssemblyRequest(BaseModel):
    video_ids: list[str]
    title: str = "Episode"
    bg_music_id: Optional[str] = None
    bg_volume: float = 0.12
    voice_volume: float = 1.0
    transition: str = "cut"


def set_episode(episode_id: str, data: dict) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO episodes (episode_id, data) VALUES (?, ?)",
            (episode_id, json.dumps(data)),
        )


def get_episode_db(episode_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT data FROM episodes WHERE episode_id = ?", (episode_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def all_episodes() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT data FROM episodes ORDER BY created_at DESC").fetchall()
    return [json.loads(r["data"]) for r in rows]


async def _assemble_episode_job(episode_id: str, req: EpisodeAssemblyRequest) -> None:
    set_episode(episode_id, {
        "episode_id": episode_id, "status": "assembling",
        "title": req.title, "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    output_root = Path(settings.output_dir).resolve()
    episodes_dir = output_root / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(episodes_dir / f"{episode_id}.mp4")

    video_paths: list[str] = []
    for vid_id in req.video_ids:
        pkg_file = output_root / f"{vid_id}_package.json"
        if not pkg_file.exists():
            continue
        try:
            pkg = json.loads(pkg_file.read_text())
            vp = pkg.get("video_path")
            if not vp:
                continue
            vp_path = Path(vp)
            if not vp_path.is_absolute():
                vp_path = output_root.parent / vp
            if vp_path.exists():
                video_paths.append(str(vp_path))
        except Exception:
            continue

    if not video_paths:
        set_episode(episode_id, {
            "episode_id": episode_id, "status": "failed",
            "title": req.title, "error": "No valid video files found for given IDs",
        })
        return

    try:
        W, H, FPS = 1280, 720, 24
        n = len(video_paths)
        inputs: list[str] = []
        for p in video_paths:
            inputs += ["-i", p]

        filter_parts: list[str] = []
        for i in range(n):
            filter_parts.append(
                f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{i}]"
            )
            filter_parts.append(f"[{i}:a]volume={req.voice_volume},aresample=44100[a{i}]")

        concat_segs = "".join(f"[v{i}][a{i}]" for i in range(n))
        filter_parts.append(f"{concat_segs}concat=n={n}:v=1:a=1[vout][aout]")
        v_map, a_map = "vout", "aout"

        if req.bg_music_id:
            bg_entry = next((e for e in _load_bg_music_index() if e["music_id"] == req.bg_music_id), None)
            if bg_entry and Path(bg_entry["path"]).exists():
                music_idx = n
                inputs += ["-i", bg_entry["path"]]
                filter_parts.append(
                    f"[{music_idx}:a]aloop=loop=-1:size=2000000000,"
                    f"volume={req.bg_volume},aresample=44100[bg]"
                )
                filter_parts.append(f"[aout][bg]amix=inputs=2:duration=first[final_a]")
                a_map = "final_a"

        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", f"[{v_map}]", "-map", f"[{a_map}]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            out_path,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg error: {stderr.decode()[-3000:]}")

        set_episode(episode_id, {
            "episode_id": episode_id, "status": "complete", "title": req.title,
            "video_path": out_path,
            "video_count": n, "created_at": datetime.now(timezone.utc).isoformat(), "error": None,
        })
        logger.info(f"Episode {episode_id} assembled: {n} clips → {out_path}")

    except Exception as e:
        logger.error(f"Episode {episode_id} assembly failed: {e}")
        set_episode(episode_id, {
            "episode_id": episode_id, "status": "failed",
            "title": req.title, "error": str(e)[:2000],
        })


@app.post("/episodes/assemble")
async def assemble_episode(
    request: EpisodeAssemblyRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None),
):
    verify_api_key(x_api_key)
    if not request.video_ids:
        raise HTTPException(status_code=400, detail="video_ids must not be empty")
    episode_id = f"ep_{uuid.uuid4().hex[:10]}"
    background_tasks.add_task(_assemble_episode_job, episode_id, request)
    return {"episode_id": episode_id, "status": "assembling"}


@app.get("/episodes/{episode_id}")
async def get_episode_status(episode_id: str):
    data = get_episode_db(episode_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return data


@app.get("/episodes")
async def list_episodes_endpoint():
    return {"episodes": all_episodes()}


# ── AI Chat (Groq) ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    messages: list[dict]
    system: Optional[str] = None


_RALPH_PATH = Path(__file__).parent / "prompts" / "ralph_masterprompt.md"
_RALPH_FALLBACK = (
    "You are Ralph, the cinematic storytelling AI embedded in RoboModal Studio. "
    "You help creators develop story arcs, characters, episode structures, cliffhangers, "
    "and visual language for serialized AI-generated video. "
    "You are a showrunner, screenwriter, and pipeline expert in one. "
    "Be concise, opinionated, and specific. Never suggest monetization or affiliate content. "
    "Opening line when starting fresh: 'Ralph here. What are we building today?'"
)
_ralph_mtime: float = 0.0
_ralph_content: str = ""


def _get_ralph_system() -> str:
    """Return Ralph's masterprompt, reloading from disk whenever the file changes."""
    global _ralph_mtime, _ralph_content
    try:
        mtime = _RALPH_PATH.stat().st_mtime
        if mtime != _ralph_mtime:
            _ralph_content = _RALPH_PATH.read_text()
            _ralph_mtime = mtime
    except FileNotFoundError:
        if not _ralph_content:
            _ralph_content = _RALPH_FALLBACK
    return _ralph_content


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """Stream a Groq response for the in-dashboard AI storytelling assistant."""
    from groq import AsyncGroq

    api_key = settings.groq_api_key
    if not api_key:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY not set in environment")

    client = AsyncGroq(api_key=api_key)
    system_prompt = request.system or _get_ralph_system()
    messages = [{"role": "system", "content": system_prompt}] + list(request.messages)

    async def generate():
        try:
            stream = await client.chat.completions.create(
                model=settings.groq_model,
                max_tokens=2048,
                messages=messages,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'type': 'text', 'content': delta})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
