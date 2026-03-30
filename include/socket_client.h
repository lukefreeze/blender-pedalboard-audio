#ifndef SOCKET_CLIENT_H
#define SOCKET_CLIENT_H

#include <string>
#include <winsock2.h>

class BlenderBridge {
private:
    SOCKET sock;
    struct sockaddr_in server;

public:
    BlenderBridge(); // Constructor
    bool connectToBlender();
    void sendUpdate(int track_id, float vol, float pan);
    void closeConnection();
};

#endif
