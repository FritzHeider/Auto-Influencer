import asyncio
import json
import logging
from openai import AsyncOpenAI
from groq import AsyncGroq
from ddgs import DDGS
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import ResearchResult, TrendTopic, HookOption
from prompts.system_prompts import RESEARCH_PROMPT

logger = logging.getLogger(__name__)


async def fetch_search_trends(niche: str) -> str:
    """Fetch trending content via DuckDuckGo for research context."""
    queries = [
        f"{niche} news today",
        f"{niche} trending tips",
        f"best {niche} advice viral",
    ]

    def _search(query: str) -> list[dict]:
        try:
            return list(DDGS().text(query, max_results=3, timeout=8))
        except Exception as e:
            logger.warning(f"DDG search failed for '{query}': {e}")
            return []

    loop = asyncio.get_event_loop()
    all_hits = await asyncio.gather(
        *[loop.run_in_executor(None, _search, q) for q in queries]
    )

    results = [
        f"- {hit['title']}: {hit['body'][:120]}"
        for hits in all_hits
        for hit in hits
    ]
    return "\n".join(results) if results else f"No live search results. Use your knowledge of {niche} trends."


async def run_research(niche: str, tone: str, demographic: str, used_topics: list[str] | None = None) -> ResearchResult:
    """Run full research stage: trend scraping + hook generation."""
    logger.info(f"Starting research for niche: {niche}")

    search_context = await fetch_search_trends(niche)
    logger.info(f"Search context fetched: {len(search_context)} chars")

    used_topics_str = "\n".join(f"- {t}" for t in used_topics) if used_topics else "None"
    prompt = RESEARCH_PROMPT.format(
        niche=niche,
        bing_context=search_context,
        used_topics=used_topics_str,
    )

    # Use Groq for speed on ideation
    groq_client = AsyncGroq(api_key=settings.groq_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    async def _groq_call():
        return await groq_client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

    try:
        response = await _groq_call()
        raw = response.choices[0].message.content
        data = json.loads(raw)
    except Exception as e:
        logger.warning(f"Groq research failed: {e}, falling back to OpenAI")
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
