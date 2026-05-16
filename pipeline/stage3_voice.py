import re
import json
import asyncio
import logging
import subprocess
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.models import Script, VoiceSpec
from prompts.system_prompts import VOICE_SPEC_PROMPT

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
TTS_CHUNK_SIZE = 4000  # OpenAI TTS hard limit is 4096 chars


def strip_script_markup(text: str) -> str:
    """Remove [PAUSE], [EMPHASIS], [BROLL:...], [AFFILIATE:...] markup for TTS."""
    cleaned = re.sub(r"\[BROLL:[^\]]+\]", "", text)
    cleaned = re.sub(r"\[AFFILIATE:[^\]]+\]", "", cleaned)
    cleaned = re.sub(r"\[EMPHASIS\]", "", cleaned)
    cleaned = re.sub(r"\[PAUSE\]", "...", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def chunk_text(text: str, max_chars: int = TTS_CHUNK_SIZE) -> list[str]:
    """Split text at sentence boundaries so each chunk is under max_chars."""
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current, current_len = [], [], 0
    for sentence in sentences:
        if current and current_len + len(sentence) + 1 > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [sentence], len(sentence)
        else:
            current.append(sentence)
            current_len += len(sentence) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def concat_audio(input_paths: list[Path], output_path: Path) -> bool:
    """Concatenate multiple MP3 files into one using ffmpeg."""
    concat_list = output_path.parent / f"{output_path.stem}_concat.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in input_paths))
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"FFmpeg concat failed: {e}")
        return False
    finally:
        concat_list.unlink(missing_ok=True)


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
        max_tokens=512,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    return VoiceSpec(**{k: v for k, v in data.items() if k not in ("rationale", "voice_description")})


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
async def _generate_openai_chunk(client: AsyncOpenAI, text: str, voice_spec: VoiceSpec, path: Path) -> bool:
    try:
        response = await client.audio.speech.create(
            model=voice_spec.openai_model,
            voice=voice_spec.voice_id,
            input=text,
            speed=voice_spec.speed,
        )
        path.write_bytes(response.content)
        return True
    except Exception as e:
        logger.error(f"OpenAI TTS chunk failed: {e}")
        return False


async def generate_openai_audio(text: str, voice_spec: VoiceSpec, output_path: Path) -> bool:
    """Generate audio via OpenAI TTS, chunking at sentence boundaries if needed."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    chunks = chunk_text(text)

    if len(chunks) == 1:
        ok = await _generate_openai_chunk(client, chunks[0], voice_spec, output_path)
        if ok:
            logger.info(f"OpenAI TTS audio saved: {output_path}")
        return ok

    logger.info(f"Script exceeds {TTS_CHUNK_SIZE} chars — generating {len(chunks)} chunks")
    chunk_paths = [output_path.parent / f"{output_path.stem}_chunk{i}.mp3" for i in range(len(chunks))]
    results = await asyncio.gather(*[
        _generate_openai_chunk(client, chunk, voice_spec, path)
        for chunk, path in zip(chunks, chunk_paths)
    ])

    if not all(results):
        for p in chunk_paths:
            p.unlink(missing_ok=True)
        return False

    ok = concat_audio(chunk_paths, output_path)
    for p in chunk_paths:
        p.unlink(missing_ok=True)
    if ok:
        logger.info(f"OpenAI TTS audio saved ({len(chunks)} chunks): {output_path}")
    return ok


async def generate_elevenlabs_audio(text: str, voice_spec: VoiceSpec, output_path: Path) -> bool:
    """Generate audio via ElevenLabs API (fallback)."""
    if not settings.elevenlabs_api_key:
        return False
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
    voice_id_override: str | None = None,
) -> tuple[VoiceSpec, str]:
    """Full voiceover pipeline: spec selection → generation → post-processing."""
    audio_dir = Path(settings.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    if voice_id_override:
        voice_spec = VoiceSpec(
            provider="openai",
            voice_id=voice_id_override,
            voice_name=voice_id_override,
        )
        logger.info(f"Using voice override: {voice_id_override}")
    else:
        voice_spec = await select_voice_spec(niche, tone, demographic, script.estimated_duration_minutes)
        logger.info(f"Selected voice: {voice_spec.voice_name} via {voice_spec.provider}")

    clean_text = strip_script_markup(script.full_text)
    raw_path = audio_dir / f"{video_id}_raw.mp3"
    final_path = audio_dir / f"{video_id}_final.mp3"

    # OpenAI TTS primary, ElevenLabs fallback
    success = await generate_openai_audio(clean_text, voice_spec, raw_path)

    if not success:
        logger.warning("OpenAI TTS failed — trying ElevenLabs fallback")
        voice_spec.provider = "elevenlabs"
        success = await generate_elevenlabs_audio(clean_text, voice_spec, raw_path)

    if not success:
        raise RuntimeError(f"All TTS providers failed for video {video_id}")

    post_process_audio(raw_path, final_path, lufs=voice_spec.ffmpeg_loudness_lufs)
    raw_path.unlink(missing_ok=True)

    return voice_spec, str(final_path)
