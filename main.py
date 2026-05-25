import uuid
import json
import asyncio
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

from config.settings import settings
from pipeline.models import VideoPackage, VoiceSpec, ThumbnailConcept
from pipeline.stage1_research import generate_story_brief
from pipeline.stage2_script import generate_script
from pipeline.stage3_voice import generate_voiceover
from pipeline.stage4_thumbnail import generate_thumbnail
from pipeline.stage5_metadata import generate_episode_metadata
from pipeline.stage6_video import generate_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def run_pipeline(
    genre: str | None = None,
    tone: str | None = None,
    series_id: str | None = None,
    series_title: str = "",
    episode_number: int = 1,
    story_prompt: str = "",
    previously_on: str = "",
    world_notes: str = "",
    characters: list[dict] | None = None,
    narration_style: str = "third_person",
    visual_style: str = "",
    color_grade: str = "",
    music_mood: str = "neutral",
    themes: list[str] | None = None,
    skip_voice: bool = False,
    skip_thumbnail: bool = False,
    skip_video: bool = False,
    scene_style: str | None = None,
    thumbnail_model: str | None = None,
    avatar_id: str | None = None,
    avatar_model: str | None = None,
    voice_id: str | None = None,
    style_locked_broll: bool = False,
    video_model: str | None = None,
    reference_image_id: str | None = None,
    use_native_audio: bool = False,
    transition: str = "cut",
    card_style: str = "pillow",
    cinematic_style: str | None = None,
    reference_all_clips: bool = False,
) -> VideoPackage:
    """
    Run the full cinematic episode pipeline.

    Produces a complete VideoPackage: story brief, episode script, voiceover,
    cover art, episode metadata, and assembled B-roll video.
    """
    genre = genre or settings.default_genre
    tone = tone or settings.default_tone

    video_id = f"ep_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    logger.info("=" * 60)
    logger.info(f"Starting episode pipeline: {video_id}")
    logger.info(f"Series: {series_title!r} | Genre: {genre} | Tone: {tone} | Ep: {episode_number}")
    logger.info("=" * 60)

    timings: dict[str, float] = {}

    # Stage 1: Story brief
    logger.info("Stage 1/5: Story brief generation")
    t0 = time.monotonic()
    brief = await generate_story_brief(
        genre=genre,
        tone=tone,
        series_title=series_title,
        world_notes=world_notes,
        characters=characters,
        previously_on=previously_on,
        story_prompt=story_prompt,
        episode_number=episode_number,
    )
    timings["story_brief"] = round(time.monotonic() - t0, 2)

    # Stage 2: Episode script
    logger.info("Stage 2/5: Episode script generation")
    t0 = time.monotonic()
    effective_visual = cinematic_style or visual_style
    script = await generate_script(
        brief=brief,
        genre=genre,
        tone=tone,
        series_title=series_title,
        episode_number=episode_number,
        narration_style=narration_style,
        visual_style=effective_visual,
        color_grade=color_grade,
        music_mood=music_mood,
        characters=characters,
    )
    timings["script"] = round(time.monotonic() - t0, 2)

    # Stage 3: Voice generation
    voice_spec = None
    audio_path = None
    if not skip_voice:
        logger.info("Stage 3/5: Voice generation (OpenAI TTS → ElevenLabs fallback)")
        t0 = time.monotonic()
        voice_spec, audio_path = await generate_voiceover(
            script, genre, tone, narration_style, video_id,
            voice_id_override=voice_id,
        )
        timings["voice"] = round(time.monotonic() - t0, 2)
    else:
        logger.info("Stage 3/5: Skipped (skip_voice=True)")
        voice_spec = VoiceSpec(provider="openai", voice_id="onyx", voice_name="onyx")

    # Stages 4+5 run in parallel: Episode metadata + Cover art
    logger.info("Stage 4+5/5: Episode metadata + cover art (parallel)")
    t0 = time.monotonic()

    episode_themes = themes or brief.themes

    async def _metadata():
        return await generate_episode_metadata(
            script, genre, series_title=series_title, themes=episode_themes
        )

    async def _cover_art():
        if skip_thumbnail:
            return None
        return await generate_thumbnail(
            script=script,
            metadata=None,  # metadata not needed for prompt — we pass themes directly
            genre=genre,
            video_id=video_id,
            visual_style=effective_visual,
            themes=episode_themes,
            thumbnail_model=thumbnail_model,
        )

    metadata_result, thumbnail_result = await asyncio.gather(_metadata(), _cover_art())
    timings["metadata_cover"] = round(time.monotonic() - t0, 2)

    metadata = metadata_result
    if thumbnail_result:
        thumbnail_concepts, winning_thumbnail, thumbnail_path = thumbnail_result
    else:
        logger.info("Cover art: Skipped (skip_thumbnail=True)")
        winning_thumbnail = ThumbnailConcept(
            concept_id=1, layout_description="placeholder", focal_element="placeholder",
            text_overlay="", accent_elements=[], color_mood="neutral",
            image_prompt="placeholder", ctr_score=0.0, is_winner=True,
        )
        thumbnail_concepts = [winning_thumbnail]
        thumbnail_path = None

    # Stage 6: Video assembly
    video_path = None
    if not skip_video and audio_path:
        logger.info("Stage 6/6: Video assembly (B-roll + ffmpeg)")
        t0 = time.monotonic()
        video_path = await generate_video(
            script, audio_path, video_id,
            scene_style=scene_style,
            avatar_id=avatar_id,
            avatar_model=avatar_model,
            style_locked_broll=style_locked_broll,
            video_model=video_model,
            reference_image_id=reference_image_id,
            use_native_audio=use_native_audio,
            transition=transition,
            card_style=card_style,
            cinematic_style=effective_visual,
            reference_all_clips=reference_all_clips,
        )
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
        series_id=series_id,
        series_title=series_title,
        episode_number=episode_number,
        genre=genre,
        episode_brief=brief,
        script=script,
        voice_spec=voice_spec,
        thumbnail_concepts=thumbnail_concepts,
        winning_thumbnail=winning_thumbnail,
        metadata=metadata,
        characters=[c.get("character_id", "") for c in (characters or []) if c.get("character_id")],
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

    logger.info("=" * 60)
    logger.info(f"Pipeline complete: {video_id}")
    logger.info(f"Series: {series_title!r} | Episode {episode_number}: '{script.episode_title}'")
    logger.info(f"Script: {script.word_count} words / {script.estimated_duration_minutes:.1f} min")
    logger.info(f"Audio: {audio_path or 'skipped'} | Thumbnail: {thumbnail_path or 'skipped'}")
    logger.info(f"Video: {video_path or 'skipped'}")
    logger.info(f"Cliffhanger: {brief.cliffhanger[:80]}...")
    logger.info(f"Timings: { {k: f'{v}s' for k, v in timings.items()} }")
    logger.info(f"Package saved: {package_path}")
    logger.info("=" * 60)

    return package


