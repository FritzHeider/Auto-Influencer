import json
import logging
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, SEOPackage, AffiliateInsertion
from prompts.system_prompts import SEO_PROMPT, AFFILIATE_PROMPT

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def generate_seo_package(
    script: Script,
    niche: str,
    demographic: str,
) -> SEOPackage:
    """Generate title, description, tags, and chapters."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    script_summary = " ".join(
        s.content[:100] for s in script.sections[:3]
    )

    prompt = SEO_PROMPT.format(
        niche=niche,
        topic=script.topic,
        hook=script.hook,
        script_summary=script_summary,
        demographic=demographic,
    )

    response = await client.chat.completions.create(
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
    seo = SEOPackage(**data)
    logger.info(f"SEO package: '{seo.title}' | {len(seo.tags)} tags")
    return seo


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def generate_affiliate_insertions(
    script: Script,
    niche: str,
) -> list[AffiliateInsertion]:
    """Generate affiliate product recommendations and script insertions."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    sections_summary = [
        {"label": s.label, "content": s.content[:200], "insertions": s.affiliate_insertions}
        for s in script.sections
    ]

    prompt = AFFILIATE_PROMPT.format(
        niche=niche,
        topic=script.topic,
        script_sections=json.dumps(sections_summary),
        existing_insertions=", ".join(script.affiliate_products),
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Respond only with valid JSON. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.6,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    affiliates = [AffiliateInsertion(**a) for a in data["affiliates"]]
    logger.info(f"Generated {len(affiliates)} affiliate insertions")
    return affiliates
