// ---------------------------------------------------------------------------
// pedalboard_processor.cpp
// Place in: C:\Users\lukeb\Documents\BlenderTool\src\
// ---------------------------------------------------------------------------
#include "pedalboard_processor.h"
#include "mixer_ui.h"
#include "ISound.h"
#include "IReader.h"

#include <cmath>
#include <algorithm>
#include <memory>
#include <cstring>
#include <cstdio>

extern "C" { extern EngineState g_state; }

static const float PI = 3.14159265358979f;

// ===========================================================================
// Param denormalisation
// ===========================================================================
static inline float denorm_threshold(float n) { return -40.0f + n * 40.0f; }
static inline float denorm_ratio    (float n) { return  1.0f  + n * 19.0f; }
static inline float denorm_attack   (float n) { return  0.1f  + n * 99.9f; }
static inline float denorm_release  (float n) { return 10.0f  + n * 990.0f; }
static inline float denorm_makeup   (float n) { return  n * 24.0f; }
static inline float denorm_gain     (float n) { return (n - 0.5f) * 24.0f; }
static inline float denorm_knee     (float n) { return  0.5f + n * 23.5f; }

static inline float time_coeff(float ms, float sr)
{
    if (ms <= 0.0f) return 0.0f;
    return std::exp(-1.0f / (sr * ms * 0.001f));
}

static inline float db2lin(float db) { return std::pow(10.0f, db / 20.0f); }
static inline float lin2db(float lin) { return 20.0f * std::log10(lin + 1e-9f); }

// ===========================================================================
// Soft-knee gain computer
// ===========================================================================
static float soft_knee_gr(float level_lin, float thr_db,
                           float ratio, float knee_db)
{
    if (level_lin <= 0.0f) return 1.0f;
    float in_db  = lin2db(level_lin);
    float half_k = knee_db * 0.5f;

    float out_db;
    if (in_db <= thr_db - half_k) {
        out_db = in_db;
    } else if (in_db <= thr_db + half_k && knee_db > 0.0f) {
        float x  = in_db - thr_db + half_k;
        out_db   = in_db + (1.0f/ratio - 1.0f) * (x * x) / (2.0f * knee_db);
    } else {
        out_db = thr_db + (in_db - thr_db) / ratio;
    }

    return db2lin(out_db - in_db);
}

// ===========================================================================
// Single-band compressor
// ===========================================================================
static void apply_comp_single(int ch, aud::sample_t* buf,
                               int frames, int n_ch,
                               const EffectSlot& fx,
                               float sr)
{
    float thr_db  = denorm_threshold(fx.params[0]);
    float ratio   = denorm_ratio    (fx.params[1]);
    float atk_ms  = denorm_attack   (fx.params[2]);
    float rel_ms  = denorm_release  (fx.params[3]);
    float mkp_lin = db2lin(denorm_makeup(fx.params[4]));
    float knee_db = denorm_knee     (fx.params[5]);

    float atk_c = time_coeff(atk_ms, sr);
    float rel_c = time_coeff(rel_ms, sr);

    CompressorBandState& st = g_state.comp_state[ch].single;
    float max_gr_db = 0.0f;

    for (int f = 0; f < frames; ++f) {
        float peak = 0.0f;
        for (int c = 0; c < n_ch; ++c)
            peak = std::max(peak, std::abs(buf[f * n_ch + c]));

        float coeff = (peak > st.envelope) ? atk_c : rel_c;
        st.envelope = coeff * st.envelope + (1.0f - coeff) * peak;

        float gr    = soft_knee_gr(st.envelope, thr_db, ratio, knee_db);
        float gr_db = (gr < 1.0f) ? lin2db(gr) : 0.0f;
        if (gr_db < max_gr_db) max_gr_db = gr_db;

        for (int c = 0; c < n_ch; ++c)
            buf[f * n_ch + c] *= gr * mkp_lin;
    }

    const float sm = 0.85f;
    g_state.gr_levels[ch][0] = sm * g_state.gr_levels[ch][0]
                              + (1.0f - sm) * (-max_gr_db);

    float rms = 0.0f;
    for (int f = 0; f < frames; ++f)
        for (int c = 0; c < n_ch; ++c) {
            float s = buf[f * n_ch + c]; rms += s * s;
        }
    rms = std::sqrt(rms / (frames * n_ch + 1e-9f));
    g_state.band_levels[ch][0] = sm * g_state.band_levels[ch][0]
                                + (1.0f - sm) * rms;
}

