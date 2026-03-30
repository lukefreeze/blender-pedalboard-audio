#include "socket_client.h"
#include <iostream>

BlenderBridge::BlenderBridge() {
    // Initialize Winsock once
    WSADATA wsa;
    WSAStartup(MAKEWORD(2,2), &wsa);
}

bool BlenderBridge::connectToBlender() {
    sock = socket(AF_INET, SOCK_STREAM, 0);
    server.sin_addr.s_addr = inet_addr("127.0.0.1");
    server.sin_family = AF_INET;
    server.sin_port = htons(65432);

    if (connect(sock, (struct sockaddr *)&server, sizeof(server)) < 0) {
        return false;
    }
    return true;
}

void BlenderBridge::sendUpdate(int track_id, float vol, float pan) {
    std::string json = "{ \"track_id\": " + std::to_string(track_id) +
                       ", \"volume\": " + std::to_string(vol) +
                       ", \"pan\": " + std::to_string(pan) + " }";
    send(sock, json.c_str(), (int)json.length(), 0);
}

void BlenderBridge::closeConnection() {
    closesocket(sock);
    WSACleanup();
}
