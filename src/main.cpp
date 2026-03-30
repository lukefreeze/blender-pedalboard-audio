#include <iostream>
#include <string>
#include "socket_client.h"

int main() {
    std::cout << "--- Pedalboard Remote Control ---" << std::endl;
    BlenderBridge bridge;

    if (bridge.connectToBlender()) {
        std::cout << "Connected to Blender!" << std::endl;
        std::cout << "Enter a volume (0.0 to 1.0) or 'q' to quit:" << std::endl;

        std::string input;
        while (true) {
            std::cout << "> ";
            std::cin >> input;

            if (input == "q") break;

            try {
                float vol = std::stof(input); // Convert text to number
                bridge.sendUpdate(1, vol, 0.0f); // Send to Blender
                std::cout << "Sent Volume: " << vol << std::endl;
                // Give the socket a millisecond to breathe
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            } catch (...) {
                std::cout << "Invalid input. Enter a number." << std::endl;
            }
        }
    } else {
        std::cerr << "Connection Failed! Is 'Start Service' running in Blender?" << std::endl;
        system("pause");
    }

    bridge.closeConnection();
    return 0;
}