// ===========================================================================
// Biquad filter
// ===========================================================================
struct BiquadCoeffs { float b0,b1,b2,a1,a2; };

static BiquadCoeffs butter_lp(float freq, float sr) {
    float w = 2.0f * PI * freq / sr;
    float cw = std::cos(w), sw = std::sin(w);
    float alpha = sw / (2.0f * 0.7071068f), a0 = 1.0f + alpha;
    BiquadCoeffs c;
    c.b0=(1.0f-cw)*0.5f/a0; c.b1=(1.0f-cw)/a0; c.b2=c.b0;
    c.a1=-2.0f*cw/a0; c.a2=(1.0f-alpha)/a0;
    return c;
}

static BiquadCoeffs butter_hp(float freq, float sr) {
    float w = 2.0f * PI * freq / sr;
    float cw = std::cos(w), sw = std::sin(w);
    float alpha = sw / (2.0f * 0.7071068f), a0 = 1.0f + alpha;
    BiquadCoeffs c;
    c.b0=(1.0f+cw)*0.5f/a0; c.b1=-(1.0f+cw)/a0; c.b2=c.b0;
    c.a1=-2.0f*cw/a0; c.a2=(1.0f-alpha)/a0;
    return c;
}

static inline float biquad_step(float x, const BiquadCoeffs& co,
                                 float& z1, float& z2)
{
    float w = x - co.a1*z1 - co.a2*z2;
    float y = co.b0*w + co.b1*z1 + co.b2*z2;
    z2 = z1; z1 = w;
    return y;
}

// ---------------------------------------------------------------------------
// lr_filter: two cascaded biquads using a flat 4-float state array
// state[0]=stage0_z1, state[1]=stage0_z2, state[2]=stage1_z1, state[3]=stage1_z2
// ---------------------------------------------------------------------------
static void lr_filter(float* buf, int n, const BiquadCoeffs& co, float* state)
{
    for (int f = 0; f < n; ++f) {
        float s = buf[f];
        s = biquad_step(s, co, state[0], state[1]);
        s = biquad_step(s, co, state[2], state[3]);
        buf[f] = s;
    }
}

// ===========================================================================
// Parametric EQ biquad coefficient builders
// All use the Audio EQ Cookbook formulas (Robert Bristow-Johnson).
// Inputs: gain_db, freq_hz, Q, sample_rate
// ===========================================================================
static BiquadCoeffs eq_low_shelf(float gain_db, float freq, float Q, float sr)
{
    float A  = std::sqrt(db2lin(gain_db));           // linear amplitude
    float w0 = 2.0f * PI * freq / sr;
    float cw = std::cos(w0), sw = std::sin(w0);
    float alpha = sw / (2.0f * Q);
    float sqA   = std::sqrt(A);

    float b0 =  A * ((A+1) - (A-1)*cw + 2*sqA*alpha);
    float b1 =  2*A * ((A-1) - (A+1)*cw);
    float b2 =  A * ((A+1) - (A-1)*cw - 2*sqA*alpha);
    float a0 =      (A+1) + (A-1)*cw + 2*sqA*alpha;
    float a1 = -2 * ((A-1) + (A+1)*cw);
    float a2 =      (A+1) + (A-1)*cw - 2*sqA*alpha;

    BiquadCoeffs c;
    c.b0 = b0/a0;  c.b1 = b1/a0;  c.b2 = b2/a0;
    c.a1 = a1/a0;  c.a2 = a2/a0;
    return c;
}

