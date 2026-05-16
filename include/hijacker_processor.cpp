// ---------------------------------------------------------------------------
// hijacker_processor.cpp
// The Hijacker — DSP effect chain (EQ, compressor, reverb, gate, delay)
// ---------------------------------------------------------------------------
#include "hijacker_processor.h"
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
// Threshold range -40..0dB matches all existing presets.
// Raw PCM WAVs sit ~20dB lower than Blender's audio graph output, so the
// envelope follower applies a sidechain gain before comparing to threshold.
// This is detector-only — the audio signal itself is NOT boosted.
static inline float denorm_threshold(float n) { return -40.0f + n * 40.0f; }
static const float SIDECHAIN_GAIN   = 10.0f;   // +20dB linear on detector only
static inline float denorm_ratio    (float n) { return  1.0f  + n * 19.0f; }
static inline float denorm_attack   (float n) { return  0.1f  + n * 99.9f; }
static inline float denorm_release  (float n) { return 10.0f  + n * 990.0f; }
static inline float denorm_makeup   (float n) { return  n * 24.0f; }
// Piecewise gain: n=0→-30dB, n=0.5→0dB, n=1→+12dB.
// n<0.5 maps 0..0.5 → -30..0dB (60dB/unit)
// n≥0.5 maps 0.5..1.0 → 0..+12dB (24dB/unit)
// n=0.5 is always 0dB — the fader default.
static inline float denorm_gain(float n) {
    if (n < 0.5f) return (n - 0.5f) * 60.0f;   // 0→-30, 0.5→0
    else          return (n - 0.5f) * 24.0f;   // 0.5→0, 1→+12
}
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

// Forward declaration — defined after apply_comp_multi
static void update_fft(int ch, int band, const float* mono_buf, int frames);

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
        peak *= SIDECHAIN_GAIN;  // detector boost — audio unchanged

        float coeff = (peak > st.envelope) ? atk_c : rel_c;
        st.envelope = coeff * st.envelope + (1.0f - coeff) * peak;

        float gr    = soft_knee_gr(st.envelope, thr_db, ratio, knee_db);
        float gr_db = (gr < 1.0f) ? lin2db(gr) : 0.0f;
        if (gr_db < max_gr_db) max_gr_db = gr_db;

        for (int c = 0; c < n_ch; ++c)
            buf[f * n_ch + c] *= gr * mkp_lin;
    }

    const float sm = 0.6f;  // faster decay than 0.85 — GR meter responds more snappily
    g_state.gr_levels[ch][0] = sm * g_state.gr_levels[ch][0]
                              + (1.0f - sm) * (-max_gr_db);

    // Build mono mix for FFT — used by the spectrum display
    std::vector<float> mono(frames);
    for (int f = 0; f < frames; ++f) {
        float s = 0.0f;
        for (int c = 0; c < n_ch; ++c) s += buf[f * n_ch + c];
        mono[f] = s / n_ch;
    }
    // Update all 4 bands with the same mono signal so the spectrum
    // display shows activity across all frequency bands
    for (int b = 0; b < PB_MB_BANDS; ++b)
        update_fft(ch, b, mono.data(), frames);

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
// Parametric EQ coefficient builders (Audio EQ Cookbook)
// ===========================================================================
static BiquadCoeffs eq_low_shelf(float gain_db, float freq, float Q, float sr)
{
    float A  = std::sqrt(db2lin(gain_db));
    float w0 = 2.0f * PI * freq / sr;
    float cw = std::cos(w0), sw = std::sin(w0);
    float alpha = sw / (2.0f * Q), sqA = std::sqrt(A);
    float b0 =  A*((A+1)-(A-1)*cw+2*sqA*alpha);
    float b1 =  2*A*((A-1)-(A+1)*cw);
    float b2 =  A*((A+1)-(A-1)*cw-2*sqA*alpha);
    float a0 =    (A+1)+(A-1)*cw+2*sqA*alpha;
    float a1 = -2*((A-1)+(A+1)*cw);
    float a2 =    (A+1)+(A-1)*cw-2*sqA*alpha;
    BiquadCoeffs c;
    c.b0=b0/a0; c.b1=b1/a0; c.b2=b2/a0; c.a1=a1/a0; c.a2=a2/a0;
    return c;
}

