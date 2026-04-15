#pragma once
#include <vector>
#include <string>

// Maximum channels the engine handles
#define PB_MAX_CHANNELS 32

// Maximum effects slots per channel in the rack
#define PB_MAX_EFFECTS  8

// ---------------------------------------------------------------------------
// EffectType — every effect the rack supports.
// Add new entries here as you build new processors.
// ---------------------------------------------------------------------------
enum class EffectType {
    NONE        = 0,
    GAIN        = 1,   // simple input gain (always first in chain)
    EQ_3BAND    = 2,   // high/mid/low shelf EQ
    COMPRESSOR  = 3,   // multiband compressor (future)
    REVERB      = 4,   // reverb (future)
};

// ---------------------------------------------------------------------------
// EffectSlot — one slot in a channel's effect chain.
// ---------------------------------------------------------------------------
struct EffectSlot {
    EffectType type    = EffectType::NONE;
    bool       enabled = false;

    // Parameters — extend as you add effects.
    // Using a flat float array keeps pybind11 bindings simple.
    float params[8]    = {0};
    // Param layout by effect type:
    //   GAIN:     params[0] = gain (0.0 - 2.0)
    //   EQ_3BAND: params[0] = high_db, params[1] = mid_db, params[2] = low_db
    //   COMPRESSOR: params[0..7] TBD
    //   REVERB:     params[0..7] TBD
};

// ---------------------------------------------------------------------------
// EngineState — shared between C++ audio thread and Python UI thread.
// Written by Python (fader moves, button presses) and read by
// PedalboardReader on the audio thread.
// meter_levels[] is written by PedalboardReader and read by Python timer.
// ---------------------------------------------------------------------------
struct EngineState {
    // --- Playback sync ---
    int   current_frame = 0;
    bool  is_playing    = false;

    // --- Per-channel mixer state ---
    float volumes[PB_MAX_CHANNELS]      = {};   // fader 0.0-1.0
    float meter_levels[PB_MAX_CHANNELS] = {};   // peak level written by audio thread
    bool  mutes[PB_MAX_CHANNELS]        = {};
    bool  solos[PB_MAX_CHANNELS]        = {};

    // --- Per-channel effects rack ---
    // effect_chain[ch][slot] — evaluated in slot order during read()
    EffectSlot effect_chain[PB_MAX_CHANNELS][PB_MAX_EFFECTS] = {};

    // --- Legacy fields kept for UI compatibility ---
    int   active_track_id = 1;
    float gains[4]    = {1.0f, 1.0f, 1.0f, 1.0f};
    float eq_high[4]  = {};
    float eq_mid[4]   = {};
    float eq_low[4]   = {};
};