static BiquadCoeffs eq_high_shelf(float gain_db, float freq, float Q, float sr)
{
    float A  = std::sqrt(db2lin(gain_db));
    float w0 = 2.0f * PI * freq / sr;
    float cw = std::cos(w0), sw = std::sin(w0);
    float alpha = sw / (2.0f * Q);
    float sqA   = std::sqrt(A);

    float b0 =  A * ((A+1) + (A-1)*cw + 2*sqA*alpha);
    float b1 = -2*A * ((A-1) + (A+1)*cw);
    float b2 =  A * ((A+1) + (A-1)*cw - 2*sqA*alpha);
    float a0 =      (A+1) - (A-1)*cw + 2*sqA*alpha;
    float a1 =  2 * ((A-1) - (A+1)*cw);
    float a2 =      (A+1) - (A-1)*cw - 2*sqA*alpha;

    BiquadCoeffs c;
    c.b0 = b0/a0;  c.b1 = b1/a0;  c.b2 = b2/a0;
    c.a1 = a1/a0;  c.a2 = a2/a0;
    return c;
}

static BiquadCoeffs eq_peak(float gain_db, float freq, float Q, float sr)
{
    float A  = db2lin(gain_db / 2.0f);               // note: half gain for peak
    float w0 = 2.0f * PI * freq / sr;
    float cw = std::cos(w0), sw = std::sin(w0);
    float alpha = sw / (2.0f * Q);

    float b0 =  1 + alpha * A;
    float b1 = -2 * cw;
    float b2 =  1 - alpha * A;
    float a0 =  1 + alpha / A;
    float a1 = -2 * cw;
    float a2 =  1 - alpha / A;

    BiquadCoeffs c;
    c.b0 = b0/a0;  c.b1 = b1/a0;  c.b2 = b2/a0;
    c.a1 = a1/a0;  c.a2 = a2/a0;
    return c;
}

// ===========================================================================
// 7-band parametric EQ
// Params (24 floats, 3 unused):
//   p[0..6]  gain  norm 0-1  (0.5=0dB, ±24dB range)
//   p[7..13] freq  log-norm  (20Hz–20kHz)
//   p[14..20] Q    log-norm  (0.1–10.0)
// Band 0 = low shelf, bands 1-5 = peak, band 6 = high shelf
// ===========================================================================
// Default centre frequencies matching Racks.py EQ7_BANDS
static const float EQ_DEF_FREQ[7]= {80.f,250.f,700.f,2000.f,5000.f,10000.f,16000.f};
static const float EQ_DEF_Q[7]   = {0.7f,1.0f,1.0f,1.0f,1.0f,1.0f,0.7f};

static inline float eq_freq_from_norm(float n) {
    // log-scale 20Hz–20kHz, matching Racks.py EQ_FREQ_MIN_LOG/MAX_LOG
    const float LOG_MIN = 1.30103f;   // log10(20)
    const float LOG_RNG = 2.69897f;   // log10(20000) - log10(20)
    return std::pow(10.0f, LOG_MIN + n * LOG_RNG);
}

static inline float eq_q_from_norm(float n) {
    // log-scale 0.1–10.0, matching Racks.py EQ_Q_MIN_LOG/MAX_LOG
    const float LOG_MIN = -1.0f;      // log10(0.1)
    const float LOG_RNG =  2.0f;      // log10(10.0) - log10(0.1)
    return std::pow(10.0f, LOG_MIN + n * LOG_RNG);
}