static BiquadCoeffs eq_high_shelf(float gain_db, float freq, float Q, float sr)
{
    float A  = std::sqrt(db2lin(gain_db));
    float w0 = 2.0f * PI * freq / sr;
    float cw = std::cos(w0), sw = std::sin(w0);
    float alpha = sw / (2.0f * Q), sqA = std::sqrt(A);
    float b0 =  A*((A+1)+(A-1)*cw+2*sqA*alpha);
    float b1 = -2*A*((A-1)+(A+1)*cw);
    float b2 =  A*((A+1)+(A-1)*cw-2*sqA*alpha);
    float a0 =    (A+1)-(A-1)*cw+2*sqA*alpha;
    float a1 =  2*((A-1)-(A+1)*cw);
    float a2 =    (A+1)-(A-1)*cw-2*sqA*alpha;
    BiquadCoeffs c;
    c.b0=b0/a0; c.b1=b1/a0; c.b2=b2/a0; c.a1=a1/a0; c.a2=a2/a0;
    return c;
}

static BiquadCoeffs eq_peak(float gain_db, float freq, float Q, float sr)
{
    float A  = db2lin(gain_db / 2.0f);
    float w0 = 2.0f * PI * freq / sr;
    float cw = std::cos(w0), sw = std::sin(w0);
    float alpha = sw / (2.0f * Q);
    float b0=1+alpha*A, b1=-2*cw, b2=1-alpha*A;
    float a0=1+alpha/A, a1=-2*cw, a2=1-alpha/A;
    BiquadCoeffs c;
    c.b0=b0/a0; c.b1=b1/a0; c.b2=b2/a0; c.a1=a1/a0; c.a2=a2/a0;
    return c;
}

static const float EQ_DEF_FREQ[7] = {80.f,250.f,700.f,2000.f,5000.f,10000.f,16000.f};
static const float EQ_DEF_Q[7]    = {0.7f,1.0f,1.0f,1.0f,1.0f,1.0f,0.7f};

static inline float eq_freq_from_norm(float n) {
    return std::pow(10.0f, 1.30103f + n * 2.69897f);
}
static inline float eq_q_from_norm(float n) {
    return std::pow(10.0f, -1.0f + n * 2.0f);
}

static void apply_eq_param(int ch, aud::sample_t* buf, int frames, int n_ch,
                            const EffectSlot& fx, float sr)
{
    BiquadCoeffs coeffs[7]; bool active[7] = {};
    for (int bi = 0; bi < 7; ++bi) {
        float gdb = (fx.params[bi] - 0.5f) * 48.0f;
        if (std::abs(gdb) < 0.5f) { active[bi]=false; continue; }
        active[bi] = true;
        float fn = fx.params[bi+7], qn = fx.params[bi+14];
        float freq = (fn>0.0f) ? eq_freq_from_norm(fn) : EQ_DEF_FREQ[bi];
        float q    = (qn>0.0f) ? eq_q_from_norm(qn)    : EQ_DEF_Q[bi];
        freq = std::max(20.0f, std::min(freq, sr*0.49f));
        q    = std::max(0.1f,  std::min(q,    10.0f));
        if      (bi==0) coeffs[bi] = eq_low_shelf (gdb, freq, q, sr);
        else if (bi==6) coeffs[bi] = eq_high_shelf(gdb, freq, q, sr);
        else            coeffs[bi] = eq_peak      (gdb, freq, q, sr);
    }
    EqChannelState& eq = g_state.eq_state[ch];
    int nc = std::min(n_ch, 2);
    for (int f = 0; f < frames; ++f) {
        for (int c = 0; c < nc; ++c) {
            float s = buf[f*n_ch+c];
            for (int bi = 0; bi < 7; ++bi) {
                if (!active[bi]) continue;
                s = biquad_step(s, coeffs[bi], eq.bands[bi].z1[c], eq.bands[bi].z2[c]);
            }
            buf[f*n_ch+c] = s / (1.0f + std::abs(s));
        }
    }

    // Update FFT for spectrum display — build mono mix then update all bands
    std::vector<float> mono_eq(frames);
    for (int f = 0; f < frames; ++f) {
        float s = 0.0f;
        for (int c = 0; c < n_ch; ++c) s += buf[f * n_ch + c];
        mono_eq[f] = s / n_ch;
    }
    for (int b = 0; b < PB_MB_BANDS; ++b)
        update_fft(ch, b, mono_eq.data(), frames);
}

