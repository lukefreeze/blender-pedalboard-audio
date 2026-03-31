import json
import os
import queue
import socket
import subprocess
import threading

import bpy

# --- 1. GLOBAL STATE ---
data_queue = queue.Queue()


class PedalboardState:
    is_connected = False
    stop_signal = False
    host = "127.0.0.1"
    port = 65432
    executable_path = r"C:\Users\lukeb\Documents\BlenderTool\build\PedalboardDAW.exe"


# --- 2. THE SOCKET THREAD ---
def socket_server_loop():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Good practice
        s.bind((PedalboardState.host, PedalboardState.port))
        s.listen()
        PedalboardState.is_connected = True

        while not PedalboardState.stop_signal:
            try:
                s.settimeout(0.5)
                conn, addr = s.accept()
                with conn:
                    # 1. SEND INITIAL SYNC DATA TO C++
                    scene = bpy.context.scene
                    # Get stored values or default to 1.0
                    masters = scene.get("channel_masters", {})
                    # Convert IDProperty to a standard dict for JSON
                    sync_packet = {
                        "type": "SYNC",
                        "levels": {str(k): v for k, v in masters.items()},
                    }
                    conn.sendall((json.dumps(sync_packet) + "\n").encode())
                    # NEW: A second loop to keep the connection alive!
                    while not PedalboardState.stop_signal:
                        data = conn.recv(1024).decode()
                        if (
                            not data
                        ):  # If data is empty, the C++ app closed the connection
                            break
                        data_queue.put(data)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Socket Error: {e}")
                break

        PedalboardState.is_connected = False


# --- 3. OPERATORS ---
class VSE_OT_Pedalboard_Modal(bpy.types.Operator):
    bl_idname = "vse.pedalboard_modal"
    bl_label = "Connect to DAW"
    _timer = None

    def modal(self, context, event):
        # 1. Heartbeat check for exit
        if PedalboardState.stop_signal:
            self.cancel(context)
            return {"FINISHED"}

        # 2. Process Incoming Data
        if event.type == "TIMER":
            if not PedalboardState.is_connected:
                return {"PASS_THROUGH"}

            while not data_queue.empty():
                try:
                    msg = data_queue.get_nowait()
                    data = json.loads(msg)

                    # The 'track_id' from C++ must match the 'channel' number in Blender
                    target_channel = data.get("track_id")
                    new_vol = data.get("volume")

                    if target_channel is not None:
                        scene = bpy.context.scene
                        if scene.sequence_editor:
                            if "channel_masters" not in scene:
                                scene["channel_masters"] = {}

                            # 1. Track fader movement
                            old_fader_val = scene["channel_masters"].get(
                                str(target_channel), 1.0
                            )
                            scene["channel_masters"][str(target_channel)] = new_vol

                            # Is the C++ fader currently moving?
                            fader_is_moving = abs(new_vol - old_fader_val) > 0.0001

                            for strip in scene.sequence_editor.sequences:
                                if (
                                    strip.type == "SOUND"
                                    and strip.channel == target_channel
                                ):
                                    if "base_vol" not in strip:
                                        strip["base_vol"] = strip.volume

                                    # 2. DETECT MANUAL BLENDER CHANGES
                                    # We ONLY update base_vol if the fader is NOT moving.
                                    # This prevents the 'Dead Fader' effect.
                                    if not fader_is_moving:
                                        expected_vol = strip["base_vol"] * new_vol
                                        if abs(strip.volume - expected_vol) > 0.001:
                                            # User moved it in Blender; recalculate what 100% should be
                                            if new_vol > 0.01:
                                                strip["base_vol"] = (
                                                    strip.volume / new_vol
                                                )
                                            else:
                                                strip["base_vol"] = strip.volume

                                    # 3. APPLY THE FADER
                                    # This must happen every frame to keep the offset active
                                    strip.volume = strip["base_vol"] * new_vol

                            for area in context.screen.areas:
                                if area.type == "SEQUENCE_EDITOR":
                                    area.tag_redraw()

                except Exception as e:
                    print(f"Pedalboard Modal Error: {e}")

        return {"PASS_THROUGH"}

    def execute(self, context):
        PedalboardState.stop_signal = False
        threading.Thread(target=socket_server_loop, daemon=True).start()
        self._timer = context.window_manager.event_timer_add(
            0.05, window=context.window
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
        PedalboardState.stop_signal = True
        PedalboardState.is_connected = False


class VSE_OT_LaunchExternal(bpy.types.Operator):
    bl_idname = "vse.pedalboard_launch"
    bl_label = "Launch External DAW"

    def execute(self, context):
        if os.path.exists(PedalboardState.executable_path):
            subprocess.Popen(
                [PedalboardState.executable_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            self.report({"INFO"}, "DAW Launched")
        else:
            self.report(
                {"ERROR"}, f"Binary not found: {PedalboardState.executable_path}"
            )
        return {"FINISHED"}


class VSE_OT_StopConnection(bpy.types.Operator):
    bl_idname = "vse.pedalboard_stop"
    bl_label = "Stop Connection"

    def execute(self, context):
        PedalboardState.stop_signal = True
        return {"FINISHED"}


# --- 4. UI PANEL ---
class VSE_PT_PedalboardBridge(bpy.types.Panel):
    bl_label = "Pedalboard Bridge"
    bl_idname = "VSE_PT_pedalboard_bridge"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Pedalboard"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        if PedalboardState.is_connected:
            box.label(text="CONNECTED", icon="CHECKMARK")
            layout.operator("vse.pedalboard_stop", text="Stop Service", icon="PAUSE")
        else:
            box.label(text="DISCONNECTED", icon="CANCEL")
            layout.operator("vse.pedalboard_modal", text="Start Service", icon="PLAY")
        layout.separator(factor=1.5)
        col = layout.column()
        col.scale_y = 1.5
        col.operator("vse.pedalboard_launch", icon="WINDOW")


# --- 5. REGISTRATION ---
classes = (
    VSE_OT_Pedalboard_Modal,
    VSE_OT_LaunchExternal,
    VSE_OT_StopConnection,
    VSE_PT_PedalboardBridge,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


if __name__ == "__main__":
    register()
