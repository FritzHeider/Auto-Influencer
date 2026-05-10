import asyncio
import json
import logging
from pathlib import Path

import fal_client
import httpx
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, SEOPackage, ThumbnailConcept
from prompts.system_prompts import THUMBNAIL_PROMPT

logger = logging.getLogger(__name__)


async def generate_thumbnail_concepts(
    script: Script,
    seo: SEOPackage,
    niche: str,
) -> list[ThumbnailConcept]:
    """Use OpenAI to plan thumbnail concepts."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = THUMBNAIL_PROMPT.format(
        niche=niche,
        title=seo.title,
        hook=script.hook,
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Respond only with valid JSON. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.9,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    concepts = [ThumbnailConcept(**c) for c in data["concepts"]]
    logger.info(f"Generated {len(concepts)} thumbnail concepts")
    return concepts


async def render_thumbnail_fal(concept: ThumbnailConcept, output_path: Path) -> bool:
    """Render winning thumbnail concept via fal.ai Flux."""
    fal_client.api_key = settings.fal_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    async def _render():
        result = await fal_client.run_async(
            settings.fal_model,
            arguments={
                "prompt": concept.image_prompt,
                "negative_prompt": "text, watermark, logo, human face, person, nsfw, blurry, low quality",
                "image_size": {"width": 1344, "height": 768},
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
            output_path.write_bytes(resp.content)

    try:
        await _render()
        logger.info(f"Thumbnail rendered via fal.ai ({settings.fal_model}): {output_path}")
        return True
    except Exception as e:
        logger.error(f"fal.ai image generation failed after retries: {e}")
        return False


async def generate_thumbnail(
    script: Script,
    seo: SEOPackage,
    niche: str,
    video_id: str,
) -> tuple[list[ThumbnailConcept], ThumbnailConcept, str | None]:
    """Full thumbnail pipeline: concept generation + rendering all concepts in parallel."""
    thumbnail_dir = Path(settings.thumbnail_dir)
    thumbnail_dir.mkdir(parents=True, exist_ok=True)

    concepts = await generate_thumbnail_concepts(script, seo, niche)

    paths = [thumbnail_dir / f"{video_id}_thumbnail_{c.concept_id}.jpg" for c in concepts]
    results = await asyncio.gather(*[
        render_thumbnail_fal(concept, path)
        for concept, path in zip(concepts, paths)
    ])

    for concept, path, ok in zip(concepts, paths, results):
        if ok:
            concept.rendered_path = str(path)

    winner = next((c for c in concepts if c.is_winner), concepts[0])
    return concepts, winner, winner.rendered_path