// ===========================================================================
// Freeverb
// ===========================================================================
static inline float comb_step(CombState& st, float in,
                                int len, float feedback, float damp)
{
    float out    = st.buf[st.pos];
    st.filter    = out * (1.0f - damp) + st.filter * damp;
    st.buf[st.pos] = in + st.filter * feedback;
    st.pos = (st.pos + 1) % len;
    return out;
}

static inline float allpass_step(AllpassState& st, float in, int len)
{
    float buf_out = st.buf[st.pos];
    float out     = -in + buf_out;
    st.buf[st.pos]= in + buf_out * 0.5f;
    st.pos = (st.pos + 1) % len;
    return out;
}

static void apply_reverb_param(int ch, aud::sample_t* buf, int frames, int n_ch,
                                const EffectSlot& fx, float sr)
{
    float room_sz   = 0.28f + fx.params[0] * 0.70f;
    float damp      = fx.params[1] * 0.95f;
    float wet       = fx.params[2];
    float dry       = 1.0f - wet * 0.7f;
    float pd_norm   = fx.params[3];
    float width     = fx.params[4];
    int   pd_samp   = (int)(pd_norm * 0.1f * sr);
    pd_samp = std::min(pd_samp, MAX_PREDELAY_SAMP - 1);

    ReverbChannelState& rv = g_state.reverb_state[ch];
    rv.sample_rate = sr;
    float sr_scale = sr / 44100.0f;

    for (int f = 0; f < frames; ++f) {
        float in_l = buf[f*n_ch + 0];
        float in_r = (n_ch > 1) ? buf[f*n_ch + 1] : in_l;

        float pd_in_l = in_l, pd_in_r = in_r;
        if (pd_samp > 0) {
            pd_in_l = rv.predelay[0][rv.pd_write[0]];
            pd_in_r = rv.predelay[1][rv.pd_write[1]];
            rv.predelay[0][rv.pd_write[0]] = in_l;
            rv.predelay[1][rv.pd_write[1]] = in_r;
            rv.pd_write[0] = (rv.pd_write[0] + 1) % std::max(1, pd_samp);
            rv.pd_write[1] = (rv.pd_write[1] + 1) % std::max(1, pd_samp);
        }
        float verb_in = (pd_in_l + pd_in_r) * 0.5f;

        float out_l = 0.0f, out_r = 0.0f;
        for (int i = 0; i < PB_REVERB_COMB; ++i) {
            int len_l = (int)(COMB_LENGTHS_L[i] * sr_scale);
            int len_r = (int)(COMB_LENGTHS_R[i] * sr_scale);
            len_l = std::max(1, std::min(len_l, MAX_COMB_LEN-1));
            len_r = std::max(1, std::min(len_r, MAX_COMB_LEN-1));
            out_l += comb_step(rv.comb[0][i], verb_in, len_l, room_sz, damp);
            out_r += comb_step(rv.comb[1][i], verb_in, len_r, room_sz, damp);
        }

        for (int i = 0; i < PB_REVERB_AP; ++i) {
            int len_l = (int)(AP_LENGTHS_L[i] * sr_scale);
            int len_r = (int)(AP_LENGTHS_R[i] * sr_scale);
            len_l = std::max(1, std::min(len_l, MAX_AP_LEN-1));
            len_r = std::max(1, std::min(len_r, MAX_AP_LEN-1));
            out_l = allpass_step(rv.ap[0][i], out_l, len_l);
            out_r = allpass_step(rv.ap[1][i], out_r, len_r);
        }

        float width_mix = width * 0.5f;
        float wet_l = out_l * (0.5f + width_mix) + out_r * (0.5f - width_mix);
        float wet_r = out_r * (0.5f + width_mix) + out_l * (0.5f - width_mix);

        buf[f*n_ch + 0] = in_l * dry + wet_l * wet;
        if (n_ch > 1)
            buf[f*n_ch + 1] = in_r * dry + wet_r * wet;
    }
}

