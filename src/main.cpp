#include "imgui.h"
#include "imgui_impl_win32.h"
#include "imgui_impl_dx11.h"
#include "socket_client.h"
#include "mixer_ui.h"
#include <d3d11.h>
#include <vector>
#include <windows.h>

// Linker settings for Stealth Mode
#pragma comment(linker, "/SUBSYSTEM:WINDOWS /ENTRY:mainCRTStartup")

// Forward declarations for DX11 Boilerplate (usually kept at bottom or separate)
LRESULT WINAPI WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

int main(int argc, char** argv) {
    // 1. DATA INITIALIZATION

    std::vector<Strip> myStrips = { {1, "CH 1"}, {2, "CH 2"}, {3, "CH 3"}, {4, "CH 4"} };
    int currentFrame = 0;
    int endFrame = 100;
    float fps = 24.0f; // Default to 24
    // 2. WINDOW SETUP
    WNDCLASSEXA wc = { sizeof(WNDCLASSEXA), CS_CLASSDC, WndProc, 0L, 0L, GetModuleHandle(NULL), NULL, NULL, NULL, NULL, "DAWClass", NULL };
    RegisterClassExA(&wc);
    HWND hwnd = CreateWindowA(wc.lpszClassName, "Pedalboard DAW Engine", WS_OVERLAPPEDWINDOW, 100, 100, 800, 650, NULL, NULL, wc.hInstance, NULL);

    // 3. DIRECTX SETUP (The functional logic)
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    IDXGISwapChain* swapChain = nullptr;
    ID3D11RenderTargetView* mainView = nullptr;

    DXGI_SWAP_CHAIN_DESC sd = {};
    sd.BufferCount = 2;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow = hwnd;
    sd.SampleDesc.Count = 1;
    sd.Windowed = TRUE;
    sd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    D3D_FEATURE_LEVEL fla[2] = { D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_0 };

    // This function call is what actually initializes your GPU hardware interface
    HRESULT hr = D3D11CreateDeviceAndSwapChain(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, fla, 2, D3D11_SDK_VERSION, &sd, &swapChain, &device, NULL, &context);

    if (FAILED(hr)) return 1;

    ID3D11Texture2D* bb;
    swapChain->GetBuffer(0, IID_PPV_ARGS(&bb));
    device->CreateRenderTargetView(bb, NULL, &mainView);
    bb->Release();

    ShowWindow(hwnd, SW_SHOWDEFAULT);

    // 3. IMGUI & BRIDGE INIT
    IMGUI_CHECKVERSION(); ImGui::CreateContext();
    ImGui_ImplWin32_Init(hwnd); ImGui_ImplDX11_Init(device, context);

    BlenderBridge bridge;
    bridge.connectToBlender();

    // 4. MAIN LOOP
    bool done = false;
    while (!done) {
        MSG msg;
        while (PeekMessage(&msg, NULL, 0U, 0U, PM_REMOVE)) {
            TranslateMessage(&msg); DispatchMessage(&msg);
            if (msg.message == WM_QUIT) done = true;
        }

        // SYNC INBOUND
        // SYNC INBOUND
        std::string incoming = bridge.receiveData();
        if (!incoming.empty()) {
            // 1. Find the start of the LAST packet in the buffer
            size_t lastBrace = incoming.find_last_of('{');
            std::string latestPacket = (lastBrace != std::string::npos) ? incoming.substr(lastBrace) : incoming;

            // 2. Parse Frame Data from latestPacket
            size_t cfPos = latestPacket.find("\"frame_current\":");
            if (cfPos != std::string::npos) currentFrame = std::stoi(latestPacket.substr(cfPos + 16));

            size_t efPos = latestPacket.find("\"frame_end\":");
            if (efPos != std::string::npos) endFrame = std::stoi(latestPacket.substr(efPos + 12));

            size_t fpsPos = latestPacket.find("\"fps\":");
            if (fpsPos != std::string::npos) fps = std::stof(latestPacket.substr(fpsPos + 6));

            // 3. Update Strips using latestPacket
            for (auto& s : myStrips) {
                std::string idKey = "\"track_id\":" + std::to_string(s.id);
                size_t idPos = latestPacket.find(idKey); // FIXED: use latestPacket

                if (idPos != std::string::npos) {
                    size_t volPos = latestPacket.find("\"volume\":", idPos); // FIXED: use latestPacket
                    if (volPos != std::string::npos) {
                        float rawVal = std::stof(latestPacket.substr(volPos + 9)); // FIXED: use latestPacket
                        float restoredVol = rawVal / 10000.0f;

                        // Interaction Guard: Only sync if we aren't currently dragging the fader
                        if (s.vol == s.last_sent_vol || s.last_sent_vol == -1.0f) {
                            s.vol = restoredVol;
                            s.last_sent_vol = restoredVol;
                        }
                    }
                }
            }
        }

        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();

        ImGui::SetNextWindowPos(ImVec2(0, 0));
        ImGui::SetNextWindowSize(ImGui::GetIO().DisplaySize);
        ImGui::Begin("Mixer Console", NULL, ImGuiWindowFlags_NoDecoration);

        // --- DIGITAL CLOCK LOGIC ---
        float totalSeconds = (fps > 0) ? (float)currentFrame / fps : 0.0f;
        int mins = (int)totalSeconds / 60;
        int secs = (int)totalSeconds % 60;
        int millis = (int)((totalSeconds - (int)totalSeconds) * 100);

        // Large Digital Clock
        ImGui::SetWindowFontScale(2.5f);
        ImGui::Text("%02d:%02d:%02d", mins, secs, millis);
        ImGui::SetWindowFontScale(1.0f); // Reset scale

        // Small Frame Counter
        ImGui::TextColored(ImVec4(0.7f, 0.7f, 0.7f, 1.0f), "FRAME: %d / %d", currentFrame, endFrame);

        float progress = (endFrame > 0) ? (float)currentFrame / (float)endFrame : 0.0f;
        ImGui::ProgressBar(progress, ImVec2(-1, 12), "");
        ImGui::Separator();
        // --- END CLOCK ---

        for (auto& s : myStrips) {
            MixerUI::RenderChannelStrip(s, bridge);
        }

        ImGui::End();

        // RENDER
        ImGui::Render();
        const float clear_color[4] = { 0.1f, 0.1f, 0.1f, 1.0f };
        context->OMSetRenderTargets(1, &mainView, NULL);
        context->ClearRenderTargetView(mainView, clear_color);
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
        swapChain->Present(1, 0);
    }
    return 0;
}

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

LRESULT WINAPI WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (ImGui_ImplWin32_WndProcHandler(hWnd, msg, wParam, lParam))
        return true;

    switch (msg) {
    case WM_SYSCOMMAND:
        if ((wParam & 0xfff0) == SC_KEYMENU) return 0;
        break;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcA(hWnd, msg, wParam, lParam);
}
