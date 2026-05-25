# Ralph — Usage Instructions

## What Ralph Does

Ralph is the AI storytelling director embedded in the RoboModal Studio dashboard. He operates on Groq `llama-3.3-70b-versatile` for fast, streaming responses.

Ralph helps you:
- **Develop series concepts** — genre, logline, world rules, character roster
- **Plan individual episodes** — beats, hooks, themes, cliffhangers
- **Solve story problems** — pacing issues, flat characters, unclear stakes
- **Choose pipeline settings** — model, voice, style, transition, music mood
- **Improve scripts** — punch up dialogue, sharpen broll cues, rewrite weak sections
- **Build character arcs** — wound/want/need framework, across-season development

## Example Prompts

```
"Help me plan a 5-episode noir thriller series. Protagonist is a corrupt detective."

"Episode 3 needs a cliffhanger that recontextualizes everything in episodes 1-2."

"My character Elena feels flat — she makes the same decisions every episode."

"Best model and voice for a melancholic sci-fi drama set on a generation ship?"

"Write 3 broll cues for a scene where Marcus discovers the vault is empty."

"Critique my hook: 'In the year 2089, everything changed.'"

"What's the visual language for gothic horror vs psychological horror?"
```

## How Ralph Connects to the Pipeline

When Ralph suggests pipeline settings, you can apply them directly in the Studio tab:
- **Model** → Video Generation Model selector
- **Voice** → Voice Override in Advanced Settings  
- **Style** → Cinematic Style Lock textarea
- **Narration** → Narration Style selector
- **Music mood** → Music Mood selector

When Ralph suggests story direction, paste it into **Story Prompt** in the Studio tab. The pipeline will incorporate it at the brief generation stage.

## Limitations

- Ralph doesn't execute pipeline runs — use the Studio tab for that
- Ralph doesn't have memory across browser sessions (conversation resets on page reload)
- Ralph's story suggestions are creative starting points — refine them in the script stage

## Customizing Ralph's System Prompt

Edit `prompts/ralph_masterprompt.md` and restart the API server. The `/chat` endpoint reads the system prompt from `api.py:_CHAT_SYSTEM` — update that string with your custom prompt or reference this file dynamically.
