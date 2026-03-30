#include <iostream>
#include <thread>
#include <chrono>
#include "socket_client.h" // Point to the header, not the cpp!

int main() {
    std::cout << "Pedalboard DAW Engine Launching..." << std::endl;
    BlenderBridge bridge;

    if (bridge.connectToBlender()) {
        std::cout << "Connected to Blender!" << std::endl;
        bridge.sendUpdate(1, 0.5f, 0.0f);
        std::this_thread::sleep_for(std::chrono::seconds(2));
    } else {
        std::cerr << "Connection Failed! Is Blender's Start Service active?" << std::endl;
        std::this_thread::sleep_for(std::chrono::seconds(3));
    }

    bridge.closeConnection();
    return 0;
}
