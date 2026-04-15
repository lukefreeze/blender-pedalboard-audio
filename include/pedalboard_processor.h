#pragma once
// ---------------------------------------------------------------------------
// pedalboard_processor.h
// Place in: C:\Users\lukeb\Documents\BlenderTool\include\
// ---------------------------------------------------------------------------

#include "mixer_ui.h"
#include <memory>

// Forward declarations
namespace aud { class ISound; class IReader; }
class PedalboardSound;

// ---------------------------------------------------------------------------
// ChannelHandle — owns the PedalboardSound for one channel.
// Python stores this as a capsule; releasing it stops processing.
// ---------------------------------------------------------------------------
struct ChannelHandle {
    PedalboardSound* sound_raw   = nullptr;  // owned
    int              channel_idx = -1;
    int              strip_channel = -1;     // VSE channel number (1-based)
};

extern "C" {
    // Create a PedalboardSound wrapping an aud::ISound*.
    // sound_ptr: raw aud::ISound* cast to void*
    // Returns ChannelHandle* as void*, or nullptr on failure.
    void* create_channel(void* sound_ptr, int channel_idx, int strip_channel);

    // Get the aud::ISound* from a ChannelHandle so Python can
    // pass it to aud.Device.play() or reconstruct an aud.Sound.
    void* get_channel_sound(void* handle);

    // Release a ChannelHandle. Safe with nullptr.
    void  release_channel(void* handle);
}
