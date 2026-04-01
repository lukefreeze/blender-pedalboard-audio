#pragma once
#include "imgui.h"
#include <vector>
#include <string>

// Forward declaration of the class
class BlenderBridge;

struct Strip {
    int id;
    char name[32];
    float vol = 1.0f;
    float last_sent_vol = -1.0f;
};

namespace MixerUI {
    // Pass by reference to the bridge
    void RenderChannelStrip(Strip& s, BlenderBridge& bridge);
}
