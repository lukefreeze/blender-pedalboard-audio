#include <pybind11/pybind11.h>
#include <pybind11/stl.h> // Converts Python lists to C++ vectors automatically
#include <vector>

namespace py = pybind11;

// This is the core logic that will eventually house Pedalboard
std::vector<float> process_audio_buffer(std::vector<float> input_buffer, float gain) {
    std::vector<float> output_buffer;
    output_buffer.reserve(input_buffer.size());

    for (float sample : input_buffer) {
        // Simple example: Just changing volume for now
        output_buffer.push_back(sample * gain);
    }

    return output_buffer;
}

// This block creates the "Bridge" to Python
PYBIND11_MODULE(pedalboard_engine, m) {
    m.doc() = "C++ Audio Engine for Blender VSE";
    m.def("process_buffer", &process_audio_buffer, "Processes an audio buffer with gain");
}
