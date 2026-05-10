import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

from pipeline.models import (
    TrendTopic, HookOption, ResearchResult,
    Script, ScriptSection, VoiceSpec,
    ThumbnailConcept, SEOPackage, AffiliateInsertion,
)
from pipeline.stage3_voice import strip_script_markup
from pipeline.stage2_script import get_affiliate_products


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_research() -> ResearchResult:
    topic = TrendTopic(
        topic_title="5 Money Mistakes Destroying Your 30s",
        search_volume_signal="high",
        competition_level="moderate",
        monetization_potential="high",
        trending_reason="Gen Z entering peak earning years and anxious about retirement",
        score=8.5,
    )
    hook = HookOption(
        text="If you're in your 30s and still making these 5 money mistakes you're already behind",
        curiosity_score=9.0,
        emotional_score=8.5,
        specificity_score=8.0,
        total_score=8.75,
    )
    return ResearchResult(
        trends=[topic],
        selected_topic=topic,
        hooks=[hook],
        winning_hook=hook.text,
        selection_rationale="Combines age-specific urgency with numbered list format for high CTR",
    )


@pytest.fixture
def sample_script(sample_research) -> Script:
    hook_section = ScriptSection(
        timestamp_start="00:00",
        timestamp_end="00:08",
        label="HOOK",
        content="If you're in your 30s [PAUSE] and still making these 5 money mistakes [EMPHASIS] you're already behind",
        broll_cues=["city skyline time-lapse", "person checking phone with worried expression"],
        affiliate_insertions=[],
    )
    return Script(
        topic=sample_research.selected_topic.topic_title,
        hook=sample_research.winning_hook,
        sections=[hook_section],
        full_text="If you're in your 30s [PAUSE] and still making these 5 money mistakes [EMPHASIS] you're already behind [BROLL: city skyline]",
        word_count=450,
        estimated_duration_minutes=8.5,
        affiliate_products=["Robinhood", "Acorns"],
    )


@pytest.fixture
def sample_seo() -> SEOPackage:
    return SEOPackage(
        title="5 Money Mistakes Destroying Your 30s (Fix These NOW)",
        description="Are you in your 30s making these common money mistakes? In this video we break down...",
        tags=["personal finance", "money mistakes", "financial advice"],
        chapters=["00:00 Introduction", "01:30 Mistake #1"],
    )


# ── Unit tests ────────────────────────────────────────────────────────────────

class TestScriptMarkupStripping:
    def test_removes_broll_cues(self):
        text = "Hello world [BROLL: city skyline] more text"
        result = strip_script_markup(text)
        assert "[BROLL:" not in result
        assert "Hello world" in result
        assert "more text" in result

    def test_removes_affiliate_markers(self):
        text = "Check out [AFFILIATE: Robinhood] for investing"
        result = strip_script_markup(text)
        assert "[AFFILIATE:" not in result
        assert "Check out" in result

    def test_removes_emphasis_markers(self):
        text = "This is [EMPHASIS] very important"
        result = strip_script_markup(text)
        assert "[EMPHASIS]" not in result
        assert "This is  very important" in result or "This is very important" in result

    def test_converts_pause_to_ellipsis(self):
        text = "Think about it [PAUSE] seriously"
        result = strip_script_markup(text)
        assert "..." in result
        assert "[PAUSE]" not in result

    def test_collapses_whitespace(self):
        text = "Word   [BROLL: something]   another"
        result = strip_script_markup(text)
        assert "  " not in result


class TestAffiliateProducts:
    def test_personal_finance_niche(self):
        products = get_affiliate_products("personal finance tips")
        assert len(products) > 0
        assert any(p in ["Robinhood", "Acorns", "Personal Capital"] for p in products)

    def test_tech_niche(self):
        products = get_affiliate_products("tech reviews")
        assert any(p in ["Amazon", "NordVPN", "Skillshare"] for p in products)

    def test_unknown_niche_returns_defaults(self):
        products = get_affiliate_products("underwater basket weaving")
        assert len(products) > 0

    def test_case_insensitive(self):
        products_lower = get_affiliate_products("personal finance")
        products_upper = get_affiliate_products("Personal Finance")
        assert products_lower == products_upper


