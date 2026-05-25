from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone


class Character(BaseModel):
    character_id: str
    name: str
    role: str = "supporting"   # protagonist | antagonist | supporting | narrator
    description: str = ""      # physical appearance + personality
    backstory: str = ""
    voice_id: str = ""         # openai voice name or elevenlabs UUID
    image_path: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Series(BaseModel):
    series_id: str
    title: str
    genre: str = "drama"       # sci-fi | fantasy | thriller | drama | documentary | horror | comedy
    tone: str = "cinematic"    # dark | uplifting | mysterious | comedic | epic | tense
    logline: str = ""
    world_notes: str = ""      # passed verbatim to every episode prompt
    visual_style: str = ""     # cinematic style prefix for broll
    color_grade: str = ""      # warm | cold | desaturated | vibrant | noir
    character_ids: list[str] = []
    episode_count: int = 0
    last_cliffhanger: str = "" # stored after each episode for "Previously on..."
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EpisodeBrief(BaseModel):
    episode_concept: str
    opening_hook: str
    key_beats: list[str]
    themes: list[str]
    character_focus: list[str]
    cliffhanger: str
    previously_on: str = ""


class ScriptSection(BaseModel):
    timestamp_start: str
    timestamp_end: str
    label: str              # COLD_OPEN | ACT_1 | ACT_2 | ACT_3 | CLIMAX | DENOUEMENT | CLIFFHANGER
    content: str
    broll_cues: list[str] = []
    shot_types: list[str] = []
    characters_present: list[str] = []
    mood: str = ""


class Script(BaseModel):
    episode_title: str
    episode_number: int = 1
    series_title: str = ""
    opening_hook: str
    sections: list[ScriptSection]
    full_text: str
    word_count: int
    estimated_duration_minutes: float
    narration_style: str = "third_person"
    cliffhanger: str = ""


class VoiceSpec(BaseModel):
    provider: str
    voice_id: str
    voice_name: str
    openai_model: str = "tts-1-hd"
    speed: float = 1.0
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    speaker_boost: bool = True
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
    rendered_path: Optional[str] = None


class EpisodeMetadata(BaseModel):
    title: str
    description: str
    tags: list[str]
    chapters: list[str] = []
    synopsis: str = ""


class VideoPackage(BaseModel):
    schema_version: str = "3"
    video_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    series_id: Optional[str] = None
    series_title: str = ""
    episode_number: int = 1
    genre: str = "drama"
    episode_brief: EpisodeBrief
    script: Script
    voice_spec: VoiceSpec
    thumbnail_concepts: list[ThumbnailConcept]
    winning_thumbnail: ThumbnailConcept
    metadata: EpisodeMetadata
    characters: list[str] = []
    audio_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    video_path: Optional[str] = None
    status: str = "pending"
    stage_timings: dict[str, float] = Field(default_factory=dict)
