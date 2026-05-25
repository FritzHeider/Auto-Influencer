import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from pipeline.models import (
    EpisodeBrief,
    Script,
    ScriptSection,
    VoiceSpec,
    ThumbnailConcept,
    EpisodeMetadata,
    VideoPackage,
)
from pipeline.stage3_voice import strip_script_markup


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_brief() -> EpisodeBrief:
    return EpisodeBrief(
        episode_concept="A detective uncovers a conspiracy reaching into the city's highest offices.",
        opening_hook="The moment she opened the envelope, she knew there was no going back.",
        key_beats=[
            "Detective finds an anonymous tip at her door",
            "First informant is found dead hours after contact",
            "She traces the money to a city councilman",
            "Her partner warns her off the case",
            "She goes rogue and breaks into city hall",
        ],
        themes=["corruption", "loyalty"],
        character_focus=["Detective Mira Cole"],
        cliffhanger="The files are gone — and someone has broken into her apartment.",
        previously_on="",
    )


@pytest.fixture
def sample_section() -> ScriptSection:
    return ScriptSection(
        timestamp_start="00:00",
        timestamp_end="00:20",
        label="COLD_OPEN",
        content="[SHOT: WIDE ESTABLISHING] [MOOD: TENSE] The city never sleeps [PAUSE] but tonight [EMPHASIS] it holds its breath. [BROLL: rain-slicked alley, detective's silhouette under a flickering streetlamp, low-angle shot]",
        broll_cues=["rain-slicked alley, detective's silhouette under a flickering streetlamp, low-angle shot"],
        shot_types=["WIDE ESTABLISHING"],
        characters_present=["Detective Mira Cole"],
        mood="tense",
    )


@pytest.fixture
def sample_script(sample_section, sample_brief) -> Script:
    return Script(
        episode_title="The Envelope",
        episode_number=1,
        series_title="Night City",
        opening_hook=sample_brief.opening_hook,
        sections=[sample_section],
        full_text=(
            "[SHOT: WIDE ESTABLISHING] [MOOD: TENSE] The city never sleeps [PAUSE] "
            "but tonight [EMPHASIS] it holds its breath. "
            "[BROLL: rain-slicked alley, detective silhouette]"
        ),
        word_count=620,
        estimated_duration_minutes=9.5,
        narration_style="third_person",
        cliffhanger=sample_brief.cliffhanger,
    )


@pytest.fixture
def sample_metadata() -> EpisodeMetadata:
    return EpisodeMetadata(
        title="Night City | Ep 1 — The Envelope",
        description="She thought it was just another anonymous tip. She was wrong.",
        tags=["noir", "thriller", "crime drama", "serialized fiction"],
        chapters=["00:00 Cold Open", "00:20 Act 1"],
        synopsis="Detective Mira Cole receives an anonymous tip that draws her into a city-wide conspiracy.",
    )


# ── Strip markup tests ────────────────────────────────────────────────────────

class TestScriptMarkupStripping:
    def test_removes_broll_cues(self):
        text = "Hello world [BROLL: rain-slicked city street at night] more text"
        result = strip_script_markup(text)
        assert "[BROLL:" not in result
        assert "Hello world" in result
        assert "more text" in result

    def test_removes_shot_types(self):
        text = "[SHOT: WIDE ESTABLISHING] The city sprawls below"
        result = strip_script_markup(text)
        assert "[SHOT:" not in result
        assert "The city sprawls below" in result

    def test_removes_mood_tags(self):
        text = "[MOOD: TENSE] She reached for the door"
        result = strip_script_markup(text)
        assert "[MOOD:" not in result
        assert "She reached for the door" in result

    def test_removes_emphasis_markers(self):
        text = "This is [EMPHASIS] very important"
        result = strip_script_markup(text)
        assert "[EMPHASIS]" not in result
        assert "very important" in result

    def test_converts_pause_to_ellipsis(self):
        text = "Think about it [PAUSE] seriously"
        result = strip_script_markup(text)
        assert "..." in result
        assert "[PAUSE]" not in result

    def test_collapses_whitespace(self):
        text = "Word   [BROLL: something long here]   another"
        result = strip_script_markup(text)
        assert "  " not in result

    def test_no_affiliate_tags_exist(self):
        text = "Clean cinematic narration with no product placements."
        result = strip_script_markup(text)
        assert result == text

    def test_multiline_broll_stripped(self):
        text = "Narration start [BROLL: sweeping\naerial shot of the city] narration end"
        result = strip_script_markup(text)
        assert "[BROLL:" not in result
        assert "Narration start" in result
        assert "narration end" in result


# ── Model tests ───────────────────────────────────────────────────────────────