async def run_batch(count: int = 3, max_concurrent: int = 3, **kwargs) -> list[VideoPackage]:
    """Run pipeline N times with bounded concurrency for batch episode production."""
    logger.info(f"Starting batch run: {count} episodes (max {max_concurrent} concurrent)")
    sem = asyncio.Semaphore(max_concurrent)

    async def _run_one(index: int) -> VideoPackage | None:
        async with sem:
            kw = dict(kwargs)
            kw["episode_number"] = kwargs.get("episode_number", 1) + index
            logger.info(f"Batch episode {index + 1}/{count} starting (Ep {kw['episode_number']})")
            try:
                return await run_pipeline(**kw)
            except Exception as e:
                logger.error(f"Batch episode {index + 1} failed: {e}")
                return None

    results = await asyncio.gather(*[_run_one(i) for i in range(count)])
    packages = [r for r in results if r is not None]
    logger.info(f"Batch complete: {len(packages)}/{count} successful")
    return packages


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cinematic Episode Pipeline")
    parser.add_argument("--genre", type=str, default="drama", help="Story genre")
    parser.add_argument("--tone", type=str, default="cinematic", help="Narrative tone")
    parser.add_argument("--series", type=str, default="", help="Series title")
    parser.add_argument("--episode", type=int, default=1, help="Episode number")
    parser.add_argument("--prompt", type=str, default="", help="Story direction prompt")
    parser.add_argument("--batch", type=int, default=1, help="Number of episodes to produce")
    parser.add_argument("--skip-voice", action="store_true")
    parser.add_argument("--skip-thumbnail", action="store_true")
    parser.add_argument("--skip-video", action="store_true")

    args = parser.parse_args()

    if args.batch > 1:
        asyncio.run(run_batch(
            count=args.batch, genre=args.genre, tone=args.tone,
            series_title=args.series, episode_number=args.episode,
            story_prompt=args.prompt,
            skip_voice=args.skip_voice, skip_thumbnail=args.skip_thumbnail,
            skip_video=args.skip_video,
        ))
    else:
        asyncio.run(run_pipeline(
            genre=args.genre, tone=args.tone,
            series_title=args.series, episode_number=args.episode,
            story_prompt=args.prompt,
            skip_voice=args.skip_voice, skip_thumbnail=args.skip_thumbnail,
            skip_video=args.skip_video,
        ))
