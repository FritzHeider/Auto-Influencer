import json
import logging
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, EpisodeMetadata
from prompts.system_prompts import EPISODE_METADATA_PROMPT

logger = logging.getLogger(__name__)

_openai_client = AsyncOpenAI(api_key=settings.openai_api_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def generate_episode_metadata(
    script: Script,
    genre: str,
    series_title: str = "",
    themes: list[str] | None = None,
) -> EpisodeMetadata:
    """Generate episode title, description, tags, chapters, and synopsis."""
    script_summary = " ".join(s.content[:120] for s in script.sections[:4])

    prompt = EPISODE_METADATA_PROMPT.format(
        series_title=series_title or "Untitled Series",
        episode_title=script.episode_title,
        episode_number=script.episode_number,
        genre=genre,
        script_summary=script_summary,
        themes=", ".join(themes or []),
        cliffhanger=script.cliffhanger,
    )

    response = await _openai_client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Respond only with valid JSON. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    metadata = EpisodeMetadata(**data)
    logger.info(f"Episode metadata: '{metadata.title}' | {len(metadata.tags)} tags")
    return metadata
