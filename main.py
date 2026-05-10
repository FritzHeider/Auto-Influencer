import uuid
import json
import asyncio
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

from config.settings import settings
from pipeline.models import VideoPackage
from pipeline.stage1_research import run_research
from pipeline.stage2_script import generate_script
from pipeline.stage3_voice import generate_voiceover
from pipeline.stage4_thumbnail import generate_thumbnail
from pipeline.stage5_monetize import generate_seo_package, generate_affiliate_insertions
from pipeline.stage6_video import generate_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_USED_TOPICS_FILE = Path(settings.output_dir) / "used_topics.json"


def load_used_topics() -> list[str]:
    if _USED_TOPICS_FILE.exists():
        return json.loads(_USED_TOPICS_FILE.read_text())
    return []


def save_used_topic(topic: str) -> None:
    topics = load_used_topics()
    if topic not in topics:
        topics.append(topic)
    _USED_TOPICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USED_TOPICS_FILE.write_text(json.dumps(topics, indent=2))


async def run_pipeline(
    niche: str | None = None,
    tone: str | None = None,
    demographic: str | None = None,
    skip_voice: bool = False,
    skip_thumbnail: bool = False,
    skip_video: bool = False,
) -> VideoPackage:
    """
    Run the full AI influencer content pipeline.

    Args:
        niche: Channel niche (defaults to settings.channel_niche)
        tone: Voice tone (defaults to settings.channel_tone)
        demographic: Target demographic (defaults to settings.channel_demographic)
        skip_voice: Skip TTS generation (useful for testing)
        skip_thumbnail: Skip image generation (useful for testing)

    Returns:
        VideoPackage with all assets and metadata
    """
    niche = niche or settings.channel_niche
    tone = tone or settings.channel_tone
    demographic = demographic or settings.channel_demographic

    video_id = f"vid_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    logger.info(f"=" * 60)
    logger.info(f"Starting pipeline for video: {video_id}")
    logger.info(f"Niche: {niche} | Tone: {tone} | Demo: {demographic}")
    logger.info(f"=" * 60)

    timings: dict[str, float] = {}

    # Stage 1: Research + hooks
    logger.info("Stage 1/5: Research + trend analysis")
    t0 = time.monotonic()
    used_topics = load_used_topics()
    if used_topics:
        logger.info(f"Dedup: avoiding {len(used_topics)} previously used topic(s)")
    research = await run_research(niche, tone, demographic, used_topics=used_topics)
    timings["research"] = round(time.monotonic() - t0, 2)

    # Stage 2: Script generation
    logger.info("Stage 2/5: Script generation")
    t0 = time.monotonic()
    script = await generate_script(research, niche, tone, demographic)
    timings["script"] = round(time.monotonic() - t0, 2)

    # Stage 3: SEO + Affiliates (run in parallel)
    logger.info("Stage 3/5: SEO + affiliate generation (parallel)")
    t0 = time.monotonic()
    seo, affiliates = await asyncio.gather(
        generate_seo_package(script, niche, demographic),
        generate_affiliate_insertions(script, niche),
    )
    timings["seo_affiliates"] = round(time.monotonic() - t0, 2)

    # Stage 4: Voice generation
    voice_spec = None
    audio_path = None
    if not skip_voice:
        logger.info("Stage 4/5: Voice generation (OpenAI TTS → ElevenLabs fallback)")
        t0 = time.monotonic()
        voice_spec, audio_path = await generate_voiceover(script, niche, tone, demographic, video_id)
        timings["voice"] = round(time.monotonic() - t0, 2)
    else:
        logger.info("Stage 4/5: Skipped (skip_voice=True)")
        from pipeline.models import VoiceSpec
        voice_spec = VoiceSpec(
            provider="openai",
            voice_id="onyx",
            voice_name="onyx",
        )

    # Stage 5: Thumbnail generation
    thumbnail_concepts = []
    winning_thumbnail = None
    thumbnail_path = None
    if not skip_thumbnail:
        logger.info("Stage 5/5: Thumbnail generation (fal.ai Flux)")
        t0 = time.monotonic()
        thumbnail_concepts, winning_thumbnail, thumbnail_path = await generate_thumbnail(
            script, seo, niche, video_id
        )
        timings["thumbnail"] = round(time.monotonic() - t0, 2)
    else:
        logger.info("Stage 5/5: Skipped (skip_thumbnail=True)")
        from pipeline.models import ThumbnailConcept
        winning_thumbnail = ThumbnailConcept(
            concept_id=1,
            layout_description="placeholder",
            focal_element="placeholder",
            text_overlay="placeholder",
            accent_elements=[],
            color_mood="neutral",
            image_prompt="placeholder",
            ctr_score=0.0,
            is_winner=True,
        )
        thumbnail_concepts = [winning_thumbnail]

    # Stage 6: Video assembly (B-roll + audio)
    video_path = None
    if not skip_video and audio_path:
        logger.info("Stage 6/6: Video assembly (fal.ai Kling B-roll + ffmpeg)")
        t0 = time.monotonic()
        video_path = await generate_video(script, audio_path, video_id)
        timings["video"] = round(time.monotonic() - t0, 2)
        if video_path:
            logger.info(f"Video assembled: {video_path}")
        else:
            logger.warning("Video assembly failed — package saved without video")
    elif skip_video:
        logger.info("Stage 6/6: Skipped (skip_video=True)")
    else:
        logger.info("Stage 6/6: Skipped (no audio)")

    # Assemble final package
    package = VideoPackage(
        video_id=video_id,
        niche=niche,
        research=research,
        script=script,
        voice_spec=voice_spec,
        thumbnail_concepts=thumbnail_concepts,
        winning_thumbnail=winning_thumbnail,
        seo=seo,
        affiliates=affiliates,
        audio_path=audio_path,
        thumbnail_path=thumbnail_path,
        video_path=video_path,
        status="ready",
        stage_timings=timings,
    )

    # Save package JSON
    output_dir = Path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    package_path = output_dir / f"{video_id}_package.json"
    package_path.write_text(package.model_dump_json(indent=2))
    save_used_topic(research.selected_topic.topic_title)

    logger.info(f"=" * 60)
    logger.info(f"Pipeline complete: {video_id}")
    logger.info(f"Topic: {research.selected_topic.topic_title}")
    logger.info(f"Script: {script.word_count} words / {script.estimated_duration_minutes:.1f} min")
    logger.info(f"Title: {seo.title}")
    logger.info(f"Audio: {audio_path or 'skipped'}")
    logger.info(f"Thumbnail: {thumbnail_path or 'skipped'}")
    logger.info(f"Video: {video_path or 'skipped'}")
    logger.info(f"Timings: { {k: f'{v}s' for k, v in timings.items()} }")
    logger.info(f"Package saved: {package_path}")
    logger.info(f"=" * 60)

    return package