// ===========================================================================
// Stereo Delay — with feedback, LP filter on tail, and ping-pong mode
//
// Design:
//   - One circular buffer per stereo channel (buf_l, buf_r in DelayChannelState)
//   - Feedback reads from the delay line, applies a one-pole LP filter to
//     darken each echo (simulates tape/air HF absorption), then writes back
//   - Ping-pong: when spread > 0.5, each echo bounces L→R→L instead of
//     repeating in the same channel — achieved by swapping which buffer
//     the feedback is written into
//   - Dry signal is always passed through at full level (mix = wet only)
//   - LP cutoff: 200 Hz (filter=0) → 20000 Hz (filter=1) on a log scale
//
// State is NOT reset between process_buffer() batch calls — DelayChannelState
// lives in EngineState and persists across chunks so the tail rings through
// correctly. It IS cleared on create_channel() / release_channel().
// ===========================================================================
static void apply_delay(int ch, aud::sample_t* buf, int frames, int n_ch,
                         const EffectSlot& fx, float sr)
{
    // Denormalise params
    float delay_ms  = 1.0f + fx.params[0] * 1999.0f;   // 1..2000 ms
    float feedback  = fx.params[1] * 0.92f;              // cap at 0.92
    float mix       = fx.params[2];
    float spread    = fx.params[3];
    bool  ping_pong = (spread > 0.5f);
    float filt_hz   = 200.0f * std::pow(100.0f, fx.params[4]);
    filt_hz = std::max(200.0f, std::min(filt_hz, sr * 0.49f));
    float lp_a = 1.0f - std::exp(-2.0f * PI * filt_hz / sr);

    int delay_samp = (int)(delay_ms * 0.001f * sr);
    delay_samp = std::max(1, delay_samp);

    // Allocate or reallocate buffer when delay time changes.
    // Buffer is sized to delay_samp + a small pad; this keeps it small
    // and means the struct stays tiny (just two pointers).
    // We add 64 samples of headroom so small knob nudges don't reallocate.
    int needed = delay_samp + 64;
    DelayChannelState& st = g_state.delay_state[ch];

    if (st.buf_l == nullptr || needed > st.buf_size) {
        delete[] st.buf_l;
        delete[] st.buf_r;
        st.buf_l = new float[needed]();  // () zero-initialises
        st.buf_r = new float[needed]();
        st.buf_size = needed;
        st.write_l = 0;
        st.write_r = 0;
        st.filter_z_l = 0.0f;
        st.filter_z_r = 0.0f;
        st.last_delay_samp = delay_samp;
    } else if (std::abs(st.last_delay_samp - delay_samp) > 4) {
        // Delay time changed — clear to avoid pitch artefacts
        memset(st.buf_l, 0, st.buf_size * sizeof(float));
        memset(st.buf_r, 0, st.buf_size * sizeof(float));
        st.write_l = 0;
        st.write_r = 0;
        st.filter_z_l = 0.0f;
        st.filter_z_r = 0.0f;
        st.last_delay_samp = delay_samp;
    }

    int buf_sz = st.buf_size;

    for (int f = 0; f < frames; ++f) {
        float dry_l = buf[f * n_ch + 0];
        float dry_r = (n_ch > 1) ? buf[f * n_ch + 1] : dry_l;

        int read_l = (st.write_l - delay_samp + buf_sz) % buf_sz;
        int read_r = (st.write_r - delay_samp + buf_sz) % buf_sz;
        float echo_l = st.buf_l[read_l];
        float echo_r = st.buf_r[read_r];

        st.filter_z_l = lp_a * echo_l + (1.0f - lp_a) * st.filter_z_l;
        st.filter_z_r = lp_a * echo_r + (1.0f - lp_a) * st.filter_z_r;
        float filt_l = st.filter_z_l;
        float filt_r = st.filter_z_r;

        if (ping_pong && n_ch > 1) {
            st.buf_l[st.write_l] = dry_l + filt_r * feedback;
            st.buf_r[st.write_r] = dry_r + filt_l * feedback;
        } else {
            st.buf_l[st.write_l] = dry_l + filt_l * feedback;
            st.buf_r[st.write_r] = dry_r + filt_r * feedback;
        }

        st.write_l = (st.write_l + 1) % buf_sz;
        st.write_r = (st.write_r + 1) % buf_sz;

        buf[f * n_ch + 0] = dry_l + echo_l * mix;
        if (n_ch > 1)
            buf[f * n_ch + 1] = dry_r + echo_r * mix;
    }
}

