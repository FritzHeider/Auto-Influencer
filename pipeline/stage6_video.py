import asyncio
import logging
import re
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import fal_client
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, ScriptSection
from pipeline.stage3_voice import strip_script_markup

logger = logging.getLogger(__name__)

BROLL_CLIP_SECONDS = 10  # Kling v2 master supports up to 10s


# ── Timestamp helpers ────────────────────────────────────────────────────────

def _ts_to_seconds(ts: str) -> float:
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _section_duration(section: ScriptSection) -> float:
    return max(_ts_to_seconds(section.timestamp_end) - _ts_to_seconds(section.timestamp_start), 1.0)


# ── Script analysis ──────────────────────────────────────────────────────────

def _extract_talking_points(content: str, max_points: int = 3) -> list[str]:
    """Pull first N clean sentences from section as talking points."""
    clean = strip_script_markup(content)
    sentences = re.split(r"(?<=[.!?])\s+", clean.strip())
    points = []
    for s in sentences:
        s = s.strip()
        if len(s) > 20 and len(points) < max_points:
            points.append(s[:85] + ("..." if len(s) > 85 else ""))
    return points


def _enrich_prompt(cue: str, topic: str, section_content: str, variant: int) -> str:
    """Build a rich, varied B-roll prompt grounded in the script."""
    # Pull a topic phrase from the section content for extra relevance
    context_phrase = strip_script_markup(section_content)[:80].split(".")[0].strip()

    angles = [
        "cinematic wide shot of",
        "smooth close-up shot of",
        "overhead aerial view of",
        "dynamic tracking shot following",
        "dramatic low-angle shot of",
        "slow-motion capture of",
        "time-lapse sequence showing",
        "handheld documentary shot of",
    ]
    angle = angles[variant % len(angles)]

    return (
        f"{angle} {cue}, conveying the idea of '{context_phrase}', "
        f"topic: {topic}, professional 4K cinematography, "
        "natural lighting, smooth motion, no text, no watermarks, "
        "cinematic color grading"
    )


# ── FFmpeg helpers ───────────────────────────────────────────────────────────

def _split_audio(audio_path: str, start_sec: float, duration_sec: float, out: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-ss", str(start_sec), "-t", str(duration_sec),
        "-c", "copy", str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and out.exists()
    except Exception as e:
        logger.error(f"Audio split failed: {e}")
        return False


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load best available font, falling back to PIL default."""
    candidates = [
        "/Library/Fonts/ArialCE.ttf",
        "/Library/Fonts/Hack-Regular.ttf",
        "/Library/Fonts/SF-Pro.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def make_talking_points_card(points: list[str], duration: float, out: Path) -> bool:
    """Render a dark slide with bullet points using Pillow, then encode to MP4."""
    if not points:
        points = ["Key Insights"]

    W, H = 1280, 720
    BG = (13, 17, 23)        # near-black
    ACCENT = (99, 110, 125)  # muted gray
    FG = (230, 230, 230)     # near-white

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    label_font = _load_font(22)
    body_font = _load_font(30)

    draw.text((80, 55), "KEY POINTS", font=label_font, fill=ACCENT)

    # Horizontal rule
    draw.line([(80, 95), (W - 80, 95)], fill=(40, 50, 60), width=1)

    y = 115
    for pt in points[:4]:
        lines = textwrap.wrap(pt, width=62)
        for j, line in enumerate(lines):
            prefix = "•  " if j == 0 else "    "
            draw.text((80, y), prefix + line, font=body_font, fill=FG)
            y += 42
        y += 14  # extra gap between points

    # Save as PNG then encode to MP4 with ffmpeg
    png_path = out.with_suffix(".png")
    img.save(str(png_path))

    frames = max(1, int(duration * 24))
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(png_path),
        "-vf", "format=yuv420p",
        "-r", "24", "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "fast",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Talking points card encode failed: {e}")
        return False
    finally:
        png_path.unlink(missing_ok=True)


def _normalize(src: Path, dst: Path) -> bool:
    """Transcode to consistent 1280x720 @ 24fps h264 for concat."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        "-r", "24", "-c:v", "libx264", "-preset", "fast", "-an",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Normalize failed: {e}")
        return False


def _concat(paths: list[Path], out: Path) -> bool:
    lst = out.parent / f"{out.stem}_list.txt"
    lst.write_text("\n".join(f"file '{p.resolve()}'" for p in paths))
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Concat failed: {e}")
        return False
    finally:
        lst.unlink(missing_ok=True)


def _mix_audio(video: Path, audio: str, out: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video), "-i", audio,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            logger.info(f"Final video: {out}")
            return True
        logger.error(f"Mix failed: {r.stderr[-500:]}")
        return False
    except Exception as e:
        logger.error(f"Mix exception: {e}")
        return False


# ── fal.ai generators ────────────────────────────────────────────────────────

