#ifndef SOCKET_CLIENT_H
#define SOCKET_CLIENT_H

#include <string>
#include <winsock2.h>
#include <ws2tcpip.h> // Good practice for modern networking

class BlenderBridge {
public:
    // Constructor & Connection
    BlenderBridge();
    bool connectToBlender();
    void closeConnection();

    // Data Transfer
    void sendData(const std::string& data);      // Used by Transport & Spacebar
    void sendUpdate(int track_id, float vol, float pan); // Used by Faders
    std::string receiveData();                  // Used to get frames from Blender

    // Networking Variables
    SOCKET sock;
    struct sockaddr_in server;
};

#endif