// ===========================================================================
// Full-spectrum analyser — 128 log-spaced bins 20Hz-20kHz
// Called from apply_effect_chain_batch on the raw pre-EQ signal.
// ===========================================================================
static void update_spec(int ch, const float* mono_buf, int frames, float sr)
{
    // N = 4096-sample ring buffer.
    // At 48kHz: frequency resolution = 48000/4096 = 11.7Hz/bin.
    // This resolves individual harmonics of voice/instruments → sharp teeth.
    // The old N=256 gave 187Hz/bin — too coarse for any detail below ~400Hz.
    const int N = PB_SPEC_WIN;  // 4096

    // Fill circular window buffer with all incoming frames
    for (int f = 0; f < frames; ++f) {
        g_state.spec_window[ch][g_state.spec_wpos[ch]] = mono_buf[f];
        g_state.spec_wpos[ch] = (g_state.spec_wpos[ch] + 1) % N;
    }

    const float log_min = std::log10(20.0f);
    const float log_max = std::log10(20000.0f);

    // Smoothing: the 4096-sample window already provides ~85ms of temporal
    // averaging, so we need much less inter-frame smoothing than before.
    // sm=0.2 keeps peaks snappy while suppressing single-frame noise spikes.
    const float sm = 0.2f;

    for (int k = 0; k < PB_SPEC_BINS; ++k) {
        float t    = (float)k / (PB_SPEC_BINS - 1);
        float f_hz = std::pow(10.0f, log_min + t * (log_max - log_min));
        float kf   = f_hz / sr * (float)N;  // fractional bin index in N-point DFT

        // Goertzel DFT at this exact frequency.
        // Cost: N muls per bin × 128 bins = 524K muls per buffer call.
        // Acceptable for a display-only path called from the audio thread.
        float re = 0.0f, im = 0.0f;
        for (int n = 0; n < N; ++n) {
            float sample = g_state.spec_window[ch][(g_state.spec_wpos[ch] + n) % N];
            // Hann window reduces spectral leakage between adjacent harmonics
            float w   = 0.5f * (1.0f - std::cos(2.0f * PI * n / (N - 1)));
            float ang = 2.0f * PI * kf * n / (float)N;
            re += sample * w * std::cos(ang);
            im -= sample * w * std::sin(ang);
        }
        float mag        = std::sqrt(re * re + im * im) / (N * 0.5f);
        float normalised = std::max(0.0f, (lin2db(mag) + 80.0f) / 80.0f);
        g_state.spec_bins[ch][k] = sm * g_state.spec_bins[ch][k]
                                  + (1.0f - sm) * normalised;
    }
}

