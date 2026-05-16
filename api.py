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

import anthropic
import fal_client
import httpx
from fastapi import FastAPI, BackgroundTasks, File, Form, HTTPException, Header, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    logger.info(f"AI Influencer Pipeline API starting up (job store: {_DB_PATH})")
    yield
    logger.info("AI Influencer Pipeline API shutting down")


app = FastAPI(
    title="AI Influencer Pipeline API",
    description="Webhook server for n8n-triggered content production",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PipelineRequest(BaseModel):
    niche: Optional[str] = None
    tone: Optional[str] = None
    demographic: Optional[str] = None
    skip_voice: bool = False
    skip_thumbnail: bool = False
    skip_video: bool = False
    webhook_callback: Optional[str] = None
    # Advanced options
    scene_style: Optional[str] = None          # cinematic | documentary | artistic | corporate
    thumbnail_model: Optional[str] = None       # override fal thumbnail model
    avatar_id: Optional[str] = None            # use stored avatar by ID
    avatar_model: Optional[str] = None         # sadtalker | hallo
    voice_id: Optional[str] = None             # openai voice override
    style_locked_broll: bool = False           # all broll clips use same angle
    video_model: Optional[str] = None          # fal.ai video model ID
    reference_image_id: Optional[str] = None   # stored reference image for i2v
    use_native_audio: bool = False             # blend model ambient audio with voiceover
    transition: str = "cut"                    # cut | crossfade
    card_style: str = "pillow"                 # pillow | remotion
    cinematic_style: Optional[str] = None      # prefix prepended to every broll prompt
    reference_all_clips: bool = False          # apply reference image to all broll via r2v


class BatchRequest(BaseModel):
    count: int = 3
    niche: Optional[str] = None
    tone: Optional[str] = None
    demographic: Optional[str] = None
    skip_voice: bool = False
    skip_thumbnail: bool = False
    skip_video: bool = False
    topics: list[str] = []                     # per-video topic overrides


class AvatarRequest(BaseModel):
    niche: str
    style: Optional[str] = None


def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    if settings.n8n_api_key and x_api_key != settings.n8n_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def _fire_webhook(url: str, payload: dict, max_attempts: int = 3) -> None:
    """POST to webhook URL with exponential backoff retry."""
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


async def run_pipeline_job(job_id: str, request: PipelineRequest):
    """Background job runner with SQLite-backed status tracking."""
    set_job(job_id, {
        "status": "running", "video_id": None, "error": None,
        "webhook_callback": request.webhook_callback,
        "webhook_status": None,
    })
    try:
        package = await run_pipeline(
            niche=request.niche,
            tone=request.tone,
            demographic=request.demographic,
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
        result = {
            "status": "complete",
            "video_id": package.video_id,
            "title": package.seo.title,
            "topic": package.research.selected_topic.topic_title,
            "word_count": package.script.word_count,
            "duration_min": package.script.estimated_duration_minutes,
            "audio_path": package.audio_path,
            "thumbnail_path": package.thumbnail_path,
            "video_path": package.video_path,
            "tags": package.seo.tags,
            "affiliates": [a.product_name for a in package.affiliates],
            "stage_timings": package.stage_timings,
            "full_script": package.script.full_text,
            "description": package.seo.description,
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
        "version": "1.0.0",
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
    """Trigger a single video pipeline run. Returns job_id immediately."""
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
    """Synchronous pipeline run — blocks until complete. Use for n8n Wait nodes."""
    verify_api_key(x_api_key)
    try:
        package = await run_pipeline(
            niche=request.niche,
            tone=request.tone,
            demographic=request.demographic,
            skip_voice=request.skip_voice,
            skip_thumbnail=request.skip_thumbnail,
            skip_video=request.skip_video,
        )
        return {
            "status": "complete",
            "video_id": package.video_id,
            "title": package.seo.title,
            "topic": package.research.selected_topic.topic_title,
            "hook": package.script.hook,
            "word_count": package.script.word_count,
            "duration_min": package.script.estimated_duration_minutes,
            "description": package.seo.description,
            "tags": package.seo.tags,
            "chapters": package.seo.chapters,
            "audio_path": package.audio_path,
            "thumbnail_path": package.thumbnail_path,
            "stage_timings": package.stage_timings,
            "affiliates": [
                {"product": a.product_name, "script_line": a.script_line}
                for a in package.affiliates
            ],
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
    """Trigger a batch run of N videos."""
    verify_api_key(x_api_key)
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"

    topics = request.topics or []

    async def run_batch_job():
        set_job(batch_id, {"status": "running", "count": request.count, "completed": 0})

        async def _run_one_with_topic(index: int):
            niche_override = topics[index] if index < len(topics) else request.niche
            try:
                from main import run_pipeline as _rp
                return await _rp(
                    niche=niche_override,
                    tone=request.tone,
                    demographic=request.demographic,
                    skip_voice=request.skip_voice,
                    skip_thumbnail=request.skip_thumbnail,
                    skip_video=request.skip_video,
                )
            except Exception as e:
                logger.error(f"Batch video {index+1} failed: {e}")
                return None

        results = await asyncio.gather(*[_run_one_with_topic(i) for i in range(request.count)])
        packages = [r for r in results if r is not None]
        set_job(batch_id, {
            "status": "complete",
            "count": request.count,
            "completed": len(packages),
            "video_ids": [p.video_id for p in packages],
            "titles": [p.seo.title for p in packages],
        })

    background_tasks.add_task(run_batch_job)
    return {"batch_id": batch_id, "status": "queued", "count": request.count}


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Poll job status."""
    data = get_job(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return data


@app.get("/jobs")
async def list_jobs_endpoint():
    """List all jobs (most recent first)."""
    return {"jobs": all_jobs()}


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
    """Generate and store a new presenter avatar image."""
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
                    f"professional {request.niche} YouTube presenter, looking directly at camera, "
                    f"clean modern studio background with soft bokeh, business casual{style_desc}, "
                    "warm confident expression, portrait photograph, sharp focus, 4K"
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
        "niche": request.niche,
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
    """List all stored avatars."""
    return {"avatars": _load_avatar_index()}


@app.delete("/avatars/{avatar_id}")
async def delete_avatar(avatar_id: str, x_api_key: Optional[str] = Header(default=None)):
    """Delete a stored avatar."""
    verify_api_key(x_api_key)
    index = _load_avatar_index()
    new_index = [e for e in index if e["avatar_id"] != avatar_id]
    if len(new_index) == len(index):
        raise HTTPException(status_code=404, detail="Avatar not found")
    avatar_path = Path(settings.avatar_store_dir) / f"{avatar_id}.jpg"
    avatar_path.unlink(missing_ok=True)
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
    """Upload a reference image (or video/audio) to use in pipeline runs."""
    verify_api_key(x_api_key)
    store_dir = Path(settings.reference_media_dir)
    store_dir.mkdir(parents=True, exist_ok=True)

    ref_id = uuid.uuid4().hex[:10]
    content = await file.read()

    # Determine extension from MIME type, with common fixes
    ext = mimetypes.guess_extension(file.content_type or "application/octet-stream") or ""
    ext = ext.replace(".jpe", ".jpg").replace(".jfif", ".jpg")
    if not ext or ext == ".bin":
        # Fall back to original filename extension
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
    """List all stored reference media."""
    return {"items": _load_ref_media_index()}


@app.delete("/reference-media/{ref_id}")
async def delete_reference_media(ref_id: str, x_api_key: Optional[str] = Header(default=None)):
    """Delete a stored reference media item."""
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
    x_api_key: Optional[str] = Header(default=None),
):
    """Generate a character image with Flux and store it as reference media."""
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
        "prompt": prompt,
    }
    index = _load_ref_media_index()
    index.append(entry)
    _save_ref_media_index(index)
    return entry


@app.get("/characters")
async def list_characters():
    """List all stored character images."""
    index = _load_ref_media_index()
    chars = [e for e in index if e.get("media_type") == "character"]
    return {"characters": chars}


@app.delete("/characters/{char_id}")
async def delete_character(char_id: str, x_api_key: Optional[str] = Header(default=None)):
    """Delete a stored character image."""
    verify_api_key(x_api_key)
    index = _load_ref_media_index()
    entry = next((e for e in index if e["ref_id"] == char_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Character not found")
    if entry.get("media_type") != "character":
        raise HTTPException(status_code=400, detail="Not a character entry")
    Path(entry["path"]).unlink(missing_ok=True)
    _save_ref_media_index([e for e in index if e["ref_id"] != char_id])
    return {"deleted": char_id}


# ── Package / file serving endpoints ─────────────────────────────────────────

@app.get("/packages/{video_id}")
async def get_package(video_id: str):
    """Return the full JSON package for a completed video."""
    package_path = Path(settings.output_dir) / f"{video_id}_package.json"
    if not package_path.exists():
        raise HTTPException(status_code=404, detail="Package not found")
    return json.loads(package_path.read_text())


@app.get("/files/{path:path}")
async def serve_output_file(path: str):
    """Serve a file from the output directory (audio, video, thumbnails, avatars)."""
    output_root = Path(settings.output_dir).resolve()
    file_path = (output_root / path).resolve()
    # Security: must remain within output_dir
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
    """Preview the enriched B-roll prompt that would be sent to the video model."""
    from pipeline.stage6_video import _enrich_prompt
    prompts = []
    max_variants = 4
    for v in range(max_variants):
        prompts.append(_enrich_prompt(
            request.cue, request.topic, request.cue,
            variant=v, scene_style=request.scene_style, style_locked=request.style_locked,
        ))
        if request.style_locked:
            break
    return {"cue": request.cue, "scene_style": request.scene_style, "prompts": prompts}


# ── Projects (filesystem scan) ────────────────────────────────────────────────

@app.get("/projects")
async def list_projects():
    """List all completed video packages from filesystem scan (survives DB resets)."""
    output_dir = Path(settings.output_dir)
    projects = []
    for pkg_file in sorted(output_dir.glob("*_package.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(pkg_file.read_text())
            seo = data.get("seo") or {}
            script = data.get("script") or {}
            research = data.get("research") or {}
            selected_topic = (research.get("selected_topic") or {}) if isinstance(research, dict) else {}
            thumb_paths = [
                c.get("rendered_path") for c in (data.get("thumbnail_concepts") or [])
                if isinstance(c, dict) and c.get("rendered_path")
            ]
            projects.append({
                "video_id": data.get("video_id", ""),
                "title": seo.get("title", "") if isinstance(seo, dict) else "",
                "topic": selected_topic.get("topic_title", "") if isinstance(selected_topic, dict) else "",
                "niche": data.get("niche", ""),
                "created_at": data.get("created_at", ""),
                "duration_min": script.get("estimated_duration_minutes") if isinstance(script, dict) else None,
                "word_count": script.get("word_count") if isinstance(script, dict) else None,
                "tags": seo.get("tags", []) if isinstance(seo, dict) else [],
                "description": seo.get("description", "") if isinstance(seo, dict) else "",
                "audio_path": data.get("audio_path"),
                "video_path": data.get("video_path"),
                "thumbnail_path": data.get("thumbnail_path"),
                "thumbnail_paths": thumb_paths,
            })
        except Exception as e:
            logger.warning(f"Could not parse {pkg_file.name}: {e}")
    return {"projects": projects}


# ── Background music ──────────────────────────────────────────────────────────

_BG_MUSIC_DIR = Path("./output/bg_music")


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
    """Upload a background music track for episode assembly."""
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

    # Resolve absolute video paths from package files
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
            filter_parts.append(
                f"[{i}:a]volume={req.voice_volume},aresample=44100[a{i}]"
            )

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
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", f"[{v_map}]",
            "-map", f"[{a_map}]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg error: {stderr.decode()[-3000:]}")

        set_episode(episode_id, {
            "episode_id": episode_id,
            "status": "complete",
            "title": req.title,
            "video_path": f"output/episodes/{episode_id}.mp4",
            "video_count": n,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
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
    """Assemble multiple completed videos into a long-form episode with optional BG music."""
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


# ── AI Chat (Claude) ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    messages: list[dict]
    system: Optional[str] = None


_CHAT_SYSTEM = (
    "You are the RoboModal Studio AI assistant — an expert in AI-powered video content creation. "
    "You help users plan cinematic storytelling videos, choose the right pipeline settings (models, "
    "styles, niches, tones), understand results, and craft compelling narratives. "
    "You are concise, direct, and creative. When suggesting pipeline settings, be specific: name "
    "exact model choices (Kling v2 Master, Veo3, etc.), cinematic styles, and prompt strategies. "
    "You have deep knowledge of fal.ai video models, storytelling structure, and YouTube/TikTok content strategy."
)


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """Stream a Claude response for the in-dashboard AI assistant."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not set in environment")

    client = anthropic.AsyncAnthropic(api_key=api_key)
    system_prompt = request.system or _CHAT_SYSTEM

    async def generate():
        try:
            async with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=system_prompt,
                messages=request.messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
