from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")
    openai_model: str = "gpt-4o"

    # ElevenLabs
    elevenlabs_api_key: str = Field(..., env="ELEVEN_LABS_API_KEY")
    elevenlabs_voice_id: str = Field(default="21m00Tcm4TlvDq8ikWAM", env="ELEVENLABS_VOICE_ID")
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity_boost: float = 0.75
    elevenlabs_style: float = 0.0
    elevenlabs_speaker_boost: bool = True

    # PlayHT (fallback)
    playht_api_key: str = Field(..., env="PLAYHT_API_KEY")
    playht_user_id: str = Field(..., env="PLAYHT_USER_ID")
    playht_voice: str = "s3://voice-cloning-zero-shot/d9ff78ba-d016-47f6-b0ef-dd630f59414e/female-cs/manifest.json"
    playht_quality: str = "premium"

    # Fireworks (image gen)
    fireworks_api_key: str = Field(..., env="FIREWORK_API_KEY")
    fireworks_model: str = "accounts/fireworks/models/stable-diffusion-xl-1024-v1-0"

    # Bing Search
    bing_api_key: str = Field(..., env="BING_API_KEY")
    bing_search_endpoint: str = "https://api.bing.microsoft.com/v7.0/search"

    # Groq (fast ideation)
    groq_api_key: str = Field(..., env="GROQ_API_KEY")
    groq_model: str = "llama-3.3-70b-versatile"

    # Channel config
    channel_niche: str = Field(default="personal finance", env="CHANNEL_NICHE")
    channel_tone: str = Field(default="authoritative", env="CHANNEL_TONE")
    channel_demographic: str = Field(default="25-45 year old professionals", env="CHANNEL_DEMOGRAPHIC")
    posting_cadence: int = Field(default=3, env="POSTING_CADENCE")

    # Output paths
    output_dir: str = Field(default="./output", env="OUTPUT_DIR")
    audio_dir: str = Field(default="./output/audio", env="AUDIO_DIR")
    thumbnail_dir: str = Field(default="./output/thumbnails", env="THUMBNAIL_DIR")
    script_dir: str = Field(default="./output/scripts", env="SCRIPT_DIR")

    # n8n webhook
    n8n_webhook_url: Optional[str] = Field(default=None, env="N8N_WEBHOOK_URL")
    n8n_api_key: Optional[str] = Field(default=None, env="N8N_API_KEY")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
