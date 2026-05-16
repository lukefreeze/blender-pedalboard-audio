#pragma once
// =============================================================================
// hijacker_audio_engine.h
// The Hijacker — Real-time multi-channel audio engine for Blender
//
// Architecture:
//   - PortAudio callback owns the audio thread
//   - All channels start from the exact same sample (true atomic sync)
//   - DSP runs in the audio thread (EQ, comp, reverb, gate, delay)
//   - Python communicates via thread-safe atomic parameter writes
//   - No rebuilds needed for parameter changes — takes effect next buffer
// =============================================================================

#include "hijacker_processor.h"
#include <atomic>
#include <mutex>
#include <string>
#include <vector>

// Maximum segments per channel (strips in the VSE)
#define HJ_MAX_SEGMENTS  64
// Audio buffer size in frames
#define HJ_BUFFER_FRAMES 512

// ---------------------------------------------------------------------------
// Segment — one VSE sound strip mapped to file + timeline positions
// ---------------------------------------------------------------------------
struct HijackerSegment {
    char   filepath[512]  = {};   // absolute path to decoded WAV
    double file_offset_s  = 0.0;  // where in the file audio starts
    double duration_s     = 0.0;  // how long this segment plays
    double timeline_pos_s = 0.0;  // when it starts on the timeline
};

// ---------------------------------------------------------------------------
// ChannelPlaylist — all segments for one VSE channel
// ---------------------------------------------------------------------------
struct HijackerChannel {
    HijackerSegment segments[HJ_MAX_SEGMENTS];
    int    n_segments      = 0;

    // Playback state (written by audio thread only)
    int    cur_segment     = -1;
    double file_pos_s      = 0.0;  // current read position in current segment's file

    // File reader state (one open file at a time per channel)
    FILE*  file_handle     = nullptr;
    int    file_sr         = 44100;
    int    file_nch        = 2;
    long   file_data_start = 0;    // byte offset of PCM data in WAV
    long   file_n_frames   = 0;    // total frames in file

    // Real-time mixer params (written by Python, read by audio thread)
    std::atomic<float> volume   {1.0f};
    std::atomic<bool>  muted    {false};
    std::atomic<bool>  soloed   {false};
    std::atomic<float> pan      {0.5f};  // 0=full left, 0.5=centre, 1=full right
    std::atomic<bool>  active   {false};  // has a playlist loaded

    // Per-channel meter (written by audio thread, read by Python)
    std::atomic<float> meter_rms  {0.0f};
    std::atomic<float> meter_peak {0.0f};
};

// ---------------------------------------------------------------------------
// Transport state (written by Python, read by audio thread)
// ---------------------------------------------------------------------------
struct HijackerTransport {
    std::atomic<bool>   playing       {false};
    std::atomic<double> playhead_s    {0.0};   // current timeline position
    std::atomic<double> seek_target_s {-1.0};  // -1 = no pending seek
    std::atomic<bool>   seek_pending  {false};
    std::atomic<int>    sample_rate   {44100};
};

// ---------------------------------------------------------------------------
// HijackerEngine — main engine class
// ---------------------------------------------------------------------------
class HijackerEngine {
public:
    HijackerEngine();
    ~HijackerEngine();

    // Lifecycle
    bool  init(int sample_rate = 44100, int buffer_frames = HJ_BUFFER_FRAMES);
    void  shutdown();
    bool  is_running() const;

    // Transport — called from Python
    void  play(double timeline_pos_s);
    void  stop();
    void  seek(double timeline_pos_s);

    // Channel setup — called from Python at play-start
    void  set_channel_playlist(int ch, const std::vector<HijackerSegment>& segs);
    void  clear_channel(int ch);
    void  clear_all_channels();

    // Real-time mixer params — safe to call during playback
    void  set_volume(int ch, float vol);
    void  set_mute(int ch, bool muted);
    void  set_solo(int ch, bool soloed);
    void  set_pan(int ch, float pan);   // 0=left, 0.5=centre, 1=right

    // Effect params — take effect next buffer (~5ms)
    void  set_effect_slot(int ch, int slot, int type,
                          const std::vector<float>& params);
    void  clear_effect_slot(int ch, int slot);

    // Metering — called from Python meter timer
    float get_meter_rms(int ch)  const;
    float get_meter_peak(int ch) const;
    double get_playhead_s()      const;

    // Expose EngineState for GR levels, FFT bins (used by rack UI)
    // Returns the global g_state which hijacker_processor.cpp also uses
    EngineState* get_state();  // defined in hijacker_audio_engine.cpp

    // PortAudio callback — public so C callback can reach it
    int audio_callback(const void* input, void* output,
                       unsigned long frames_per_buffer);

private:
    void  _open_segment(int ch, int seg_idx, double seek_within_seg_s = 0.0);
    void  _close_channel_file(int ch);
    bool  _read_wav_header(int ch);
    int   _read_pcm_frames(int ch, float* buf, int n_frames);
    void  _process_channel_buffer(int ch, float* buf,
                                  int frames, int n_ch, float sr);
    void  _apply_seek(double target_s);

    HijackerChannel  channels_[PB_MAX_CHANNELS];
    HijackerTransport transport_;
    // state_ removed — using global g_state shared with hijacker_processor.cpp

    void*  pa_stream_   = nullptr;   // PaStream* — void* avoids portaudio.h in header
    bool   initialized_ = false;
    int    sample_rate_ = 44100;
    int    buffer_frames_ = HJ_BUFFER_FRAMES;
    double playhead_s_  = 0.0;      // authoritative playhead (audio thread)

    // Solo: if any channel is soloed, only soloed channels play
    bool   any_soloed_  = false;

    std::mutex playlist_mutex_;  // protects channel playlist writes
};

// Global engine instance (accessed via pybind11 wrapper)
extern HijackerEngine* g_hijacker_engine;
