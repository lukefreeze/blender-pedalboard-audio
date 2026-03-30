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
    # Ensure this matches your actual compiled .exe location
    executable_path = r"C:\Users\lukeb\Documents\BlenderTool\build\PedalboardDAW.exe"


# --- 2. THE SOCKET THREAD ---
def socket_server_loop():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind((PedalboardState.host, PedalboardState.port))
            s.listen()
            PedalboardState.is_connected = True  # Set connected state
            while not PedalboardState.stop_signal:
                try:
                    conn, addr = s.accept()
                    with conn:
                        data = conn.recv(1024).decode()
                        if data:
                            data_queue.put(data)
                except socket.timeout:
                    continue
        except Exception as e:
            print(f"Socket Error: {e}")
        finally:
            PedalboardState.is_connected = False


# --- 3. OPERATORS ---
class VSE_OT_Pedalboard_Modal(bpy.types.Operator):
    bl_idname = "vse.pedalboard_modal"
    bl_label = "Connect to DAW"
    _timer = None

    def modal(self, context, event):
        # Always allow the timer to pass through
        if event.type == "TIMER":
            while not data_queue.empty():
                try:
                    msg = data_queue.get_nowait()
                    data = json.loads(msg)

                    # Search the whole scene for the strip if active_strip fails
                    target_strip = context.scene.sequence_editor.active_strip
                    if not target_strip or target_strip.type != "SOUND":
                        # Fallback: Find by name if provided, or just use selected
                        target_strip = next(
                            (
                                s
                                for s in context.selected_sequences
                                if s.type == "SOUND"
                            ),
                            None,
                        )

                    if target_strip:
                        target_strip.volume = data.get("volume", target_strip.volume)
                        target_strip.pan = data.get("pan", target_strip.pan)
                        # This tells Blender: "Don't just change the data, REDRAW the UI"
                        for area in context.screen.areas:
                            if area.type == "SEQUENCE_EDITOR":
                                area.tag_redraw()
                except Exception as e:
                    print(f"Update caught error: {e}")

            return {"PASS_THROUGH"}

        # If we stop the service, kill the modal
        if PedalboardState.stop_signal:
            self.cancel(context)
            return {"FINISHED"}

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
            # This 'creationflags' trick opens a new, separate CMD window
            # so you can see C++ errors even if the app crashes.
            subprocess.Popen(
                [PedalboardState.executable_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            self.report({"INFO"}, "DAW Launched")
        else:
            self.report(
                {"ERROR"}, f"Binary not found at: {PedalboardState.executable_path}"
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


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
