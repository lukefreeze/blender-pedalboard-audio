#pragma once
#include <vector>
#include <string>

#define PB_MAX_CHANNELS  32
#define PB_MAX_EFFECTS    8
#define PB_MB_BANDS       4
#define PB_FFT_BINS      32   // FFT bins per band for spectrum display
#define PB_REVERB_COMB    8   // Freeverb comb filter count per channel
#define PB_REVERB_AP      4   // Freeverb allpass filter count per channel

enum class EffectType {
    NONE        = 0,
    GAIN        = 1,
    EQ_3BAND    = 2,   // legacy stub — kept so enum values don't shift
    COMP_SINGLE = 3,
    COMP_MULTI  = 4,
    REVERB      = 5,   // legacy stub slot — kept for enum stability
    EQ_PARAM    = 6,   // 7-band parametric EQ
    REVERB_PARAM= 7,   // Freeverb algorithmic reverb
};

// ---------------------------------------------------------------------------
// EffectSlot params layout:
//
// COMP_SINGLE (6 params used, 24 allocated):
//   p[0]=threshold_norm  p[1]=ratio_norm    p[2]=attack_norm
//   p[3]=release_norm    p[4]=makeup_norm   p[5]=knee_norm
//
// COMP_MULTI (24 params):
//   p[0..3] =threshold per band   p[4..7] =ratio per band
//   p[8..11]=attack per band      p[12..15]=release per band
//   p[16..19]=gain per band       p[20..23]=knee per band
//
// EQ_PARAM (21 params used, 24 allocated):
//   p[0..6]  = gain  norm 0-1  (0.5=0dB, ±24dB range)
//   p[7..13] = freq  log-norm  (20Hz–20kHz)
//   p[14..20]= Q     log-norm  (0.1–10.0)
//   Band order: L(low_shelf), 1-5(peak), H(high_shelf)
//
// REVERB_PARAM (5 params used, 24 allocated):
//   p[0] = room_size  0-1  (comb filter feedback, 0=dry 1=huge)
//   p[1] = damping    0-1  (HF absorption in comb filters)
//   p[2] = wet        0-1  (wet level)
//   p[3] = pre_delay  0-1  (maps to 0-100ms)
//   p[4] = width      0-1  (stereo spread, 0=mono 1=full stereo)
// ---------------------------------------------------------------------------
struct EffectSlot {
    EffectType type    = EffectType::NONE;
    bool       enabled = false;
    float      params[24] = {0};
};

// Per-channel per-band compressor envelope state
struct CompressorBandState {
    float envelope  = 0.0f;
    float gr_smooth = 0.0f;
};

struct CompressorChannelState {
    CompressorBandState single;
    CompressorBandState bands[PB_MB_BANDS];
    float lp_z[PB_MB_BANDS][2][2][2] = {};
    float hp_z[PB_MB_BANDS][2][2][2] = {};
};

// Per-band FFT state for spectrum display
struct FFTBandState {
    float window_buf[PB_FFT_BINS * 2] = {};
    int   write_pos = 0;
};

// Per-band biquad state for parametric EQ — Direct Form II Transposed
struct EqBandState {
    float z1[2] = {0.0f, 0.0f};  // [audio_channel]
    float z2[2] = {0.0f, 0.0f};
};

struct EqChannelState {
    EqBandState bands[7] = {};
};

// ---------------------------------------------------------------------------
// Freeverb reverb state — one per channel
// 8 comb filters + 4 allpass per stereo channel
// Each comb filter has a delay buffer, read/write index, and filter state
// ---------------------------------------------------------------------------
static const int COMB_LENGTHS_L[8] = {1116,1188,1277,1356,1422,1491,1557,1617};
static const int COMB_LENGTHS_R[8] = {1116+23,1188+23,1277+23,1356+23,
                                        1422+23,1491+23,1557+23,1617+23};
static const int AP_LENGTHS_L[4]   = {556, 441, 341, 225};
static const int AP_LENGTHS_R[4]   = {556+23, 441+23, 341+23, 225+23};
static const int MAX_COMB_LEN      = 1640 + 23 + 1;
static const int MAX_AP_LEN        = 579 + 1;
static const int MAX_PREDELAY_SAMP = 4411;   // 100ms @ 44100Hz

struct CombState {
    float buf[MAX_COMB_LEN] = {};
    int   pos    = 0;
    float filter = 0.0f;   // simple one-pole LPF state
};

struct AllpassState {
    float buf[MAX_AP_LEN] = {};
    int   pos = 0;
};

struct ReverbChannelState {
    CombState    comb[2][PB_REVERB_COMB]   = {};  // [ch][filter]
    AllpassState ap  [2][PB_REVERB_AP]     = {};
    float        predelay[2][MAX_PREDELAY_SAMP] = {};
    int          pd_write[2] = {0, 0};
    float        sample_rate = 44100.0f;
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

    // Real-time display data
    float band_levels[PB_MAX_CHANNELS][PB_MB_BANDS]              = {};
    float gr_levels  [PB_MAX_CHANNELS][PB_MB_BANDS]              = {};
    float fft_bins   [PB_MAX_CHANNELS][PB_MB_BANDS][PB_FFT_BINS] = {};

    // Effects rack
    EffectSlot effect_chain[PB_MAX_CHANNELS][PB_MAX_EFFECTS] = {};

    // Persistent DSP state
    CompressorChannelState comp_state  [PB_MAX_CHANNELS] = {};
    FFTBandState           fft_state   [PB_MAX_CHANNELS][PB_MB_BANDS] = {};
    EqChannelState         eq_state    [PB_MAX_CHANNELS] = {};
    ReverbChannelState     reverb_state[PB_MAX_CHANNELS] = {};

    // Legacy
    int   active_track_id = 1;
    float gains[4]    = {1.0f, 1.0f, 1.0f, 1.0f};
    float eq_high[4]  = {};
    float eq_mid[4]   = {};
    float eq_low[4]   = {};
};
