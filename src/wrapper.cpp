// ---------------------------------------------------------------------------
// wrapper.cpp
// Place in: C:\Users\lukeb\Documents\BlenderTool\src\
// ---------------------------------------------------------------------------

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "mixer_ui.h"
#include "pedalboard_processor.h"
#include "ISound.h"

namespace py = pybind11;

extern "C" {
    EngineState g_state;
}

EngineState* get_state() { return &g_state; }


// ---------------------------------------------------------------------------
// hook_channel_py
//
// The new approach — no capsule extraction needed.
//
// Python calls:
//   import aud
//   raw_sound = aud.Sound.file(filepath)          # load from disk
//   handle, pb_sound = pedalboard_engine.hook_channel(raw_sound, ch_idx, strip_ch)
//   aud_handle = aud.Device().play(pb_sound)       # play through device
//
// We accept the aud.Sound object, extract its ISound* via the
// internal _as_ptr() method that aud.Sound exposes in Blender 4.x,
// wrap it in PedalboardSound, then return both the ChannelHandle
// (as a capsule for lifetime management) and a new aud.Sound
// built from our PedalboardSound pointer.
// ---------------------------------------------------------------------------

// Try to extract aud::ISound* from a Python aud.Sound object.
// Uses aud.Sound._as_ptr() which Blender 4.x exposes, falling back
// to the capsule probe approach.
static void* extract_isound_ptr(py::object py_sound)
{
    // Method 1: aud.Sound._as_ptr() — returns the ISound* as an integer
    // This is the clean, version-stable path for Blender 4.x
    try {
        py::object ptr_obj = py_sound.attr("_as_ptr")();
        uintptr_t  ptr_val = ptr_obj.cast<uintptr_t>();
        if (ptr_val) return reinterpret_cast<void*>(ptr_val);
    } catch (...) {}

    // Method 2: PyCapsule probe (fallback for older builds)
    static const char* NAMES[] = {
        "__soundptr__", "_sound", "_as_parameter_", nullptr
    };
    PyObject* obj = py_sound.ptr();
    for (int i = 0; NAMES[i]; ++i) {
        PyObject* attr = PyObject_GetAttrString(obj, NAMES[i]);
        if (!attr) { PyErr_Clear(); continue; }
        if (PyCapsule_CheckExact(attr)) {
            void* p = PyCapsule_GetPointer(attr, "aud::ISound");
            if (!p) { PyErr_Clear(); p = PyCapsule_GetPointer(attr, nullptr); }
            if (!p) PyErr_Clear();
            Py_DECREF(attr);
            if (p) return p;
        }
        Py_DECREF(attr);
    }

    return nullptr;
}


py::tuple hook_channel_py(py::object py_sound, int channel_idx, int strip_channel)
{
    if (channel_idx < 0 || channel_idx >= PB_MAX_CHANNELS)
        throw std::invalid_argument("channel_idx out of range");

    void* raw_ptr = extract_isound_ptr(py_sound);
    if (!raw_ptr)
        throw std::runtime_error(
            "Could not extract ISound* from aud.Sound object.\n"
            "This should not happen with aud.Sound.file() — "
            "make sure you are passing an aud.Sound, not a bpy.types.Sound.");

    void* handle_ptr = create_channel(raw_ptr, channel_idx, strip_channel);
    if (!handle_ptr)
        throw std::runtime_error("create_channel() failed");

    void* sound_raw = get_channel_sound(handle_ptr);

    // Build a new Python aud.Sound from our PedalboardSound*.
    // We use aud._sound_from_pointer() in reverse — but since that's for
    // bSound pointers, we instead construct a capsule and pass it to
    // aud.Sound() constructor which accepts a raw pointer capsule.
    PyObject* capsule = PyCapsule_New(sound_raw, "aud::ISound",
                                      [](PyObject*){});  // no-op: owned by handle
    if (!capsule)
        throw std::runtime_error("PyCapsule_New failed");

    py::module_ aud_mod = py::module_::import("aud");
    py::object  result;
    try {
        result = aud_mod.attr("Sound")(
            py::reinterpret_steal<py::object>(capsule));
    } catch (py::error_already_set& e) {
        // aud.Sound() constructor didn't accept our capsule.
        // Fall back to returning the raw pointer as an integer so
        // Python can use aud._sound_from_pointer() or ctypes.
        PyErr_Clear();
        result = py::int_(reinterpret_cast<uintptr_t>(sound_raw));
    }

    // Keep the ChannelHandle alive via a capsule attribute.
    // Destructor calls release_channel() when Python GC collects it.
    py::capsule handle_capsule(handle_ptr, "pb_channel", [](void* p) {
        release_channel(p);
    });

    // Return (pb_sound_or_ptr, handle_capsule)
    // Python stores handle_capsule to keep the hook alive.
    return py::make_tuple(result, handle_capsule);
}


// ---------------------------------------------------------------------------
// Probe — call from Python to debug extraction
// ---------------------------------------------------------------------------
py::dict probe_sound(py::object py_sound)
{
    py::dict result;
    void* ptr = extract_isound_ptr(py_sound);
    result["ptr_found"]  = py::bool_(ptr != nullptr);
    result["ptr_value"]  = py::int_(reinterpret_cast<uintptr_t>(ptr));
    result["type"]       = py::str(py::str(py_sound.get_type()));

    // List all attributes
    py::list attrs;
    PyObject* dir_list = PyObject_Dir(py_sound.ptr());
    if (dir_list) {
        for (Py_ssize_t k = 0; k < PyList_Size(dir_list); ++k) {
            const char* name = PyUnicode_AsUTF8(PyList_GetItem(dir_list, k));
            PyObject*   val  = PyObject_GetAttrString(py_sound.ptr(), name);
            if (!val) { PyErr_Clear(); continue; }
            py::dict a;
            a["name"] = py::str(name);
            a["type"] = py::str(Py_TYPE(val)->tp_name);
            a["is_capsule"] = py::bool_(PyCapsule_CheckExact(val) != 0);
            Py_DECREF(val);
            attrs.append(a);
        }
        Py_DECREF(dir_list);
    }
    result["all_attributes"] = attrs;
    return result;
}


