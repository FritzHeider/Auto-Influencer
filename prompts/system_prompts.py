STORY_BRIEF_PROMPT = """You are a cinematic story architect for serialized AI-generated video episodes.

Series: {series_title}
Genre: {genre}
Tone: {tone}
World context: {world_notes}
Characters available: {characters}
Previously on (last episode cliffhanger): {previously_on}
Creative direction from creator: {story_prompt}

Generate the concept for Episode {episode_number}.

Produce:
1. episode_concept — compelling 2-3 sentence episode premise
2. opening_hook — cinematic spoken hook (15-25 words) designed to arrest attention immediately
3. key_beats — exactly 5 major story moments that carry the episode arc
4. themes — 2-3 central themes explored this episode
5. character_focus — which characters are featured and what drives them
6. cliffhanger — the episode-ending revelation or unresolved tension that demands a next episode
7. previously_on — a "Previously on [series]..." recap paragraph (empty string if episode 1)

Respond ONLY with valid JSON:
{{
  "episode_concept": "string",
  "opening_hook": "string",
  "key_beats": ["beat1", "beat2", "beat3", "beat4", "beat5"],
  "themes": ["theme1", "theme2"],
  "character_focus": ["character name"],
  "cliffhanger": "string",
  "previously_on": "string"
}}"""


EPISODE_SCRIPT_PROMPT = """You are a cinematic screenwriter for serialized AI-generated video episodes.

Series: {series_title}
Genre: {genre}
Tone: {tone}
Narration style: {narration_style}
Episode number: {episode_number}
Episode concept: {episode_concept}
Opening hook: {opening_hook}
Key story beats: {key_beats}
Characters in this episode: {characters}
Visual style prefix: {visual_style}
Color grade: {color_grade}
Music mood: {music_mood}
Target length: 8-12 minutes spoken (~1400-1800 words)

CINEMATIC EPISODE STRUCTURE:
[00:00-00:20] COLD_OPEN — drop into action or atmosphere; use the opening hook verbatim
[00:20-02:00] ACT_1 — establish episode conflict, world, character stakes
[02:00-05:00] ACT_2 — escalation, complications, revelations, character development
[05:00-08:30] ACT_3 — tension peaks, stakes raised, character tested
[08:30-10:00] CLIMAX — central confrontation, revelation, or turning point
[10:00-10:45] DENOUEMENT — partial resolution; world is changed by what happened
[10:45-11:00] CLIFFHANGER — unresolved tension or revelation; tease next episode

MARKUP RULES:
- [PAUSE] for dramatic 0.5s silence
- [EMPHASIS] on key narrative words
- [BROLL: vivid scene description] at every visual change — be specific: lighting, atmosphere, camera position
- [SHOT: type] before major scenes — WIDE ESTABLISHING | CLOSE-UP | TRACKING | AERIAL | HANDHELD | STATIC
- [MOOD: emotion] for atmospheric score notes — TENSE | HOPEFUL | MELANCHOLIC | TRIUMPHANT | MYSTERIOUS
- Write for ears and cinematic imagination
- No affiliate content, no product mentions, no calls to subscribe
- Each broll cue must be a vivid, specific cinematic description the video model can visualize

Respond ONLY with valid JSON:
{{
  "episode_title": "string",
  "episode_number": {episode_number},
  "series_title": "{series_title}",
  "opening_hook": "string",
  "sections": [
    {{
      "timestamp_start": "00:00",
      "timestamp_end": "00:20",
      "label": "COLD_OPEN",
      "content": "spoken content with markup",
      "broll_cues": ["vivid cinematic scene description"],
      "shot_types": ["WIDE ESTABLISHING"],
      "characters_present": ["character name"],
      "mood": "mysterious"
    }}
  ],
  "full_text": "complete script as single string",
  "word_count": 0,
  "estimated_duration_minutes": 0.0,
  "narration_style": "{narration_style}",
  "cliffhanger": "string"
}}"""


VOICE_SPEC_PROMPT = """You are an audio director for cinematic serialized video episodes.

Genre: {genre}
Tone: {tone}
Narration style: {narration_style}
Episode length: {duration} minutes

Select the optimal narrator voice. Consider genre atmosphere, emotional depth, and narrative role.

Available OpenAI voices:
- alloy — neutral, balanced, American
- ash — clear, confident male, American
- coral — warm, engaging female, American
- echo — measured, calm male, American — good for thriller/drama
- fable — expressive, storytelling male, British — best for epic/fantasy
- nova — energetic, upbeat female, American
- onyx — deep, authoritative male, American — best for dark/tense content
- sage — wise, thoughtful, American — best for mystery/documentary
- shimmer — soft, friendly female, American

Use "tts-1-hd". Set speed 0.88-1.05 (slower for dramatic/dark content).

Respond ONLY with valid JSON:
{{
  "provider": "openai",
  "voice_id": "string",
  "voice_name": "string",
  "openai_model": "tts-1-hd",
  "speed": 1.0,
  "ffmpeg_loudness_lufs": -14.0,
  "ffmpeg_eq_preset": "youtube",
  "rationale": "string"
}}"""


COVER_ART_PROMPT = """You are a cinematic title card and episode cover art designer.

Series: {series_title}
Episode title: {episode_title}
Episode number: {episode_number}
Genre: {genre}
Visual style: {visual_style}
Opening hook: {hook}
Themes: {themes}

Generate 3 cover art concepts. Each should feel like a premium streaming series thumbnail or film poster.
Cinematic, atmospheric, emotionally evocative. No real human faces — use silhouettes, environments, symbols, abstraction.

Respond ONLY with valid JSON:
{{
  "concepts": [
    {{
      "concept_id": 1,
      "layout_description": "string",
      "focal_element": "string — no faces, use silhouettes/environments/symbols",
      "text_overlay": "max 5 words — episode title or key phrase",
      "accent_elements": ["element"],
      "color_mood": "string",
      "image_prompt": "detailed 150-200 word cinematic image generation prompt, photorealistic film still quality, anamorphic lens, no text, no logos, no watermarks, no human faces",
      "ctr_score": 0.0,
      "is_winner": false
    }}
  ]
}}

Set is_winner: true on the highest ctr_score concept only."""


EPISODE_METADATA_PROMPT = """You are a content strategist for a cinematic serialized video series.

Series: {series_title}
Episode title: {episode_title}
Episode number: {episode_number}
Genre: {genre}
Script summary: {script_summary}
Themes: {themes}
Cliffhanger: {cliffhanger}

Generate episode metadata for YouTube/streaming distribution.

Respond ONLY with valid JSON:
{{
  "title": "string (70 chars max — series name + Ep N + episode title)",
  "description": "string (200-300 words — atmospheric opening, episode synopsis, series context, no ads, no affiliate links)",
  "tags": ["tag1"],
  "chapters": ["00:00 Cold Open", "00:20 Act 1"],
  "synopsis": "string (2-3 sentences, TV Guide style, present tense)"
}}

Rules:
- title: include series name, episode number, episode title
- description: cinematic, builds anticipation, ends with series hook
- tags: 15 tags mixing genre, series name, themes, cinematic keywords
- chapters: match script section timestamps exactly"""
