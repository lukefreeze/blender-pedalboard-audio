#include "mixer_ui.h"
#include "socket_client.h"
#include "imgui.h"

void MixerUI::RenderChannelStrip(Strip& s, BlenderBridge& bridge) {
    ImGui::PushID(s.id);

    // INCREASE HEIGHT to 600 or 650 to fit everything
    ImGui::BeginChild("TrackChild", ImVec2(90, 620), true);

    float windowWidth = ImGui::GetWindowSize().x;

    // Center Name
    ImGui::SetCursorPosX((windowWidth - ImGui::CalcTextSize(s.name).x) * 0.5f);
    ImGui::Text(s.name);
    ImGui::Separator();

    // --- MUTE & SOLO BUTTONS (Top of strip) ---
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 4.0f);

    // Mute Button logic
    if (s.is_muted) ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.8f, 0.1f, 0.1f, 1.0f));
    else ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.2f, 0.2f, 0.2f, 1.0f));

    if (ImGui::Button("M", ImVec2(35, 30))) {
        s.is_muted = !s.is_muted;
        bridge.sendData("{\"track_id\":" + std::to_string(s.id) +
                        ",\"command\":\"toggle_mute\",\"value\":" +
                        (s.is_muted ? "true" : "false") + "}");
    }
    ImGui::PopStyleColor();
    ImGui::SameLine();

    // Solo Button logic
    if (s.is_soloed) ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.8f, 0.6f, 0.0f, 1.0f));
    else ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.2f, 0.2f, 0.2f, 1.0f));

    if (ImGui::Button("S", ImVec2(35, 30))) {
        s.is_soloed = !s.is_soloed;
        // Sending command to Blender (Logic to be expanded for soloing groups)
        bridge.sendData("{\"track_id\":" + std::to_string(s.id) +
                        ",\"command\":\"toggle_solo\",\"value\":" +
                        (s.is_soloed ? "true" : "false") + "}");
    }
    ImGui::PopStyleColor();
    ImGui::PopStyleVar();

    ImGui::Spacing();

    // --- THE FADER ---
    ImGui::SetCursorPosX((windowWidth - 45) * 0.5f);
    if (ImGui::VSliderFloat("##v", ImVec2(45, 350), &s.vol, 0.0f, 1.0f, "")) {
        bridge.sendUpdate(s.id, s.vol, 0.0f);
        s.last_sent_vol = s.vol;
    }

    // --- NUMERICAL INPUT ---
    ImGui::SetNextItemWidth(70.0f);
    ImGui::SetCursorPosX((windowWidth - 70.0f) * 0.5f);
    if (ImGui::InputFloat("##num", &s.vol, 0.01f, 0.1f, "%.2f")) {
        s.vol = (s.vol < 0.0f) ? 0.0f : (s.vol > 1.0f) ? 1.0f : s.vol;
        bridge.sendUpdate(s.id, s.vol, 0.0f);
        s.last_sent_vol = s.vol;
    }

    ImGui::EndChild();
    ImGui::PopID();
    ImGui::SameLine();
}
