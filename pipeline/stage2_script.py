import json
import logging
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, ScriptSection, ResearchResult
from prompts.system_prompts import SCRIPT_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_AFFILIATES = {
    "personal finance": ["Robinhood", "Acorns", "Personal Capital"],
    "investing": ["Public.com", "M1 Finance", "Masterworks"],
    "real estate": ["Fundrise", "Arrived Homes", "Roofstock"],
    "tech": ["Amazon", "NordVPN", "Skillshare"],
    "ai": ["Jasper", "Midjourney", "Copy.ai"],
    "productivity": ["Notion", "Todoist", "Grammarly"],
    "business": ["Shopify", "FreshBooks", "Fiverr"],
    "entrepreneurship": ["Shopify", "Teachable", "ConvertKit"],
    "health": ["AG1", "Whoop", "Calm"],
    "fitness": ["MyProtein", "Gainful", "Whoop"],
    "mental health": ["BetterHelp", "Calm", "Headspace"],
    "cooking": ["HelloFresh", "Thrive Market", "Made In Cookware"],
    "travel": ["Booking.com", "Airbnb", "SafetyWing"],
    "gaming": ["Razer", "SteelSeries", "NordVPN"],
    "beauty": ["Sephora", "ILIA Beauty", "Fenty Beauty"],
    "fashion": ["ASOS", "ThredUp", "Rent the Runway"],
    "parenting": ["KiwiCo", "Lovevery", "Amazon"],
    "education": ["Skillshare", "Coursera", "Brilliant"],
    "automotive": ["CarGurus", "Carvana", "Meineke"],
    "pets": ["Chewy", "BarkBox", "Petco"],
}


def get_affiliate_products(niche: str) -> list[str]:
    """Return default affiliate products for niche."""
    niche_lower = niche.lower()
    for key, products in DEFAULT_AFFILIATES.items():
        if key in niche_lower:
            return products
    return ["Amazon", "Skillshare", "NordVPN"]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
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
