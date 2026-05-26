// =============================================================================
// hijacker_audio_engine.cpp
// The Hijacker — Real-time multi-channel audio engine
//
// Audio thread runs a PortAudio callback that:
//   1. Advances each channel's playhead through its segment playlist
//   2. Reads PCM frames from the WAV file for each channel
//   3. Applies the DSP effect chain (EQ → comp → reverb → gate → delay)
//   4. Mixes all channels into the output buffer
//   5. Updates metering
//
// Python communicates via atomic writes — no locks in the hot path.
// Playlist changes use a mutex but only happen at play-start, not mid-buffer.
// =============================================================================

#include "hijacker_audio_engine.h"
#include <portaudio.h>
#include <cstring>
#include <cmath>
#include <cstdio>
#include <algorithm>
#include <vector>

// g_state is defined in wrapper.cpp — shared with hijacker_processor.cpp
extern "C" { extern EngineState g_state; }

HijackerEngine* g_hijacker_engine = nullptr;

EngineState* HijackerEngine::get_state() { return &g_state; }

// ---------------------------------------------------------------------------
// PortAudio static callback — routes to engine instance
// ---------------------------------------------------------------------------
static int _pa_callback(const void* input, void* output,
                         unsigned long frames,
                         const PaStreamCallbackTimeInfo* time_info,
                         PaStreamCallbackFlags flags,
                         void* user_data)
{
    HijackerEngine* eng = static_cast<HijackerEngine*>(user_data);
    return eng->audio_callback(input, output, frames);
}

// ---------------------------------------------------------------------------
// HijackerEngine — lifecycle
// ---------------------------------------------------------------------------
HijackerEngine::HijackerEngine() {}

HijackerEngine::~HijackerEngine() {
    shutdown();
}

bool HijackerEngine::init(int sample_rate, int buffer_frames) {
    if (initialized_) return true;

    sample_rate_   = sample_rate;
    buffer_frames_ = buffer_frames;

    PaError err = Pa_Initialize();
    if (err != paNoError) {
        printf("[HIJACKER] PortAudio init failed: %s\n", Pa_GetErrorText(err));
        return false;
    }

    // Print all available host APIs for diagnostics
    int n_apis = Pa_GetHostApiCount();
    printf("[HIJACKER] Available host APIs:\n");
    PaHostApiIndex wmme_idx   = -1;
    PaHostApiIndex wasapi_idx = -1;
    for (int i = 0; i < n_apis; ++i) {
        const PaHostApiInfo* api = Pa_GetHostApiInfo(i);
        printf("[HIJACKER]   [%d] %s (default device: %d)\n",
               i, api->name, api->defaultOutputDevice);
        if (api->type == paMME)    wmme_idx   = i;
        if (api->type == paWASAPI) wasapi_idx = i;
    }

    // Prefer WASAPI on Windows — natively supports float32 output.
    // WMME may reinterpret float32 bytes as int16 causing distortion.
    PaDeviceIndex dev_idx = Pa_GetDefaultOutputDevice();
    const char* api_name  = "default";
    if (wasapi_idx >= 0) {
        const PaHostApiInfo* wasapi = Pa_GetHostApiInfo(wasapi_idx);
        if (wasapi->defaultOutputDevice != paNoDevice) {
            dev_idx  = wasapi->defaultOutputDevice;
            api_name = "WASAPI";
            printf("[HIJACKER] Using WASAPI output device\n");
        }
    } else if (wmme_idx >= 0) {
        const PaHostApiInfo* wmme = Pa_GetHostApiInfo(wmme_idx);
        if (wmme->defaultOutputDevice != paNoDevice) {
            dev_idx  = wmme->defaultOutputDevice;
            api_name = "WMME";
            printf("[HIJACKER] Using WMME output device\n");
        }
    }

    PaStreamParameters out_params;
    out_params.device                    = dev_idx;
    out_params.channelCount              = 2;
    out_params.sampleFormat              = paFloat32;
    out_params.suggestedLatency          =
        Pa_GetDeviceInfo(dev_idx)->defaultHighOutputLatency;
    out_params.hostApiSpecificStreamInfo = nullptr;

    const PaDeviceInfo* dev_info = Pa_GetDeviceInfo(dev_idx);
    double device_sr = dev_info->defaultSampleRate;
    printf("[HIJACKER] Device default SR: %.0fHz, low latency: %.3fs, high: %.3fs\n",
           device_sr,
           dev_info->defaultLowOutputLatency,
           dev_info->defaultHighOutputLatency);

    if (Pa_IsFormatSupported(nullptr, &out_params, (double)sample_rate_)
        == paFormatIsSupported) {
        printf("[HIJACKER] float32 @ %dHz supported\n", sample_rate_);
    } else {
        sample_rate_ = (int)device_sr;
        printf("[HIJACKER] using device native rate %dHz\n", sample_rate_);
    }
    printf("[HIJACKER] opening: %s via %s @ %dHz\n",
           dev_info->name, api_name, sample_rate_);

    err = Pa_OpenStream(
        (PaStream**)&pa_stream_,
        nullptr,
        &out_params,
        sample_rate_,
        paFramesPerBufferUnspecified,  // let PortAudio choose buffer size
        paClipOff,
        _pa_callback,
        this
    );

    if (err != paNoError) {
        printf("[HIJACKER] Pa_OpenStream failed: %s\n", Pa_GetErrorText(err));
        Pa_Terminate();
        return false;
    }

    err = Pa_StartStream((PaStream*)pa_stream_);
    if (err != paNoError) {
        printf("[HIJACKER] Pa_StartStream failed: %s\n", Pa_GetErrorText(err));
        Pa_CloseStream((PaStream*)pa_stream_);
        Pa_Terminate();
        return false;
    }

    initialized_ = true;
    printf("[HIJACKER] Audio engine started — %dHz %d-frame buffer\n",
           sample_rate_, buffer_frames_);
    return true;
}