// ===========================================================================
// FFT
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
// ===========================================================================
static const float CROSSOVER_HZ[3] = {120.0f, 800.0f, 5000.0f};

static void apply_comp_multi(int ch, aud::sample_t* buf,
                              int frames, int n_ch,
                              const EffectSlot& fx,
                              float sr)
{
    static const int MAX_F = 4096;
    if (frames > MAX_F) frames = MAX_F;

    // band_buf is per-channel — declared inside the channel loop below.
    // Previously shared across channels which caused right ch to compress
    // left ch data (stereo cross-contamination → distortion).
    float tmp[MAX_F];

    BiquadCoeffs lp_co[3], hp_co[3];
    for (int x = 0; x < 3; ++x) {
        lp_co[x] = butter_lp(CROSSOVER_HZ[x], sr);
        hp_co[x] = butter_hp(CROSSOVER_HZ[x], sr);
    }

    // Accumulate per-band compressor output — summed across channels
    // Band compression uses per-channel buffers but envelope follows mono
    // peak so stereo image is preserved (same GR applied to L and R).
    float band_sum[PB_MB_BANDS][MAX_F];
    for (int b = 0; b < PB_MB_BANDS; ++b)
        memset(band_sum[b], 0, frames * sizeof(float));

    for (int c = 0; c < n_ch; ++c)
    {
        // Per-channel band buffers — MUST be separate per channel to avoid
        // cross-contamination. Previously declared outside the loop (bug).
        float band_buf[PB_MB_BANDS][MAX_F];

        float mono[MAX_F];
        for (int f = 0; f < frames; ++f) mono[f] = buf[f * n_ch + c];

        float* lp0_state = &g_state.comp_state[ch].lp_z[0][0][c][0];
        float* lp0_state1= &g_state.comp_state[ch].lp_z[0][1][c][0];
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

        // ---------------------------------------------------------------
        // Linkwitz-Riley crossover — LP and HP are BOTH 4th-order
        // (two 2nd-order Butterworth stages in series).
        // LR4 LP + LR4 HP sum flat in amplitude AND phase — no peaks
        // at crossover frequencies, no distortion on reconstruction.
        //
        // Previously: LP used lr_filter (4th-order), HP used single
        // biquad_step (2nd-order) → mismatched order → phase mismatch
        // → 3dB peaks at each crossover when bands summed → distortion.
        // ---------------------------------------------------------------

        // Band 0: Low  (< 120Hz) — LR4 LP
        memcpy(band_buf[0], mono, frames * sizeof(float));
        lr_filter(band_buf[0], frames, lp_co[0], lp0_state);   // 2nd-order pass 1
        lr_filter(band_buf[0], frames, lp_co[0], lp0_state1);  // 2nd-order pass 2 → 4th-order

        // Band 1: L-Mid (120Hz–800Hz) — LR4 HP then LR4 LP
        memcpy(tmp, mono, frames * sizeof(float));
        lr_filter(tmp, frames, hp_co[0], hp0_state);   // HP 2nd-order pass 1
        lr_filter(tmp, frames, hp_co[0], hp0_state1);  // HP 2nd-order pass 2 → 4th-order
        memcpy(band_buf[1], tmp, frames * sizeof(float));
        lr_filter(band_buf[1], frames, lp_co[1], lp1_state);
        lr_filter(band_buf[1], frames, lp_co[1], lp1_state1);

        // Band 2: H-Mid (800Hz–5kHz) — LR4 HP then LR4 LP
        memcpy(tmp, mono, frames * sizeof(float));
        lr_filter(tmp, frames, hp_co[1], hp1_state);
        lr_filter(tmp, frames, hp_co[1], hp1_state1);
        memcpy(band_buf[2], tmp, frames * sizeof(float));
        lr_filter(band_buf[2], frames, lp_co[2], lp2_state);
        lr_filter(band_buf[2], frames, lp_co[2], lp2_state1);

        // Band 3: High (> 5kHz) — LR4 HP only
        memcpy(band_buf[3], mono, frames * sizeof(float));
        lr_filter(band_buf[3], frames, hp_co[2], hp2_state);
        lr_filter(band_buf[3], frames, hp_co[2], hp2_state1);

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
                // Sidechain gain: boost detector level by SIDECHAIN_GAIN so
                // presets calibrated for Blender audio graph levels fire
                // correctly on raw PCM WAVs which sit ~20dB lower.
                float peak = std::abs(s) * SIDECHAIN_GAIN;
                float coeff= (peak > st.envelope) ? atk_c : rel_c;
                st.envelope= coeff * st.envelope + (1.0f - coeff) * peak;

                float gr    = soft_knee_gr(st.envelope, thr_db, ratio, knee_db);
                float gr_db = (gr < 1.0f) ? lin2db(gr) : 0.0f;
                if (gr_db < max_gr_db) max_gr_db = gr_db;

                band_buf[b][f] = s * gr * gain_lin;  // audio unaffected by sidechain gain
            }

            if (c == 0) {
                const float sm = 0.85f;
                g_state.gr_levels[ch][b] = sm * g_state.gr_levels[ch][b]
                                         + (1.0f - sm) * (-max_gr_db);
                update_fft(ch, b, band_buf[b], frames);
            }
        }

        for (int f = 0; f < frames; ++f) {
            buf[f * n_ch + c] = band_buf[0][f] + band_buf[1][f]
                               + band_buf[2][f] + band_buf[3][f];
        }

        // Accumulate for band_levels (uses first channel only for efficiency)
        if (c == 0) {
            for (int b = 0; b < PB_MB_BANDS; ++b)
                memcpy(band_sum[b], band_buf[b], frames * sizeof(float));
        }
    }

    const float sm = 0.85f;
    for (int b = 0; b < PB_MB_BANDS; ++b) {
        float rms = 0.0f;
        for (int f = 0; f < frames; ++f) rms += band_sum[b][f] * band_sum[b][f];
        rms = std::sqrt(rms / (frames + 1e-9f));
        g_state.band_levels[ch][b] = sm * g_state.band_levels[ch][b]
                                    + (1.0f - sm) * rms;
    }
}

