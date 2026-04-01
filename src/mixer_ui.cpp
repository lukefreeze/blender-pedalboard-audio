#include "mixer_ui.h"
#include "socket_client.h"
#include "imgui.h"

void MixerUI::RenderChannelStrip(Strip& s, BlenderBridge& bridge) {
    ImGui::PushID(s.id);
    ImGui::BeginChild("TrackChild", ImVec2(90, 520), true);

    float windowWidth = ImGui::GetWindowSize().x;

    // Center Name
    float textWidth = ImGui::CalcTextSize(s.name).x;
    ImGui::SetCursorPosX((windowWidth - textWidth) * 0.5f);
    ImGui::Text(s.name);
    ImGui::Separator();

    // 1. THE FADER (Fix: Use 's', range 0.0 to 1.0, and pass 3 args to sendUpdate)
    // We pass &s.vol as a pointer so ImGui can modify it directly
    if (ImGui::VSliderFloat("##v", ImVec2(45, 250), &s.vol, 0.0f, 1.0f, "")) {
        // Send Track ID, Volume, and Pan (default 0.0f)
        bridge.sendUpdate(s.id, s.vol, 0.0f);
        s.last_sent_vol = s.vol;
    }

    // Unity Line (Optional visual guide at 1.0)
    ImVec2 fMin = ImGui::GetItemRectMin();
    ImVec2 fMax = ImGui::GetItemRectMax();
    float unityY = fMin.y; // At the very top since max is 1.0
    ImGui::GetWindowDrawList()->AddLine(ImVec2(fMin.x, unityY), ImVec2(fMax.x, unityY), IM_COL32(255, 255, 255, 150), 2.0f);

    // 2. NUMERICAL INPUT (Fix: Use 's')
    ImGui::SetNextItemWidth(70.0f);
    ImGui::SetCursorPosX((windowWidth - 70.0f) * 0.5f);
    if (ImGui::InputFloat("##num", &s.vol, 0.01f, 0.1f, "%.2f")) {
        // Clamp values manually for the input box
        if (s.vol < 0.0f) s.vol = 0.0f;
        if (s.vol > 1.0f) s.vol = 1.0f;

        bridge.sendUpdate(s.id, s.vol, 0.0f);
        s.last_sent_vol = s.vol;
    }

    ImGui::EndChild();
    ImGui::PopID();
    ImGui::SameLine();
}
