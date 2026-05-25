import json
import logging
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, ScriptSection, EpisodeBrief
from prompts.system_prompts import EPISODE_SCRIPT_PROMPT

logger = logging.getLogger(__name__)

_openai_client = AsyncOpenAI(api_key=settings.openai_api_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def generate_script(
    brief: EpisodeBrief,
    genre: str,
    tone: str,
    series_title: str = "",
    episode_number: int = 1,
    narration_style: str = "third_person",
    visual_style: str = "",
    color_grade: str = "",
    music_mood: str = "neutral",
    characters: list[dict] | None = None,
) -> Script:
    """Generate a full cinematic episode script from the story brief."""
    logger.info(f"Generating episode script — Ep {episode_number}: {brief.episode_concept[:60]}...")

    char_summary = "\n".join(
        f"- {c.get('name', 'Unknown')} ({c.get('role', 'supporting')}): {c.get('description', '')}"
        for c in (characters or [])
    ) or "No specific characters defined."

    prompt = EPISODE_SCRIPT_PROMPT.format(
        series_title=series_title or "Untitled Series",
        genre=genre,
        tone=tone,
        narration_style=narration_style,
        episode_number=episode_number,
        episode_concept=brief.episode_concept,
        opening_hook=brief.opening_hook,
        key_beats="\n".join(f"- {b}" for b in brief.key_beats),
        characters=char_summary,
        visual_style=visual_style or "cinematic, high production value",
        color_grade=color_grade or "neutral",
        music_mood=music_mood,
    )

    response = await _openai_client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert cinematic screenwriter for serialized AI video episodes. "
                    "Respond only with valid JSON. No markdown, no backticks, no preamble."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.85,
        max_tokens=6000,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    data.setdefault("episode_number", episode_number)
    data.setdefault("series_title", series_title)
    data.setdefault("narration_style", narration_style)
    data.setdefault("cliffhanger", brief.cliffhanger)
    try:
        script = Script.model_validate(data)
    except Exception as e:
        logger.error(f"Script schema mismatch: {e} | keys returned: {list(data)}")
        raise

    logger.info(
        f"Script complete: {script.word_count} words, "
        f"~{script.estimated_duration_minutes:.1f} min, "
        f"{len(script.sections)} sections — '{script.episode_title}'"
    )
    return script
