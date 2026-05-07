# Blender Pedalboard Audio — Development Roadmap

---

## BUGS / IMPROVEMENTS TO EXISTING FEATURES

### DeepFilterNet — Inference Quality
**Status: FIXED** — v2 runner with correct exponential normalisation deployed.
Old runner used static `(x+60)/40` normalisation. New runner uses frame-by-frame
exponential mean/unit normalisation (α=0.99, 1s decay) matching the official
libdf implementation. Rebuild exe with PyInstaller to deploy.

---

### DeepFilterNet — "My Audio Doesn't Sound Professional"
**The real ask:** Make crappy laptop mic audio sound like it was recorded in a
professional studio — not just noise-removed but actually broadcast quality.

**What's needed is a chain, not a single tool:**
1. **DeepFilterNet** (fixed) — removes background noise and room reverb
2. **EQ** — roll off low-end mud below 80Hz, presence boost around 3-5kHz
3. **Compressor** — tighten dynamic range (all these already exist as DSP racks)
4. **De-esser** — tame harsh sibilance (new DSP rack needed)
5. **Exciter / Harmonic enhancer** — adds the "studio warmth" (new DSP rack)
6. **Subtle room reverb** — makes it sound placed in a space rather than a dead box

**The practical fix now:** Document a preset chain — "Laptop Mic → Broadcast"
that chains DNF + existing EQ + Compressor presets in a specific order.

---

### Re-processing a Channel With Different Settings
**The issue:** After clicking PROCESS on the DNF rack, it places the output on a
new VSE channel. If you change knobs and click PROCESS again, it creates a second
output strip without removing the first.

**Fix needed:** Add a REPROCESS state — when a rack already has processed output,
the PROCESS button removes the previous output strip and replaces it cleanly.

---

### Undo Support
Blender's undo (Ctrl+Z) doesn't cover changes made inside our GPU modal operator.

**Fix:** Register `bpy.ops.ed.undo_push` after significant actions (rack add/delete,
channel assign, process complete). Minor actions like knob drags can be batched.

---

### Save Trigger
Changes inside the tool don't mark the .blend file as unsaved.

**Fix:** Set the scene dirty flag after any significant change so the title bar
shows the unsaved indicator and Blender prompts to save on exit.

---

## PYTORCH-DEPENDENT RACKS (Beta — Optional User Setup)

These racks require PyTorch installed in the **user's own system Python**.
Blender's Python is NEVER touched — no security warnings, no admin rights needed.
The rack calls the user's system Python as a subprocess, same as piper.exe.

**Why not freeze into an exe like DeepFilterNet:**
- PyTorch is ~2GB — too large to bundle
- These models can't be exported to ONNX (custom ops / buggy export)
- System Python subprocess is cleaner and more flexible

**User setup (one-time, done in their own terminal):**
```
pip install torch torchaudio
pip install rvc-python        # for RVC
pip install resemble-enhance  # for Resemble Enhance
```

**Detection logic:** The rack auto-detects system Python on load by running
`python -c "import torch"`. If found, rack works normally. If not found, the
rack body shows a clear setup guide with exact commands. Racks are marked BETA.

**Security:** Installing to system Python is standard practice — no different
from installing any other Python tool. No Blender internals are touched.

---

### RVC — Voice Conversion (BETA)
**What it does:** Takes recorded dialogue and converts it to sound like a completely
different voice — same words, same timing, different speaker character. This is
what powers most AI voice changers online.

**Use cases:** Change a voiceover to a different voice character, match a temp
recording to an unavailable actor, create consistent narration from multiple
takes recorded by different people.

**Distribution model:** System Python subprocess
- Detect system Python with torch + rvc-python
- Call rvc_runner.py via subprocess (not a frozen exe)
- Voice models (.pth files) in ai_engines/rvc/voices/
- Users download pre-trained voices from Hugging Face or train their own (~10min audio)

**Why not ONNX:** RVC's official ONNX export is buggy — the OBS plugin developer
who put serious effort into it concluded quality is worse than PyTorch and has
unresolved issues. PyTorch via system Python is the only reliable path.

**UI layout:**
- Rail: collapse, badge, RVC — VOICE CONVERSION, presets, BETA, ON/OFF, X
- Setup warning zone (when PyTorch not found): amber panel with pip commands
- Left panel: source channel selector + voice model cards (.pth files, scrollable)
- Centre: output waveform, CONVERT button, PREVIEW button
- Right panel: 3 knobs — PITCH (semitones), INDEX RATE (0-1), BREATHINESS (0-1)
- Bottom: full-width output waveform, status bar

---

### Resemble Enhance — Voice Restoration (BETA)
**What it does:** Single-model audio restoration — denoising + dereverberation +
enhancement + bandwidth extension in one pass. Specifically designed to make
low-quality recordings (laptop mic, phone, field recorder) sound broadcast-quality.
This is the closest thing to "laptop mic → professional studio" in one click.

**Distribution model:** System Python subprocess
- Detect system Python with torch + resemble-enhance
- Call resemble_runner.py via subprocess
- No additional model downloads — models auto-download on first use (~400MB, cached)

