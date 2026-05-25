from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o"

    # ElevenLabs — optional fallback TTS, env var is ELEVEN_API_KEY
    elevenlabs_api_key: Optional[str] = Field(default=None, validation_alias="ELEVEN_API_KEY")

    # fal.ai (image + video gen)
    fal_key: str = Field(validation_alias="FAL_KEY")
    fal_model: str = "fal-ai/flux/dev"
    fal_thumbnail_pro_model: str = "fal-ai/flux-pro/v1.1"
    fal_thumbnail_ultra_model: str = "fal-ai/flux-pro/v1.1-ultra"
    fal_video_model: str = "fal-ai/kling-video/v2/master/text-to-video"
    fal_video_i2v_model: str = "fal-ai/kling-video/v2/master/image-to-video"
    fal_kling_v21_model: str = "fal-ai/kling-video/v2.1/master/text-to-video"
    fal_kling_v3_model: str = "fal-ai/kling-video/v3/pro/text-to-video"
    fal_veo3_model: str = "fal-ai/veo3"
    fal_luma_model: str = "fal-ai/luma-dream-machine/ray-2-flash"
    fal_ltx_model: str = "fal-ai/ltx-video"
    fal_minimax_model: str = "fal-ai/minimax/video-01-live"
    fal_avatar_model: str = "fal-ai/sadtalker"
    fal_hallo_model: str = "fal-ai/hallo"

    # Groq (fast ideation)
    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"

    # Series defaults
    default_genre: str = "drama"
    default_tone: str = "cinematic"

    # Output paths
    output_dir: str = "./output"
    audio_dir: str = "./output/audio"
    thumbnail_dir: str = "./output/thumbnails"
    video_dir: str = "./output/video"
    script_dir: str = "./output/scripts"
    avatar_store_dir: str = "./output/avatars"
    reference_media_dir: str = "./output/reference_media"

    # n8n webhook
    n8n_webhook_url: Optional[str] = None
    n8n_api_key: Optional[str] = None

    # CORS — set to specific origins in production (e.g. ["https://yourdomain.com"])
    allowed_origins: list[str] = ["*"]


settings = Settings()
