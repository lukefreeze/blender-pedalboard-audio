# The Hijacker — Development Roadmap

---

## STATUS: Alpha

Core features are complete and working on Windows. UI polish is the final remaining step before v1.0 release. macOS and Linux builds exist and are pending real-world testing.

---

## COMPLETED ✅

### Mixdown Rack
- [x] Mix mode — all channels summed to one stereo file
- [x] Bake mode — each channel rendered individually, effects baked in
- [x] WAV 16/24/32-bit float, FLAC format support
- [x] 44.1 / 48 / 96kHz sample rate options
- [x] Custom timeline range
- [x] Auto-versioned output filenames
- [x] Background rendering — Blender stays responsive
- [x] Import modes — mute, replace, or keep active channels after render

### Core Engine
- [x] Custom C++ audio engine (`hijacker_engine`) — PortAudio real-time playback
- [x] pybind11 Python bindings — full engine control from Blender
- [x] Cross-platform GitHub Actions CI — Windows / macOS / Linux builds on every push
- [x] WAV reader — 16/24/32-bit PCM and IEEE float, multi-channel
- [x] True atomic multi-channel sync — all channels start from the exact same sample

### Mixing Desk
- [x] 9-channel GPU-drawn HUD in Blender's Node Editor
- [x] Per-channel fader with dB readout (linear multiplier → dB conversion)
- [x] Gain knob, stereo pan, mute, solo
- [x] Real-time VU meters with peak hold (19-segment LED style)
- [x] Send slots — per-channel rack assignment
- [x] Scrollable, scalable HUD — zoom and pan with mouse
- [x] PNG skin system — full visual reskinning via `skins/default/`
- [x] Table background image — studio desk aesthetic
- [x] Per-channel fader handle PNGs

### DSP Racks
- [x] Single-band compressor
- [x] 4-band multiband compressor
- [x] 7-band parametric EQ
- [x] Freeverb reverb
- [x] Noise gate (downward expander)
- [x] Stereo delay with ping-pong and LP filter
- [x] Presets system (◄ ► arrows) on all racks

### AI Racks
- [x] DeepFilterNet — AI noise reduction (ONNX, fully distributable)
- [x] Demucs — stem separation
- [x] KNNVC — voice conversion
- [x] Piper TTS — offline neural text-to-speech
- [x] Booster — loudness enhancement
- [x] Voicefixer — voice restoration
- [x] Whisper — speech-to-text / subtitle generation

### Bug Fixes
- [x] Forward seek during playback (frame delta threshold)
- [x] VU meter stale reference fix
- [x] Fader visual bottom pad with correct drag tracking
- [x] Send slot layout — rack positioning stable with 4+ racks
- [x] Mixer desk background anchored (no stretch on slot expansion)

---

## IN PROGRESS 🔧

### UI Polish — Final PNG Skins
The last major task before v1.0. All rack panels need final PNG background art to match the mixing desk aesthetic.

- [ ] Single-band compressor rack skin
- [ ] Multiband compressor rack skin
- [ ] Parametric EQ rack skin
- [ ] Reverb rack skin
- [ ] Noise gate rack skin
- [ ] Delay rack skin
- [ ] DeepFilterNet rack skin
- [ ] Demucs rack skin
- [ ] KNNVC rack skin
- [ ] Piper TTS rack skin
- [ ] Booster rack skin
- [ ] Voicefixer rack skin
- [ ] Whisper rack skin
- [ ] Add rack / Add AI rack button skins

---

## REMAINING BEFORE v1.0 📋

### Distribution & Packaging
- [ ] Universal addon zip — single zip containing all platform binaries, platform auto-detected at load time
- [ ] `__init__.py` for Blender addon registration
- [ ] GitHub Actions release job — builds zip on version tag push
- [ ] Static PortAudio linking on macOS (remove brew dependency for end users)

### Mixdown Rack ✅
Fully complete. Two render modes (Mix — all channels summed to stereo, Bake — each channel rendered individually with effects baked). Supports WAV 16/24/32-bit, FLAC, 44.1/48/96kHz. Custom timeline range, auto-versioned output filenames, background rendering so Blender stays responsive.

### Platform Testing
- [ ] macOS real-world testing in Blender 4.5
- [ ] Linux real-world testing in Blender 4.5
- [ ] PortAudio static linking on macOS for zero-dependency distribution

### Polish
- [ ] Undo support — `bpy.ops.ed.undo_push` after significant actions
- [ ] Save trigger — mark .blend dirty after changes
- [ ] Re-process handling — replace previous output strip cleanly on second PROCESS click

---

## FUTURE (Post v1.0) 🔮

### New DSP Racks
- [ ] De-esser — tame harsh sibilance on dialogue
- [ ] Harmonic exciter — adds studio warmth and presence
- [ ] Mid/Side processor

### New AI Racks
- [ ] RVC voice conversion — convert voice to a target character using .pth models
- [ ] Resemble Enhance — single-model broadcast quality restoration
- [ ] Matchering — AI master to a reference track

### Distribution
- [ ] In-addon model downloader — browse and download AI models from HuggingFace
- [ ] In-addon voice downloader for Piper — browse voices without leaving Blender
- [ ] Whisper model size selector with download-on-demand

### Platform
- [ ] macOS ARM native build (Apple Silicon)
- [ ] AI engine executables for macOS and Linux (currently Windows only)

---

## PRIORITY ORDER

1. 🔧 **UI polish** — final rack PNG skins (in progress)
2. 📋 **Mixdown rack** — required before any public release
3. 📋 **Universal zip packaging** — one zip, all platforms
4. 📋 **macOS / Linux testing**
5. 📋 **Undo + save trigger** — polish
6. 🔮 **De-esser + exciter** — post v1.0
7. 🔮 **RVC + Resemble Enhance** — post v1.0
8. 🔮 **Model downloader UI** — post v1.0
