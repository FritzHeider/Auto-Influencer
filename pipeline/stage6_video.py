import asyncio
import json
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

BROLL_CLIP_SECONDS = 10   # default clip length (Kling v2 max)
_REMOTION_DIR = Path(__file__).parent.parent / "video-cards"
MAX_CLIPS_PER_CUE = 3
AVATAR_MAX_SECONDS = 10

# ── Video model capability registry ──────────────────────────────────────────

VIDEO_MODEL_REGISTRY: dict[str, dict] = {
    "fal-ai/kling-video/v2/master/text-to-video": {
        "max_duration": 10,
        "base_args": {"duration": "10", "aspect_ratio": "16:9"},
        "i2v_model": "fal-ai/kling-video/v2/master/image-to-video",
        "i2v_start_key": "image_url",
        "i2v_end_key": None,
        "supports_audio": False,
        "audio_key": None,
        "retry_attempts": 3,
    },
    "fal-ai/kling-video/v2.1/master/text-to-video": {
        "max_duration": 10,
        "base_args": {"duration": "10", "aspect_ratio": "16:9"},
        "i2v_model": "fal-ai/kling-video/v2.1/master/image-to-video",
        "i2v_start_key": "image_url",
        "i2v_end_key": None,
        "supports_audio": False,
        "audio_key": None,
        "retry_attempts": 2,
    },
    "fal-ai/kling-video/v3/pro/text-to-video": {
        "max_duration": 10,
        "base_args": {
            "duration": 10, "aspect_ratio": "16:9", "cfg_scale": 0.5,
            "negative_prompt": "blur, distort, low quality",
        },
        "i2v_model": "fal-ai/kling-video/v3/pro/image-to-video",
        "i2v_start_key": "start_image_url",
        "i2v_end_key": "end_image_url",
        "supports_audio": True,
        "audio_key": "generate_audio",
        "retry_attempts": 1,
    },
    "fal-ai/veo3": {
        "max_duration": 8,
        "base_args": {"aspect_ratio": "16:9"},
        "i2v_model": "fal-ai/veo3/image-to-video",
        "i2v_start_key": "image_url",
        "i2v_end_key": None,
        "supports_audio": True,
        "audio_key": "audio_enabled",  # t2v uses audio_enabled
        "retry_attempts": 1,
    },
    "fal-ai/veo3/image-to-video": {
        "max_duration": 8,
        "base_args": {"aspect_ratio": "auto"},
        "i2v_model": None,
        "i2v_start_key": "image_url",
        "i2v_end_key": None,
        "supports_audio": True,
        "audio_key": "audio",  # i2v uses "audio", different from t2v
        "retry_attempts": 1,
    },
    "fal-ai/luma-dream-machine/ray-2-flash": {
        "max_duration": 5,
        "base_args": {"aspect_ratio": "16:9"},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "supports_audio": False,
        "audio_key": None,
        "retry_attempts": 2,
    },
    "fal-ai/ltx-video": {
        "max_duration": 5,
        "base_args": {},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "supports_audio": False,
        "audio_key": None,
        "retry_attempts": 2,
    },
    "fal-ai/minimax/video-01-live": {
        "max_duration": 6,
        "base_args": {},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "supports_audio": False,
        "audio_key": None,
        "retry_attempts": 2,
    },
    # ── Seedance 2.0 (ByteDance) ──────────────────────────────────────────────
    "bytedance/seedance-2.0/text-to-video": {
        "max_duration": 10,
        "base_args": {"duration": "10", "resolution": "720p", "aspect_ratio": "16:9"},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "r2v_model": "bytedance/seedance-2.0/reference-to-video",
        "r2v_ref_key": "image_urls",
        "r2v_prompt_ref": "@Image1",
        "supports_audio": True,
        "audio_key": "generate_audio",
        "retry_attempts": 3,
    },
    "bytedance/seedance-2.0/fast/text-to-video": {
        "max_duration": 10,
        "base_args": {"duration": "10", "resolution": "720p", "aspect_ratio": "16:9"},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "r2v_model": "bytedance/seedance-2.0/fast/reference-to-video",
        "r2v_ref_key": "image_urls",
        "r2v_prompt_ref": "@Image1",
        "supports_audio": True,
        "audio_key": "generate_audio",
        "retry_attempts": 3,
    },
    "bytedance/seedance-2.0/reference-to-video": {
        "max_duration": 10,
        "base_args": {"duration": "10", "resolution": "720p", "aspect_ratio": "16:9"},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "supports_audio": True,
        "audio_key": "generate_audio",
        "retry_attempts": 3,
    },
    "bytedance/seedance-2.0/fast/reference-to-video": {
        "max_duration": 10,
        "base_args": {"duration": "10", "resolution": "720p", "aspect_ratio": "16:9"},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "supports_audio": True,
        "audio_key": "generate_audio",
        "retry_attempts": 3,
    },
    # ── Happy Horse (Alibaba) ─────────────────────────────────────────────────
    "alibaba/happy-horse/text-to-video": {
        "max_duration": 10,
        "base_args": {"duration": 10, "resolution": "1080p", "aspect_ratio": "16:9"},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "r2v_model": "alibaba/happy-horse/reference-to-video",
        "r2v_ref_key": "image_urls",
        "r2v_prompt_ref": "character1",
        "supports_audio": False,
        "audio_key": None,
        "retry_attempts": 3,
    },
    "alibaba/happy-horse/reference-to-video": {
        "max_duration": 10,
        "base_args": {"duration": 10, "resolution": "1080p", "aspect_ratio": "16:9"},
        "i2v_model": None,
        "i2v_start_key": None,
        "i2v_end_key": None,
        "supports_audio": False,
        "audio_key": None,
        "retry_attempts": 3,
    },
}