async def run_batch(count: int = 3, **kwargs) -> list[VideoPackage]:
    """Run pipeline N times in parallel for batch content production."""
    logger.info(f"Starting batch run: {count} videos (parallel)")

    async def _run_one(index: int) -> VideoPackage | None:
        logger.info(f"Batch video {index + 1}/{count} starting")
        try:
            return await run_pipeline(**kwargs)
        except Exception as e:
            logger.error(f"Batch video {index + 1} failed: {e}")
            return None

    results = await asyncio.gather(*[_run_one(i) for i in range(count)])
    packages = [r for r in results if r is not None]
    logger.info(f"Batch complete: {len(packages)}/{count} successful")
    return packages


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Influencer Content Pipeline")
    parser.add_argument("--niche", type=str, help="Channel niche")
    parser.add_argument("--tone", type=str, help="Voice tone")
    parser.add_argument("--demographic", type=str, help="Target demographic")
    parser.add_argument("--batch", type=int, default=1, help="Number of videos to produce")
    parser.add_argument("--skip-voice", action="store_true", help="Skip TTS generation")
    parser.add_argument("--skip-thumbnail", action="store_true", help="Skip thumbnail generation")
    parser.add_argument("--skip-video", action="store_true", help="Skip video assembly")

    args = parser.parse_args()

    if args.batch > 1:
        asyncio.run(
            run_batch(
                count=args.batch,
                niche=args.niche,
                tone=args.tone,
                demographic=args.demographic,
                skip_voice=args.skip_voice,
                skip_thumbnail=args.skip_thumbnail,
                skip_video=args.skip_video,
            )
        )
    else:
        asyncio.run(
            run_pipeline(
                niche=args.niche,
                tone=args.tone,
                demographic=args.demographic,
                skip_voice=args.skip_voice,
                skip_thumbnail=args.skip_thumbnail,
                skip_video=args.skip_video,
            )
        )
