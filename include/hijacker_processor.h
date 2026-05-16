#pragma once
// ---------------------------------------------------------------------------
// hijacker_processor.h
// The Hijacker — DSP effect chain processor
// Place in: C:\Users\lukeb\Documents\BlenderTool\include\
// ---------------------------------------------------------------------------

#include "mixer_ui.h"
#include <memory>

// Forward declarations
namespace aud { class ISound; class IReader; typedef float sample_t; }
class HijackerSound;

// ---------------------------------------------------------------------------
// ChannelHandle — owns the HijackerSound for one channel.
// ---------------------------------------------------------------------------
struct ChannelHandle {
    HijackerSound* sound_raw    = nullptr;
    int            channel_idx  = -1;
    int            strip_channel = -1;
};

// ---------------------------------------------------------------------------
// Batch DSP — called from wrapper.cpp's process_buffer() and from
// hijacker_audio_engine.cpp's audio callback for real-time processing.
// Applies the full effect chain for channel_idx to an interleaved
// float buffer of (frames * n_channels) samples in-place.
// Also updates fft_bins and gr_levels in g_state.
// ---------------------------------------------------------------------------
void apply_effect_chain_batch(int channel_idx, aud::sample_t* buf,
                               int frames, int n_channels, float sample_rate);

extern "C" {
    void* create_channel(void* sound_ptr, int channel_idx, int strip_channel);
    void* get_channel_sound(void* handle);
    void  release_channel(void* handle);
}
