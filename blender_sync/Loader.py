import importlib
import os
import subprocess
import sys
import threading

import bpy

# Ensure paths are correct
dir_path = r"C:\Users\lukeb\Documents\BlenderTool"
if dir_path not in sys.path:
    sys.path.append(dir_path)

import blender_sync.logic
import blender_sync.network

importlib.reload(blender_sync.network)
importlib.reload(blender_sync.logic)

from blender_sync.logic import process_command
from blender_sync.network import PedalboardState, socket_server_loop


# --- OPERATORS ---
class VSE_OT_Pedalboard_Start(bpy.types.Operator):
    bl_idname = "vse.pedalboard_start"
    bl_label = "Start Pedalboard Service"
    _timer = None

    def modal(self, context, event):
        if event.type == "TIMER":
            if PedalboardState.stop_signal:
                context.window_manager.event_timer_remove(self._timer)
                return {"FINISHED"}

            while not PedalboardState.data_queue.empty():
                msg = PedalboardState.data_queue.get()
                process_command(msg)

        return {"PASS_THROUGH"}

    def execute(self, context):
        PedalboardState.stop_signal = False
        threading.Thread(target=socket_server_loop, daemon=True).start()
        self._timer = context.window_manager.event_timer_add(
            0.01, window=context.window
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}


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
        from blender_sync.network import PedalboardState

        PedalboardState.stop_signal = True
        return {"FINISHED"}


# --- PANEL ---
class VSE_PT_PedalboardBridge(bpy.types.Panel):
    bl_label = "Pedalboard Bridge (Modular)"
    bl_idname = "VSE_PT_pedalboard_bridge"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Pedalboard"

    def draw(self, context):
        layout = self.layout
        if PedalboardState.is_connected:
            layout.label(text="CONNECTED", icon="CHECKMARK")
            layout.operator("vse.pedalboard_stop", text="Stop Service", icon="PAUSE")
        else:
            layout.label(text="DISCONNECTED", icon="CANCEL")
            layout.operator("vse.pedalboard_start", text="Start Service", icon="PLAY")

        layout.separator()
        layout.operator("vse.pedalboard_launch", icon="WINDOW")


# --- REGISTRATION ---
classes = (
    VSE_OT_Pedalboard_Start,
    VSE_OT_LaunchExternal,
    VSE_OT_StopConnection,
    VSE_PT_PedalboardBridge,
)


def register():
    # Helper to stop "already registered" errors[cite: 1]
    for cls in classes:
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)
        bpy.utils.register_class(cls)


if __name__ == "__main__":
    register()
