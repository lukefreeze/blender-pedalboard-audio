#include "imgui.h"
#include "imgui_impl_win32.h"
#include "imgui_impl_dx11.h"
#include "socket_client.h"
#include <d3d11.h>
#include <tchar.h>
#include <vector>

// Data for our Mixer Strips
struct Strip { int id; char name[32]; float vol = 0.5f; };
std::vector<Strip> myStrips = { {1, "Audio 1"}, {2, "Audio 2"}, {3, "Audio 3"} };

// Boilerplate variables for DirectX
static ID3D11Device* g_pd3dDevice = NULL;
static ID3D11DeviceContext* g_pd3dDeviceContext = NULL;
static IDXGISwapChain* g_pSwapChain = NULL;
static ID3D11RenderTargetView* g_mainRenderTargetView = NULL;

// Forward declarations of helper functions
bool CreateDeviceD3D(HWND hWnd);
void CleanupDeviceD3D();
void CreateRenderTarget();
void CleanupRenderTarget();
LRESULT WINAPI WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

int main() {
    // 1. Create Windows Window
    WNDCLASSEX wc = { sizeof(WNDCLASSEX), CS_CLASSDC, WndProc, 0L, 0L, GetModuleHandle(NULL), NULL, NULL, NULL, NULL, _T("Pedalboard Window"), NULL };
    ::RegisterClassEx(&wc);
    HWND hwnd = ::CreateWindow(wc.lpszClassName, _T("Pedalboard DAW Engine"), WS_OVERLAPPEDWINDOW, 100, 100, 800, 600, NULL, NULL, wc.hInstance, NULL);

    // 2. Initialize Direct3D
    if (!CreateDeviceD3D(hwnd)) { CleanupDeviceD3D(); ::UnregisterClass(wc.lpszClassName, wc.hInstance); return 1; }

    // 3. Show the window
    ::ShowWindow(hwnd, SW_SHOWDEFAULT);
    ::UpdateWindow(hwnd);

    // 4. Setup ImGui
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGui_ImplWin32_Init(hwnd);
    ImGui_ImplDX11_Init(g_pd3dDevice, g_pd3dDeviceContext);

    // 5. Connect to Blender
    BlenderBridge bridge;
    bridge.connectToBlender();

    // 6. MAIN LOOP (The program stays here until you close it)
    bool done = false;
    while (!done) {
        MSG msg;
        while (::PeekMessage(&msg, NULL, 0U, 0U, PM_REMOVE)) {
            ::TranslateMessage(&msg);
            ::DispatchMessage(&msg);
            if (msg.message == WM_QUIT) done = true;
        }
        if (done) break;

        // Start ImGui Frame
        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();

        // --- DRAW YOUR MIXING DESK HERE ---
        ImGui::SetNextWindowPos(ImVec2(0, 0));
        ImGui::SetNextWindowSize(ImGui::GetIO().DisplaySize);
        ImGui::Begin("Mixer Console", NULL, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove);

        for (auto& s : myStrips) {
            ImGui::BeginGroup();
            ImGui::Text(s.name);
            if (ImGui::VSliderFloat("##v", ImVec2(40, 200), &s.vol, 0.0f, 1.0f, "")) {
                bridge.sendUpdate(s.id, s.vol, 0.0f); // Live Update!
            }
            ImGui::EndGroup();
            ImGui::SameLine();
        }
        ImGui::End();

        // Rendering
        const float clear_color_with_alpha[4] = { 0.1f, 0.1f, 0.1f, 1.0f };
        g_pd3dDeviceContext->OMSetRenderTargets(1, &g_mainRenderTargetView, NULL);
        g_pd3dDeviceContext->ClearRenderTargetView(g_mainRenderTargetView, clear_color_with_alpha);
        ImGui::Render();
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
        g_pSwapChain->Present(1, 0);
    }

    // Cleanup
    ImGui_ImplDX11_Shutdown();
    ImGui_ImplWin32_Shutdown();
    ImGui::DestroyContext();
    CleanupDeviceD3D();
    ::DestroyWindow(hwnd);
    ::UnregisterClass(wc.lpszClassName, wc.hInstance);

    return 0;
}

// --- BOILERPLATE FUNCTIONS (Required for Windows to work) ---
// (I will omit the full 100 lines of D3D creation for brevity,
// but you need the WndProc and CreateDeviceD3D functions from the
// ImGui example_win32_directx11/main.cpp file here)