static void apply_eq_param(int ch, aud::sample_t* buf,
                            int frames, int n_ch,
                            const EffectSlot& fx,
                            float sr)
{
    // Denormalise params and build biquad coefficients for each band
    BiquadCoeffs coeffs[7];
    bool         active[7] = {};

    for (int bi = 0; bi < 7; ++bi) {
        float gn  = fx.params[bi];          // gain norm
        float fn  = fx.params[bi + 7];      // freq norm
        float qn  = fx.params[bi + 14];     // Q norm
        float gdb = (gn - 0.5f) * 48.0f;   // ±24dB range

        if (std::abs(gdb) < 0.5f) {
            active[bi] = false;
            continue;                        // skip flat bands — saves CPU
        }
        active[bi] = true;

        float freq = (fn > 0.0f) ? eq_freq_from_norm(fn) : EQ_DEF_FREQ[bi];
        float q    = (qn > 0.0f) ? eq_q_from_norm(qn)    : EQ_DEF_Q[bi];
        // Clamp to safe ranges
        freq = std::max(20.0f, std::min(freq, sr * 0.49f));
        q    = std::max(0.1f,  std::min(q,    10.0f));

        if (bi == 0)
            coeffs[bi] = eq_low_shelf (gdb, freq, q, sr);
        else if (bi == 6)
            coeffs[bi] = eq_high_shelf(gdb, freq, q, sr);
        else
            coeffs[bi] = eq_peak      (gdb, freq, q, sr);
    }

    // Apply biquad chain per audio channel, maintaining persistent state
    EqChannelState& eq = g_state.eq_state[ch];
    int n_audio_ch = std::min(n_ch, 2);     // max stereo

    for (int f = 0; f < frames; ++f) {
        for (int c = 0; c < n_audio_ch; ++c) {
            float s = buf[f * n_ch + c];
            for (int bi = 0; bi < 7; ++bi) {
                if (!active[bi]) continue;
                s = biquad_step(s, coeffs[bi],
                                eq.bands[bi].z1[c],
                                eq.bands[bi].z2[c]);
            }
            // Soft clip — prevents overs from heavy boost settings
            s = s / (1.0f + std::abs(s));
            buf[f * n_ch + c] = s;
        }
    }
}

// ===========================================================================
// FFT (DFT for PB_FFT_BINS bins with Hann window)
// ===========================================================================
static void update_fft(int ch, int band, const float* mono_buf, int frames)
{
    FFTBandState& fs = g_state.fft_state[ch][band];
    const int N = PB_FFT_BINS * 2;

    for (int f = 0; f < frames && f < N; ++f) {
        fs.window_buf[fs.write_pos] = mono_buf[f];
        fs.write_pos = (fs.write_pos + 1) % N;
    }

    for (int k = 0; k < PB_FFT_BINS; ++k) {
        float re = 0.0f, im = 0.0f;
        for (int n = 0; n < N; ++n) {
            float sample = fs.window_buf[(fs.write_pos + n) % N];
            float w   = 0.5f * (1.0f - std::cos(2.0f * PI * n / (N - 1)));
            float ang = 2.0f * PI * k * n / N;
            re += sample * w * std::cos(ang);
            im -= sample * w * std::sin(ang);
        }
        float mag = std::sqrt(re*re + im*im) / (N * 0.5f);
        float normalised = std::max(0.0f, (lin2db(mag) + 80.0f) / 80.0f);
        const float sm = 0.6f;
        g_state.fft_bins[ch][band][k] = sm * g_state.fft_bins[ch][band][k]
                                       + (1.0f - sm) * normalised;
    }
}

// ===========================================================================
// Multiband compressor
// Crossovers: 120Hz, 800Hz, 5kHz
// Filter states stored as flat arrays in CompressorChannelState:
//   lp_z[band][stage*2 + ch]  — 4 states per band per audio channel
//   Same for hp_z
// We use a simpler flat layout here: one state set per crossover
// processed in mono (mixed down) then output back to stereo.
// ===========================================================================
static const float CROSSOVER_HZ[3] = {120.0f, 800.0f, 5000.0f};

// Flat filter state per crossover point: [stage0_z1, stage0_z2, stage1_z1, stage1_z2]
// We store two sets per crossover (one per audio channel, max stereo)
// Layout in CompressorChannelState: lp_z[band][2][2][2]
// We'll access it as a flat pointer with stride 4 per audio channel

