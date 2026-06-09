# The Hijacker — Blender Audio Addon

A professional audio mixing and processing suite for Blender's Video Sequence Editor, powered by a custom C++ audio engine that runs entirely independently of Blender's native audio system.

![Blender 4.5](https://img.shields.io/badge/Blender-4.5-orange) ![Platform Windows](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-blue) ![Python 3.11](https://img.shields.io/badge/Python-3.11-green) ![Status Alpha](https://img.shields.io/badge/Status-Alpha-red) ![License MIT](https://img.shields.io/badge/License-MIT-yellow)

> **Alpha:** Core features are complete and working. UI polish is the final remaining step before release.

---

## What Is The Hijacker?

Blender's built-in audio tools are limited to basic volume and pan — there's no per-channel DSP, no real-time processing, and no way to route audio through effects chains. The Hijacker replaces Blender's audio playback entirely with a custom C++ engine that gives you a full mixing desk and AI processing suite directly inside Blender.

The engine (`hijacker_engine.pyd` / `.so`) is a compiled C++ extension handling all audio I/O, mixing, and DSP natively — bypassing Python's GIL and Blender's `aud` limitations entirely. The UI is a GPU-drawn HUD living in Blender's Node Editor, rendered with Blender's `gpu` module — no external GUI frameworks, no ImGui, no native windows.

---

## Features

### Mixing Desk
- Full 9-channel mixing desk rendered directly in Blender's Node Editor
- Per-channel: volume fader (with dB readout), gain knob, stereo pan, mute, solo
- Real-time VU meters with peak hold
- Scrollable, scalable HUD — fits any screen size and zoom level
- PNG skin system — full visual reskinning via `ui/assets/skins/default/`
- Table background aesthetic — the desk sits on a virtual studio table surface

### DSP Racks (real-time, per-channel)
| Rack | Controls |
|------|----------|
| **Compressor** (single-band) | Threshold, Ratio, Attack, Release, Makeup, Knee |
| **Compressor** (multiband) | 4-band with independent controls per band |
| **7-Band Parametric EQ** | Low shelf, 5× parametric, high shelf |
| **Reverb** | Freeverb algorithm — Room, Damping, Width, Wet, Pre-delay |
| **Noise Gate** | Threshold, Attack, Release, Hold, Range |
| **Stereo Delay** | Time, Feedback, Mix, Ping-pong, LP filter |

All DSP racks support presets accessible via ◄ ► arrows in the rack rail.

### AI Racks (offline, background-threaded)
All AI racks run as background subprocesses — Blender stays fully responsive during processing.

| Rack | What It Does |
|------|-------------|
| **DeepFilterNet** | AI noise reduction — removes background noise, HVAC, wind, hum |
| **Demucs** | Stem separation — splits a track into vocals, drums, bass, other |
| **KNNVC** | Voice conversion — converts recorded dialogue to a different voice |
| **Piper TTS** | Offline neural text-to-speech — no internet, no API keys |
| **Booster** | AI loudness enhancement and harmonic exciter |
| **Voicefixer** | Voice restoration — repairs degraded, clipped or low-quality recordings |
| **Whisper** | Speech-to-text — transcribes audio and generates VSE subtitle strips |

---

## Architecture

```
blender_sync/
  Loader.py                     ← Blender registration, sys.path bootstrap
  Racks.py                      ← All rack data, draw, hit-test, presets
  hijacker_engine.pyd           ← Compiled C++ engine (Windows)
  hijacker_engine.so            ← Compiled C++ engine (macOS / Linux)
  core/
    engine.py                   ← C++ extension import and lifecycle
    audio.py                    ← Playback handlers, seek, frame sync
    meters.py                   ← VU meter timer, peak hold, envelope cache
    properties.py               ← Blender PropertyGroups
    constants.py                ← All layout constants
    ai_*.py                     ← Per-AI-rack background processing modules
  ui/
    mixer/
      mixer_hud.py              ← draw_callback_px, scrollbars, UI state globals
      interaction.py            ← Modal operator, hit testing, keyboard input
      channel_strip.py          ← One fader strip — all section draw logic
      draw_utils.py             ← GPU primitives, LED meter, numbox
      texture_cache.py          ← PNG → gpu.texture loader, SKIN_MAP
    assets/
      skins/default/            ← Active skin PNGs
      skins/working/            ← Development / source art (not distributed)
    racks/
      rack_base.py              ← Shared rack drawing utilities
      rack_comp.py              ← Compressor rack
      rack_eq.py                ← EQ rack
      rack_reverb.py            ← Reverb rack
      rack_noisegate.py         ← Noise gate rack
      rack_delay.py             ← Delay rack
      rack_deepfilternet.py     ← DeepFilterNet rack UI
      rack_demucs.py            ← Demucs rack UI
      rack_knnvc.py             ← KNNVC voice conversion rack UI
      rack_piper.py             ← Piper TTS rack UI
      rack_booster.py           ← Booster rack UI
      rack_voicefixer.py        ← Voicefixer rack UI
      rack_whisper.py           ← Whisper rack UI
  ai_engines/
    deepfilternet/              ← DeepFilterNet ONNX runner
    demucs/                     ← Demucs runner
    knnvc/                      ← KNNVC runner + voice models
    piper/                      ← Piper binary + espeak-ng-data + voices
    booster/                    ← Booster runner
    voicefixer/                 ← Voicefixer runner
    whisper/                    ← Whisper runner
src/                            ← C++ engine source
  hijacker_audio_engine.cpp     ← PortAudio real-time engine, WAV reader
  hijacker_processor.cpp        ← DSP effect chain (EQ, comp, reverb, gate, delay)
  wrapper.cpp                   ← pybind11 Python bindings
  mixer_ui.cpp                  ← Stub (UI is GPU-drawn in Python)
  imgui/                        ← ImGui (retained for build compatibility only)
include/                        ← C++ headers
build.bat                       ← Windows build script (MSVC + vcpkg PortAudio)
.github/workflows/build.yml     ← GitHub Actions cross-platform CI builds
```

---

## Installation

### Requirements
- Blender 4.5
- Windows x64 (macOS and Linux builds available — testing in progress)

### Install from release
1. Download the latest release zip from the [Releases](../../releases) page
2. In Blender: `Edit → Preferences → Add-ons → Install`
3. Select the zip and enable **The Hijacker**
4. Open a Node Editor area and click **The Hijacker** button in the header

### Install from source
1. Clone the repo
2. In Blender: `Edit → Preferences → Add-ons → Install`
3. Point Blender at `blender_sync/Loader.py`
4. Enable **The Hijacker**

---

## Building the C++ Engine

The engine is pre-built for all platforms via GitHub Actions. If you need to build locally on Windows:

```bat
build.bat
```

Requires MSVC, Python 3.11 headers, pybind11, and PortAudio static lib. See `build.bat` for full dependency list.

Cross-platform builds (Windows / macOS / Linux) run automatically on every push to `feature/hijacker-engine` via `.github/workflows/build.yml`. Download artifacts from the [Actions](../../actions) tab.

---

## Platform Support

| Platform | Engine Build | Tested in Blender |
|----------|-------------|-------------------|
| Windows x64 | ✅ | ✅ |
| macOS | ✅ | 🔜 Testing soon |
| Linux x64 | ✅ | 🔜 Testing soon |

---

## Development Branch

Active development is on `feature/hijacker-engine`. The `main` branch contains early prototypes and is not representative of the current state.

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full development plan.

**Remaining before v1.0:**
- [ ] UI polish pass — final PNG skins for all rack panels
- [ ] Universal addon zip packaging with platform auto-detection
- [ ] Mixdown rack — render all channels to a final audio file
- [ ] Undo support and save trigger
- [ ] macOS and Linux real-world testing

---

## License

MIT
