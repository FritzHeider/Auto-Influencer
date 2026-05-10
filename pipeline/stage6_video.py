import asyncio
import logging
import re
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import fal_client
import httpx
from PIL import Image, ImageDraw, ImageFont
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, ScriptSection
from pipeline.stage3_voice import strip_script_markup

logger = logging.getLogger(__name__)

BROLL_CLIP_SECONDS = 10   # Kling v2 master max duration
MAX_CLIPS_PER_CUE = 3     # cap unique variants per cue (avoids runaway clip counts)
AVATAR_MAX_SECONDS = 10   # max avatar clip duration per section


# ── Timestamp helpers ────────────────────────────────────────────────────────

def _ts_to_seconds(ts: str) -> float:
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _section_duration(section: ScriptSection) -> float:
    return max(
        _ts_to_seconds(section.timestamp_end) - _ts_to_seconds(section.timestamp_start),
        1.0,
    )


# ── Script analysis ──────────────────────────────────────────────────────────

def _extract_talking_points(content: str, max_points: int = 3) -> list[str]:
    clean = strip_script_markup(content)
    sentences = re.split(r"(?<=[.!?])\s+", clean.strip())
    points = []
    for s in sentences:
        s = s.strip()
        if len(s) > 20 and len(points) < max_points:
            points.append(s[:85] + ("..." if len(s) > 85 else ""))
    return points


def _enrich_prompt(cue: str, topic: str, section_content: str, variant: int) -> str:
    context = strip_script_markup(section_content)[:80].split(".")[0].strip()
    angles = [
        "cinematic wide shot of",
        "smooth close-up shot of",
        "overhead aerial view of",
        "dynamic tracking shot of",
        "dramatic low-angle shot of",
        "slow-motion capture of",
        "time-lapse sequence showing",
        "handheld documentary shot of",
    ]
    return (
        f"{angles[variant % len(angles)]} {cue}, "
        f"conveying '{context}', topic: {topic}, "
        "professional 4K cinematography, smooth motion, "
        "no text, no watermarks, cinematic color grading"
    )


# ── Clip plan ────────────────────────────────────────────────────────────────

@dataclass
class ClipSlot:
    """Everything needed to generate and place one video segment."""
    kind: str              # "card" | "avatar" | "broll"
    sec_idx: int
    cue_idx: int = 0
    variant: int = 0
    prompt: str = ""
    points: list[str] = field(default_factory=list)
    card_duration: float = 3.5
    avatar_audio_start: float = 0.0
    avatar_audio_dur: float = 0.0
    out: Path = field(default=None)


def _plan(script: Script, audio_path: str, work: Path) -> list[ClipSlot]:
    slots: list[ClipSlot] = []
    for sec_idx, section in enumerate(script.sections):
        sec_dur = _section_duration(section)
        sec_start = _ts_to_seconds(section.timestamp_start)
        points = _extract_talking_points(section.content)
        cues = section.broll_cues or [f"professional footage about {script.topic}"]

        # Opening talking-points card
        slots.append(ClipSlot(
            kind="card", sec_idx=sec_idx,
            points=points, card_duration=4.0,
            out=work / f"s{sec_idx:02d}_open.mp4",
        ))

        # Avatar intro
        av_dur = min(AVATAR_MAX_SECONDS, sec_dur * 0.35)
        if av_dur >= 3.0:
            slots.append(ClipSlot(
                kind="avatar", sec_idx=sec_idx,
                avatar_audio_start=sec_start, avatar_audio_dur=av_dur,
                out=work / f"s{sec_idx:02d}_av.mp4",
            ))

        # B-roll clips per cue
        broll_budget = max(sec_dur - 4.0 - av_dur, BROLL_CLIP_SECONDS * len(cues))
        per_cue = broll_budget / len(cues)
        n = min(MAX_CLIPS_PER_CUE, max(1, round(per_cue / BROLL_CLIP_SECONDS)))

        for cue_idx, cue in enumerate(cues):
            for v in range(n):
                clip_num = len([s for s in slots if s.kind == "broll"])
                slots.append(ClipSlot(
                    kind="broll", sec_idx=sec_idx, cue_idx=cue_idx, variant=v,
                    prompt=_enrich_prompt(cue, script.topic, section.content, v),
                    out=work / f"c{clip_num:03d}.mp4",
                ))
            # Talking-points card between cues (not after the last one)
            if cue_idx < len(cues) - 1:
                slots.append(ClipSlot(
                    kind="card", sec_idx=sec_idx, cue_idx=cue_idx,
                    points=points, card_duration=3.0,
                    out=work / f"s{sec_idx:02d}_cue{cue_idx:02d}_tp.mp4",
                ))

    broll_count = sum(1 for s in slots if s.kind == "broll")
    avatar_count = sum(1 for s in slots if s.kind == "avatar")
    logger.info(f"Plan: {len(slots)} segments — {broll_count} B-roll, {avatar_count} avatar, "
                f"{len(slots)-broll_count-avatar_count} cards")
    return slots


# ── FFmpeg helpers ───────────────────────────────────────────────────────────

