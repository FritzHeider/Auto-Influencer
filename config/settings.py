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

    # ElevenLabs — env var is ELEVEN_API_KEY (not ELEVENLABS_*)
    elevenlabs_api_key: str = Field(validation_alias="ELEVEN_API_KEY")
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity_boost: float = 0.75
    elevenlabs_style: float = 0.0
    elevenlabs_speaker_boost: bool = True

    # PlayHT (fallback)
    playht_api_key: str
    playht_user_id: str
    playht_voice: str = "s3://voice-cloning-zero-shot/d9ff78ba-d016-47f6-b0ef-dd630f59414e/female-cs/manifest.json"
    playht_quality: str = "premium"

    # Fireworks (image gen) — env var is FIREWORK_API_KEY (no trailing S)
    fireworks_api_key: str = Field(validation_alias="FIREWORK_API_KEY")
    fireworks_model: str = "accounts/fireworks/models/stable-diffusion-xl-1024-v1-0"

    # Bing Search
    bing_api_key: str
    bing_search_endpoint: str = "https://api.bing.microsoft.com/v7.0/search"

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
    script_dir: str = "./output/scripts"

    # n8n webhook
    n8n_webhook_url: Optional[str] = None
    n8n_api_key: Optional[str] = None


settings = Settings()