**Why not ONNX:** Resemble Enhance uses custom CUDA operations that cannot be
exported to ONNX. No workaround exists currently.

**UI layout:**
- Rail: collapse, badge, RESEMBLE ENHANCE, presets, BETA, ON/OFF, X
- Setup warning zone (when PyTorch not found): same amber panel pattern as RVC
- Left panel: source channel selector + before/after level meters
- Centre: output waveform comparison (original vs enhanced), ENHANCE button, PREVIEW
- Right panel: 2 knobs — DENOISE (0-1), ENHANCE (0-1)
- Bottom: full-width waveform, status bar showing enhancement stats

---

## NEW AI RACKS TO BUILD (ONNX / fully distributable)

### Whisper — Speech to Text / Subtitle Generation
**What it does:** Transcribes audio from a VSE channel into text, generates subtitle
strips in the VSE timeline automatically.

**Distribution:** ONNX + PyInstaller exe — fully distributable, zero user setup.

**How it works:** Whisper ONNX model runs as subprocess exe. Takes WAV input,
outputs timestamped JSON. Addon creates VSE text strips at correct frames.

**Models:** tiny/base/small/medium/large — base.en is the default for English.
Larger = more accurate, slower. Models downloaded on demand (~150MB for base).

**UI:** Source channel, model size picker, TRANSCRIBE button, subtitle preview,
export as .srt option.

---

### Demucs — Stem Separation
**What it does:** Splits a music track into vocals, drums, bass, other (guitars/keys).
Each stem placed on its own VSE channel for independent mixing and processing.

**Distribution:** Check ONNX feasibility — htdemucs may be exportable. If not,
falls back to system Python subprocess pattern (same as RVC/Resemble).

**Why it's useful:** Lower drums without touching vocals. Run DeepFilterNet only
on the vocal stem. Re-mix a music track for a film scene.

**UI:** Source channel, model picker (htdemucs = best), SEPARATE button.
4 new strips placed on consecutive channels above the original.

---

### Matchering — AI Master to Reference
**What it does:** Analyses a reference track and matches your mix's EQ curve,
stereo width, and loudness to it. Feed it a BBC Radio 4 clip as reference and
your podcast will be mastered to that standard.

**Distribution:** matchering Python library — check if ONNX/PyInstaller viable.
If not, system Python subprocess.

**UI:** Two channel selectors (your mix + reference track), MATCH button.

---

## MIXDOWN RACK — CRUCIAL FEATURE

**What it does:** Renders all active channels through their full processing chains
to a final mixed-down audio file, then places it back in the VSE.

**Why it's crucial:** Blender's video export needs real audio files in the VSE.
The mixdown bridges our live processing engine to Blender's export pipeline.

**Features needed:**
- Channel inclusion selector (tick which channels to include)
- Master volume + limiter on the output
- Format options: WAV 24-bit, WAV 32-bit float, FLAC, MP3
- Version control: auto-version filenames (mixdown_v01.wav, v02, etc.)
- Restore points: keep previous N versions for rollback
- Place on new channel vs replace existing
- Export folder option (relative to .blend file)
- Stems export: mixdown each channel separately + combined mix
- Confirmation step with prominent destructive action warning

---

## DISTRIBUTION & PACKAGING

*(Do after tool is feature-complete)*

1. Addon zip builder — script assembling distributable .zip excluding dev files
2. AI binary bundling — download-on-demand with setup wizard in addon preferences
3. Voice downloader UI — browse and download Piper voices from HuggingFace in-addon
4. Model downloader — same for DeepFilterNet ONNX models, Whisper models

### GitHub Actions CI:
- Trigger on release tag (v1.0.0 etc.)
- Build deepfilter_runner + whisper_runner for Windows x64, macOS ARM, macOS x64, Linux x64
- Bundle into release artifacts
- Users download the right platform binary

---

## UI OVERHAUL

*(Do last — after all features are built — VFX artist's domain)*

- PNG texture overlays for rack panels (knob backgrounds, VU meter bezels)
- Custom knob graphics instead of drawn circles
- Rack-specific themes (DNF = HAL 9000 red, Piper = soft blue, RVC = deep purple)
- Animated elements — VU meter needles, moving waveforms, LED strips
- Texture cache system (ui/mixer/texture_cache.py) already exists for this

---

## PRIORITY ORDER

1. ✅ Fix DeepFilterNet inference — DONE (v2 runner, rebuild exe)
2. **RVC rack** — UI + system Python detection + rvc_runner.py (IN PROGRESS)
3. **Resemble Enhance rack** — UI + system Python detection + resemble_runner.py
4. **Whisper** — ONNX, fully distributable, subtitle generation
5. **Demucs** — check ONNX feasibility first
6. **Mixdown rack** — crucial before any distribution
7. Undo + save trigger fixes — polish
8. De-esser + exciter DSP racks — polish
9. Matchering — nice to have
10. Distribution packaging + GitHub Actions — pre-release
11. UI overhaul — release
