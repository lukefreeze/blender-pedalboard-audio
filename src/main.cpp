#include <sstream>
#include "transport_ui.h"
#include "imgui.h"
#include "imgui_impl_win32.h"
#include "imgui_impl_dx11.h"
#include "socket_client.h"
#include "mixer_ui.h"
#include <d3d11.h>
#include <vector>
#include <windows.h>


// Linker settings for Stealth Mode
//#pragma comment(linker, "/SUBSYSTEM:WINDOWS /ENTRY:mainCRTStartup")

// Forward declarations for DX11 Boilerplate (usually kept at bottom or separate)
LRESULT WINAPI WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

int main(int argc, char** argv) {
    // 1. DATA INITIALIZATION
    printf("Pedalboard Engine Started...\n");

    std::vector<Strip> myStrips = { {1, "CH 1"}, {2, "CH 2"}, {3, "CH 3"}, {4, "CH 4"} };
    int currentFrame = 0;
    int endFrame = 100;
    float fps = 24.0f; // Default to 24
    bool isPlaying = false;
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
            // If we get too much data at once, it can lag.
            // This stringstream approach is good, but let's make it safer.
            std::stringstream ss(incoming);
            std::string line;
            while (std::getline(ss, line)) {
                if (line.length() < 10) continue; // Ignore tiny/garbage fragments

                // ... (Keep the rest of your existing logic for currentFrame, fps, etc.)

                // 1. Process Frame/Time Data
                size_t cfPos = line.find("\"frame_current\":");
                if (cfPos != std::string::npos) currentFrame = std::stoi(line.substr(cfPos + 16));

                size_t efPos = line.find("\"frame_end\":");
                if (efPos != std::string::npos) endFrame = std::stoi(line.substr(efPos + 12));

                size_t fpsPos = line.find("\"fps\":");
                if (fpsPos != std::string::npos) fps = std::stof(line.substr(fpsPos + 6));

                size_t playPos = line.find("\"is_playing\":");
                if (playPos != std::string::npos) isPlaying = (line.find("true", playPos) != std::string::npos);

                // 2. Process Fader Restore or Updates
                // Change the search to look for the word "RESTORE" anywhere in the line
                bool isRestore = (line.find("\"type\": \"RESTORE\"") != std::string::npos) ||
                                    (line.find("\"type\":\"RESTORE\"") != std::string::npos);

                for (auto& s : myStrips) {
                    std::string idKey = "\"track_id\": " + std::to_string(s.id);
                    std::string idKeyAlt = "\"track_id\":" + std::to_string(s.id);

                    // Check for both spaced and unspaced JSON (Python can vary)
                    size_t idPos = line.find(idKey);
                    if (idPos == std::string::npos) idPos = line.find(idKeyAlt);

                    if (idPos != std::string::npos) {
                        size_t volPos = line.find("\"volume\":", idPos);
                        if (volPos != std::string::npos) {
                            // Find the number after the colon
                            size_t valueStart = line.find_first_of("0123456789", volPos);
                            float rawVal = std::stof(line.substr(valueStart));
                            float restoredVol = rawVal / 10000.0f;

                            if (isRestore || s.vol == s.last_sent_vol || s.last_sent_vol == -1.0f) {
                                s.vol = restoredVol;
                                s.last_sent_vol = restoredVol;

                                // FORCE PRINT TO TERMINAL
                                printf("C++ RESTORE EVENT: Strip %d set to %.2f\n", s.id, s.vol);
                            }
                        }
                    }
                }
            }
        }

        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();

        // 1. Global Spacebar Check
        if (ImGui::IsKeyPressed(ImGuiKey_Space)) {
            bridge.sendData("{\"command\": \"toggle_play\"}");
        }

        ImGui::SetNextWindowPos(ImVec2(0, 0));
        ImGui::SetNextWindowSize(ImGui::GetIO().DisplaySize);
        ImGui::Begin("Mixer Console", NULL, ImGuiWindowFlags_NoDecoration);

        // --- RESTORED DIGITAL CLOCK ---
        float totalSeconds = (fps > 0) ? (float)currentFrame / fps : 0.0f;
        int mins = (int)totalSeconds / 60;
        int secs = (int)totalSeconds % 60;
        int millis = (int)((totalSeconds - (int)totalSeconds) * 100);

        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.0f, 1.0f, 0.4f, 1.0f)); // Matrix Green
        ImGui::SetWindowFontScale(3.0f);
        ImGui::Text("%02d:%02d:%02d", mins, secs, millis);
        ImGui::SetWindowFontScale(1.0f);
        ImGui::PopStyleColor();

        ImGui::TextColored(ImVec4(0.6f, 0.6f, 0.6f, 1.0f), "FRAME: %d / %d | FPS: %.2f", currentFrame, endFrame, fps);
        // ------------------------------

        ImGui::Separator();

        // --- DOCK TRANSPORT UI ---
        // We pass 'true' or change the logic in transport_ui.cpp to NOT use ImGui::Begin
        RenderTransportWindow(bridge, currentFrame, endFrame, isPlaying);

        ImGui::Separator();

        // 3. Fader Strips
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

    // --- UPDATE THIS BLOCK AT THE VERY BOTTOM ---
    printf("C++ DEBUG: Window closed. Cleaning up socket...\n");

    // 1. Tell Blender we are disconnecting
    bridge.sendData("{\"command\": \"closing\"}");

    // 2. Shut down the WinSock connection
    bridge.closeConnection();

    // 3. Cleanup DX11 (Optional but good practice)
    ImGui_ImplDX11_Shutdown();
    ImGui_ImplWin32_Shutdown();
    ImGui::DestroyContext();

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
