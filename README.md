# Blender Pedalboard Audio

A professional audio mixing and processing suite for Blender's Video Sequence Editor, built on a custom C++ audio engine that operates independently of Blender's native `aud` module and Python's GIL limitations.

![Blender 4.5](https://img.shields.io/badge/Blender-4.5-orange) ![Platform Windows](https://img.shields.io/badge/Platform-Windows-blue) ![Python 3.11](https://img.shields.io/badge/Python-3.11-green) ![License MIT](https://img.shields.io/badge/License-MIT-yellow)

---

## What This Is

Blender's built-in audio tools are limited to basic volume and pan — there's no per-channel DSP, no real-time processing, and no way to route audio through effects chains. This addon replaces Blender's audio playback entirely with a custom engine that gives you:

- **Per-channel audio routing** with independent faders, gain, pan, mute, and solo
- **Real-time DSP effect racks** on every channel — compressor, EQ, reverb, noise gate, delay
- **AI processing racks** that run offline, background-threaded inference without blocking Blender
- **A GPU-drawn HUD** that lives in Blender's Node Editor — a full mixing desk rendered entirely with `gpu` primitives and `gpu_extras.batch`

The engine is a compiled C++ `.pyd` extension (`pedalboard_engine`) that handles all audio I/O, mixing, and DSP natively — bypassing both Python's GIL and Blender's `aud` limitations entirely.

---

## Features

### Mixing Desk
- Full-width fader strips for every VSE audio channel
- Per-channel: volume fader, gain knob, stereo pan, mute, solo
- Real-time VU meters with peak hold
- FFT waveform timeline display
- Scrollable, scalable HUD — fits any screen size

### DSP Racks (real-time, per-channel)
| Rack | Controls |
|------|----------|
| **Compressor** (single-band) | Threshold, Ratio, Attack, Release, Makeup, Knee |
| **Compressor** (multiband) | 3-band with independent controls |
| **7-Band EQ** | Low shelf, 5× parametric, high shelf |
| **Reverb** | Freeverb algorithm — Room, Damping, Width, Wet |
| **Noise Gate** | Threshold, Attack, Release, Hold |
| **Delay** | Time, Feedback, Mix, Sync-to-BPM |

All DSP racks have 6–10 presets accessible via ◄ ► arrows in the rack rail.

### AI Racks (offline, background-threaded)
AI racks run as subprocess executables — fully self-contained, no Python packages required on the user's machine.

#### DeepFilterNet — AI Noise Reduction
- Removes background noise, HVAC, wind, hum from dialogue and field recordings
- Uses DeepFilterNet3 ONNX inference (enc/erb_dec/df_dec models)
- Processes in background thread — Blender stays fully responsive
- Places cleaned audio on the next free VSE channel; ON/OFF toggles A/B comparison
- 3 knobs: Attenuation, Sensitivity, Post Gain
- 10 presets: Dialogue Clean → Heavy Denoise → Bypass

#### Piper TTS — Text to Speech
- Offline neural TTS — no internet required, no API keys
- In-rack text editor with cursor, text selection (Shift+arrows, Ctrl+A), word wrap
- Scrollable voice selector — auto-discovers any `.onnx` voice model in `ai_engines/piper/voices/`
- ▶ PREVIEW: instant audition via `aud.Device()` without touching the VSE
- GENERATE: places output strip at the playhead on the target channel
- 3 knobs: Speed (0.5–2×), Noise (expressiveness), Noise W (duration variation)
- 10 presets: Narration → Audiobook → Whisper → Character

---

## Architecture

```
blender_sync/
  Loader.py                     ← Blender registration, sys.path bootstrap
  Racks.py                      ← All rack data, draw, hit-test, presets
  core/
    engine.py                   ← C++ .pyd import and lifecycle
    audio.py                    ← DSP chain, playback handlers, effect routing
    meters.py                   ← VU meter timer, envelope cache
    properties.py               ← Blender PropertyGroups
    constants.py                ← All layout numbers
    ai_deepfilternet.py         ← DeepFilterNet background processing
    ai_piper.py                 ← Piper TTS background processing
  ui/
    mixer/
      mixer_hud.py              ← draw_callback_px, scrollbars, UI state
      interaction.py            ← Modal operator, hit testing, keyboard input
      channel_strip.py          ← One fader strip
      draw_utils.py             ← GPU primitives
      texture_cache.py          ← PNG → gpu.texture loader
    racks/
      rack_base.py              ← Shared rack drawing utilities
      rack_comp.py              ← Compressor rack
      rack_eq.py                ← EQ rack
      rack_reverb.py            ← Reverb rack
      rack_noisegate.py         ← Noise gate rack
      rack_delay.py             ← Delay rack
      rack_deepfilternet.py     ← DeepFilterNet rack UI (HAL 9000 eye)
      rack_piper.py             ← Piper TTS rack UI
  ai_engines/
    deepfilternet/
      deepfilter_runner.py      ← Standalone ONNX runner (PyInstaller → .exe)
      win_x64/                  ← Pre-built Windows executable (not in git)
      models/                   ← ONNX model files (not in git)
    piper/
      voices/                   ← Voice .onnx models (not in git) + .onnx.json configs
      win_x64/                  ← Piper binary + espeak-ng-data (not in git)
```

---

## Setup

### Requirements
- Blender 4.2+ (tested on 4.5)
- Windows x64 (macOS/Linux support planned)

### Installation
1. Clone or download the repo
2. Copy `blender_sync/` into your Blender addons folder or point Blender at it
3. Copy AI engine binaries and models (see below — not included in git due to size)
4. Enable the addon in Blender Preferences → Add-ons

### AI Engine Setup

**DeepFilterNet:**
- Copy `enc.onnx`, `erb_dec.onnx`, `df_dec.onnx`, `config.ini` into `ai_engines/deepfilternet/models/`
- Build or download `deepfilter_runner.exe` into `ai_engines/deepfilternet/win_x64/`
- See `ai_engines/deepfilternet/BUILD.txt` for build instructions

**Piper TTS:**
- Download Piper for Windows from https://github.com/rhasspy/piper/releases and place contents in `ai_engines/piper/win_64/`
- Download voice models from https://huggingface.co/rhasspy/piper-voices and place `.onnx` + `.onnx.json` pairs in `ai_engines/piper/voices/`
- The addon auto-discovers all voices in that folder

---

## Roadmap

- [ ] Whisper — speech-to-text, generate subtitles from a channel
- [ ] Demucs — stem separation (vocals / drums / bass / other)
- [ ] Matchering — AI mastering against a reference track
- [ ] macOS ARM + Linux builds of AI engine executables
- [ ] Voice downloader UI in addon preferences
- [ ] GitHub Actions CI for cross-platform builds on release tags

---

## Branch

Active development is on `refactor/split-into-multiple-python-files`. The `main` branch contains early prototypes and is not representative of the current state.