class TestModels:
    def test_video_package_serialization(self, sample_research, sample_script, sample_seo):
        from pipeline.models import VideoPackage
        voice_spec = VoiceSpec(
            provider="elevenlabs",
            voice_id="21m00Tcm4TlvDq8ikWAM",
            voice_name="Rachel",
            stability=0.5,
            similarity_boost=0.75,
            style=0.0,
            speaker_boost=True,
        )
        thumbnail = ThumbnailConcept(
            concept_id=1,
            layout_description="Dark background with gold coins",
            focal_element="Stack of gold coins with upward arrow",
            text_overlay="STOP LOSING MONEY",
            accent_elements=["red warning border", "dollar sign icon"],
            color_mood="high contrast",
            fireworks_prompt="Photorealistic stack of gold coins...",
            ctr_score=8.2,
            is_winner=True,
        )
        pkg = VideoPackage(
            video_id="vid_test_001",
            niche="personal finance",
            research=sample_research,
            script=sample_script,
            voice_spec=voice_spec,
            thumbnail_concepts=[thumbnail],
            winning_thumbnail=thumbnail,
            seo=sample_seo,
            affiliates=[],
        )
        # Test round-trip JSON serialization
        json_str = pkg.model_dump_json()
        data = json.loads(json_str)
        assert data["video_id"] == "vid_test_001"
        assert data["niche"] == "personal finance"
        assert data["script"]["word_count"] == 450

    def test_trend_topic_scoring(self):
        topic = TrendTopic(
            topic_title="Test",
            search_volume_signal="high",
            competition_level="untapped",
            monetization_potential="high",
            trending_reason="test",
            score=9.5,
        )
        assert topic.score == 9.5

    def test_research_result_has_winning_hook(self, sample_research):
        assert len(sample_research.winning_hook) > 10
        assert sample_research.selected_topic.score == 8.5


class TestResearchStage:
    @pytest.mark.asyncio
    async def test_run_research_uses_groq(self, sample_research):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({
            "trends": [sample_research.selected_topic.model_dump()],
            "selected_topic": sample_research.selected_topic.model_dump(),
            "hooks": [h.model_dump() for h in sample_research.hooks],
            "winning_hook": sample_research.winning_hook,
            "selection_rationale": sample_research.selection_rationale,
        })

        with patch("pipeline.stage1_research.fetch_bing_trends", new_callable=AsyncMock) as mock_bing:
            mock_bing.return_value = "Mock bing context"
            with patch("pipeline.stage1_research.AsyncGroq") as mock_groq_cls:
                mock_groq = AsyncMock()
                mock_groq.chat.completions.create.return_value = mock_response
                mock_groq_cls.return_value = mock_groq

                from pipeline.stage1_research import run_research
                result = await run_research("personal finance", "authoritative", "25-45 professionals")

                assert result.selected_topic.topic_title == sample_research.selected_topic.topic_title
                assert result.winning_hook == sample_research.winning_hook
                mock_bing.assert_called_once()


class TestVoiceStage:
    @pytest.mark.asyncio
    async def test_elevenlabs_success_skips_playht(self, sample_script):
        with patch("pipeline.stage3_voice.select_voice_spec", new_callable=AsyncMock) as mock_spec:
            mock_spec.return_value = VoiceSpec(
                provider="elevenlabs",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                voice_name="Rachel",
                stability=0.5,
                similarity_boost=0.75,
                style=0.0,
                speaker_boost=True,
            )
            with patch("pipeline.stage3_voice.generate_elevenlabs_audio", new_callable=AsyncMock) as mock_el:
                mock_el.return_value = True
                with patch("pipeline.stage3_voice.post_process_audio", return_value=True):
                    from pipeline.stage3_voice import generate_voiceover
                    from pathlib import Path
                    import tempfile
                    with tempfile.TemporaryDirectory() as tmpdir:
                        with patch("pipeline.stage3_voice.settings") as mock_settings:
                            mock_settings.audio_dir = tmpdir
                            mock_settings.elevenlabs_api_key = "test"
                            mock_settings.playht_api_key = "test"
                            mock_settings.playht_user_id = "test"
                            mock_settings.playht_voice = "test"
                            mock_settings.playht_quality = "premium"
                            spec, path = await generate_voiceover(
                                sample_script, "finance", "authoritative", "25-45", "vid_test"
                            )
                            assert spec.provider == "elevenlabs"
                            mock_el.assert_called_once()