static void apply_comp_multi(int ch, aud::sample_t* buf,
                              int frames, int n_ch,
                              const EffectSlot& fx,
                              float sr)
{
    static const int MAX_F = 4096;
    if (frames > MAX_F) frames = MAX_F;

    float band_buf[PB_MB_BANDS][MAX_F];
    float tmp[MAX_F];

    BiquadCoeffs lp_co[3], hp_co[3];
    for (int x = 0; x < 3; ++x) {
        lp_co[x] = butter_lp(CROSSOVER_HZ[x], sr);
        hp_co[x] = butter_hp(CROSSOVER_HZ[x], sr);
    }

    // Initialise band_buf to zero — accumulate across channels
    for (int b = 0; b < PB_MB_BANDS; ++b)
        memset(band_buf[b], 0, frames * sizeof(float));

    for (int c = 0; c < n_ch; ++c)
    {
        float mono[MAX_F];
        for (int f = 0; f < frames; ++f) mono[f] = buf[f * n_ch + c];

        // Each lp_z/hp_z entry: [band][stage][ch][z1/z2]
        // Access as flat pointer: base + c*2  gives [z1,z2] for this channel
        // stride per band = 2 stages * 2 channels * 2 states = 8 floats

        float* lp0_state = &g_state.comp_state[ch].lp_z[0][0][c][0]; // band0 lp, stage0, ch c
        float* lp0_state1= &g_state.comp_state[ch].lp_z[0][1][c][0]; // band0 lp, stage1, ch c
        float* hp0_state = &g_state.comp_state[ch].hp_z[0][0][c][0];
        float* hp0_state1= &g_state.comp_state[ch].hp_z[0][1][c][0];
        float* lp1_state = &g_state.comp_state[ch].lp_z[1][0][c][0];
        float* lp1_state1= &g_state.comp_state[ch].lp_z[1][1][c][0];
        float* hp1_state = &g_state.comp_state[ch].hp_z[1][0][c][0];
        float* hp1_state1= &g_state.comp_state[ch].hp_z[1][1][c][0];
        float* lp2_state = &g_state.comp_state[ch].lp_z[2][0][c][0];
        float* lp2_state1= &g_state.comp_state[ch].lp_z[2][1][c][0];
        float* hp2_state = &g_state.comp_state[ch].hp_z[2][0][c][0];
        float* hp2_state1= &g_state.comp_state[ch].hp_z[2][1][c][0];

        // Band 0: LP120 (2 cascaded biquads)
        memcpy(band_buf[0], mono, frames * sizeof(float));
        lr_filter(band_buf[0], frames, lp_co[0], lp0_state);

        // Band 1: HP120 then LP800
        memcpy(tmp, mono, frames * sizeof(float));
        // HP stage 1
        for (int f = 0; f < frames; ++f)
            tmp[f] = biquad_step(tmp[f], hp_co[0], hp0_state[0], hp0_state[1]);
        for (int f = 0; f < frames; ++f)
            tmp[f] = biquad_step(tmp[f], hp_co[0], hp0_state1[0], hp0_state1[1]);
        memcpy(band_buf[1], tmp, frames * sizeof(float));
        // LP stage 2
        for (int f = 0; f < frames; ++f)
            band_buf[1][f] = biquad_step(band_buf[1][f], lp_co[1], lp1_state[0], lp1_state[1]);
        for (int f = 0; f < frames; ++f)
            band_buf[1][f] = biquad_step(band_buf[1][f], lp_co[1], lp1_state1[0], lp1_state1[1]);

        // Band 2: HP800 then LP5k
        memcpy(tmp, mono, frames * sizeof(float));
        for (int f = 0; f < frames; ++f)
            tmp[f] = biquad_step(tmp[f], hp_co[1], hp1_state[0], hp1_state[1]);
        for (int f = 0; f < frames; ++f)
            tmp[f] = biquad_step(tmp[f], hp_co[1], hp1_state1[0], hp1_state1[1]);
        memcpy(band_buf[2], tmp, frames * sizeof(float));
        for (int f = 0; f < frames; ++f)
            band_buf[2][f] = biquad_step(band_buf[2][f], lp_co[2], lp2_state[0], lp2_state[1]);
        for (int f = 0; f < frames; ++f)
            band_buf[2][f] = biquad_step(band_buf[2][f], lp_co[2], lp2_state1[0], lp2_state1[1]);

        // Band 3: HP5k
        memcpy(band_buf[3], mono, frames * sizeof(float));
        for (int f = 0; f < frames; ++f)
            band_buf[3][f] = biquad_step(band_buf[3][f], hp_co[2], hp2_state[0], hp2_state[1]);
        for (int f = 0; f < frames; ++f)
            band_buf[3][f] = biquad_step(band_buf[3][f], hp_co[2], hp2_state1[0], hp2_state1[1]);

        // Compress each band
        for (int b = 0; b < PB_MB_BANDS; ++b) {
            float thr_db  = denorm_threshold(fx.params[b]);
            float ratio   = denorm_ratio    (fx.params[b + 4]);
            float atk_ms  = denorm_attack   (fx.params[b + 8]);
            float rel_ms  = denorm_release  (fx.params[b + 12]);
            float gain_lin= db2lin(denorm_gain(fx.params[b + 16]));
            float knee_db = denorm_knee     (fx.params[b + 20]);

            float atk_c = time_coeff(atk_ms, sr);
            float rel_c = time_coeff(rel_ms, sr);
            CompressorBandState& st = g_state.comp_state[ch].bands[b];
            float max_gr_db = 0.0f;

            for (int f = 0; f < frames; ++f) {
                float s    = band_buf[b][f];
                float peak = std::abs(s);
                float coeff= (peak > st.envelope) ? atk_c : rel_c;
                st.envelope= coeff * st.envelope + (1.0f - coeff) * peak;

                float gr    = soft_knee_gr(st.envelope, thr_db, ratio, knee_db);
                float gr_db = (gr < 1.0f) ? lin2db(gr) : 0.0f;
                if (gr_db < max_gr_db) max_gr_db = gr_db;

                band_buf[b][f] = s * gr * gain_lin;
            }

            const float sm = 0.85f;
            g_state.gr_levels[ch][b] = sm * g_state.gr_levels[ch][b]
                                     + (1.0f - sm) * (-max_gr_db);

            // FFT for spectrum — channel 0 only to save CPU
            if (c == 0) update_fft(ch, b, band_buf[b], frames);
        }

        // Sum bands back to interleaved output
        for (int f = 0; f < frames; ++f) {
            buf[f * n_ch + c] = band_buf[0][f] + band_buf[1][f]
                               + band_buf[2][f] + band_buf[3][f];
        }
    }

    // Band level RMS for display
    const float sm = 0.85f;
    for (int b = 0; b < PB_MB_BANDS; ++b) {
        float rms = 0.0f;
        for (int f = 0; f < frames; ++f) rms += band_buf[b][f] * band_buf[b][f];
        rms = std::sqrt(rms / (frames + 1e-9f));
        g_state.band_levels[ch][b] = sm * g_state.band_levels[ch][b]
                                    + (1.0f - sm) * rms;
    }
}

