import json
import logging
from openai import AsyncOpenAI

from config.settings import settings
from pipeline.models import Script, ScriptSection, ResearchResult
from prompts.system_prompts import SCRIPT_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_AFFILIATES = {
    "personal finance": ["Robinhood", "Acorns", "Personal Capital"],
    "tech": ["Amazon", "NordVPN", "Skillshare"],
    "health": ["AG1", "Whoop", "Calm"],
    "productivity": ["Notion", "Todoist", "Grammarly"],
    "real estate": ["Fundrise", "Arrived Homes", "Roofstock"],
}


def get_affiliate_products(niche: str) -> list[str]:
    """Return default affiliate products for niche."""
    niche_lower = niche.lower()
    for key, products in DEFAULT_AFFILIATES.items():
        if key in niche_lower:
            return products
    return ["Amazon", "Skillshare", "NordVPN"]


async def generate_script(research: ResearchResult, niche: str, tone: str, demographic: str) -> Script:
    """Generate full video script from research results."""
    logger.info(f"Generating script for: {research.selected_topic.topic_title}")

    affiliate_products = get_affiliate_products(niche)
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = SCRIPT_PROMPT.format(
        niche=niche,
        tone=tone,
        demographic=demographic,
        topic=research.selected_topic.topic_title,
        hook=research.winning_hook,
        affiliate_products=", ".join(affiliate_products),
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert YouTube scriptwriter. "
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

    sections = [ScriptSection(**s) for s in data["sections"]]

    script = Script(
        topic=data["topic"],
        hook=data["hook"],
        sections=sections,
        full_text=data["full_text"],
        word_count=data["word_count"],
        estimated_duration_minutes=data["estimated_duration_minutes"],
        affiliate_products=data.get("affiliate_products", affiliate_products),
    )

    logger.info(
        f"Script complete: {script.word_count} words, "
        f"~{script.estimated_duration_minutes:.1f} min, "
        f"{len(script.sections)} sections"
    )
    return script