_DEFAULT_SPEC = VIDEO_MODEL_REGISTRY["fal-ai/kling-video/v2/master/text-to-video"]

SCENE_ANGLES: dict[str, list[str]] = {
    "cinematic": [
        "cinematic wide shot of", "dramatic low-angle shot of",
        "slow-motion capture of", "dynamic tracking shot of",
    ],
    "documentary": [
        "handheld documentary shot of", "overhead aerial view of",
        "smooth close-up shot of", "intimate observational shot of",
    ],
    "artistic": [
        "time-lapse sequence showing", "slow-motion capture of",
        "abstract artistic interpretation of", "stylized visual of",
    ],
    "corporate": [
        "smooth close-up shot of", "dynamic tracking shot of",
        "clean professional shot of", "overhead aerial view of",
    ],
}
_DEFAULT_ANGLES = [
    "cinematic wide shot of", "smooth close-up shot of", "overhead aerial view of",
    "dynamic tracking shot of", "dramatic low-angle shot of", "slow-motion capture of",
    "time-lapse sequence showing", "handheld documentary shot of",
]


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


def _enrich_prompt(
    cue: str,
    topic: str,
    section_content: str,
    variant: int,
    scene_style: str | None = None,
    style_locked: bool = False,
    cinematic_style: str | None = None,
    video_model: str | None = None,
) -> str:
    context = strip_script_markup(section_content)[:60].split(".")[0].strip()
    angles = SCENE_ANGLES.get(scene_style or "", _DEFAULT_ANGLES)
    idx = 0 if style_locked else (variant % len(angles))
    angle = angles[idx]
    model_id = video_model or ""

    if "happy-horse" in model_id:
        # Happy Horse: brevity-first (~20-30 words), plain prose, one cue per shot
        parts = [f"{cue}, {context}", angle.rstrip(" of")]
        if cinematic_style:
            parts.insert(0, cinematic_style)
        prompt = ", ".join(p for p in parts if p)
        return " ".join(prompt.split()[:30])
    else:
        # Kling, Seedance, default: declarative template, concrete cues, no prestige adjectives
        parts = [f"{angle} {cue}", context, "no text overlays"]
        if cinematic_style:
            parts.insert(0, cinematic_style)
        return ", ".join(p for p in parts if p)


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
    start_image_url: str | None = None   # fal.ai URL for i2v/r2v reference image
    use_r2v: bool = False                # route to r2v endpoint (character consistency)
    out: Path = field(default=None)


