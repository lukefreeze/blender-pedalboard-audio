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

extern "C" {
    extern EngineState g_state;
}

// ===========================================================================
// apply_effect_chain
// Processes one buffer through every enabled effect slot for a channel.
// Called from PedalboardReader::read() after the inner reader fills the buffer.
// Add new effect implementations here as you build the rack.
// ===========================================================================
static void apply_effect_chain(int channel_idx, aud::sample_t* buffer,
                                int frames, int channels)
{
    for (int slot = 0; slot < PB_MAX_EFFECTS; ++slot)
    {
        const EffectSlot& fx = g_state.effect_chain[channel_idx][slot];
        if (!fx.enabled) continue;

        switch (fx.type)
        {
        case EffectType::GAIN:
        {
            float gain = fx.params[0];
            for (int f = 0; f < frames; ++f)
                for (int c = 0; c < channels; ++c)
                    buffer[f * channels + c] *= gain;
            break;
        }

        case EffectType::EQ_3BAND:
        {
            // Simple gain-only EQ shelves as a placeholder.
            // Replace with proper biquad filters when you implement real EQ.
            float high_gain = std::pow(10.0f, fx.params[0] / 20.0f);
            float mid_gain  = std::pow(10.0f, fx.params[1] / 20.0f);
            float low_gain  = std::pow(10.0f, fx.params[2] / 20.0f);
            float avg_gain  = (high_gain + mid_gain + low_gain) / 3.0f;
            for (int f = 0; f < frames; ++f)
                for (int c = 0; c < channels; ++c)
                    buffer[f * channels + c] *= avg_gain;
            break;
        }

        case EffectType::COMPRESSOR:
            // TODO: implement multiband compressor
            break;

        case EffectType::REVERB:
            // TODO: implement reverb
            break;

        default:
            break;
        }
    }
}


// ===========================================================================
// PedalboardReader — aud::IReader implementation.
// Delegates everything to the inner reader except read(), which:
//   1. Applies the fader volume (g_state.volumes[channel_idx])
//   2. Runs the effects chain for this channel
//   3. Calculates and stores the peak level for the meter
// ===========================================================================

class PedalboardReader : public aud::IReader
{
public:
    PedalboardReader(std::shared_ptr<aud::IReader> inner, int channel_idx)
        : m_inner(inner), m_channel_idx(channel_idx) {}

    virtual bool isSeekable() const override { return m_inner->isSeekable(); }
    virtual void seek(int position)   override { m_inner->seek(position); }
    virtual int  getLength()    const override { return m_inner->getLength(); }
    virtual int  getPosition()  const override { return m_inner->getPosition(); }
    virtual aud::Specs getSpecs() const override { return m_inner->getSpecs(); }

    virtual void read(int& length, bool& eos, aud::sample_t* buffer) override
    {
        m_inner->read(length, eos, buffer);
        if (length <= 0 || !buffer) return;

        aud::Specs specs    = m_inner->getSpecs();
        int        channels = std::max(1, (int)specs.channels);
        int        frames   = length / channels;

        // 1. Apply fader volume
        float gain     = g_state.volumes[m_channel_idx];
        float max_peak = 0.0f;

        for (int f = 0; f < frames; ++f)
            for (int c = 0; c < channels; ++c)
            {
                int   idx = f * channels + c;
                float s   = buffer[idx];
                float abs_s = std::abs(s);
                if (abs_s > max_peak) max_peak = abs_s;
                buffer[idx] = s * gain;
            }

        // 2. Run effects chain (compressor, reverb, EQ etc.)
        apply_effect_chain(m_channel_idx, buffer, frames, channels);

        // 3. Write peak for meter display
        g_state.meter_levels[m_channel_idx] = std::min(max_peak, 1.0f);
    }

private:
    std::shared_ptr<aud::IReader> m_inner;
    int m_channel_idx;
};


// ===========================================================================
// PedalboardSound — aud::ISound factory.
// Blender calls createReader() each time it starts or seeks this sound.
// ===========================================================================

class PedalboardSound : public aud::ISound
{
public:
    PedalboardSound(std::shared_ptr<aud::ISound> inner, int channel_idx)
        : m_inner(inner), m_channel_idx(channel_idx) {}

    virtual std::shared_ptr<aud::IReader> createReader() override
    {
        return std::make_shared<PedalboardReader>(
            m_inner->createReader(), m_channel_idx);
    }

    // Expose the raw pointer so wrapper.cpp can pass it back to Python
    aud::ISound* get_raw() { return this; }

private:
    std::shared_ptr<aud::ISound> m_inner;
    int m_channel_idx;
};


// ===========================================================================
// C-linkage entry points
// ===========================================================================

extern "C" {

void* create_channel(void* sound_ptr, int channel_idx, int strip_channel)
{
    if (!sound_ptr || channel_idx < 0 || channel_idx >= PB_MAX_CHANNELS)
        return nullptr;

    aud::ISound* raw = reinterpret_cast<aud::ISound*>(sound_ptr);
    std::shared_ptr<aud::ISound> inner(raw, [](aud::ISound*){});

    ChannelHandle* handle  = new ChannelHandle();
    handle->sound_raw      = new PedalboardSound(inner, channel_idx);
    handle->channel_idx    = channel_idx;
    handle->strip_channel  = strip_channel;

    // Initialise volume to 1.0 if not already set
    if (g_state.volumes[channel_idx] == 0.0f)
        g_state.volumes[channel_idx] = 1.0f;

    g_state.meter_levels[channel_idx] = 0.0f;
    return handle;
}

void* get_channel_sound(void* handle_ptr)
{
    if (!handle_ptr) return nullptr;
    ChannelHandle* h = reinterpret_cast<ChannelHandle*>(handle_ptr);
    return h->sound_raw;
}

void release_channel(void* handle_ptr)
{
    if (!handle_ptr) return;
    ChannelHandle* h = reinterpret_cast<ChannelHandle*>(handle_ptr);
    if (h->channel_idx >= 0 && h->channel_idx < PB_MAX_CHANNELS)
        g_state.meter_levels[h->channel_idx] = 0.0f;
    delete h->sound_raw;
    delete h;
}

} // extern "C"
