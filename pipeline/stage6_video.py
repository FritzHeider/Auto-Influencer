import asyncio
import logging
import subprocess
from pathlib import Path

import fal_client
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, ScriptSection

logger = logging.getLogger(__name__)

CLIP_DURATION = 5  # seconds per generated clip


def _ts_to_seconds(ts: str) -> float:
    """Convert 'MM:SS' or 'HH:MM:SS' timestamp to seconds."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _section_duration(section: ScriptSection) -> float:
    return max(_ts_to_seconds(section.timestamp_end) - _ts_to_seconds(section.timestamp_start), 1.0)


def _plan_clips(script: Script) -> list[tuple[str, float]]:
    """
    Return (prompt, duration_seconds) for every clip slot.
    B-roll cues within a section share the section's time evenly.
    Sections with no cues get a generic fallback prompt.
    """
    slots: list[tuple[str, float]] = []
    for section in script.sections:
        dur = _section_duration(section)
        cues = section.broll_cues or [f"cinematic footage related to {script.topic}"]
        per_cue = dur / len(cues)
        for cue in cues:
            slots.append((cue, per_cue))
    return slots


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def _generate_clip(prompt: str, output_path: Path) -> bool:
    """Generate a single 5-second 16:9 clip via fal.ai Kling."""
    fal_client.api_key = settings.fal_key
    try:
        result = await fal_client.run_async(
            settings.fal_video_model,
            arguments={
                "prompt": prompt,
                "duration": "5",
                "aspect_ratio": "16:9",
            },
        )
        video_url = result["video"]["url"]
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(video_url)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
        logger.info(f"Clip saved: {output_path.name}")
        return True
    except Exception as e:
        logger.error(f"Clip generation failed for '{prompt[:50]}': {e}")
        return False


def _extend_clip(clip_path: Path, duration: float, output_path: Path) -> bool:
    """Loop a clip to fill the target duration using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(clip_path),
        "-t", str(duration),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        "-r", "24",
        "-c:v", "libx264",
        "-preset", "fast",
        "-an",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"ffmpeg extend failed: {e}")
        return False


def _concat_clips(clip_paths: list[Path], output_path: Path) -> bool:
    """Concatenate video clips with ffmpeg concat demuxer."""
    list_file = output_path.parent / f"{output_path.stem}_concat.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"ffmpeg concat failed: {e}")
        return False
    finally:
        list_file.unlink(missing_ok=True)


def _mix_audio(video_path: Path, audio_path: Path, output_path: Path) -> bool:
    """Replace video's audio track with the voiceover, trimming to the shorter of the two."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            logger.info(f"Final video with audio: {output_path}")
            return True
        logger.error(f"ffmpeg mix error: {result.stderr[-500:]}")
        return False
    except Exception as e:
        logger.error(f"ffmpeg mix failed: {e}")
        return False


async def generate_video(script: Script, audio_path: str, video_id: str) -> str | None:
    """Full video assembly: B-roll generation → extend → concat → mix audio."""
    video_dir = Path(settings.video_dir)
    video_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = video_dir / video_id
    clips_dir.mkdir(exist_ok=True)

    slots = _plan_clips(script)
    logger.info(f"Generating {len(slots)} B-roll clips for {video_id}")

    # Generate all raw clips in parallel
    raw_paths = [clips_dir / f"raw_{i:02d}.mp4" for i in range(len(slots))]
    results = await asyncio.gather(*[
        _generate_clip(prompt, path)
        for (prompt, _), path in zip(slots, raw_paths)
    ])

    if not any(results):
        logger.error("All clip generations failed — aborting video assembly")
        return None

    # Extend each successful clip to its allocated duration
    extended_paths: list[Path] = []
    for i, ((_, duration), raw_path, ok) in enumerate(zip(slots, raw_paths, results)):
        if not ok:
            logger.warning(f"Skipping failed clip {i} in assembly")
            continue
        ext_path = clips_dir / f"ext_{i:02d}.mp4"
        if _extend_clip(raw_path, duration, ext_path):
            extended_paths.append(ext_path)
            raw_path.unlink(missing_ok=True)
        else:
            logger.warning(f"Extend failed for clip {i}, using raw clip")
            extended_paths.append(raw_path)

    if not extended_paths:
        logger.error("No extended clips — aborting")
        return None

    # Concatenate all clips into silent video
    silent_path = video_dir / f"{video_id}_silent.mp4"
    if not _concat_clips(extended_paths, silent_path):
        logger.error("Concat failed — aborting")
        return None

    # Mix in voiceover
    final_path = video_dir / f"{video_id}_final.mp4"
    success = _mix_audio(silent_path, Path(audio_path), final_path)
    silent_path.unlink(missing_ok=True)
    for p in extended_paths:
        p.unlink(missing_ok=True)
    try:
        clips_dir.rmdir()
    except OSError:
        pass

    return str(final_path) if success else None
