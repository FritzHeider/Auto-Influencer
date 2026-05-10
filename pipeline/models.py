from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone


class TrendTopic(BaseModel):
    topic_title: str
    search_volume_signal: str
    competition_level: str
    monetization_potential: str
    trending_reason: str
    score: float = 0.0


class HookOption(BaseModel):
    text: str
    curiosity_score: float
    emotional_score: float
    specificity_score: float
    total_score: float


class ResearchResult(BaseModel):
    trends: list[TrendTopic]
    selected_topic: TrendTopic
    hooks: list[HookOption]
    winning_hook: str
    selection_rationale: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScriptSection(BaseModel):
    timestamp_start: str
    timestamp_end: str
    label: str
    content: str
    broll_cues: list[str] = []
    affiliate_insertions: list[str] = []


class Script(BaseModel):
    topic: str
    hook: str
    sections: list[ScriptSection]
    full_text: str
    word_count: int
    estimated_duration_minutes: float
    affiliate_products: list[str] = []
    reading_ease_score: Optional[float] = None


class VoiceSpec(BaseModel):
    provider: str  # openai or elevenlabs
    voice_id: str  # openai: alloy/onyx/nova/etc  elevenlabs: voice UUID
    voice_name: str
    # OpenAI TTS fields
    openai_model: str = "tts-1-hd"
    speed: float = 1.0
    # ElevenLabs fields (used only when provider=elevenlabs)
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    speaker_boost: bool = True
    # Post-processing
    ffmpeg_loudness_lufs: float = -14.0
    ffmpeg_eq_preset: str = "youtube"


class ThumbnailConcept(BaseModel):
    concept_id: int
    layout_description: str
    focal_element: str
    text_overlay: str
    accent_elements: list[str]
    color_mood: str
    image_prompt: str
    ctr_score: float
    is_winner: bool = False


class SEOPackage(BaseModel):
    title: str
    description: str
    tags: list[str]
    chapters: list[str] = []


class AffiliateInsertion(BaseModel):
    product_name: str
    program: str
    commission_rate: str
    script_line: str
    description_placement: str


class VideoPackage(BaseModel):
    video_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    niche: str
    research: ResearchResult
    script: Script
    voice_spec: VoiceSpec
    thumbnail_concepts: list[ThumbnailConcept]
    winning_thumbnail: ThumbnailConcept
    seo: SEOPackage
    affiliates: list[AffiliateInsertion]
    audio_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    status: str = "pending"


class ChannelMetrics(BaseModel):
    subscriber_count: int
    avg_views_per_video: int
    monthly_revenue_usd: float
    channel_age_months: int
    niche: str


class RevenueProjection(BaseModel):
    at_1k_subs: float
    at_10k_subs: float
    at_100k_subs: float
    estimated_cpm_low: float
    estimated_cpm_high: float
    assumptions: list[str]
