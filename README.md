# Blender Pedalboard Audio Integration

A specialized tool to inject high-performance audio processing into the Blender VSE using a C++ wrapper of Spotify's Pedalboard library.

## Features
- **Real-time Processing:** Audio effects processing within the Blender timeline.
- **Custom UI:** Embedded controls inside the VSE properties panel.
- **High Performance:** C++ backend to handle heavy DSP tasks without lagging the Blender UI.

## Tech Stack
- **Python:** Blender Addon API & UI.
- **C++:** Pedalboard wrapper.
- **Git:** Version control and documentation.

- Build Environment: Python 3.11.9 (Standardized for Blender 4.5 compatibility)
- 
## Future Roadmap (Phase 2.0)
- **AI Audio Restoration:** Integrate `DeepFilterNet` for real-time noise suppression.
- **Voice Enhancement:** Explore `Resemble AI` for high-fidelity speech synthesis and cleanup within the VSE.
