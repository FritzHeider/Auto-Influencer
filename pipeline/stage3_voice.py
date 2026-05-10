import re
import json
import asyncio
import logging
import subprocess
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from config.settings import settings
from pipeline.models import Script, VoiceSpec
from prompts.system_prompts import VOICE_SPEC_PROMPT

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
PLAYHT_TTS_URL = "https://api.play.ht/api/v2/tts/stream"


def strip_script_markup(text: str) -> str:
    """Remove [PAUSE], [EMPHASIS], [BROLL:...], [AFFILIATE:...] markup for TTS."""
    cleaned = re.sub(r"\[BROLL:[^\]]+\]", "", text)
    cleaned = re.sub(r"\[AFFILIATE:[^\]]+\]", "", cleaned)
    cleaned = re.sub(r"\[EMPHASIS\]", "", cleaned)
    cleaned = re.sub(r"\[PAUSE\]", "...", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


async def select_voice_spec(niche: str, tone: str, demographic: str, duration: float) -> VoiceSpec:
    """Use OpenAI to select optimal voice settings for this content."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = VOICE_SPEC_PROMPT.format(
        niche=niche,
        tone=tone,
        demographic=demographic,
        duration=f"{duration:.1f}",
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Respond only with valid JSON. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=1024,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    return VoiceSpec(**{k: v for k, v in data.items() if k != "rationale"})


async def generate_elevenlabs_audio(text: str, voice_spec: VoiceSpec, output_path: Path) -> bool:
    """Generate audio via ElevenLabs API."""
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_spec.voice_id)
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {
            "stability": voice_spec.stability,
            "similarity_boost": voice_spec.similarity_boost,
            "style": voice_spec.style,
            "use_speaker_boost": voice_spec.speaker_boost,
        },
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(
                url,
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            logger.info(f"ElevenLabs audio saved: {output_path}")
            return True
        except Exception as e:
            logger.error(f"ElevenLabs failed: {e}")
            return False


async def generate_playht_audio(text: str, output_path: Path) -> bool:
    """Generate audio via PlayHT as fallback."""
    payload = {
        "text": text,
        "voice": settings.playht_voice,
        "output_format": "mp3",
        "quality": settings.playht_quality,
        "speed": 1.0,
        "temperature": 0.6,
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(
                PLAYHT_TTS_URL,
                headers={
                    "Authorization": f"Bearer {settings.playht_api_key}",
                    "X-User-Id": settings.playht_user_id,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json=payload,
            )
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            logger.info(f"PlayHT audio saved: {output_path}")
            return True
        except Exception as e:
            logger.error(f"PlayHT failed: {e}")
            return False


def post_process_audio(input_path: Path, output_path: Path, lufs: float = -14.0) -> bool:
    """Apply FFmpeg post-processing: loudness normalization + EQ."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-af", (
            f"loudnorm=I={lufs}:TP=-1.5:LRA=11,"
            "equalizer=f=200:width_type=o:width=2:g=-3,"
            "equalizer=f=3000:width_type=o:width=1:g=2,"
            "equalizer=f=8000:width_type=o:width=1:g=1"
        ),
        "-ar", "44100",
        "-b:a", "192k",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info(f"FFmpeg post-processing complete: {output_path}")
            return True
        else:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False
    except FileNotFoundError:
        logger.warning("FFmpeg not found — skipping post-processing, using raw audio")
        input_path.rename(output_path)
        return True
    except Exception as e:
        logger.error(f"FFmpeg exception: {e}")
        return False


async def generate_voiceover(
    script: Script,
    niche: str,
    tone: str,
    demographic: str,
    video_id: str,
) -> tuple[VoiceSpec, str]:
    """Full voiceover pipeline: spec selection + generation + post-processing."""
    audio_dir = Path(settings.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    voice_spec = await select_voice_spec(niche, tone, demographic, script.estimated_duration_minutes)
    logger.info(f"Selected voice: {voice_spec.voice_name} (stability={voice_spec.stability})")

    clean_text = strip_script_markup(script.full_text)
    raw_path = audio_dir / f"{video_id}_raw.mp3"
    final_path = audio_dir / f"{video_id}_final.mp3"

    success = await generate_elevenlabs_audio(clean_text, voice_spec, raw_path)

    if not success:
        logger.warning("ElevenLabs failed — trying PlayHT fallback")
        voice_spec.provider = "playht"
        success = await generate_playht_audio(clean_text, raw_path)

    if not success:
        raise RuntimeError(f"All TTS providers failed for video {video_id}")

    post_process_audio(raw_path, final_path, lufs=voice_spec.ffmpeg_loudness_lufs)

    return voice_spec, str(final_path)
