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
    fal_video_model: str = "fal-ai/kling-video/v2/master/text-to-video"
    fal_avatar_model: str = "fal-ai/sadtalker"

    # Groq (fast ideation)
    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"

    # Channel config
    channel_niche: str = "personal finance"
    channel_tone: str = "authoritative"
    channel_demographic: str = "25-45 year old professionals"
    posting_cadence: int = 3

    # Output paths
    output_dir: str = "./output"
    audio_dir: str = "./output/audio"
    thumbnail_dir: str = "./output/thumbnails"
    video_dir: str = "./output/video"
    script_dir: str = "./output/scripts"

    # n8n webhook
    n8n_webhook_url: Optional[str] = None
    n8n_api_key: Optional[str] = None


settings = Settings()
