import json
import httpx
import logging
from openai import AsyncOpenAI
from groq import AsyncGroq

from config.settings import settings
from pipeline.models import ResearchResult, TrendTopic, HookOption
from prompts.system_prompts import RESEARCH_PROMPT

logger = logging.getLogger(__name__)


async def fetch_bing_trends(niche: str) -> str:
    """Fetch trending content from Bing Search for context."""
    queries = [
        f"{niche} news today",
        f"{niche} trending 2024",
        f"best {niche} tips viral",
    ]
    results = []

    async with httpx.AsyncClient() as client:
        for query in queries:
            try:
                resp = await client.get(
                    settings.bing_search_endpoint,
                    headers={"Ocp-Apim-Subscription-Key": settings.bing_api_key},
                    params={"q": query, "count": 5, "freshness": "Day"},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("webPages", {}).get("value", [])[:3]:
                    results.append(f"- {item['name']}: {item['snippet']}")
            except Exception as e:
                logger.warning(f"Bing search failed for '{query}': {e}")

    return "\n".join(results) if results else f"No live search results. Use your knowledge of {niche} trends."


async def run_research(niche: str, tone: str, demographic: str) -> ResearchResult:
    """Run full research stage: trend scraping + hook generation."""
    logger.info(f"Starting research for niche: {niche}")

    bing_context = await fetch_bing_trends(niche)
    logger.info(f"Bing context fetched: {len(bing_context)} chars")

    prompt = RESEARCH_PROMPT.format(
        niche=niche,
        bing_context=bing_context,
    )

    # Use Groq for speed on ideation
    groq_client = AsyncGroq(api_key=settings.groq_api_key)

    try:
        response = await groq_client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"Groq research failed: {e}, falling back to OpenAI")
        openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await openai_client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)

    trends = [TrendTopic(**t) for t in data["trends"]]
    selected = TrendTopic(**data["selected_topic"])
    hooks = [HookOption(**h) for h in data["hooks"]]

    result = ResearchResult(
        trends=trends,
        selected_topic=selected,
        hooks=hooks,
        winning_hook=data["winning_hook"],
        selection_rationale=data["selection_rationale"],
    )

    logger.info(f"Research complete. Selected topic: {selected.topic_title}")
    logger.info(f"Winning hook: {result.winning_hook[:60]}...")
    return result
