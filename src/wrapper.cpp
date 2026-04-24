// ---------------------------------------------------------------------------
// wrapper.cpp
// Place in: C:\Users\lukeb\Documents\BlenderTool\src\
// ---------------------------------------------------------------------------
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include "mixer_ui.h"
#include "pedalboard_processor.h"
#include "ISound.h"

namespace py = pybind11;
extern "C" { EngineState g_state; }
EngineState* get_state() { return &g_state; }

// ---------------------------------------------------------------------------
// process_buffer — batch DSP entry point.
// Called from Python with a (n_samples, n_channels) float32 numpy array.
// Applies the full effect chain for channel_idx and returns processed array.
// Also populates fft_bins and gr_levels for display.
// ---------------------------------------------------------------------------
py::array_t<float> process_buffer(int channel_idx,
                                   py::array_t<float, py::array::c_style> samples,
                                   int sample_rate)
{
    if (channel_idx < 0 || channel_idx >= PB_MAX_CHANNELS)
        throw std::invalid_argument("channel_idx out of range");

    py::buffer_info info = samples.request();
    if (info.ndim != 2)
        throw std::runtime_error("samples must be 2D array (n_frames, n_channels)");

    int n_frames   = (int)info.shape[0];
    int n_channels = (int)info.shape[1];
    float sr       = (float)sample_rate;

    // Make a mutable copy — we process in-place
    py::array_t<float> output({n_frames, n_channels});
    py::buffer_info out_info = output.request();
    float* out_ptr = (float*)out_info.ptr;
    float* in_ptr  = (float*)info.ptr;

    // Copy input to output buffer
    int total = n_frames * n_channels;
    for (int i = 0; i < total; ++i) out_ptr[i] = in_ptr[i];

    // Reset compressor state for a fresh batch pass
    g_state.comp_state[channel_idx] = CompressorChannelState{};
    g_state.eq_state[channel_idx]   = EqChannelState{};
    for (int b = 0; b < PB_MB_BANDS; ++b)
        g_state.fft_state[channel_idx][b] = FFTBandState{};

    // Set volume to 1.0 for batch processing (volume handled by Blender handle)
    float saved_vol = g_state.volumes[channel_idx];
    g_state.volumes[channel_idx] = 1.0f;

    // Process in chunks matching what the audio thread would see
    const int CHUNK = 1024;
    for (int offset = 0; offset < n_frames; offset += CHUNK) {
        int chunk_frames = std::min(CHUNK, n_frames - offset);
        int chunk_samples = chunk_frames * n_channels;

        // apply_effect_chain expects interleaved samples
        // our array is (frames, channels) which is already interleaved
        float* chunk_ptr = out_ptr + offset * n_channels;

        // Apply fader (1.0) + effect chain
        apply_effect_chain_batch(channel_idx, chunk_ptr,
                                  chunk_frames, n_channels, sr);
    }

    // Restore volume
    g_state.volumes[channel_idx] = saved_vol;

    printf("[ENGINE] ch%d batch processed %d frames @ %dHz\n",
           channel_idx, n_frames, sample_rate);

    return output;
}