def _plan(
    script: Script,
    audio_path: str,
    work: Path,
    scene_style: str | None = None,
    style_locked_broll: bool = False,
    cinematic_style: str | None = None,
    video_model: str | None = None,
) -> list[ClipSlot]:
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
                    prompt=_enrich_prompt(
                        cue, script.topic, section.content, v,
                        scene_style=scene_style, style_locked=style_locked_broll,
                        cinematic_style=cinematic_style, video_model=video_model,
                    ),
                    out=work / f"c{clip_num:03d}.mp4",
                ))
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
        "-acodec", "libmp3lame", "-q:a", "2",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and out.exists()
    except Exception as e:
        logger.error(f"Audio split failed: {e}")
        return False


def _normalize(src: Path, dst: Path, keep_audio: bool = False) -> bool:
    audio_args = ["-c:a", "aac", "-b:a", "128k"] if keep_audio else ["-an"]
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        "-r", "24", "-c:v", "libx264", "-preset", "fast",
        *audio_args, str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Normalize failed: {e}")
        return False


def _clip_has_audio(path: Path) -> bool:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_streams", "-select_streams", "a", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return "codec_name" in r.stdout
    except Exception:
        return False


def _add_silent_audio(src: Path, dst: Path) -> bool:
    """Add a silent audio track to a video-only file so concat filter works."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest", str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Add silent audio failed: {e}")
        return False


def _get_video_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(r.stdout)
        for s in data.get("streams", []):
            dur = float(s.get("duration", 0))
            if dur > 0:
                return dur
    except Exception:
        pass
    return 5.0  # safe fallback


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


def _concat_with_audio(paths: list[Path], out: Path) -> bool:
    """Concat clips that all carry audio tracks (for native audio mode)."""
    n = len(paths)
    inputs = []
    for p in paths:
        inputs += ["-i", str(p)]
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_str = f"{streams}concat=n={n}:v=1:a=1[vout][aout]"
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_str,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            logger.error(f"Audio concat failed: {r.stderr[-400:]}")
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Audio concat exception: {e}")
        return False


def _crossfade_concat(paths: list[Path], out: Path, xfade_dur: float = 0.5) -> bool:
    """Concat with xfade dissolve transitions (video-only output, no audio)."""
    if len(paths) == 1:
        import shutil
        shutil.copy2(str(paths[0]), str(out))
        return out.exists()

    durations = [_get_video_duration(p) for p in paths]
    inputs: list[str] = []
    for p in paths:
        inputs += ["-i", str(p)]

    n = len(paths)
    filter_parts: list[str] = []
    cumulative = 0.0
    prev_label = "[0:v]"

    for i in range(1, n):
        cumulative += durations[i - 1]
        # offset = sum of prior clip durations minus accumulated overlap
        offset = max(0.0, cumulative - i * xfade_dur)
        out_label = "[vout]" if i == n - 1 else f"[xf{i}]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade"
            f":duration={xfade_dur:.3f}:offset={offset:.3f}{out_label}"
        )
        prev_label = out_label

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-an",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            logger.error(f"Crossfade concat failed: {r.stderr[-400:]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Crossfade concat exception: {e}")
        return False


def _mix_audio(video: Path, audio: str, out: Path, has_native_audio: bool = False) -> bool:
    if has_native_audio:
        # Blend model ambient audio (12% volume) under voiceover (100%)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video), "-i", audio,
            "-filter_complex",
            "[0:a]volume=0.12[amb];"
            "[1:a:0]aformat=sample_rates=44100:channel_layouts=stereo[vo];"
            "[vo][amb]amix=inputs=2:duration=first[aout]",
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", str(out),
        ]
    else:
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


def _make_card_remotion(slot: ClipSlot) -> bool:
    """Render an animated card via Remotion. Falls back to Pillow on failure."""
    if not _REMOTION_DIR.exists():
        logger.warning("Remotion project not found at %s, using Pillow fallback", _REMOTION_DIR)
        return _make_card(slot)
    props = json.dumps({
        "points": slot.points[:4],
        "title": "KEY POINTS",
        "duration": slot.card_duration,
    })
    cmd = [
        "npx", "remotion", "render", "Card",
        str(slot.out.resolve()),
        f"--props={props}",
        "--codec=h264",
        "--log=error",
        "--overwrite",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            cwd=str(_REMOTION_DIR),
        )
        if r.returncode == 0 and slot.out.exists():
            logger.info(f"Remotion card: {slot.out.name}")
            return True
        logger.error(f"Remotion render failed: {r.stderr[-300:]}  →  falling back to Pillow")
        return _make_card(slot)
    except Exception as e:
        logger.error(f"Remotion card exception: {e}  →  falling back to Pillow")
        return _make_card(slot)


# ── fal.ai generators ────────────────────────────────────────────────────────

async def _load_avatar_from_store(avatar_id: str) -> bytes | None:
    avatar_path = Path(settings.avatar_store_dir) / f"{avatar_id}.jpg"
    if avatar_path.exists():
        return avatar_path.read_bytes()
    logger.warning(f"Avatar {avatar_id} not found in store")
    return None


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


async def _run_avatar_slot(
    slot: ClipSlot,
    avatar_bytes: bytes,
    audio_path: str,
    work: Path,
    avatar_model: str | None = None,
) -> bool:
    model = avatar_model or settings.fal_avatar_model
    aud = work / f"s{slot.sec_idx:02d}_aud.mp3"
    raw = work / f"s{slot.sec_idx:02d}_av_raw.mp4"
    if not _split_audio(audio_path, slot.avatar_audio_start, slot.avatar_audio_dur, aud):
        return False
    try:
        image_url, audio_url = await asyncio.gather(
            fal_client.upload_async(avatar_bytes, "image/jpeg"),
            fal_client.upload_async(aud.read_bytes(), "audio/mpeg"),
        )
        if model == settings.fal_hallo_model:
            result = await fal_client.run_async(
                model,
                arguments={
                    "source_image_url": image_url,
                    "audio_url": audio_url,
                },
            )
        else:
            result = await fal_client.run_async(
                model,
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
            logger.info(f"Avatar ({model.split('/')[-1]}): {slot.out.name}")
            raw.unlink(missing_ok=True)
            return True
        return False
    except Exception as e:
        logger.warning(f"Avatar slot failed (s{slot.sec_idx}): {e}")
        return False
    finally:
        aud.unlink(missing_ok=True)


async def _run_broll_slot(
    slot: ClipSlot,
    video_model: str | None = None,
    use_native_audio: bool = False,
) -> bool:
    model = video_model or settings.fal_video_model
    spec = VIDEO_MODEL_REGISTRY.get(model, _DEFAULT_SPEC)
    fal_client.api_key = settings.fal_key
    raw = slot.out.with_suffix(".raw.mp4")

    # Route to r2v endpoint for character/style consistency across all clips
    if slot.use_r2v and slot.start_image_url and spec.get("r2v_model"):
        r2v_key = spec["r2v_model"]
        r2v_spec = VIDEO_MODEL_REGISTRY.get(r2v_key, spec)
        endpoint = r2v_key
        args = dict(r2v_spec.get("base_args", spec.get("base_args", {})))
        args[spec.get("r2v_ref_key", "image_urls")] = [slot.start_image_url]
        prompt_ref = spec.get("r2v_prompt_ref", "")
        args["prompt"] = f"{prompt_ref} {slot.prompt}".strip() if prompt_ref else slot.prompt
        audio_spec = r2v_spec
    # Route to i2v endpoint if reference image is provided (first clip, image = first frame)
    elif slot.start_image_url and spec.get("i2v_model"):
        endpoint = spec["i2v_model"]
        i2v_spec = VIDEO_MODEL_REGISTRY.get(endpoint, spec)
        args = dict(i2v_spec.get("base_args", spec.get("base_args", {})))
        start_key = spec.get("i2v_start_key", "image_url")
        args[start_key] = slot.start_image_url
        args["prompt"] = slot.prompt
        audio_spec = i2v_spec
    else:
        endpoint = model
        args = dict(spec.get("base_args", {}))
        args["prompt"] = slot.prompt
        audio_spec = spec

    # Only set audio key when audio is wanted — omit entirely when off (some models error on falsy args)
    if use_native_audio and audio_spec.get("supports_audio") and audio_spec.get("audio_key"):
        args[audio_spec["audio_key"]] = True

    attempts = spec.get("retry_attempts", 2)
    for attempt in range(1, attempts + 1):
        try:
            result = await asyncio.wait_for(
                fal_client.run_async(endpoint, arguments=args),
                timeout=900,
            )
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.get(result["video"]["url"])
                resp.raise_for_status()
                raw.write_bytes(resp.content)
            keep = use_native_audio and audio_spec.get("supports_audio", False)
            if _normalize(raw, slot.out, keep_audio=keep):
                logger.info(f"B-roll ({endpoint.split('/')[-1]}): {slot.out.name}")
                raw.unlink(missing_ok=True)
                return True
            raw.unlink(missing_ok=True)
            return False
        except Exception as e:
            raw.unlink(missing_ok=True)
            if attempt < attempts:
                delay = 3 * attempt
                logger.warning(
                    f"B-roll attempt {attempt}/{attempts} failed, retrying in {delay}s: {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"B-roll failed '{slot.prompt[:50]}': {e}")
    return False


async def _run_slot(
    slot: ClipSlot,
    avatar_bytes: bytes | None,
    audio_path: str,
    work: Path,
    avatar_model: str | None = None,
    video_model: str | None = None,
    use_native_audio: bool = False,
    card_style: str = "pillow",
) -> bool:
    if slot.kind == "card":
        return _make_card_remotion(slot) if card_style == "remotion" else _make_card(slot)
    if slot.kind == "avatar":
        if avatar_bytes:
            return await _run_avatar_slot(slot, avatar_bytes, audio_path, work, avatar_model)
        return False
    if slot.kind == "broll":
        return await _run_broll_slot(slot, video_model=video_model, use_native_audio=use_native_audio)
    return False


# ── Reference image loader ───────────────────────────────────────────────────

async def _resolve_reference_image(reference_image_id: str) -> str | None:
    """Load reference image from local store and upload to fal.ai. Returns fal URL."""
    store_dir = Path(settings.reference_media_dir)
    ref_path: Path | None = None
    for ext in [".jpg", ".jpeg", ".png", ".webp"]:
        p = store_dir / f"{reference_image_id}{ext}"
        if p.exists():
            ref_path = p
            break
    if not ref_path:
        logger.warning(f"Reference image {reference_image_id} not found in {store_dir}")
        return None
    suffix = ref_path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
    try:
        fal_url = await fal_client.upload_async(ref_path.read_bytes(), mime)
        logger.info(f"Reference image uploaded to fal.ai: {ref_path.name}")
        return fal_url
    except Exception as e:
        logger.error(f"Reference image upload failed: {e}")
        return None


# ── Main orchestrator ────────────────────────────────────────────────────────

async def generate_video(
    script: Script,
    audio_path: str,
    video_id: str,
    scene_style: str | None = None,
    avatar_id: str | None = None,
    avatar_model: str | None = None,
    style_locked_broll: bool = False,
    video_model: str | None = None,
    reference_image_id: str | None = None,
    use_native_audio: bool = False,
    transition: str = "cut",
    card_style: str = "pillow",
    cinematic_style: str | None = None,
    reference_all_clips: bool = False,
) -> str | None:
    """
    Full video assembly:
    1. Plan all segments upfront
    2. Resolve avatar + optional reference image (uploaded fresh to fal.ai)
    3. Fire ALL segments in parallel
    4. Concat (cut or crossfade), mix voiceover
    """
    video_dir = Path(settings.video_dir)
    video_dir.mkdir(parents=True, exist_ok=True)
    work = video_dir / video_id
    work.mkdir(exist_ok=True)

    fal_client.api_key = settings.fal_key
    effective_model = video_model or settings.fal_video_model

    # Native audio and crossfade are mutually exclusive (xfade concat strips audio)
    effective_transition = "cut" if use_native_audio else transition

    # 1. Resolve avatar image
    if avatar_id:
        logger.info(f"Loading avatar from store: {avatar_id}")
        avatar_bytes = await _load_avatar_from_store(avatar_id)
        if not avatar_bytes:
            logger.warning("Stored avatar not found, generating new one")
            avatar_bytes = await _generate_avatar_image(" ".join(script.topic.split()[:2]))
    else:
        logger.info("Generating presenter avatar...")
        avatar_bytes = await _generate_avatar_image(" ".join(script.topic.split()[:2]))

    # 2. Resolve reference image (fresh upload — fal URLs have short TTL)
    ref_fal_url: str | None = None
    if reference_image_id:
        ref_fal_url = await _resolve_reference_image(reference_image_id)

    # 3. Plan all segments
    slots = _plan(
        script, audio_path, work,
        scene_style=scene_style,
        style_locked_broll=style_locked_broll,
        cinematic_style=cinematic_style,
        video_model=effective_model,
    )

    # 4. Apply reference image to broll slots
    if ref_fal_url:
        broll_slots = [s for s in slots if s.kind == "broll"]
        if reference_all_clips:
            for slot in broll_slots:
                slot.start_image_url = ref_fal_url
                slot.use_r2v = True
            logger.info(f"Reference image → r2v on all {len(broll_slots)} broll slots")
        elif broll_slots:
            broll_slots[0].start_image_url = ref_fal_url
            logger.info(f"Reference image → i2v on first broll: {broll_slots[0].out.name}")

    # 5. Generate all segments in parallel
    logger.info(
        f"Generating {len(slots)} segments in parallel "
        f"(model: {effective_model.split('/')[-1]}, "
        f"cards: {card_style}, native_audio: {use_native_audio}, transition: {effective_transition})"
    )
    results = await asyncio.gather(*[
        _run_slot(s, avatar_bytes, audio_path, work, avatar_model, effective_model, use_native_audio, card_style)
        for s in slots
    ], return_exceptions=True)

    # 6. Collect successful segments in plan order
    segments: list[Path] = []
    for slot, ok in zip(slots, results):
        if ok is True and slot.out.exists():
            segments.append(slot.out)
        elif isinstance(ok, Exception):
            logger.warning(f"Slot {slot.kind} s{slot.sec_idx} raised: {ok}")

    if not segments:
        logger.error("No segments produced")
        return None

    # 7. When native audio is on: ensure every clip has an audio track so concat works
    if use_native_audio:
        prepared: list[Path] = []
        for seg in segments:
            if _clip_has_audio(seg):
                prepared.append(seg)
            else:
                au_seg = seg.with_name(seg.stem + "_au.mp4")
                if _add_silent_audio(seg, au_seg):
                    prepared.append(au_seg)
                    seg.unlink(missing_ok=True)
                else:
                    prepared.append(seg)
        segments = prepared

    logger.info(f"Concatenating {len(segments)}/{len(slots)} segments...")
    silent = video_dir / f"{video_id}_silent.mp4"

    if effective_transition == "crossfade" and len(segments) > 1:
        concat_ok = _crossfade_concat(segments, silent)
    elif use_native_audio:
        concat_ok = _concat_with_audio(segments, silent)
    else:
        concat_ok = _concat(segments, silent)

    if not concat_ok:
        return None

    final = video_dir / f"{video_id}_final.mp4"
    ok = _mix_audio(silent, audio_path, final, has_native_audio=use_native_audio)

    silent.unlink(missing_ok=True)
    for p in segments:
        p.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass

    return str(final) if ok else None