def _split_audio(audio_path: str, start: float, dur: float, out: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-ss", str(start), "-t", str(dur),
        "-c", "copy", str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and out.exists()
    except Exception as e:
        logger.error(f"Audio split failed: {e}")
        return False


def _normalize(src: Path, dst: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2",
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
        logger.error(f"Mix failed: {r.stderr[-400:]}")
        return False
    except Exception as e:
        logger.error(f"Mix exception: {e}")
        return False


# ── Pillow card renderer ─────────────────────────────────────────────────────

def _load_font(size: int):
    for path in [
        "/Library/Fonts/ArialCE.ttf",
        "/Library/Fonts/Hack-Regular.ttf",
        "/Library/Fonts/SF-Pro.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _make_card(slot: ClipSlot) -> bool:
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (13, 17, 23))
    draw = ImageDraw.Draw(img)
    draw.text((80, 55), "KEY POINTS", font=_load_font(22), fill=(99, 110, 125))
    draw.line([(80, 95), (W - 80, 95)], fill=(40, 50, 60), width=1)

    y = 115
    body = _load_font(30)
    for pt in slot.points[:4]:
        for j, line in enumerate(textwrap.wrap(pt, width=60)):
            draw.text((80, y), ("•  " if j == 0 else "    ") + line, font=body, fill=(230, 230, 230))
            y += 44
        y += 16

    png = slot.out.with_suffix(".png")
    img.save(str(png))
    frames = max(1, int(slot.card_duration * 24))
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(png),
        "-vf", "format=yuv420p",
        "-r", "24", "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "fast",
        str(slot.out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Card encode failed: {e}")
        return False
    finally:
        png.unlink(missing_ok=True)


# ── fal.ai generators ────────────────────────────────────────────────────────

async def _generate_avatar_image(niche: str) -> bytes | None:
    fal_client.api_key = settings.fal_key
    try:
        result = await fal_client.run_async(
            "fal-ai/flux/dev",
            arguments={
                "prompt": (
                    f"professional {niche} YouTube presenter, looking directly at camera, "
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


async def _run_avatar_slot(slot: ClipSlot, avatar_bytes: bytes, audio_path: str, work: Path) -> bool:
    aud = work / f"s{slot.sec_idx:02d}_aud.mp3"
    raw = work / f"s{slot.sec_idx:02d}_av_raw.mp4"
    if not _split_audio(audio_path, slot.avatar_audio_start, slot.avatar_audio_dur, aud):
        return False
    try:
        image_url, audio_url = await asyncio.gather(
            fal_client.upload_async(avatar_bytes, "image/jpeg"),
            fal_client.upload_async(aud.read_bytes(), "audio/mpeg"),
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
            raw.write_bytes(resp.content)
        if _normalize(raw, slot.out):
            logger.info(f"Avatar: {slot.out.name}")
            raw.unlink(missing_ok=True)
            return True
        return False
    except Exception as e:
        logger.warning(f"Avatar slot failed (s{slot.sec_idx}): {e}")
        return False
    finally:
        aud.unlink(missing_ok=True)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=3, max=15), reraise=True)
async def _run_broll_slot(slot: ClipSlot) -> bool:
    fal_client.api_key = settings.fal_key
    raw = slot.out.with_suffix(".raw.mp4")
    try:
        result = await fal_client.run_async(
            settings.fal_video_model,
            arguments={
                "prompt": slot.prompt,
                "duration": "10",
                "aspect_ratio": "16:9",
            },
        )
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.get(result["video"]["url"])
            resp.raise_for_status()
            raw.write_bytes(resp.content)
        if _normalize(raw, slot.out):
            logger.info(f"B-roll: {slot.out.name}")
            raw.unlink(missing_ok=True)
            return True
        return False
    except Exception as e:
        logger.error(f"B-roll failed '{slot.prompt[:50]}': {e}")
        raw.unlink(missing_ok=True)
        return False


async def _run_slot(slot: ClipSlot, avatar_bytes: bytes | None, audio_path: str, work: Path) -> bool:
    if slot.kind == "card":
        return _make_card(slot)
    if slot.kind == "avatar":
        if avatar_bytes:
            return await _run_avatar_slot(slot, avatar_bytes, audio_path, work)
        return False
    if slot.kind == "broll":
        return await _run_broll_slot(slot)
    return False


# ── Main orchestrator ────────────────────────────────────────────────────────

async def generate_video(script: Script, audio_path: str, video_id: str) -> str | None:
    """
    Full video assembly:
    1. Plan all segments upfront
    2. Generate avatar image once, then fire ALL segments in parallel
    3. Concat in planned order, mix voiceover
    """
    video_dir = Path(settings.video_dir)
    video_dir.mkdir(parents=True, exist_ok=True)
    work = video_dir / video_id
    work.mkdir(exist_ok=True)

    # 1. Generate presenter avatar image
    logger.info("Generating presenter avatar...")
    niche_hint = " ".join(script.topic.split()[:2])
    avatar_bytes = await _generate_avatar_image(niche_hint)

    # 2. Plan all segments
    fal_client.api_key = settings.fal_key
    slots = _plan(script, audio_path, work)

    # 3. Generate all segments in parallel
    logger.info(f"Generating {len(slots)} segments in parallel...")
    results = await asyncio.gather(*[
        _run_slot(s, avatar_bytes, audio_path, work)
        for s in slots
    ], return_exceptions=True)

    # 4. Collect successful segments in plan order
    segments: list[Path] = []
    for slot, ok in zip(slots, results):
        if ok is True and slot.out.exists():
            segments.append(slot.out)
        elif isinstance(ok, Exception):
            logger.warning(f"Slot {slot.kind} s{slot.sec_idx} raised: {ok}")

    if not segments:
        logger.error("No segments produced")
        return None

    logger.info(f"Concatenating {len(segments)}/{len(slots)} segments...")
    silent = video_dir / f"{video_id}_silent.mp4"
    if not _concat(segments, silent):
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