// ===========================================================================
// apply_effect_chain
// ===========================================================================
void apply_effect_chain_batch(int ch, aud::sample_t* buf,
                                int frames, int n_ch, float sr)
{
    for (int slot = 0; slot < PB_MAX_EFFECTS; ++slot) {
        const EffectSlot& fx = g_state.effect_chain[ch][slot];
        if (!fx.enabled) continue;
        switch (fx.type) {
        case EffectType::GAIN:
            for (int f = 0; f < frames; ++f)
                for (int c = 0; c < n_ch; ++c)
                    buf[f*n_ch+c] *= fx.params[0];
            break;
        case EffectType::EQ_3BAND: {
            float avg = (db2lin(fx.params[0]) + db2lin(fx.params[1])
                       + db2lin(fx.params[2])) / 3.0f;
            for (int f = 0; f < frames; ++f)
                for (int c = 0; c < n_ch; ++c)
                    buf[f*n_ch+c] *= avg;
            break; }
        case EffectType::COMP_SINGLE:
            apply_comp_single(ch, buf, frames, n_ch, fx, sr); break;
        case EffectType::COMP_MULTI:
            apply_comp_multi (ch, buf, frames, n_ch, fx, sr); break;
        case EffectType::EQ_PARAM:
            apply_eq_param   (ch, buf, frames, n_ch, fx, sr); break;
        case EffectType::REVERB: break;
        default: break;
        }
    }
}

// ===========================================================================
// PedalboardReader
// ===========================================================================
class PedalboardReader : public aud::IReader
{
public:
    PedalboardReader(std::shared_ptr<aud::IReader> inner, int ch)
        : m_inner(inner), m_ch(ch) {}

