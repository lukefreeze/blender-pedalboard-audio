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
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((PedalboardState.host, PedalboardState.port))
        s.listen()
        PedalboardState.is_connected = True

        while not PedalboardState.stop_signal:
            try:
                s.settimeout(0.5)
                conn, addr = s.accept()
                with conn:
                    scene = bpy.context.scene
                    masters = scene.get("channel_masters", {})
                    sync_packet = {
                        "type": "SYNC",
                        "levels": {str(k): v for k, v in masters.items()},
                    }
                    conn.sendall((json.dumps(sync_packet) + "\n").encode())

                    while not PedalboardState.stop_signal:
                        data = conn.recv(1024).decode()
                        if not data:
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
        if PedalboardState.stop_signal:
            self.cancel(context)
            return {"FINISHED"}

        if event.type == "TIMER":
            if not PedalboardState.is_connected:
                return {"PASS_THROUGH"}

            while not data_queue.empty():
                try:
                    msg = data_queue.get_nowait()
                    data = json.loads(msg)
                    target_channel = data.get("track_id")

                    # 1. FORCE FLOAT CONVERSION
                    # This prevents the "0 or 1" integer rounding issue
                    new_vol = float(data.get("volume", 1.0))

                    if target_channel is not None:
                        scene = bpy.context.scene
                        if scene.sequence_editor:
                            if "channel_masters" not in scene:
                                scene["channel_masters"] = {}
                            scene["channel_masters"][str(target_channel)] = new_vol

                            # 2. ADJUST MULTIPLIER
                            # We treat 1.0 as the 'Standard' volume.
                            # If the fader is at 1.5, it boosts; if below 1.0, it cuts.
                            multiplier = new_vol

                            for strip in scene.sequence_editor.sequences:
                                if (
                                    strip.type == "SOUND"
                                    and strip.channel == target_channel
                                ):
                                    # Ensure we have a base volume to multiply against
                                    if "base_vol" not in strip:
                                        strip["base_vol"] = (
                                            strip.volume if strip.volume > 0 else 1.0
                                        )

                                    # 3. APPLY VOLUME
                                    # This scales the strip's original volume by the fader value
                                    strip.volume = strip["base_vol"] * multiplier

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
            # CREATE_NEW_CONSOLE ensures we see the crash error
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


def register():
    bpy.utils.register_class(VSE_OT_Pedalboard_Modal)
    bpy.utils.register_class(VSE_OT_LaunchExternal)
    bpy.utils.register_class(VSE_OT_StopConnection)
    bpy.utils.register_class(VSE_PT_PedalboardBridge)


if __name__ == "__main__":
    register()
