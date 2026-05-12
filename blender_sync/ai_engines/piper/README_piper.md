# Piper TTS Engine

Offline text-to-speech using [Piper](https://github.com/rhasspy/piper).
The binary is pre-built and self-contained — no Python dependencies needed.

## Structure

```
ai_engines/piper/
  win_x64/
    piper.exe                      ← TTS binary (Windows)
    onnxruntime.dll                ← ONNX runtime
    onnxruntime_providers_shared.dll
    espeak-ng.dll                  ← phoneme engine
    piper_phonemize.dll
    libtashkeel_model.ort          ← Arabic tashkeel model
    espeak-ng-data/                ← phoneme dictionaries (~50MB)
  macos_arm/
    piper                          ← future: Apple Silicon build
  linux_x64/
    piper                          ← future: Linux build
  voices/
    en_US-lessac-medium.onnx       ← default voice model
    en_US-lessac-medium.onnx.json  ← voice config
    (drop additional .onnx + .json pairs here to add voices)
```

## Adding voices

1. Download any Piper voice from https://huggingface.co/rhasspy/piper-voices
2. Each voice is two files: `<name>.onnx` and `<name>.onnx.json`
3. Drop both into `ai_engines/piper/voices/`
4. The rack will auto-discover them on next Blender reload

## macOS / Linux

Piper pre-built binaries for macOS and Linux are available at
https://github.com/rhasspy/piper/releases — download and place the
binary + its dependencies in the appropriate platform subfolder.
The `voices/` folder is shared across all platforms.

## Usage (CLI reference)

```
piper.exe --model voices/en_US-lessac-medium.onnx \
          --output_file output.wav \
          --length_scale 1.0 \
          --noise_scale 0.667 \
          --noise_w 0.8 \
          < input.txt
```

- `--length_scale` controls speed (1.0 = normal, 0.5 = faster, 2.0 = slower)
- `--noise_scale` controls expressiveness/variation (0.0–1.0)
- `--noise_w` controls phoneme duration variation (0.0–1.0)
- Text is read from stdin, one utterance per line
