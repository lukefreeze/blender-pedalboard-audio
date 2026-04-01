#include "transport_ui.h"

void RenderTransportWindow(BlenderBridge& bridge, int currentFrame, int endFrame, bool isPlaying) {
    // We use a unique ID for this section to prevent collisions with faders
    ImGui::PushID("TransportSection");

    // Play/Stop Button
    if (ImGui::Button(isPlaying ? "STOP ##play" : "PLAY ##play", ImVec2(100, 40))) {
        bridge.sendData("{\"command\": \"toggle_play\"}");
    }

    ImGui::SameLine();

    // Scrub Bar
    int scrubFrameInt = currentFrame;
    ImGui::PushItemWidth(-1);
    if (ImGui::SliderInt("##scrub", &scrubFrameInt, 1, endFrame, "Timeline: %d")) {
        bridge.sendData("{\"command\": \"set_frame\", \"value\": " + std::to_string(scrubFrameInt) + "}");
    }
    ImGui::PopItemWidth();

    // CRITICAL: This must match the PushID at the top exactly
    ImGui::PopID();
}
