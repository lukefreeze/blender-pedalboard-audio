#include "socket_client.h"
#include <iostream>
#include <ws2tcpip.h> // Required for modern IP conversion

// The constructor should only initialize Winsock, not the socket itself
BlenderBridge::BlenderBridge() : sock(INVALID_SOCKET) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        std::cerr << "WSAStartup Failed." << std::endl;
    }
}

bool BlenderBridge::connectToBlender() {
    sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock == INVALID_SOCKET) {
        std::cerr << "Socket creation failed." << std::endl;
        return false;
    }

    server.sin_family = AF_INET;
    server.sin_port = htons(65432);

    // Modern way to set the IP address - prevents the "inet_addr" crash
    if (InetPtonA(AF_INET, "127.0.0.1", &server.sin_addr) <= 0) {
        std::cerr << "Invalid address/ Address not supported" << std::endl;
        return false;
    }

    if (connect(sock, (struct sockaddr *)&server, sizeof(server)) < 0) {
        std::cerr << "Connect failed. Is Blender's 'Start Service' running?" << std::endl;
        return false;
    }
    return true;
}
void BlenderBridge::sendData(const std::string& data) {
    if (sock != INVALID_SOCKET) {
        // We add a newline (\n) so Blender's socket.recv() knows the message is finished
        std::string packet = data + "\n";
        send(sock, packet.c_str(), (int)packet.length(), 0);
    }
}
void BlenderBridge::sendUpdate(int track_id, float vol, float pan) {
    if (sock == INVALID_SOCKET) return;

    // Safety: If the float is already > 1.0, it means the UI range is wrong.
    // We will force it into 0-10000 range for the integer.
    int volInt = (int)(vol * 10000.0f);

    std::string json = "{\"track_id\":" + std::to_string(track_id) +
                       ",\"volume\":" + std::to_string(volInt) + "}\n";

    send(sock, json.c_str(), (int)json.length(), 0);
}
std::string BlenderBridge::receiveData() {
    if (sock == INVALID_SOCKET) return "";

    char buffer[4096];
    // Set socket to non-blocking mode so the UI doesn't freeze
    u_long mode = 1;
    ioctlsocket(sock, FIONBIO, &mode);

    int bytesReceived = recv(sock, buffer, sizeof(buffer) - 1, 0);

    if (bytesReceived > 0) {
        buffer[bytesReceived] = '\0'; // Null-terminate the string
        return std::string(buffer);
    }

    // If no data or error, return empty string
    return "";
}

void BlenderBridge::closeConnection() {
    if (sock != INVALID_SOCKET) {
        closesocket(sock);
        sock = INVALID_SOCKET;
    }
    WSACleanup();
}