PYBIND11_MODULE(pedalboard_engine, m)
{
    m.doc() = "Pedalboard audio engine";
    m.def("get_state",      &get_state, py::return_value_policy::reference);
    m.def("process_buffer", &process_buffer,
          py::arg("channel_idx"), py::arg("samples"), py::arg("sample_rate"),
          "Batch process a (n_frames, n_channels) float32 array through the "
          "effect chain. Returns processed array. Updates fft_bins/gr_levels.");

    // Effect type constants
    m.attr("FX_NONE")        = (int)EffectType::NONE;
    m.attr("FX_GAIN")        = (int)EffectType::GAIN;
    m.attr("FX_EQ_3BAND")    = (int)EffectType::EQ_3BAND;
    m.attr("FX_EQ_PARAM")    = (int)EffectType::EQ_PARAM;
    m.attr("FX_COMP_SINGLE") = (int)EffectType::COMP_SINGLE;
    m.attr("FX_COMP_MULTI")  = (int)EffectType::COMP_MULTI;
    m.attr("FX_REVERB")      = (int)EffectType::REVERB;
    m.attr("MB_BANDS")       = PB_MB_BANDS;
    m.attr("FFT_BINS")       = PB_FFT_BINS;

    py::class_<EffectSlot>(m, "EffectSlot")
        .def_readwrite("enabled", &EffectSlot::enabled)
        .def_property("type",
            [](const EffectSlot& s){ return (int)s.type; },
            [](EffectSlot& s, int t){ s.type=(EffectType)t; })
        .def_property("params",
            [](const EffectSlot& s){
                return std::vector<float>(s.params, s.params+24); },
            [](EffectSlot& s, std::vector<float> v){
                for(int i=0;i<24&&i<(int)v.size();i++) s.params[i]=v[i]; });

    py::class_<EngineState>(m, "EngineState")
        .def_readwrite("active_track_id", &EngineState::active_track_id)
        .def_readwrite("current_frame",   &EngineState::current_frame)
        .def_readwrite("is_playing",      &EngineState::is_playing)
        .def_property("volumes",
            [](EngineState& s){ return std::vector<float>(s.volumes,s.volumes+PB_MAX_CHANNELS); },
            [](EngineState& s, std::vector<float> v){
                for(int i=0;i<PB_MAX_CHANNELS&&i<(int)v.size();i++) s.volumes[i]=v[i]; })
        .def_property("meter_levels",
            [](EngineState& s){ return std::vector<float>(s.meter_levels,s.meter_levels+PB_MAX_CHANNELS); },
            [](EngineState& s, std::vector<float> v){
                for(int i=0;i<PB_MAX_CHANNELS&&i<(int)v.size();i++) s.meter_levels[i]=v[i]; })
        .def_property("mutes",
            [](EngineState& s){
                std::vector<bool> v;
                for(int i=0;i<PB_MAX_CHANNELS;i++) v.push_back(s.mutes[i]);
                return v; },
            [](EngineState& s, std::vector<bool> v){
                for(int i=0;i<PB_MAX_CHANNELS&&i<(int)v.size();i++) s.mutes[i]=v[i]; })
        .def_property("solos",
            [](EngineState& s){
                std::vector<bool> v;
                for(int i=0;i<PB_MAX_CHANNELS;i++) v.push_back(s.solos[i]);
                return v; },
            [](EngineState& s, std::vector<bool> v){
                for(int i=0;i<PB_MAX_CHANNELS&&i<(int)v.size();i++) s.solos[i]=v[i]; })
        .def("get_band_levels",
            [](EngineState& s, int ch) -> std::vector<float> {
                if(ch<0||ch>=PB_MAX_CHANNELS) throw std::out_of_range("ch");
                return std::vector<float>(s.band_levels[ch],s.band_levels[ch]+PB_MB_BANDS); },
            py::arg("channel"))
        .def("get_gr_levels",
            [](EngineState& s, int ch) -> std::vector<float> {
                if(ch<0||ch>=PB_MAX_CHANNELS) throw std::out_of_range("ch");
                return std::vector<float>(s.gr_levels[ch],s.gr_levels[ch]+PB_MB_BANDS); },
            py::arg("channel"))
        .def("get_fft_bins",
            [](EngineState& s, int ch, int band) -> std::vector<float> {
                if(ch<0||ch>=PB_MAX_CHANNELS||band<0||band>=PB_MB_BANDS)
                    throw std::out_of_range("ch/band");
                return std::vector<float>(s.fft_bins[ch][band],
                                          s.fft_bins[ch][band]+PB_FFT_BINS); },
            py::arg("channel"), py::arg("band"))
        .def("get_effect_slot",
            [](EngineState& s, int ch, int slot) -> EffectSlot& {
                if(ch<0||ch>=PB_MAX_CHANNELS||slot<0||slot>=PB_MAX_EFFECTS)
                    throw std::out_of_range("ch/slot");
                return s.effect_chain[ch][slot]; },
            py::arg("channel"), py::arg("slot"),
            py::return_value_policy::reference)
        .def_property("gains",
            [](EngineState& s){ return std::vector<float>(s.gains,s.gains+4); },
            [](EngineState& s, std::vector<float> v){
                for(int i=0;i<4&&i<(int)v.size();i++) s.gains[i]=v[i]; });
}