// ===========================================================================
// Noise Gate
// ===========================================================================
static inline float gate_thr_db  (float n){ return -60.0f + n * 60.0f;  }
static inline float gate_atk_ms  (float n){ return   0.1f + n * 99.9f;  }
static inline float gate_hold_ms (float n){ return   0.0f + n * 500.0f; }
static inline float gate_rel_ms  (float n){ return  10.0f + n * 990.0f; }
static inline float gate_range_db(float n){ return -90.0f + n * 90.0f;  }

static void apply_noise_gate(int ch, aud::sample_t* buf,
                              int frames, int n_ch,
                              const EffectSlot& fx, float sr)
{
    float thr_lin    = db2lin(gate_thr_db  (fx.params[0]));
    float atk_c      = time_coeff(gate_atk_ms (fx.params[1]), sr);
    float hold_total = gate_hold_ms(fx.params[2]) * 0.001f * sr;
    float rel_c      = time_coeff(gate_rel_ms (fx.params[3]), sr);
    float range_lin  = db2lin(gate_range_db(fx.params[4]));

    GateChannelState& st = g_state.gate_state[ch];
    float max_gr_db = 0.0f;

    for (int f = 0; f < frames; ++f) {
        float peak = 0.0f;
        for (int c = 0; c < n_ch; ++c)
            peak = std::max(peak, std::abs(buf[f * n_ch + c]));

        float env_c  = (peak > st.envelope) ? atk_c : rel_c;
        st.envelope  = env_c * st.envelope + (1.0f - env_c) * peak;

        if (st.envelope >= thr_lin) {
            st.is_open   = true;
            st.hold_samp = hold_total;
        } else if (st.is_open) {
            if (st.hold_samp > 0.0f)
                st.hold_samp -= 1.0f;
            else
                st.is_open = false;
        }

        float target   = st.is_open ? 1.0f : range_lin;
        float cur_lin  = db2lin(st.gain_db);
        float gain_c   = (target > cur_lin) ? atk_c : rel_c;
        float smooth   = gain_c * cur_lin + (1.0f - gain_c) * target;
        smooth         = std::max(smooth, 1e-9f);
        st.gain_db     = lin2db(smooth);

        float gr_db = (smooth < 1.0f) ? lin2db(smooth) : 0.0f;
        if (gr_db < max_gr_db) max_gr_db = gr_db;

        for (int c = 0; c < n_ch; ++c)
            buf[f * n_ch + c] *= smooth;
    }

    const float sm = 0.85f;
    g_state.gr_levels[ch][1] = sm * g_state.gr_levels[ch][1]
                              + (1.0f - sm) * (-max_gr_db);
    g_state.gr_levels[ch][2] = st.is_open ? 1.0f : 0.0f;
}