void HijackerEngine::shutdown() {
    if (!initialized_) return;
    stop();
    if (pa_stream_) {
        Pa_StopStream((PaStream*)pa_stream_);
        Pa_CloseStream((PaStream*)pa_stream_);
        pa_stream_ = nullptr;
    }
    Pa_Terminate();
    for (int i = 0; i < PB_MAX_CHANNELS; ++i)
        _close_channel_file(i);
    initialized_ = false;
    printf("[HIJACKER] Audio engine shut down\n");
}

bool HijackerEngine::is_running() const {
    return initialized_ && pa_stream_ &&
           Pa_IsStreamActive((PaStream*)pa_stream_) == 1;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------
void HijackerEngine::play(double timeline_pos_s) {
    _apply_seek(timeline_pos_s);
    transport_.playing.store(true);
    printf("[HIJACKER] play from %.3fs\n", timeline_pos_s);
}

void HijackerEngine::stop() {
    transport_.playing.store(false);
    printf("[HIJACKER] stopped at %.3fs\n", playhead_s_);
}

void HijackerEngine::seek(double timeline_pos_s) {
    transport_.seek_target_s.store(timeline_pos_s);
    transport_.seek_pending.store(true);
    printf("[HIJACKER] seek to %.3fs\n", timeline_pos_s);
}

void HijackerEngine::_apply_seek(double target_s) {
    std::lock_guard<std::mutex> lock(playlist_mutex_);
    playhead_s_ = target_s;

    for (int ch = 0; ch < PB_MAX_CHANNELS; ++ch) {
        if (!channels_[ch].active.load()) continue;

        // Find which segment covers this timeline position
        int seg_idx = -1;
        double seek_within = 0.0;
        for (int si = 0; si < channels_[ch].n_segments; ++si) {
            const HijackerSegment& seg = channels_[ch].segments[si];
            double seg_end = seg.timeline_pos_s + seg.duration_s;
            if (target_s >= seg.timeline_pos_s && target_s < seg_end) {
                seg_idx      = si;
                seek_within  = target_s - seg.timeline_pos_s;
                break;
            }
        }

        if (seg_idx >= 0) {
            _open_segment(ch, seg_idx, seek_within);
        } else {
            _close_channel_file(ch);
            channels_[ch].cur_segment = -1;
        }

        // Flush biquad filter state after every seek.
        // The crossover filters (lp_z, hp_z) hold memory from the previous
        // playback position. After a seek the new audio starts at a different
        // point but the filters still contain old values — the first 1-2 buffers
        // produce a settling transient that sounds like a click/pop.
        // Zeroing the state here gives clean filter initialisation from silence.
        memset(g_state.comp_state[ch].lp_z, 0, sizeof(g_state.comp_state[ch].lp_z));
        memset(g_state.comp_state[ch].hp_z, 0, sizeof(g_state.comp_state[ch].hp_z));
        // Also zero the compressor envelopes — avoids a burst of GR from
        // a high envelope value left over from before the seek.
        for (int b = 0; b < PB_MB_BANDS; ++b) {
            g_state.comp_state[ch].bands[b].envelope  = 0.0f;
            g_state.comp_state[ch].bands[b].gr_smooth = 0.0f;
        }
        g_state.comp_state[ch].single.envelope  = 0.0f;
        g_state.comp_state[ch].single.gr_smooth = 0.0f;
    }
}

// ---------------------------------------------------------------------------
// Channel playlist management
// ---------------------------------------------------------------------------
void HijackerEngine::set_channel_playlist(int ch,
    const std::vector<HijackerSegment>& segs)
{
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    std::lock_guard<std::mutex> lock(playlist_mutex_);

    _close_channel_file(ch);
    channels_[ch].n_segments = 0;

    int n = std::min((int)segs.size(), HJ_MAX_SEGMENTS);
    for (int i = 0; i < n; ++i)
        channels_[ch].segments[i] = segs[i];
    channels_[ch].n_segments = n;
    channels_[ch].cur_segment = -1;
    channels_[ch].active.store(n > 0);
    printf("[HIJACKER] ch%d playlist: %d segments\n", ch + 1, n);
}

void HijackerEngine::clear_channel(int ch) {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    std::lock_guard<std::mutex> lock(playlist_mutex_);
    _close_channel_file(ch);
    channels_[ch].n_segments  = 0;
    channels_[ch].cur_segment = -1;
    channels_[ch].active.store(false);
    // Reset meters so deleted strips don't leave stale readings that
    // keep the VU bar and rack waveforms animating after removal.
    channels_[ch].meter_rms.store(0.0f);
    channels_[ch].meter_peak.store(0.0f);
    g_state.meter_levels[ch] = 0.0f;
}

void HijackerEngine::clear_all_channels() {
    for (int i = 0; i < PB_MAX_CHANNELS; ++i)
        clear_channel(i);
}

// ---------------------------------------------------------------------------
// Mixer params
// ---------------------------------------------------------------------------
void HijackerEngine::set_volume(int ch, float vol) {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    channels_[ch].volume.store(vol);
}

void HijackerEngine::set_mute(int ch, bool muted) {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    channels_[ch].muted.store(muted);
    // No seek needed on unmute — muted channels still advance file_pos_s
    // in the audio callback so they stay in perfect sync at all times.
}

void HijackerEngine::set_solo(int ch, bool soloed) {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    channels_[ch].soloed.store(soloed);
}

void HijackerEngine::set_pan(int ch, float pan) {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    channels_[ch].pan.store(std::max(0.0f, std::min(1.0f, pan)));
}

// ---------------------------------------------------------------------------
// Effect slots — delegate to existing EngineState (reuses all DSP code)
// ---------------------------------------------------------------------------
void HijackerEngine::set_effect_slot(int ch, int slot, int type,
                                      const std::vector<float>& params) {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    if (slot < 0 || slot >= PB_MAX_EFFECTS) return;
    g_state.effect_chain[ch][slot].type    = (EffectType)type;
    g_state.effect_chain[ch][slot].enabled = (type != 0);
    int n = std::min((int)params.size(), 24);
    for (int i = 0; i < n; ++i)
        g_state.effect_chain[ch][slot].params[i] = params[i];
}

void HijackerEngine::clear_effect_slot(int ch, int slot) {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return;
    if (slot < 0 || slot >= PB_MAX_EFFECTS) return;
    g_state.effect_chain[ch][slot].type    = EffectType::NONE;
    g_state.effect_chain[ch][slot].enabled = false;
}

// ---------------------------------------------------------------------------
// Metering
// ---------------------------------------------------------------------------
float  HijackerEngine::get_meter_rms(int ch)  const {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return 0.0f;
    return channels_[ch].meter_rms.load();
}
float  HijackerEngine::get_meter_peak(int ch) const {
    if (ch < 0 || ch >= PB_MAX_CHANNELS) return 0.0f;
    return channels_[ch].meter_peak.load();
}
double HijackerEngine::get_playhead_s() const {
    return playhead_s_;
}

// ---------------------------------------------------------------------------
// WAV file helpers
// ---------------------------------------------------------------------------
void HijackerEngine::_close_channel_file(int ch) {
    if (channels_[ch].file_handle) {
        fclose(channels_[ch].file_handle);
        channels_[ch].file_handle = nullptr;
    }
    channels_[ch].file_pos_s = 0.0;
}

bool HijackerEngine::_read_wav_header(int ch) {
    FILE* f = channels_[ch].file_handle;
    if (!f) return false;

    fseek(f, 0, SEEK_SET);

    // RIFF header
    char riff[4]; fread(riff, 1, 4, f);
    if (memcmp(riff, "RIFF", 4) != 0) return false;
    fseek(f, 4, SEEK_CUR);  // skip file size
    char wave[4]; fread(wave, 1, 4, f);
    if (memcmp(wave, "WAVE", 4) != 0) return false;

    // Walk chunks
    int      nch = 2;
    int      sr  = 44100;
    int      bps = 16;
    long     data_start = 0;
    long     data_size  = 0;
    bool     got_fmt    = false;

    while (true) {
        char id[4] = {};
        uint32_t sz = 0;
        if (fread(id, 1, 4, f) != 4) break;
        if (fread(&sz, 4, 1, f) != 1) break;

        long chunk_start = ftell(f);

        if (memcmp(id, "fmt ", 4) == 0 && sz >= 16) {
            uint16_t fmt_tag, ch16;
            uint32_t sr32, byte_rate;
            uint16_t block_align, bps16;
            fread(&fmt_tag,     2, 1, f);
            fread(&ch16,        2, 1, f);
            fread(&sr32,        4, 1, f);
            fread(&byte_rate,   4, 1, f);
            fread(&block_align, 2, 1, f);
            fread(&bps16,       2, 1, f);
            nch = (int)ch16;
            sr  = (int)sr32;
            bps = (int)bps16;
            got_fmt = true;
            // Skip remainder of fmt chunk (handles 16, 18, 40 byte variants)
            fseek(f, chunk_start + (long)sz, SEEK_SET);
        } else if (memcmp(id, "data", 4) == 0) {
            data_start = ftell(f);
            data_size  = (long)sz;
            break;
        } else {
            // Skip unknown chunk — ensure even alignment
            long skip = (long)sz + ((long)sz & 1);
            fseek(f, chunk_start + skip, SEEK_SET);
        }
    }

    if (!got_fmt || data_start == 0) {
        printf("[HIJACKER] WAV parse failed: got_fmt=%d data_start=%ld\n",
               got_fmt, data_start);
        return false;
    }

    // Support 16-bit PCM (fmt_tag=1), 24-bit PCM (fmt_tag=1),
    // and 32-bit IEEE float (fmt_tag=3). Reject anything else.
    // fmt_tag is stored in the lower byte of the first fmt field (already
    // read as fmt_tag above — we need to re-read it from the local below).
    // NOTE: fmt_tag was read into a local var in the fmt chunk block above.
    // We stash it via bps/nch already, but need to store fmt_tag too.
    // Rather than restructure, we detect float WAV by bps==32 and
    // re-read the fmt_tag from file position (simpler: store it locally).
    if (bps != 16 && bps != 24 && bps != 32) {
        printf("[HIJACKER] WAV bps=%d not supported\n", bps);
        return false;
    }

    int bytes_per_sample = bps / 8;
    channels_[ch].file_sr         = sr;
    channels_[ch].file_nch        = nch;
    channels_[ch].file_bps        = bps;
    channels_[ch].file_data_start = data_start;
    channels_[ch].file_n_frames   = data_size / (nch * bytes_per_sample);

    printf("[HIJACKER] WAV: %dHz %dch %d-bit, %ld frames, data@%ld\n",
           sr, nch, bps, channels_[ch].file_n_frames, data_start);
    return true;
}

void HijackerEngine::_open_segment(int ch, int seg_idx,
                                    double seek_within_seg_s) {
    _close_channel_file(ch);

    const HijackerSegment& seg = channels_[ch].segments[seg_idx];
    channels_[ch].cur_segment = seg_idx;

    FILE* f = fopen(seg.filepath, "rb");
    if (!f) {
        printf("[HIJACKER] ch%d: can't open %s\n", ch+1, seg.filepath);
        return;
    }
    channels_[ch].file_handle = f;

    if (!_read_wav_header(ch)) {
        printf("[HIJACKER] ch%d: bad WAV header %s\n", ch+1, seg.filepath);
        _close_channel_file(ch);
        return;
    }

    // Seek to correct position in file
    double file_pos = seg.file_offset_s + seek_within_seg_s;
    channels_[ch].file_pos_s = file_pos;

    int sr  = channels_[ch].file_sr;
    int nch = channels_[ch].file_nch;
    int bps = channels_[ch].file_bps;
    int bytes_per_sample = bps / 8;
    long frame = (long)(file_pos * sr);
    frame = std::max(0L, std::min(frame, channels_[ch].file_n_frames - 1));
    long byte_offset = channels_[ch].file_data_start + frame * nch * bytes_per_sample;
    fseek(f, byte_offset, SEEK_SET);
}

int HijackerEngine::_read_pcm_frames(int ch, float* buf, int n_frames) {
    FILE* f = channels_[ch].file_handle;
    if (!f) return 0;

    int file_sr  = channels_[ch].file_sr;
    int nch      = channels_[ch].file_nch;
    int bps      = channels_[ch].file_bps;
    int bps_size = bps / 8;   // bytes per sample

    // Helper lambda: read raw bytes into a float scratch buffer.
    // Handles 16-bit PCM, 24-bit PCM, 32-bit IEEE float.
    // Returns number of frames read.
    auto read_raw_to_float = [&](float* dst, int want_frames) -> int {
        int samples = want_frames * nch;
        // Raw byte buffer — 4 bytes per sample, max src_needed*nch samples
        // src_needed is capped at HJ_BUFFER_FRAMES+2, nch<=8 worst case
        uint8_t raw[(HJ_BUFFER_FRAMES + 4) * 8 * 4];
        size_t bytes_read = fread(raw, bps_size, samples, f);
        int frames_read = (int)(bytes_read / nch);

        for (int i = 0; i < frames_read * nch; ++i) {
            float s = 0.0f;
            if (bps == 16) {
                int16_t v;
                memcpy(&v, raw + i * 2, 2);
                s = v / 32768.0f;
            } else if (bps == 24) {
                // 24-bit little-endian signed — sign-extend to 32 bits
                int32_t v = (int32_t)(((uint32_t)raw[i*3 + 2] << 24) |
                                      ((uint32_t)raw[i*3 + 1] << 16) |
                                      ((uint32_t)raw[i*3 + 0] << 8)) >> 8;
                s = v / 8388608.0f;
            } else {
                // 32-bit IEEE float
                memcpy(&s, raw + i * 4, 4);
            }
            // Expand mono to stereo inline if needed
            if (nch == 1) {
                dst[i * 2]     = s;
                dst[i * 2 + 1] = s;
            } else {
                dst[i] = s;
            }
        }
        return frames_read;
    };

    // Direct path — no resampling needed
    if (file_sr == sample_rate_) {
        int to_read = std::min(n_frames, HJ_BUFFER_FRAMES);
        int frames_read = read_raw_to_float(buf, to_read);
        channels_[ch].file_pos_s += (double)frames_read / file_sr;
        return frames_read;
    }

    // Sample rate conversion path — linear interpolation resampler
    double ratio   = (double)file_sr / (double)sample_rate_;
    int src_needed = (int)(n_frames * ratio) + 2;
    src_needed     = std::min(src_needed, HJ_BUFFER_FRAMES);

    float src[HJ_BUFFER_FRAMES * 2] = {};
    int src_frames = read_raw_to_float(src, src_needed);
    if (src_frames < 2) return 0;

    // Linear interpolate to output rate
    int frames_out = 0;
    for (int i = 0; i < n_frames; ++i) {
        double src_pos = i * ratio;
        int    si      = (int)src_pos;
        float  frac    = (float)(src_pos - si);
        if (si + 1 >= src_frames) break;
        buf[i * 2]     = src[si*2]     + frac * (src[(si+1)*2]     - src[si*2]);
        buf[i * 2 + 1] = src[si*2 + 1] + frac * (src[(si+1)*2 + 1] - src[si*2 + 1]);
        frames_out++;
    }

    channels_[ch].file_pos_s += (double)src_frames / file_sr;
    return frames_out;
}

// ---------------------------------------------------------------------------
// Audio callback — THE HOT PATH
// Called by PortAudio every ~5-10ms. Must be fast and lock-free.
// ---------------------------------------------------------------------------
int HijackerEngine::audio_callback(const void* /*input*/, void* output,
                                    unsigned long frames_per_buffer)
{
    float* out = static_cast<float*>(output);
    unsigned long n_out = frames_per_buffer * 2;
    memset(out, 0, n_out * sizeof(float));

    // DEBUG sine test
    static double sine_phase = 0.0;
    bool debug_sine = false;
    if (debug_sine) {
        for (unsigned long i = 0; i < frames_per_buffer; ++i) {
            float s = (float)(0.3 * sin(sine_phase));
            out[i*2] = s; out[i*2+1] = s;
            sine_phase += 2.0 * 3.14159265 * 440.0 / sample_rate_;
        }
        return paContinue;
    }

    if (!transport_.playing.load()) return paContinue;

    // Handle pending seek
    if (transport_.seek_pending.load()) {
        double target = transport_.seek_target_s.load();
        transport_.seek_pending.store(false);
        _apply_seek(target);
    }

    // Check solo state
    any_soloed_ = false;
    for (int ch = 0; ch < PB_MAX_CHANNELS; ++ch)
        if (channels_[ch].active.load() && channels_[ch].soloed.load())
            { any_soloed_ = true; break; }

    double dt = (double)frames_per_buffer / sample_rate_;

    // Per-channel mixing
    for (int ch = 0; ch < PB_MAX_CHANNELS; ++ch) {
        if (!channels_[ch].active.load()) continue;

        bool is_muted  = channels_[ch].muted.load();
        bool is_silent = any_soloed_ && !channels_[ch].soloed.load();
        float vol      = channels_[ch].volume.load();

        // Per-channel buffer
        std::vector<float> ch_buf(frames_per_buffer * 2, 0.0f);

        // Always read file data — even when muted or silenced by solo.
        // This advances file_pos_s in lockstep with the playhead so that
        // unmuting is perfectly in sync with no drift, no seek, no click.
        {
            int frames_needed = (int)frames_per_buffer;
            int buf_offset    = 0;

            while (frames_needed > 0) {
                int cur_seg = channels_[ch].cur_segment;

                // cur_segment == -1 means no file is open yet (either never
                // opened, or playhead was before all segments at seek time).
                // Scan forward to find a segment the playhead has reached.
                if (cur_seg < 0) {
                    int found = -1;
                    for (int si = 0; si < channels_[ch].n_segments; ++si) {
                        const HijackerSegment& s = channels_[ch].segments[si];
                        double seg_end = s.timeline_pos_s + s.duration_s;
                        if (playhead_s_ >= s.timeline_pos_s && playhead_s_ < seg_end) {
                            found = si;
                            break;
                        }
                    }
                    if (found >= 0) {
                        double seek_within = playhead_s_ - channels_[ch].segments[found].timeline_pos_s;
                        _open_segment(ch, found, seek_within);
                    } else {
                        break;  // playhead not yet at any segment
                    }
                    continue;   // re-check cur_segment after opening
                }

                if (cur_seg >= channels_[ch].n_segments) break;

                const HijackerSegment& seg = channels_[ch].segments[cur_seg];
                double seg_timeline_end = seg.timeline_pos_s + seg.duration_s;

                if (playhead_s_ < seg.timeline_pos_s) break;

                if (playhead_s_ >= seg_timeline_end) {
                    int next_seg = cur_seg + 1;
                    if (next_seg < channels_[ch].n_segments)
                        _open_segment(ch, next_seg, 0.0);
                    else {
                        _close_channel_file(ch);
                        channels_[ch].cur_segment = -1;
                    }
                    break;
                }

                int got = _read_pcm_frames(ch, ch_buf.data() + buf_offset * 2,
                                            frames_needed);
                if (got == 0) {
                    int next_seg = cur_seg + 1;
                    if (next_seg < channels_[ch].n_segments)
                        _open_segment(ch, next_seg, 0.0);
                    else
                        channels_[ch].cur_segment = -1;
                    break;
                }
                buf_offset    += got;
                frames_needed -= got;
            }
        }

        // Skip DSP and output mix for muted/silent/silent-by-solo channels
        if (is_muted || is_silent || vol < 0.0001f) {
            channels_[ch].meter_rms.store(0.0f);
            channels_[ch].meter_peak.store(0.0f);
            g_state.meter_levels[ch] = 0.0f;
            continue;
        }

        // Apply volume PRE-DSP so the compressor sees the correct level.
        // Use the current segment's volume (strip.volume baked in at load time)
        // combined with the channel fader volume. This means two strips on the
        // same channel with different strip.volumes will compress differently,
        // matching what the user set on each strip in the VSE.
        float seg_vol = 1.0f;
        {
            int cur = channels_[ch].cur_segment;
            if (cur >= 0 && cur < channels_[ch].n_segments)
                seg_vol = channels_[ch].segments[cur].volume;
        }
        float total_vol = vol * seg_vol;
        if (fabsf(total_vol - 1.0f) > 0.0001f) {
            for (int i = 0; i < (int)frames_per_buffer * 2; ++i)
                ch_buf[i] *= total_vol;
        }

        // Apply DSP effect chain
        _process_channel_buffer(ch, ch_buf.data(), (int)frames_per_buffer, 2,
                                 (float)sample_rate_);

        // Metering — post-fader, post-DSP
        float rms_sum = 0.0f, peak = 0.0f;
        for (int i = 0; i < (int)frames_per_buffer * 2; ++i) {
            float s = ch_buf[i];
            rms_sum += s * s;
            if (s < 0) s = -s;
            if (s > peak) peak = s;
        }
        float rms = sqrtf(rms_sum / (frames_per_buffer * 2));
        channels_[ch].meter_rms.store(rms);
        channels_[ch].meter_peak.store(peak);
        g_state.meter_levels[ch] = rms;

        // Apply pan
        float pan_val = channels_[ch].pan.load();
        if (fabsf(pan_val - 0.5f) > 0.01f) {
            float angle  = pan_val * 1.5707963f;
            float gain_l = cosf(angle);
            float gain_r = sinf(angle);
            for (int i = 0; i < (int)frames_per_buffer; ++i) {
                ch_buf[i * 2]     *= gain_l;
                ch_buf[i * 2 + 1] *= gain_r;
            }
        }

        // Mix into output
        for (int i = 0; i < (int)frames_per_buffer * 2; ++i)
            out[i] += ch_buf[i];
    }

    // Soft limiter on final output — only activates above 0.95 to catch
    // true overs from loud multi-channel mixes. Transparent at normal levels.
    // Uses cubic soft-clip: y = x - x³/3 mapped to ±1.0 range.
    for (unsigned long i = 0; i < frames_per_buffer * 2; ++i) {
        float s = out[i];
        if (s > 0.95f) {
            float over = s - 0.95f;
            out[i] = 0.95f + over / (1.0f + over);   // asymptotes to 1.0
        } else if (s < -0.95f) {
            float over = -s - 0.95f;
            out[i] = -(0.95f + over / (1.0f + over));
        }
    }

    // Advance master playhead
    playhead_s_ += dt;
    transport_.playhead_s.store(playhead_s_);

    return paContinue;
}

void HijackerEngine::_process_channel_buffer(int ch, float* buf,
                                              int frames, int n_ch, float sr)
{
    // Only apply DSP if at least one effect slot is enabled
    // This avoids apply_effect_chain_batch corrupting clean audio
    // when no effects are assigned
    bool has_effects = false;
    for (int s = 0; s < PB_MAX_EFFECTS; ++s) {
        if (g_state.effect_chain[ch][s].enabled) {
            has_effects = true;
            break;
        }
    }
    if (!has_effects) return;

    apply_effect_chain_batch(ch, buf, frames, n_ch, sr);
}
