#pragma once
#include "imgui.h"
#include <vector>
#include <string>

// Forward declaration of the class
class BlenderBridge;

struct Strip {
    int id;
    const char* name;
    float vol = 1.0f;
    float last_sent_vol = -1.0f;
    bool is_muted = false;  // Add this
    bool is_soloed = false; // Add this
};

namespace MixerUI {
    // Pass by reference to the bridge
    void RenderChannelStrip(Strip& s, BlenderBridge& bridge);
}
