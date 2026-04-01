#include "mixer_ui.h"
#include "socket_client.h" // This provides the actual definition of BlenderBridge

void MixerUI::RenderChannelStrip(Strip& s, BlenderBridge& bridge) {
    ImGui::PushID(s.id);
    ImGui::BeginChild("TrackChild", ImVec2(90, 520), true);

    float windowWidth = ImGui::GetWindowSize().x;

    // Center Name
    float textWidth = ImGui::CalcTextSize(s.name).x;
    ImGui::SetCursorPosX((windowWidth - textWidth) * 0.5f);
    ImGui::Text(s.name);
    ImGui::Separator();

    // Peak Meter Placeholder
    float meterValue = s.vol / 1.5f;
    ImVec2 p0 = ImGui::GetCursorScreenPos();
    ImVec2 p1 = ImVec2(p0.x + 10, p0.y + 250);
    ImGui::GetWindowDrawList()->AddRectFilled(p0, p1, IM_COL32(30, 30, 30, 255));
    ImGui::GetWindowDrawList()->AddRectFilled(ImVec2(p0.x, p1.y - (250.0f * (meterValue > 1.0f ? 1.0f : meterValue))), p1, IM_COL32(0, 255, 0, 255));

    ImGui::Dummy(ImVec2(10, 250));
    ImGui::SameLine(0, 5);

    // The Fader
    if (ImGui::VSliderFloat("##v", ImVec2(45, 250), &s.vol, 0.0f, 1.5f, "")) {
        int int_vol = (int)(s.vol * 10000.0f);
        if (s.vol != s.last_sent_vol) {
            bridge.sendUpdate(s.id, (float)int_vol, 0.0f);
            s.last_sent_vol = s.vol;
        }
    }

    // Unity Line (1.0)
    ImVec2 fMin = ImGui::GetItemRectMin();
    ImVec2 fMax = ImGui::GetItemRectMax();
    float unityY = fMax.y - ((fMax.y - fMin.y) * (1.0f / 1.5f));
    ImGui::GetWindowDrawList()->AddLine(ImVec2(fMin.x, unityY), ImVec2(fMax.x, unityY), IM_COL32(255, 255, 255, 150), 2.0f);

    // Numerical Input Box
    ImGui::SetNextItemWidth(70.0f);
    ImGui::SetCursorPosX((windowWidth - 70.0f) * 0.5f);
    if (ImGui::InputFloat("##num", &s.vol, 0.01f, 0.1f, "%.2f")) {
        if (s.vol < 0.0f) s.vol = 0.0f;
        bridge.sendUpdate(s.id, (float)((int)(s.vol * 10000.0f)), 0.0f);
        s.last_sent_vol = s.vol;
    }

    ImGui::EndChild();
    ImGui::PopID();
    ImGui::SameLine();
}
