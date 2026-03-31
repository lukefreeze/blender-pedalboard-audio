#include "socket_client.h"
#include <iostream>

// The constructor should only initialize Winsock, not the socket itself
BlenderBridge::BlenderBridge() : sock(INVALID_SOCKET) {
    WSADATA wsa;
    WSAStartup(MAKEWORD(2,2), &wsa);
}

bool BlenderBridge::connectToBlender() {
    sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock == INVALID_SOCKET) return false;

    server.sin_addr.s_addr = inet_addr("127.0.0.1");
    server.sin_family = AF_INET;
    server.sin_port = htons(65432);

    if (connect(sock, (struct sockaddr *)&server, sizeof(server)) < 0) {
        return false;
    }
    return true;
}

void BlenderBridge::sendUpdate(int track_id, float vol, float pan) {
    if (sock == INVALID_SOCKET) return;

    std::string json = "{ \"track_id\": " + std::to_string(track_id) +
                       ", \"volume\": " + std::to_string(vol) +
                       ", \"pan\": " + std::to_string(pan) + " }";

    // We send the data, but we DO NOT close the socket here!
    int result = send(sock, json.c_str(), (int)json.length(), 0);

    if (result == SOCKET_ERROR) {
        std::cerr << "Send failed. Connection lost." << std::endl;
    }
}

void BlenderBridge::closeConnection() {
    if (sock != INVALID_SOCKET) {
        closesocket(sock);
        sock = INVALID_SOCKET;
    }
    WSACleanup();
}

// ... keep your existing sendUpdate and closeConnection functions ...

std::string BlenderBridge::receiveData() {
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