    bool isSeekable() const override { return m_inner->isSeekable(); }
    void seek(int p)         override { m_inner->seek(p); }
    int  getLength()   const override { return m_inner->getLength(); }
    int  getPosition() const override { return m_inner->getPosition(); }
    aud::Specs getSpecs() const override { return m_inner->getSpecs(); }

    void read(int& length, bool& eos, aud::sample_t* buffer) override
    {
        m_inner->read(length, eos, buffer);
        if (length <= 0 || !buffer) return;

        aud::Specs specs = m_inner->getSpecs();
        int   n_ch   = std::max(1, (int)specs.channels);
        int   frames = length / n_ch;
        float sr     = (float)specs.rate;
        if (sr <= 0.0f) sr = 44100.0f;

        float gain = g_state.volumes[m_ch];
        for (int f = 0; f < frames; ++f)
            for (int c = 0; c < n_ch; ++c)
                buffer[f*n_ch+c] *= gain;

        apply_effect_chain_batch(m_ch, buffer, frames, n_ch, sr);

        float post = 0.0f;
        for (int f = 0; f < frames; ++f)
            for (int c = 0; c < n_ch; ++c)
                post = std::max(post, std::abs(buffer[f*n_ch+c]));
        g_state.meter_levels[m_ch] = std::min(post, 1.0f);
    }

private:
    std::shared_ptr<aud::IReader> m_inner;
    int m_ch;
};

// ===========================================================================
// PedalboardSound
// ===========================================================================
class PedalboardSound : public aud::ISound
{
public:
    PedalboardSound(std::shared_ptr<aud::ISound> inner, int ch)
        : m_inner(inner), m_ch(ch) {}

    std::shared_ptr<aud::IReader> createReader() override {
        return std::make_shared<PedalboardReader>(m_inner->createReader(), m_ch);
    }
    aud::ISound* get_raw() { return this; }

private:
    std::shared_ptr<aud::ISound> m_inner;
    int m_ch;
};

// ===========================================================================
// C-linkage
// ===========================================================================
extern "C" {

void* create_channel(void* sound_ptr, int ch, int strip_ch)
{
    if (!sound_ptr || ch < 0 || ch >= PB_MAX_CHANNELS) return nullptr;
    aud::ISound* raw = reinterpret_cast<aud::ISound*>(sound_ptr);
    std::shared_ptr<aud::ISound> inner(raw, [](aud::ISound*){});
    ChannelHandle* h = new ChannelHandle();
    h->sound_raw     = new PedalboardSound(inner, ch);
    h->channel_idx   = ch;
    h->strip_channel = strip_ch;
    if (g_state.volumes[ch] == 0.0f) g_state.volumes[ch] = 1.0f;
    g_state.meter_levels[ch] = 0.0f;
    g_state.comp_state[ch]   = CompressorChannelState{};
    g_state.eq_state[ch]     = EqChannelState{};
    for (int b = 0; b < PB_MB_BANDS; ++b)
        g_state.fft_state[ch][b] = FFTBandState{};
    printf("[ENGINE] ch=%d strip=%d started\n", ch, strip_ch);
    return h;
}

void* get_channel_sound(void* h) {
    if (!h) return nullptr;
    return reinterpret_cast<ChannelHandle*>(h)->sound_raw;
}

void release_channel(void* h) {
    if (!h) return;
    ChannelHandle* c = reinterpret_cast<ChannelHandle*>(h);
    if (c->channel_idx >= 0 && c->channel_idx < PB_MAX_CHANNELS) {
        g_state.meter_levels[c->channel_idx] = 0.0f;
        for (int b = 0; b < PB_MB_BANDS; ++b) {
            g_state.band_levels[c->channel_idx][b] = 0.0f;
            g_state.gr_levels  [c->channel_idx][b] = 0.0f;
            memset(g_state.fft_bins[c->channel_idx][b], 0,
                   sizeof(g_state.fft_bins[0][0]));
        }
    }
    delete c->sound_raw;
    delete c;
}

} // extern "C"
