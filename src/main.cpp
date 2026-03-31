#include "imgui.h"
#include "imgui_impl_win32.h"
#include "imgui_impl_dx11.h"
#include "socket_client.h"
#include <d3d11.h>
#include <vector>
#include <string>
#include <iostream>
#include <windows.h>

#pragma comment(linker, "/SUBSYSTEM:CONSOLE")

struct Strip {
    int id;
    char name[32];
    float vol = 1.0f;
};

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

LRESULT WINAPI WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (ImGui_ImplWin32_WndProcHandler(hWnd, msg, wParam, lParam)) return true;
    if (msg == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcA(hWnd, msg, wParam, lParam);
}

int main(int argc, char** argv) {
    // 1. DATA INITIALIZATION
    std::vector<Strip> myStrips = { {1, "CH 1"}, {2, "CH 2"}, {3, "CH 3"}, {4, "CH 4"} };

    // Initialize tracking array with -1.0 to force the first update to sync
    static float last_vols[32];
    for(int i = 0; i < 32; i++) last_vols[i] = -1.0f;

    // 2. WINDOW SETUP
    WNDCLASSEXA wc = { sizeof(WNDCLASSEXA), CS_CLASSDC, WndProc, 0L, 0L, GetModuleHandle(NULL), NULL, NULL, NULL, NULL, "DAWClass", NULL };
    RegisterClassExA(&wc);
    HWND hwnd = CreateWindowA(wc.lpszClassName, "Pedalboard DAW Engine", WS_OVERLAPPEDWINDOW, 100, 100, 800, 650, NULL, NULL, wc.hInstance, NULL);
    ShowWindow(hwnd, SW_SHOWDEFAULT);

    // 3. DIRECTX SETUP
    ID3D11Device* device = NULL;
    ID3D11DeviceContext* context = NULL;
    IDXGISwapChain* swapChain = NULL;
    ID3D11RenderTargetView* mainView = NULL;

    DXGI_SWAP_CHAIN_DESC sd = {};
    sd.BufferCount = 2;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow = hwnd;
    sd.SampleDesc.Count = 1;
    sd.Windowed = TRUE;
    sd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    D3D_FEATURE_LEVEL fla[2] = { D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_0 };
    D3D11CreateDeviceAndSwapChain(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, fla, 2, D3D11_SDK_VERSION, &sd, &swapChain, &device, NULL, &context);

    ID3D11Texture2D* bb;
    swapChain->GetBuffer(0, IID_PPV_ARGS(&bb));
    device->CreateRenderTargetView(bb, NULL, &mainView);
    bb->Release();

    // 4. IMGUI SETUP
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGui_ImplWin32_Init(hwnd);
    ImGui_ImplDX11_Init(device, context);

    // 5. BRIDGE SETUP
    BlenderBridge bridge;
    bridge.connectToBlender();

    bool done = false;
    while (!done) {
        MSG msg;
        while (PeekMessage(&msg, NULL, 0U, 0U, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessage(&msg);
            if (msg.message == WM_QUIT) done = true;
        }
        if (done) break;

        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();

        // --- INCOMING SYNC FROM BLENDER ---
        std::string incoming = bridge.receiveData();
        if (!incoming.empty()) {
            for (auto& s : myStrips) {
                // Find the specific track ID in the incoming string
                std::string idKey = "\"track_id\":" + std::to_string(s.id);
                size_t idPos = incoming.find(idKey);
                if (idPos != std::string::npos) {
                    size_t volPos = incoming.find("\"volume\":", idPos);
                    if (volPos != std::string::npos) {
                        // Extract the number and convert back to 0.0-1.5 range
                        float rawVal = std::stof(incoming.substr(volPos + 9));
                        float restoredVol = rawVal / 10000.0f;

                        // Only apply if we aren't currently dragging the fader
                        if (!ImGui::IsItemActive()) {
                            s.vol = restoredVol;
                            if (s.id < 32) last_vols[s.id] = restoredVol;
                        }
                    }
                }
            }
        }

        // --- DRAW MIXER CONSOLE ---
        ImGui::SetNextWindowPos(ImVec2(0, 0));
        ImGui::SetNextWindowSize(ImGui::GetIO().DisplaySize);
        ImGui::Begin("Mixer Console", NULL, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove);

        for (auto& s : myStrips) {
            ImGui::PushID(s.id);
            ImGui::BeginChild("TrackChild", ImVec2(90, 520), true);

            float windowWidth = ImGui::GetWindowSize().x;

            // PRESERVED: Your Channel Name Logic
            float textWidth = ImGui::CalcTextSize(s.name).x;
            ImGui::SetCursorPosX((windowWidth - textWidth) * 0.5f);
            ImGui::Text(s.name);
            ImGui::Separator();

            // PRESERVED: Meter + Fader Block Centering
            float blockWidth = 60.0f;
            ImGui::SetCursorPosX((windowWidth - blockWidth) * 0.5f);

            // PRESERVED: Your Peak Meter Drawing
            float meterValue = s.vol / 1.5f;
            ImVec2 p0 = ImGui::GetCursorScreenPos();
            ImVec2 p1 = ImVec2(p0.x + 10, p0.y + 250);
            ImGui::GetWindowDrawList()->AddRectFilled(p0, p1, IM_COL32(30, 30, 30, 255));
            ImGui::GetWindowDrawList()->AddRectFilled(ImVec2(p0.x, p1.y - (250.0f * (meterValue > 1.0f ? 1.0f : meterValue))), p1, IM_COL32(0, 255, 0, 255));

            ImGui::Dummy(ImVec2(10, 250));
            ImGui::SameLine(0, 5);

            // --- THE FADER ---
            if (ImGui::VSliderFloat("##v", ImVec2(45, 250), &s.vol, 0.0f, 1.5f, "")) {
                int int_vol = (int)(s.vol * 10000.0f);
                if (int_vol != (int)(last_vols[s.id] * 10000.0f)) {
                    last_vols[s.id] = s.vol;
                    bridge.sendUpdate(s.id, (float)int_vol, 0.0f);
                    printf("[DAW] Sent Ch %d: %d\n", s.id, int_vol);
                }
            }

            // PRESERVED: Your Unity Line
            ImVec2 fMin = ImGui::GetItemRectMin();
            ImVec2 fMax = ImGui::GetItemRectMax();
            float unityY = fMax.y - ((fMax.y - fMin.y) * (1.0f / 1.5f));
            ImGui::GetWindowDrawList()->AddLine(ImVec2(fMin.x, unityY), ImVec2(fMax.x, unityY), IM_COL32(255, 255, 255, 150), 2.0f);

            ImGui::Spacing();

            // --- FIXED: Single Numerical Input ---
            ImGui::SetNextItemWidth(70.0f);
            ImGui::SetCursorPosX((windowWidth - 70.0f) * 0.5f);
            if (ImGui::InputFloat("##num", &s.vol, 0.01f, 0.1f, "%.2f")) {
                if (s.vol < 0.0f) s.vol = 0.0f;
                int int_vol = (int)(s.vol * 10000.0f);
                bridge.sendUpdate(s.id, (float)int_vol, 0.0f);
                last_vols[s.id] = s.vol;
            }

            ImGui::EndChild();
            ImGui::PopID();
            ImGui::SameLine();
        }
        ImGui::End();

        // RENDER
        const float clear_color[4] = { 0.12f, 0.12f, 0.12f, 1.0f };
        context->OMSetRenderTargets(1, &mainView, NULL);
        context->ClearRenderTargetView(mainView, clear_color);
        ImGui::Render();
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
        swapChain->Present(1, 0);
    }

    return 0;
}
