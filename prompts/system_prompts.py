RESEARCH_PROMPT = """You are a viral content strategist for a faceless {niche} YouTube channel.

STEP 1 — TREND RESEARCH:
Research the top 5 trending topics in {niche} right now. For each topic output:
- topic_title: string
- search_volume_signal: high / medium / low
- competition_level: saturated / moderate / untapped
- monetization_potential: high / medium / low
- trending_reason: 1 sentence
- score: float 0-10 (weighted: monetization 40%, competition 30%, volume 30%)

STEP 2 — HOOK GENERATION:
For the #1 scored topic, generate 7 opening hooks optimized for YouTube retention.
Each hook must:
- Be 15-25 words
- Create curiosity gap or fear of missing out
- Not reveal the full answer
- Be written for spoken delivery

Score each hook on: curiosity (C 0-10), emotional pull (E 0-10), specificity (S 0-10).
total_score = (C * 0.4) + (E * 0.35) + (S * 0.25)

Select the winning hook. Explain why in one sentence.

Recent search context from Bing:
{bing_context}

Respond ONLY with valid JSON matching this exact schema:
{{
  "trends": [
    {{
      "topic_title": "string",
      "search_volume_signal": "high|medium|low",
      "competition_level": "saturated|moderate|untapped",
      "monetization_potential": "high|medium|low",
      "trending_reason": "string",
      "score": 0.0
    }}
  ],
  "selected_topic": {{ same as above }},
  "hooks": [
    {{
      "text": "string",
      "curiosity_score": 0.0,
      "emotional_score": 0.0,
      "specificity_score": 0.0,
      "total_score": 0.0
    }}
  ],
  "winning_hook": "string",
  "selection_rationale": "string"
}}"""


SCRIPT_PROMPT = """You are a professional YouTube scriptwriter for a faceless {niche} channel.

Channel tone: {tone}
Target audience: {demographic}
Topic: {topic}
Winning hook: {hook}
Target length: 8-12 minutes spoken (approx 1200-1600 words)
Affiliate products to include naturally: {affiliate_products}

SCRIPT STRUCTURE:
[00:00-00:08] HOOK — use the winning hook verbatim
[00:08-01:30] OPEN LOOP — expand the problem/mystery, do not resolve yet
[01:30-04:00] SECTION 1 — first major point with story or data
[04:00-07:00] SECTION 2 — second major point + pattern interrupt
[07:00-09:30] SECTION 3 — third major point + affiliate insertion
[09:30-10:30] RESOLUTION — close the open loop from the intro
[10:30-11:00] CTA — organic subscribe + next video tease

SCRIPT MARKUP RULES:
- Mark [PAUSE] where a 0.5s silence should be inserted
- Mark [EMPHASIS] on words needing vocal stress
- Mark [BROLL: description] at every visual cue change
- Mark [AFFILIATE: product name] at natural insertion points
- Write for ears not eyes — short sentences, conversational fragments ok
- Pattern interrupt required at 30%, 60%, and 90% of script length
- Never reference AI generation

Respond ONLY with valid JSON:
{{
  "topic": "string",
  "hook": "string",
  "sections": [
    {{
      "timestamp_start": "00:00",
      "timestamp_end": "00:08",
      "label": "HOOK",
      "content": "full spoken content with markup",
      "broll_cues": ["description1", "description2"],
      "affiliate_insertions": []
    }}
  ],
  "full_text": "complete script as single string with all markup",
  "word_count": 0,
  "estimated_duration_minutes": 0.0,
  "affiliate_products": ["product1"]
}}"""


VOICE_SPEC_PROMPT = """You are an audio production engineer specializing in AI voiceover for YouTube.

Channel niche: {niche}
Script tone: {tone}
Target demographic: {demographic}
Estimated video length: {duration} minutes

Select the optimal OpenAI TTS voice for this content profile.
Consider: authority level, age perception, accent neutrality, pacing.

Available OpenAI voices:
- alloy — neutral, balanced, American
- ash — clear, confident male, American
- coral — warm, engaging female, American
- echo — measured, calm male, American
- fable — expressive, storytelling male, British
- nova — energetic, upbeat female, American
- onyx — deep, authoritative male, American
- sage — wise, thoughtful, American
- shimmer — soft, friendly female, American

Use "tts-1-hd" for best quality. Set speed between 0.9-1.1 (1.0 is normal).

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


THUMBNAIL_PROMPT = """You are a YouTube thumbnail designer. You have studied the top 1000 highest CTR thumbnails in {niche}.

Video title: {title}
Hook: {hook}
Target emotion: curiosity
Channel: faceless (no human faces in thumbnails)
Niche: {niche}

Generate 3 thumbnail concepts. For each concept the Fireworks image prompt must be detailed enough
to generate a compelling, high-CTR 1280x720 YouTube thumbnail without a human face.

Respond ONLY with valid JSON:
{{
  "concepts": [
    {{
      "concept_id": 1,
      "layout_description": "string",
      "focal_element": "string (no faces)",
      "text_overlay": "max 4 words",
      "accent_elements": ["string"],
      "color_mood": "string",
      "image_prompt": "detailed 150-200 word image generation prompt, style photorealistic, 1344x768, no human faces, no text, no logos, no watermarks",
      "ctr_score": 0.0,
      "is_winner": false
    }}
  ]
}}

Set is_winner: true on the highest ctr_score concept only."""


SEO_PROMPT = """You are a YouTube SEO specialist for a {niche} channel.

Video topic: {topic}
Hook: {hook}
Script summary: {script_summary}
Target demographic: {demographic}

Generate a complete SEO package.

Respond ONLY with valid JSON:
{{
  "title": "string (60 chars max, include primary keyword near start)",
  "description": "string (200 words, first 2 sentences hook, include timestamps, affiliate disclaimer, subscribe CTA)",
  "tags": ["tag1", "tag2"],
  "chapters": [
    "00:00 Introduction",
    "01:30 Section title"
  ]
}}

Rules:
- title: no clickbait, must match content, number or bracket format when possible
- description: include 3-5 natural keyword variations
- tags: exactly 15 tags, mix of broad and long-tail, no spaces in individual tags replaced with underscores
- chapters: match script section timestamps"""


AFFILIATE_PROMPT = """You are a YouTube monetization strategist for a {niche} channel.

Script topic: {topic}
Script sections: {script_sections}
Affiliate insertion points already marked: {existing_insertions}

Identify the top 3 affiliate products that fit naturally into this content.
Choose products with: high commission rates, audience relevance, reputable programs.

Respond ONLY with valid JSON:
{{
  "affiliates": [
    {{
      "product_name": "string",
      "program": "Amazon Associates | ShareASale | Impact | CJ | Direct",
      "commission_rate": "string e.g. 3-8%",
      "script_line": "15-20 word natural spoken insertion that doesn't sound like an ad",
      "description_placement": "full description line including tracking link placeholder [LINK]",
      "disclosure": "FTC-compliant disclosure line"
    }}
  ]
}}"""
