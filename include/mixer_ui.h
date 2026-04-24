#pragma once
#include <vector>
#include <string>

#define PB_MAX_CHANNELS 32
#define PB_MAX_EFFECTS   8
#define PB_MB_BANDS      4
#define PB_FFT_BINS     32   // FFT bins per band for spectrum display

enum class EffectType {
    NONE        = 0,
    GAIN        = 1,
    EQ_3BAND    = 2,   // legacy stub — kept so enum values don't shift
    COMP_SINGLE = 3,
    COMP_MULTI  = 4,
    REVERB      = 5,
    EQ_PARAM    = 6,   // 7-band parametric EQ (replaces EQ_3BAND in practice)
};

// ---------------------------------------------------------------------------
// EffectSlot params layout:
//
// COMP_SINGLE (24 params):
//   p[0]=threshold_norm  p[1]=ratio_norm    p[2]=attack_norm
//   p[3]=release_norm    p[4]=makeup_norm   p[5]=knee_norm
//
// COMP_MULTI (24 params):
//   p[0..3] =threshold per band   p[4..7] =ratio per band
//   p[8..11]=attack per band      p[12..15]=release per band
//   p[16..19]=gain per band       p[20..23]=knee per band
//
// EQ_PARAM (21 params, 3 unused):
//   p[0..6]  = gain  per band, norm 0-1  (0.5 = 0dB, range ±24dB)
//   p[7..13] = freq  per band, log-norm 0-1 (20Hz–20kHz)
//   p[14..20]= Q     per band, log-norm 0-1 (0.1–10.0)
//   Band order: L(low_shelf), 1-5(peak), H(high_shelf)
// ---------------------------------------------------------------------------
struct EffectSlot {
    EffectType type    = EffectType::NONE;
    bool       enabled = false;
    float      params[24] = {0};
};

// Per-band biquad state for parametric EQ — Direct Form II Transposed
// Two channels (stereo), two delay elements per channel
struct EqBandState {
    float z1[2] = {0.0f, 0.0f};   // [audio_channel]
    float z2[2] = {0.0f, 0.0f};
};

// Seven-band parametric EQ state per channel
struct EqChannelState {
    EqBandState bands[7] = {};
};

// Per-channel per-band compressor envelope state
struct CompressorBandState {
    float envelope  = 0.0f;
    float gr_smooth = 0.0f;
};

struct CompressorChannelState {
    CompressorBandState single;
    CompressorBandState bands[PB_MB_BANDS];
    // Linkwitz-Riley crossover biquad filter delays [band][stage][ch][z1/z2]
    float lp_z[PB_MB_BANDS][2][2][2] = {};
    float hp_z[PB_MB_BANDS][2][2][2] = {};
};

// Per-band FFT state for spectrum display
struct FFTBandState {
    float window_buf[PB_FFT_BINS * 2] = {};  // rolling input window
    int   write_pos = 0;
};

struct EngineState {
    // Playback sync
    int   current_frame = 0;
    bool  is_playing    = false;

    // Per-channel mixer state
    float volumes[PB_MAX_CHANNELS]      = {};
    float meter_levels[PB_MAX_CHANNELS] = {};
    bool  mutes[PB_MAX_CHANNELS]        = {};
    bool  solos[PB_MAX_CHANNELS]        = {};

    // Real-time display data (written by audio thread, read by Python)
    float band_levels[PB_MAX_CHANNELS][PB_MB_BANDS]              = {};
    float gr_levels  [PB_MAX_CHANNELS][PB_MB_BANDS]              = {};
    float fft_bins   [PB_MAX_CHANNELS][PB_MB_BANDS][PB_FFT_BINS] = {};

    // Effects rack
    EffectSlot effect_chain[PB_MAX_CHANNELS][PB_MAX_EFFECTS] = {};

    // Persistent DSP state
    CompressorChannelState comp_state[PB_MAX_CHANNELS] = {};
    FFTBandState           fft_state [PB_MAX_CHANNELS][PB_MB_BANDS] = {};
    EqChannelState         eq_state  [PB_MAX_CHANNELS] = {};

    // Legacy
    int   active_track_id = 1;
    float gains[4]    = {1.0f, 1.0f, 1.0f, 1.0f};
    float eq_high[4]  = {};
    float eq_mid[4]   = {};
    float eq_low[4]   = {};
};