// ---------------------------------------------------------------------------
// pybind11 module
// ---------------------------------------------------------------------------
PYBIND11_MODULE(pedalboard_engine, m)
{
    m.doc() = "Pedalboard audio engine — per-channel gain, metering and effects";

    m.def("get_state",    &get_state, py::return_value_policy::reference);
    m.def("probe_sound",  &probe_sound,  py::arg("aud_sound"),
          "Diagnostic: inspect an aud.Sound object for pointer extraction.");

    // hook_channel(aud_sound, channel_idx, strip_channel)
    // -> (pb_sound_or_ptr, handle_capsule)
    // aud_sound     : aud.Sound from aud.Sound.file(filepath)
    // channel_idx   : 0-based engine channel index
    // strip_channel : VSE channel number (1-based), for reference
    // Returns a tuple: the wrapped sound object and a handle capsule.
    // Store BOTH — the sound to pass to aud.Device.play(),
    // the capsule to keep the hook alive.
    m.def("hook_channel", &hook_channel_py,
          py::arg("aud_sound"), py::arg("channel_idx"), py::arg("strip_channel"),
          "Wrap an aud.Sound in the Pedalboard gain/effects/meter pipeline.");

    // --- EffectSlot binding ---
    py::class_<EffectSlot>(m, "EffectSlot")
        .def_readwrite("enabled", &EffectSlot::enabled)
        .def_property("type",
            [](const EffectSlot& s) { return (int)s.type; },
            [](EffectSlot& s, int t) { s.type = (EffectType)t; })
        .def_property("params",
            [](const EffectSlot& s) {
                return std::vector<float>(s.params, s.params + 8);
            },
            [](EffectSlot& s, std::vector<float> v) {
                for (int i = 0; i < 8 && i < (int)v.size(); i++)
                    s.params[i] = v[i];
            });

    // Effect type constants — use these from Python:
    //   pedalboard_engine.FX_GAIN, pedalboard_engine.FX_EQ_3BAND, etc.
    m.attr("FX_NONE")       = (int)EffectType::NONE;
    m.attr("FX_GAIN")       = (int)EffectType::GAIN;
    m.attr("FX_EQ_3BAND")   = (int)EffectType::EQ_3BAND;
    m.attr("FX_COMPRESSOR") = (int)EffectType::COMPRESSOR;
    m.attr("FX_REVERB")     = (int)EffectType::REVERB;

    // --- EngineState binding ---
    py::class_<EngineState>(m, "EngineState")
        .def_readwrite("active_track_id", &EngineState::active_track_id)
        .def_readwrite("current_frame",   &EngineState::current_frame)
        .def_readwrite("is_playing",      &EngineState::is_playing)
        .def_property("volumes",
            [](EngineState& s) {
                return std::vector<float>(s.volumes, s.volumes + PB_MAX_CHANNELS);
            },
            [](EngineState& s, std::vector<float> v) {
                for (int i = 0; i < PB_MAX_CHANNELS && i < (int)v.size(); i++)
                    s.volumes[i] = v[i];
            })
        .def_property("meter_levels",
            [](EngineState& s) {
                return std::vector<float>(
                    s.meter_levels, s.meter_levels + PB_MAX_CHANNELS);
            },
            [](EngineState& s, std::vector<float> v) {
                for (int i = 0; i < PB_MAX_CHANNELS && i < (int)v.size(); i++)
                    s.meter_levels[i] = v[i];
            })
        .def_property("mutes",
            [](EngineState& s) {
                std::vector<bool> v;
                for (int i = 0; i < PB_MAX_CHANNELS; i++) v.push_back(s.mutes[i]);
                return v;
            },
            [](EngineState& s, std::vector<bool> v) {
                for (int i = 0; i < PB_MAX_CHANNELS && i < (int)v.size(); i++)
                    s.mutes[i] = v[i];
            })
        .def_property("solos",
            [](EngineState& s) {
                std::vector<bool> v;
                for (int i = 0; i < PB_MAX_CHANNELS; i++) v.push_back(s.solos[i]);
                return v;
            },
            [](EngineState& s, std::vector<bool> v) {
                for (int i = 0; i < PB_MAX_CHANNELS && i < (int)v.size(); i++)
                    s.solos[i] = v[i];
            })
        // Effect chain access: get_effect_slot(channel, slot) -> EffectSlot
        .def("get_effect_slot",
            [](EngineState& s, int ch, int slot) -> EffectSlot& {
                if (ch < 0 || ch >= PB_MAX_CHANNELS ||
                    slot < 0 || slot >= PB_MAX_EFFECTS)
                    throw std::out_of_range("channel or slot out of range");
                return s.effect_chain[ch][slot];
            },
            py::arg("channel"), py::arg("slot"),
            py::return_value_policy::reference,
            "Get a reference to an effect slot. Modify it directly to change the chain.")
        // Legacy fields
        .def_property("gains",
            [](EngineState& s) {
                return std::vector<float>(s.gains, s.gains + 4);
            },
            [](EngineState& s, std::vector<float> v) {
                for (int i = 0; i < 4 && i < (int)v.size(); i++) s.gains[i] = v[i];
            });
}
