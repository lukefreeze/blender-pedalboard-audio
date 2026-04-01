#ifndef TRANSPORT_UI_H
#define TRANSPORT_UI_H

#include "imgui.h"
#include "socket_client.h"
#include <string>

// This function will be called in your main loop
void RenderTransportWindow(BlenderBridge& bridge, int currentFrame, int endFrame, bool isPlaying);

#endif
