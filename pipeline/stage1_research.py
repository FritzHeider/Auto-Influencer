import json
import logging
from groq import AsyncGroq
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import EpisodeBrief
from prompts.system_prompts import STORY_BRIEF_PROMPT

logger = logging.getLogger(__name__)

_groq_client = AsyncGroq(api_key=settings.groq_api_key)
_openai_client = AsyncOpenAI(api_key=settings.openai_api_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def generate_story_brief(
    genre: str,
    tone: str,
    series_title: str = "",
    world_notes: str = "",
    characters: list[dict] | None = None,
    previously_on: str = "",
    story_prompt: str = "",
    episode_number: int = 1,
) -> EpisodeBrief:
    """Generate a story brief / episode concept using Groq (fast ideation)."""
    logger.info(f"Generating story brief — Series: {series_title!r}, Ep {episode_number}, Genre: {genre}")

    char_summary = ", ".join(
        f"{c.get('name', 'Unknown')} ({c.get('role', 'supporting')})"
        for c in (characters or [])
    ) or "No recurring characters defined"

    prompt = STORY_BRIEF_PROMPT.format(
        series_title=series_title or "Untitled Series",
        genre=genre,
        tone=tone,
        world_notes=world_notes or "No world notes provided.",
        characters=char_summary,
        previously_on=previously_on or "This is the first episode.",
        story_prompt=story_prompt or "Let the story emerge organically from the genre and characters.",
        episode_number=episode_number,
    )

    try:
        response = await _groq_client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.warning(f"Groq story brief failed: {e}, falling back to OpenAI")
        response = await _openai_client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": "Respond only with valid JSON. No markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)

    try:
        brief = EpisodeBrief.model_validate(data)
    except Exception as e:
        logger.error(f"Story brief schema mismatch: {e} | keys returned: {list(data)}")
        raise

    logger.info(f"Story brief complete. Hook: {brief.opening_hook[:60]}...")
    logger.info(f"Cliffhanger: {brief.cliffhanger[:60]}...")
    return brief
