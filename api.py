import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config.settings import settings
from main import run_pipeline, run_batch
from pipeline.models import VideoPackage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory job tracker (use Redis in production)
jobs: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI Influencer Pipeline API starting up")
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


async def run_pipeline_job(job_id: str, request: PipelineRequest):
    """Background job runner with status tracking."""
    jobs[job_id] = {"status": "running", "video_id": None, "error": None}
    try:
        package = await run_pipeline(
            niche=request.niche,
            tone=request.tone,
            demographic=request.demographic,
            skip_voice=request.skip_voice,
            skip_thumbnail=request.skip_thumbnail,
        )
        jobs[job_id] = {
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
        }

        # Optional callback to n8n or other webhook
        if request.webhook_callback:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(request.webhook_callback, json=jobs[job_id], timeout=10)

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        jobs[job_id] = {"status": "failed", "error": str(e)}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/run")
async def trigger_pipeline(
    request: PipelineRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None),
):
    """Trigger a single video pipeline run. Returns job_id immediately."""
    verify_api_key(x_api_key)
    import uuid
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    jobs[job_id] = {"status": "queued"}
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
    import uuid
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"

    async def run_batch_job():
        jobs[batch_id] = {"status": "running", "count": request.count, "completed": 0}
        packages = await run_batch(
            count=request.count,
            niche=request.niche,
            tone=request.tone,
            demographic=request.demographic,
            skip_voice=request.skip_voice,
            skip_thumbnail=request.skip_thumbnail,
        )
        jobs[batch_id] = {
            "status": "complete",
            "count": request.count,
            "completed": len(packages),
            "video_ids": [p.video_id for p in packages],
            "titles": [p.seo.title for p in packages],
        }

    background_tasks.add_task(run_batch_job)
    return {"batch_id": batch_id, "status": "queued", "count": request.count}


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Poll job status."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.get("/jobs")
async def list_jobs():
    """List all jobs."""
    return {"jobs": jobs}
