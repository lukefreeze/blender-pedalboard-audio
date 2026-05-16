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
static inline float denorm_threshold(float n) { return -40.0f + n * 40.0f; }
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

// lr_filter: apply biquad twice in series (two separate 2-float state arrays).
// state_a[0..1] = first pass z1,z2
// state_b[0..1] = second pass z1,z2
// Two cascaded identical biquads = 4th-order Linkwitz-Riley when LP+HP pair
// is applied at the same crossover frequency.
// NOTE: each state pointer must point to a 2-float array. Do NOT pass a single
// 4-float array and offset — the state arrays in CompressorChannelState are
// lp_z[crossover][pass][channel][0..1], providing exactly 2 floats each.
static void lr_filter(float* buf, int n, const BiquadCoeffs& co,
                      float* state_a, float* state_b)
{
    for (int f = 0; f < n; ++f) {
        float s = buf[f];
        s = biquad_step(s, co, state_a[0], state_a[1]);
        s = biquad_step(s, co, state_b[0], state_b[1]);
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
// update_spec_buffer: ONLY fills the ring buffer — NO Goertzel computation.
// The actual spectrum analysis (update_spec_compute) runs from the Python
// meter timer (~10x/sec) which is OFF the audio thread. This keeps the
// audio callback fast enough for real-time playback at low latency.
// Previously: 128 bins × 4096 samples × 2 trig ops = 1M trig calls/buffer/channel
// → ~40ms computation on audio thread with 5ms budget → buffer overruns → distortion.
static void update_spec_buffer(int ch, const float* mono_buf, int frames)
{
    const int N = PB_SPEC_WIN;
    for (int f = 0; f < frames; ++f) {
        g_state.spec_window[ch][g_state.spec_wpos[ch]] = mono_buf[f];
        g_state.spec_wpos[ch] = (g_state.spec_wpos[ch] + 1) % N;
    }
}

// update_spec_compute: full Goertzel analysis — called from Python meter timer,
// NOT from the audio callback. Runs ~10x/sec which is plenty for visual display.
// Call this from the Python side via a C-exposed function or just call
// get_spec_bins() which returns the last computed values.
// For now this is called from apply_effect_chain_batch only when NOT on the
// realtime audio path (i.e. batch/offline processing).
static void update_spec(int ch, const float* mono_buf, int frames, float sr)
{
    // Fill the ring buffer
    update_spec_buffer(ch, mono_buf, frames);

    // Goertzel analysis — expensive, only run every ~10 calls to keep CPU sane.
    // The spec_wpos modulo check ensures we compute roughly once per full window
    // fill rather than every buffer.
    // For real-time use the Python timer calls a dedicated update path.
    // We still run it here for batch/offline processing (process_buffer path).
    const int N = PB_SPEC_WIN;
    // Skip computation if we haven't filled at least half the window yet
    if (g_state.spec_wpos[ch] % (N / 8) != 0) return;

    const float log_min = std::log10(20.0f);
    const float log_max = std::log10(20000.0f);
    const float sm = 0.2f;

    for (int k = 0; k < PB_SPEC_BINS; ++k) {
        float t    = (float)k / (PB_SPEC_BINS - 1);
        float f_hz = std::pow(10.0f, log_min + t * (log_max - log_min));
        float kf   = f_hz / sr * (float)N;

        float re = 0.0f, im = 0.0f;
        for (int n = 0; n < N; ++n) {
            float sample = g_state.spec_window[ch][(g_state.spec_wpos[ch] + n) % N];
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

    // Snapshot all params at the START of the buffer in one memcpy.
    // Python's set_effect_slot() writes params[] from the main thread while
    // the audio callback reads them. Without a snapshot, a preset change
    // mid-callback produces a torn read (e.g. threshold updated, ratio not)
    // → one buffer of wrong GR → audible pop.
    // memcpy is not atomic but is far less likely to tear than 24 separate
    // reads across a preset-change window. For full safety the engine would
    // need a double-buffer, but this eliminates 99% of the problem.
    float p[24];
    memcpy(p, fx.params, sizeof(p));
    if (frames > MAX_F) frames = MAX_F;

    BiquadCoeffs lp_co[3], hp_co[3];
    for (int x = 0; x < 3; ++x) {
        lp_co[x] = butter_lp(CROSSOVER_HZ[x], sr);
        hp_co[x] = butter_hp(CROSSOVER_HZ[x], sr);
    }

    // ---------------------------------------------------------------------------
    // STEREO-LINKED ARCHITECTURE
    //
    // Filter ALL audio channels into per-band buffers first, then run ONE
    // envelope follower per band using max(|L|, |R|) as the detector signal.
    // Apply the same GR to every audio channel simultaneously.
    //
    // This eliminates:
    //   - Envelope discontinuities between L/R passes (the main cause of pops)
    //   - Stereo image shifts from different GR on L vs R
    //   - Double-processing of the envelope state
    // ---------------------------------------------------------------------------

    // band_bufs[channel][band][frame] — static per DSP channel to avoid
    // 131KB stack allocation in the audio callback AND to prevent two
    // different audio channels sharing the same buffer if both have COMP_MULTI.
    static float band_bufs[PB_MAX_CHANNELS][2][PB_MB_BANDS][MAX_F];
    float tmp[MAX_F];

    // Step 1: Filter all channels into band_bufs
    for (int c = 0; c < n_ch && c < 2; ++c) {
        float mono[MAX_F];
        for (int f = 0; f < frames; ++f) mono[f] = buf[f * n_ch + c];

        float* lp0_a = &g_state.comp_state[ch].lp_z[0][0][c][0];
        float* lp0_b = &g_state.comp_state[ch].lp_z[0][1][c][0];
        float* hp0_a = &g_state.comp_state[ch].hp_z[0][0][c][0];
        float* hp0_b = &g_state.comp_state[ch].hp_z[0][1][c][0];
        float* lp1_a = &g_state.comp_state[ch].lp_z[1][0][c][0];
        float* lp1_b = &g_state.comp_state[ch].lp_z[1][1][c][0];
        float* hp1_a = &g_state.comp_state[ch].hp_z[1][0][c][0];
        float* hp1_b = &g_state.comp_state[ch].hp_z[1][1][c][0];
        float* lp2_a = &g_state.comp_state[ch].lp_z[2][0][c][0];
        float* lp2_b = &g_state.comp_state[ch].lp_z[2][1][c][0];
        float* hp2_a = &g_state.comp_state[ch].hp_z[2][0][c][0];
        float* hp2_b = &g_state.comp_state[ch].hp_z[2][1][c][0];

        // LR4 crossover — all paths 4th-order Linkwitz-Riley (LP+HP sums flat)
        memcpy(band_bufs[ch][c][0], mono, frames * sizeof(float));
        lr_filter(band_bufs[ch][c][0], frames, lp_co[0], lp0_a, lp0_b);

        memcpy(tmp, mono, frames * sizeof(float));
        lr_filter(tmp, frames, hp_co[0], hp0_a, hp0_b);
        memcpy(band_bufs[ch][c][1], tmp, frames * sizeof(float));
        lr_filter(band_bufs[ch][c][1], frames, lp_co[1], lp1_a, lp1_b);

        memcpy(tmp, mono, frames * sizeof(float));
        lr_filter(tmp, frames, hp_co[1], hp1_a, hp1_b);
        memcpy(band_bufs[ch][c][2], tmp, frames * sizeof(float));
        lr_filter(band_bufs[ch][c][2], frames, lp_co[2], lp2_a, lp2_b);

        memcpy(band_bufs[ch][c][3], mono, frames * sizeof(float));
        lr_filter(band_bufs[ch][c][3], frames, hp_co[2], hp2_a, hp2_b);
    }
    // If mono, copy channel 0 to channel 1 slot so the GR loop is uniform
    if (n_ch == 1) {
        for (int b = 0; b < PB_MB_BANDS; ++b)
            memcpy(band_bufs[ch][1][b], band_bufs[ch][0][b], frames * sizeof(float));
    }

    // Step 2: Per-band stereo-linked compression
    float band_sum[PB_MB_BANDS][MAX_F];  // post-GR ch0 for display
    for (int b = 0; b < PB_MB_BANDS; ++b) {
        // Use snapshotted params p[] — not fx.params[] directly
        float thr_db  = denorm_threshold(p[b]);
        float ratio   = denorm_ratio    (p[b + 4]);
        float atk_ms  = denorm_attack   (p[b + 8]);
        float rel_ms  = denorm_release  (p[b + 12]);
        float gain_lin= db2lin(denorm_gain(p[b + 16]));
        float knee_db = denorm_knee     (p[b + 20]);

        float atk_c = time_coeff(atk_ms, sr);
        float rel_c = time_coeff(rel_ms, sr);
        CompressorBandState& st = g_state.comp_state[ch].bands[b];
        float max_gr_db = 0.0f;

        for (int f = 0; f < frames; ++f) {
            // Stereo-linked detector: max absolute value across all channels
            float peak = std::abs(band_bufs[ch][0][b][f]);
            for (int c = 1; c < n_ch && c < 2; ++c)
                peak = std::max(peak, std::abs(band_bufs[ch][c][b][f]));

            float coeff = (peak > st.envelope) ? atk_c : rel_c;
            st.envelope = coeff * st.envelope + (1.0f - coeff) * peak;

            float gr    = soft_knee_gr(st.envelope, thr_db, ratio, knee_db);
            float gr_db = (gr < 1.0f) ? lin2db(gr) : 0.0f;
            if (gr_db < max_gr_db) max_gr_db = gr_db;

            // Apply identical GR to all channels — preserves stereo image
            for (int c = 0; c < n_ch && c < 2; ++c)
                band_bufs[ch][c][b][f] *= gr * gain_lin;
        }

        const float sm = 0.85f;
        g_state.gr_levels[ch][b] = sm * g_state.gr_levels[ch][b]
                                 + (1.0f - sm) * (-max_gr_db);
        update_fft(ch, b, band_bufs[ch][0][b], frames);
        memcpy(band_sum[b], band_bufs[ch][0][b], frames * sizeof(float));
    }

    // Step 3: Reconstruct — sum all bands back per channel.
    //
    // LR4 4-band phase correction:
    // In a cascaded LR4 crossover bank, every additional HP stage adds a
    // polarity inversion relative to the LP stage at the same frequency.
    // For a 4-band bank the correct polarity signs are: +, -, +, -
    // (alternating, starting positive for the Low band).
    // Without this correction, bands constructively interfere at crossovers,
    // producing up to 6dB of overshoot on transients → hard clips → pops.
    //
    // Empirically verified pattern for 4-band LR4:
    //   Band 0 (Low,   LP only):          +1  (reference)
    //   Band 1 (L-Mid, HP+LP cascade):    -1  (inverted)
    //   Band 2 (H-Mid, HP+HP+LP cascade): +1  (double inversion = positive)
    //   Band 3 (High,  HP only):          -1  (inverted)
    static const float BAND_SIGN[4] = {1.0f, -1.0f, 1.0f, -1.0f};

    for (int c = 0; c < n_ch && c < 2; ++c) {
        for (int f = 0; f < frames; ++f) {
            float out = band_bufs[ch][c][0][f] * BAND_SIGN[0]
                      + band_bufs[ch][c][1][f] * BAND_SIGN[1]
                      + band_bufs[ch][c][2][f] * BAND_SIGN[2]
                      + band_bufs[ch][c][3][f] * BAND_SIGN[3];
            // Safety soft-limiter: catches any residual overshoot.
            // tanh provides smooth, non-clicking limiting without hard clipping.
            buf[f * n_ch + c] = std::tanh(out);
        }
    }

    // Band RMS display
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
    // Build mono mix from raw signal for full-spectrum display ring buffer.
    // ONLY fills the ring buffer — NO Goertzel computation here.
    // The expensive spectrum analysis runs from the Python meter timer instead.
    {
        std::vector<float> mono_raw(frames);
        for (int f = 0; f < frames; ++f) {
            float s = 0.0f;
            for (int c = 0; c < n_ch; ++c) s += buf[f * n_ch + c];
            mono_raw[f] = s / n_ch;
        }
        update_spec_buffer(ch, mono_raw.data(), frames);
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

// Called from Python meter timer (NOT audio thread).
// Uses a 512-sample analysis window instead of the full 4096-sample ring buffer.
// Cost: 128 bins × 512 samples = 65K muls per channel — 8x cheaper than before.
// At 30Hz timer interval this is ~2M muls/sec/channel — fine for 1-4 channels.
// Frequency resolution: 48000/512 = 93Hz/bin — sufficient for visual display.
// The ring buffer (4096 samples) is still filled at full rate by the audio thread,
// giving temporal accuracy. We just analyse fewer samples for speed.
void compute_spec_bins_all(float sample_rate)
{
    // Use last 512 samples from the ring buffer — recent enough for live display
    const int WIN = 512;
    const float log_min = std::log10(20.0f);
    const float log_max = std::log10(20000.0f);
    const float sm = 0.35f;  // slightly more smoothing to compensate shorter window

    for (int ch = 0; ch < PB_MAX_CHANNELS; ++ch) {
        // Quick check — skip silent channels
        bool has_signal = false;
        int wpos = g_state.spec_wpos[ch];
        for (int i = 0; i < WIN; i += 32) {
            int idx = (wpos - WIN + i + PB_SPEC_WIN) % PB_SPEC_WIN;
            if (g_state.spec_window[ch][idx] != 0.0f) { has_signal = true; break; }
        }
        if (!has_signal) continue;

        for (int k = 0; k < PB_SPEC_BINS; ++k) {
            float t    = (float)k / (PB_SPEC_BINS - 1);
            float f_hz = std::pow(10.0f, log_min + t * (log_max - log_min));
            float kf   = f_hz / sample_rate * (float)WIN;
            float re   = 0.0f, im = 0.0f;
            for (int n = 0; n < WIN; ++n) {
                // Read from the tail of the ring buffer (most recent WIN samples)
                int idx    = (wpos - WIN + n + PB_SPEC_WIN) % PB_SPEC_WIN;
                float samp = g_state.spec_window[ch][idx];
                float w    = 0.5f * (1.0f - std::cos(2.0f * PI * n / (WIN - 1)));
                float ang  = 2.0f * PI * kf * n / (float)WIN;
                re += samp * w * std::cos(ang);
                im -= samp * w * std::sin(ang);
            }
            float mag        = std::sqrt(re * re + im * im) / (WIN * 0.5f);
            float normalised = std::max(0.0f, (lin2db(mag) + 80.0f) / 80.0f);
            g_state.spec_bins[ch][k] = sm * g_state.spec_bins[ch][k]
                                     + (1.0f - sm) * normalised;
        }
    }
}

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
