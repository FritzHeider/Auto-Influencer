import json
import logging
import sqlite3
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header
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


class PipelineRequest(BaseModel):
    niche: Optional[str] = None
    tone: Optional[str] = None
    demographic: Optional[str] = None
    skip_voice: bool = False
    skip_thumbnail: bool = False
    skip_video: bool = False
    webhook_callback: Optional[str] = None


class BatchRequest(BaseModel):
    count: int = 3
    niche: Optional[str] = None
    tone: Optional[str] = None
    demographic: Optional[str] = None
    skip_voice: bool = False
    skip_thumbnail: bool = False


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
    set_job(job_id, {"status": "running", "video_id": None, "error": None})
    try:
        package = await run_pipeline(
            niche=request.niche,
            tone=request.tone,
            demographic=request.demographic,
            skip_voice=request.skip_voice,
            skip_thumbnail=request.skip_thumbnail,
            skip_video=request.skip_video,
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
            "tags": package.seo.tags,
            "affiliates": [a.product_name for a in package.affiliates],
            "stage_timings": package.stage_timings,
        }
        set_job(job_id, result)

        if request.webhook_callback:
            await _fire_webhook(request.webhook_callback, result)

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        set_job(job_id, {"status": "failed", "error": str(e)})


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

    async def run_batch_job():
        set_job(batch_id, {"status": "running", "count": request.count, "completed": 0})
        packages = await run_batch(
            count=request.count,
            niche=request.niche,
            tone=request.tone,
            demographic=request.demographic,
            skip_voice=request.skip_voice,
            skip_thumbnail=request.skip_thumbnail,
        )
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