// ===========================================================================
// apply_effect_chain_batch
// ===========================================================================
void apply_effect_chain_batch(int ch, aud::sample_t* buf,
                                int frames, int n_ch, float sr)
{
    // Build mono mix from raw signal for full-spectrum EQ display
    // Must happen BEFORE any DSP so we show the unprocessed spectrum
    {
        std::vector<float> mono_raw(frames);
        for (int f = 0; f < frames; ++f) {
            float s = 0.0f;
            for (int c = 0; c < n_ch; ++c) s += buf[f * n_ch + c];
            mono_raw[f] = s / n_ch;
        }
        update_spec(ch, mono_raw.data(), frames, sr);
    }

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
        case EffectType::REVERB_PARAM:
            apply_reverb_param(ch, buf, frames, n_ch, fx, sr); break;
        case EffectType::GATE_PARAM:
            apply_noise_gate (ch, buf, frames, n_ch, fx, sr); break;
        case EffectType::DELAY_PARAM:
            apply_delay      (ch, buf, frames, n_ch, fx, sr); break;
        case EffectType::REVERB: break;
        default: break;
        }
    }
}

// ===========================================================================
// HijackerReader / HijackerSound
// ===========================================================================
class HijackerReader : public aud::IReader
{
public:
    HijackerReader(std::shared_ptr<aud::IReader> inner, int ch)
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

class HijackerSound : public aud::ISound
{
public:
    HijackerSound(std::shared_ptr<aud::ISound> inner, int ch)
        : m_inner(inner), m_ch(ch) {}

    std::shared_ptr<aud::IReader> createReader() override {
        return std::make_shared<HijackerReader>(m_inner->createReader(), m_ch);
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
    h->sound_raw     = new HijackerSound(inner, ch);
    h->channel_idx   = ch;
    h->strip_channel = strip_ch;
    if (g_state.volumes[ch] == 0.0f) g_state.volumes[ch] = 1.0f;
    g_state.meter_levels[ch]   = 0.0f;
    g_state.comp_state[ch]     = CompressorChannelState{};
    g_state.eq_state[ch]       = EqChannelState{};
    g_state.reverb_state[ch]   = ReverbChannelState{};
    g_state.gate_state[ch]     = GateChannelState{};
    // Free any existing delay buffers before resetting state
    delete[] g_state.delay_state[ch].buf_l;
    delete[] g_state.delay_state[ch].buf_r;
    g_state.delay_state[ch] = DelayChannelState{};
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
        delete[] g_state.delay_state[c->channel_idx].buf_l;
        delete[] g_state.delay_state[c->channel_idx].buf_r;
        g_state.delay_state[c->channel_idx] = DelayChannelState{};
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