async def _generate_avatar_image(niche: str, topic: str) -> bytes | None:
    """Generate a reusable presenter portrait with Flux."""
    fal_client.api_key = settings.fal_key
    try:
        result = await fal_client.run_async(
            "fal-ai/flux/dev",
            arguments={
                "prompt": (
                    f"professional {niche} content creator, looking directly at camera, "
                    "clean modern studio background with soft bokeh, business casual, "
                    "warm confident expression, portrait photograph, sharp focus, 4K"
                ),
                "image_size": {"width": 512, "height": 512},
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": 1,
                "output_format": "jpeg",
                "enable_safety_checker": False,
            },
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(result["images"][0]["url"])
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.error(f"Avatar image failed: {e}")
        return None


async def _generate_avatar_clip(avatar_bytes: bytes, audio_path: Path, out: Path) -> bool:
    """Lip-synced talking head via SadTalker."""
    fal_client.api_key = settings.fal_key
    try:
        image_url, audio_url = await asyncio.gather(
            fal_client.upload_async(avatar_bytes, "image/jpeg"),
            fal_client.upload_async(audio_path.read_bytes(), "audio/mpeg"),
        )
        result = await fal_client.run_async(
            settings.fal_avatar_model,
            arguments={
                "source_image_url": image_url,
                "driven_audio_url": audio_url,
                "still_mode": True,
                "expression_scale": 1.2,
                "preprocess": "crop",
                "enhancer": "gfpgan",
            },
        )
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.get(result["video"]["url"])
            resp.raise_for_status()
            out.write_bytes(resp.content)
        logger.info(f"Avatar clip: {out.name}")
        return True
    except Exception as e:
        logger.warning(f"Avatar clip failed (skipping): {e}")
        return False


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=3, max=15), reraise=True)
async def _generate_broll_clip(prompt: str, out: Path) -> bool:
    """Single unique B-roll clip via Kling v2 master (10s, 16:9)."""
    fal_client.api_key = settings.fal_key
    try:
        result = await fal_client.run_async(
            settings.fal_video_model,
            arguments={
                "prompt": prompt,
                "duration": "10",
                "aspect_ratio": "16:9",
            },
        )
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.get(result["video"]["url"])
            resp.raise_for_status()
            out.write_bytes(resp.content)
        logger.info(f"B-roll: {out.name}")
        return True
    except Exception as e:
        logger.error(f"B-roll failed '{prompt[:60]}': {e}")
        return False


# ── Main orchestrator ────────────────────────────────────────────────────────

async def generate_video(script: Script, audio_path: str, video_id: str) -> str | None:
    """
    Assemble final video:
      per section → [talking points card] + [lip-synced avatar] + [B-roll clips]
      between every cue → [talking points card]
    All B-roll clips are unique variants (no looping).
    Final assembly mixes in the full voiceover.
    """
    video_dir = Path(settings.video_dir)
    video_dir.mkdir(parents=True, exist_ok=True)
    work = video_dir / video_id
    work.mkdir(exist_ok=True)

    # Generate the presenter avatar once
    logger.info("Generating presenter avatar image...")
    niche_hint = " ".join(script.topic.split()[:2])
    avatar_bytes = await _generate_avatar_image(niche_hint, script.topic)

    segments: list[Path] = []
    clip_idx = 0

    for sec_idx, section in enumerate(script.sections):
        sec_dur = _section_duration(section)
        sec_start = _ts_to_seconds(section.timestamp_start)
        points = _extract_talking_points(section.content)
        cues = section.broll_cues or [f"professional footage representing {script.topic}"]

        logger.info(f"Section {sec_idx + 1}/{len(script.sections)}: {section.label} ({sec_dur:.0f}s, {len(cues)} cues)")

        # ① Talking points card — opens each section
        card = work / f"s{sec_idx:02d}_open.mp4"
        if make_talking_points_card(points, 4.0, card):
            segments.append(card)

        # ② Lip-synced avatar intro — covers first portion of section audio
        avatar_clip_dur = min(10.0, sec_dur * 0.35)
        if avatar_bytes and avatar_clip_dur >= 3.0:
            aud = work / f"s{sec_idx:02d}_aud.mp3"
            av_raw = work / f"s{sec_idx:02d}_av_raw.mp4"
            av_norm = work / f"s{sec_idx:02d}_av.mp4"
            if _split_audio(audio_path, sec_start, avatar_clip_dur, aud):
                ok = await _generate_avatar_clip(avatar_bytes, aud, av_raw)
                if ok and _normalize(av_raw, av_norm):
                    segments.append(av_norm)
                    av_raw.unlink(missing_ok=True)
            aud.unlink(missing_ok=True)

        # ③ B-roll clips — unique variants per cue, no looping
        broll_budget = max(sec_dur - 4.0 - avatar_clip_dur, BROLL_CLIP_SECONDS * len(cues))
        per_cue_time = broll_budget / len(cues)
        n_per_cue = max(1, round(per_cue_time / BROLL_CLIP_SECONDS))

        for cue_idx, cue in enumerate(cues):
            prompts = [
                _enrich_prompt(cue, script.topic, section.content, v)
                for v in range(n_per_cue)
            ]
            raw_paths = [work / f"c{clip_idx + v:03d}_raw.mp4" for v in range(n_per_cue)]

            results = await asyncio.gather(*[
                _generate_broll_clip(p, rp) for p, rp in zip(prompts, raw_paths)
            ])

            for v, (raw, ok) in enumerate(zip(raw_paths, results)):
                if ok:
                    norm = work / f"c{clip_idx + v:03d}.mp4"
                    if _normalize(raw, norm):
                        segments.append(norm)
                    raw.unlink(missing_ok=True)

            clip_idx += n_per_cue

            # Talking points card between cues (skip after last cue in section)
            if cue_idx < len(cues) - 1:
                between = work / f"s{sec_idx:02d}_c{cue_idx:02d}_tp.mp4"
                if make_talking_points_card(points, 3.0, between):
                    segments.append(between)

    if not segments:
        logger.error("No segments produced — aborting")
        return None

    logger.info(f"Concatenating {len(segments)} segments...")
    silent = video_dir / f"{video_id}_silent.mp4"
    if not _concat(segments, silent):
        logger.error("Concat failed")
        return None

    final = video_dir / f"{video_id}_final.mp4"
    ok = _mix_audio(silent, audio_path, final)

    silent.unlink(missing_ok=True)
    for p in segments:
        p.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass

    return str(final) if ok else None
