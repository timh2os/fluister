# Fluister

**Hold a key, speak, release. Clean, punctuated text appears at your cursor, in any app.**

Fluister (Dutch for "whisper") is a free, local-first dictation tool for macOS. It does what Wispr Flow does: it doesn't just transcribe, it *edits*. Filler words disappear, self-corrections are applied ("Tuesday, no wait, Wednesday" becomes "Wednesday"), punctuation is added and the tone fits the app you are typing in. Speech recognition and the editing pass both run on your own Mac by default. No account, no subscription, no audio leaving your machine.

```
hold right Option  →  speak  →  release  →  text at your cursor
```

## Why another dictation tool?

Good open-source dictation apps exist ([Handy](https://github.com/cjpais/Handy), [VoiceInk](https://github.com/Beingpax/VoiceInk)), but most paste the raw transcript. What makes Wispr Flow feel magic is the second stage: a language model that turns spoken rambling into written text. Fluister makes that formatting pass the core of the product, with swappable backends for every stage.

## Features

- **Push-to-talk** on right Option (configurable), or the fn/globe key (experimental). Esc cancels.
- **Streaming speech recognition** with NVIDIA Parakeet on Apple Silicon. The text is nearly ready the moment you let go, with a live preview while you talk.
- **LLM formatting pass**: removes filler (um, uh, eh), applies self-corrections, punctuates, handles "new line" and "new paragraph". Works for Dutch and English, including when you mix them.
- **Per-app tone**: formal in Mail, casual in Slack and WhatsApp, verbatim identifiers in code editors.
- **Personal dictionary and replacements** for names and jargon it should always spell right.
- **Learns from your corrections**: if you keep retyping the same word after a dictation, Fluister suggests a replacement rule.
- **Language picker and script guard**: the menu bar has a Language submenu (Auto, Nederlands, English). Pinning a language uses Whisper, because Parakeet cannot be forced into one. In Auto mode, if Parakeet decodes a short clip as Cyrillic (a known quirk of the multilingual v3 model), the clip is re-transcribed with Whisper forced to Dutch and English. Needs `mlx-whisper` (installed by `install.sh` on Apple Silicon).
- **Silence and hallucination guard**: a Silero voice-activity check means an accidental tap pastes nothing, and classic "Thank you." hallucinations are dropped.
- **No clipped words**: an always-on mic keeps 300 ms of pre-roll and records 250 ms past release.
- **Pastes without touching your clipboard** where possible (Accessibility API insertion), with a clipboard fallback.
- **Remote desktop viewers**: list apps that show another machine (VNC viewers, screen sharing) under `injection.remote_apps` and Fluister pastes there with slower key timing and a chosen modifier (`remote_paste_modifier`: `cmd` or `ctrl`), instead of using the Accessibility API. Needs clipboard sync in the viewer.
- **Small touches**: start/stop sounds, other audio muted while you dictate, a trailing space, optional auto-Enter per app.
- **Menu bar app** with a small pill overlay at the bottom of the screen that never steals focus.
- **Private**: history is a local JSON file you can switch off.

On an M-series Mac, a typical dictation lands about 0.3 s of recognition plus about 1 s of formatting after you release the key.

## Install

Requirements: macOS on Apple Silicon (recommended), Python 3.10+, and [Ollama](https://ollama.com) for the free local formatter.

```bash
git clone https://github.com/LuckyG-one/fluister.git
cd fluister
./install.sh              # or: ./install.sh --autostart  to launch at login
.venv/bin/python -m fluister.main
```

The installer creates a virtual environment, installs the dependencies (plus `parakeet-mlx` on Apple Silicon), creates your personal `config.yaml` and pulls the `qwen3:4b-instruct` model through Ollama.

Then hold **right Option**, say something, and release. The first run downloads the speech model, so give it a moment.

### macOS permissions

Grant these under System Settings > Privacy & Security to the app you start Fluister from (Terminal, iTerm, your IDE, or Python when using `--autostart`):

| Permission | Why |
|---|---|
| Microphone | Recording your voice |
| Accessibility | Inserting text at the cursor |
| Input Monitoring | Listening for the push-to-talk key |

Restart Fluister after changing permissions.

## Configuration

Everything lives in `config.yaml` (created from [config.example.yaml](config.example.yaml), which documents every option). Use **Open config.yaml** in the menu bar, edit, and restart Fluister.

```yaml
hotkey:
  key: alt_r            # alt_r, alt_l, cmd_r, ctrl_r, shift_r, f8, f9

dictionary:             # always spell these exactly like this
  - Claude Code

replacements:           # hard fixes after formatting
  "Get Hub": "GitHub"

app_styles:
  Mail: "Formal email register, complete sentences."
  Slack: "Casual and short."

injection:
  auto_submit_apps: []  # e.g. [ChatGPT] to press Return after pasting
```

### Backends

Every stage is swappable. Local is the default; cloud is strictly opt-in.

| Stage | Option | Where | Cost | Notes |
|---|---|---|---|---|
| Speech | Parakeet TDT 0.6b v3 (`parakeet`) | local | free | Default on Apple Silicon, streaming, multilingual |
| Speech | Whisper large-v3-turbo (`mlx`) | local | free | Apple Silicon, slightly better on rare proper nouns |
| Speech | Whisper large-v3-turbo (`fasterwhisper`) | local | free | Cross-platform, CPU |
| Speech | Groq / OpenAI Whisper | cloud | ~$0.04 per audio hour | Needs an API key |
| Format | Ollama `qwen3:4b-instruct` | local | free | Default |
| Format | Any OpenAI-compatible API | cloud | pennies | Gemini Flash-Lite, Groq, OpenRouter, LM Studio, DeepSeek... |
| Format | `none` | | | Paste the raw transcript |

If the formatting step fails for any reason, the raw transcript is pasted instead, so a dictation is never lost.

## How it works

```
 hold key                    release
    |                           |
    v                           v
 hotkey.py ---> audio.py ---> asr.py ---> vad.py ---> llm.py ---> inject.py
 (pynput or     (16 kHz mono,  (Parakeet    (speech     (Ollama or   (AX insert,
  fn tap)        pre-roll)      streaming,   gate)       any OpenAI-  clipboard
                    |           Whisper)                 compatible)  fallback)
                    v                                       ^
                 overlay.py  (pill: waveform,          context: frontmost app,
                              live preview)            dictionary, app style
```

Everything runs in a single Python process: a Qt menu bar app with a frameless pill overlay. `corrections.py` watches the text field briefly after each paste to learn from your edits.

## Running at login

`./install.sh --autostart` installs a launch agent. To remove it:

```bash
launchctl bootout gui/$(id -u)/io.github.luckyg-one.fluister
rm ~/Library/LaunchAgents/io.github.luckyg-one.fluister.plist
```

Logs go to `~/Library/Logs/Fluister/fluister.log`.

## Known limitations

- macOS first. Windows and Linux should work in principle with the `fasterwhisper` backend, but are untested.
- With `audio.always_on` (the default) macOS shows the orange microphone indicator permanently. Set it to `false` to open the mic only while you hold the key.
- The fn/globe key mode is experimental and only works on Apple keyboards.
- When the clipboard fallback is used, clipboard managers may briefly see the dictated text.

## Roadmap

A settings window instead of editing YAML, snippets, a voice command mode ("delete the last sentence"), rewriting selected text by voice, and packaging as a proper `.app`.

## Credits

Fluister stands on the shoulders of [Handy](https://github.com/cjpais/Handy) (the VAD gate, pre-roll and feedback ideas), [VoiceInk](https://github.com/Beingpax/VoiceInk), [FreeFlow](https://github.com/zachlatta/freeflow) and FluidVoice, and on the models behind it: [NVIDIA Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) via [parakeet-mlx](https://github.com/senstella/parakeet-mlx), [Whisper](https://github.com/openai/whisper), [Silero VAD](https://github.com/snakers4/silero-vad) and [Qwen3](https://github.com/QwenLM/Qwen3). Inspired by [Wispr Flow](https://wisprflow.ai); not affiliated with it.

## License

[MIT](LICENSE)
