import json
import base64
import logging
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from config.settings import settings
from pipeline.models import Script, SEOPackage, ThumbnailConcept
from prompts.system_prompts import THUMBNAIL_PROMPT

logger = logging.getLogger(__name__)

FIREWORKS_IMAGE_URL = "https://api.fireworks.ai/inference/v1/image_generation/accounts/fireworks/models/stable-diffusion-xl-1024-v1-0"


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


async def render_thumbnail_fireworks(concept: ThumbnailConcept, output_path: Path) -> bool:
    """Render winning thumbnail concept via Fireworks AI."""
    payload = {
        "prompt": concept.fireworks_prompt,
        "negative_prompt": "text, watermark, logo, human face, person, nsfw, blurry, low quality",
        "width": 1280,
        "height": 720,
        "num_inference_steps": 30,
        "guidance_scale": 7.5,
        "num_images": 1,
        "output_image_format": "JPEG",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(
                FIREWORKS_IMAGE_URL,
                headers={
                    "Authorization": f"Bearer {settings.fireworks_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            image_b64 = data["output"][0]
            image_bytes = base64.b64decode(image_b64)
            output_path.write_bytes(image_bytes)
            logger.info(f"Thumbnail rendered: {output_path}")
            return True
        except Exception as e:
            logger.error(f"Fireworks image generation failed: {e}")
            return False


async def generate_thumbnail(
    script: Script,
    seo: SEOPackage,
    niche: str,
    video_id: str,
) -> tuple[list[ThumbnailConcept], ThumbnailConcept, str | None]:
    """Full thumbnail pipeline: concept generation + rendering."""
    thumbnail_dir = Path(settings.thumbnail_dir)
    thumbnail_dir.mkdir(parents=True, exist_ok=True)

    concepts = await generate_thumbnail_concepts(script, seo, niche)
    winner = next((c for c in concepts if c.is_winner), concepts[0])

    output_path = thumbnail_dir / f"{video_id}_thumbnail.jpg"
    success = await render_thumbnail_fireworks(winner, output_path)

    thumbnail_path = str(output_path) if success else None
    return concepts, winner, thumbnail_path