class TestModels:
    def test_episode_brief_fields(self, sample_brief):
        assert len(sample_brief.key_beats) == 5
        assert sample_brief.previously_on == ""
        assert "conspiracy" in sample_brief.episode_concept

    def test_script_section_defaults(self, sample_section):
        assert sample_section.label == "COLD_OPEN"
        assert len(sample_section.broll_cues) == 1
        assert sample_section.mood == "tense"

    def test_video_package_serialization(self, sample_brief, sample_script, sample_metadata):
        voice_spec = VoiceSpec(
            provider="openai",
            voice_id="echo",
            voice_name="echo",
            openai_model="tts-1-hd",
            speed=0.92,
        )
        thumbnail = ThumbnailConcept(
            concept_id=1,
            layout_description="Dark rain-soaked street with solitary silhouette",
            focal_element="silhouette under lamplight",
            text_overlay="THE ENVELOPE",
            accent_elements=["rain streaks", "neon reflections"],
            color_mood="noir — cold blue-grey",
            image_prompt="Cinematic noir city street, heavy rain, lone figure under flickering lamp, anamorphic lens, photorealistic...",
            ctr_score=8.7,
            is_winner=True,
        )
        pkg = VideoPackage(
            video_id="ep_test_001",
            series_id="ser_abc123",
            series_title="Night City",
            episode_number=1,
            genre="thriller",
            episode_brief=sample_brief,
            script=sample_script,
            voice_spec=voice_spec,
            thumbnail_concepts=[thumbnail],
            winning_thumbnail=thumbnail,
            metadata=sample_metadata,
        )
        data = json.loads(pkg.model_dump_json())
        assert data["video_id"] == "ep_test_001"
        assert data["series_title"] == "Night City"
        assert data["episode_number"] == 1
        assert data["script"]["word_count"] == 620
        assert data["winning_thumbnail"]["is_winner"] is True
        assert "affiliates" not in data
        assert "seo" not in data

    def test_video_package_schema_version(self, sample_brief, sample_script, sample_metadata):
        voice_spec = VoiceSpec(provider="openai", voice_id="onyx", voice_name="onyx")
        thumbnail = ThumbnailConcept(
            concept_id=1, layout_description="x", focal_element="x", text_overlay="x",
            accent_elements=[], color_mood="dark", image_prompt="x", ctr_score=7.0, is_winner=True,
        )
        pkg = VideoPackage(
            video_id="ep_schema_test",
            episode_brief=sample_brief,
            script=sample_script,
            voice_spec=voice_spec,
            thumbnail_concepts=[thumbnail],
            winning_thumbnail=thumbnail,
            metadata=sample_metadata,
        )
        assert pkg.schema_version == "3"


# ── Story brief stage ─────────────────────────────────────────────────────────

class TestStoryBriefStage:
    @pytest.mark.asyncio
    async def test_generate_story_brief_uses_groq(self, sample_brief):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(sample_brief.model_dump())

        with patch("pipeline.stage1_research._groq_client") as mock_groq:
            mock_groq.chat.completions.create = AsyncMock(return_value=mock_response)

            from pipeline.stage1_research import generate_story_brief
            result = await generate_story_brief(
                genre="thriller",
                tone="dark",
                series_title="Night City",
                episode_number=1,
            )

            assert result.opening_hook == sample_brief.opening_hook
            assert len(result.key_beats) == 5
            mock_groq.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_story_brief_falls_back_to_openai(self, sample_brief):
        mock_oai_response = MagicMock()
        mock_oai_response.choices[0].message.content = json.dumps(sample_brief.model_dump())

        with patch("pipeline.stage1_research._groq_client") as mock_groq:
            mock_groq.chat.completions.create = AsyncMock(side_effect=Exception("Groq down"))
            with patch("pipeline.stage1_research._openai_client") as mock_oai:
                mock_oai.chat.completions.create = AsyncMock(return_value=mock_oai_response)

                from pipeline.stage1_research import generate_story_brief
                result = await generate_story_brief(genre="drama", tone="cinematic")

                assert result.cliffhanger == sample_brief.cliffhanger
                mock_oai.chat.completions.create.assert_called_once()


# ── Voice stage ───────────────────────────────────────────────────────────────

class TestVoiceStage:
    @pytest.mark.asyncio
    async def test_openai_tts_primary(self, sample_script):
        with patch("pipeline.stage3_voice.select_voice_spec", new_callable=AsyncMock) as mock_spec:
            mock_spec.return_value = VoiceSpec(
                provider="openai", voice_id="echo", voice_name="echo",
                openai_model="tts-1-hd", speed=0.92,
            )
            with patch("pipeline.stage3_voice.generate_openai_audio", new_callable=AsyncMock) as mock_oai:
                mock_oai.return_value = True
                with patch("pipeline.stage3_voice.post_process_audio", return_value=True):
                    with patch("pipeline.stage3_voice.asyncio.to_thread", new_callable=AsyncMock):
                        from pipeline.stage3_voice import generate_voiceover
                        import tempfile
                        with tempfile.TemporaryDirectory() as tmpdir:
                            with patch("pipeline.stage3_voice.settings") as mock_settings:
                                mock_settings.audio_dir = tmpdir
                                mock_settings.openai_api_key = "test"
                                spec, path = await generate_voiceover(
                                    sample_script, "thriller", "dark", "third_person", "ep_test_001"
                                )
                                assert spec.provider == "openai"
                                mock_oai.assert_called_once()

    @pytest.mark.asyncio
    async def test_elevenlabs_fallback_on_openai_failure(self, sample_script):
        with patch("pipeline.stage3_voice.select_voice_spec", new_callable=AsyncMock) as mock_spec:
            mock_spec.return_value = VoiceSpec(
                provider="openai", voice_id="onyx", voice_name="onyx",
            )
            with patch("pipeline.stage3_voice.generate_openai_audio", new_callable=AsyncMock) as mock_oai:
                mock_oai.return_value = False
                with patch("pipeline.stage3_voice.generate_elevenlabs_audio", new_callable=AsyncMock) as mock_el:
                    mock_el.return_value = True
                    with patch("pipeline.stage3_voice.post_process_audio", return_value=True):
                        with patch("pipeline.stage3_voice.asyncio.to_thread", new_callable=AsyncMock):
                            from pipeline.stage3_voice import generate_voiceover
                            import tempfile
                            with tempfile.TemporaryDirectory() as tmpdir:
                                with patch("pipeline.stage3_voice.settings") as mock_settings:
                                    mock_settings.audio_dir = tmpdir
                                    mock_settings.openai_api_key = "test"
                                    spec, path = await generate_voiceover(
                                        sample_script, "thriller", "dark", "third_person", "ep_test_001"
                                    )
                                    mock_oai.assert_called_once()
                                    mock_el.assert_called_once()
