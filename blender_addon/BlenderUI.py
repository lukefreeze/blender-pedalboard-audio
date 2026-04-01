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
                    # --- REPLACED SECTION START ---
                    while not PedalboardState.stop_signal:
                        # 1. Fetch latest data every loop iteration
                        scene = bpy.data.scenes[0]
                        sync_packet = {
                            "type": "SYNC",
                            "frame_current": scene.frame_current,
                            "frame_end": scene.frame_end,
                            "fps": scene.render.fps / scene.render.fps_base,
                        }

                        try:
                            # 2. Push updates to C++ (The Heartbeat)
                            conn.sendall((json.dumps(sync_packet) + "\n").encode())

                            # 3. Listen for Faders (Non-blocking)
                            conn.setblocking(False)
                            data = conn.recv(1024).decode()
                            if data:
                                data_queue.put(data)
                        except (BlockingIOError, socket.error):
                            pass

                        import time

                        time.sleep(0.01)  # Optional: Caps the heartbeat to ~100Hz
                    # --- REPLACED SECTION END ---
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
        if event.type == "TIMER":
            if PedalboardState.stop_signal:
                self.cancel(context)
                return {"FINISHED"}

            # --- ADD THIS LINE HERE ---
            scene = context.scene

            while not data_queue.empty():
                try:
                    msg = data_queue.get_nowait()
                    data = json.loads(msg.strip())

                    target_channel = data.get("track_id")
                    incoming_int = int(data.get("volume", 10000))
                    multiplier = incoming_int / 10000.0

                    if target_channel is not None and scene.sequence_editor:
                        for strip in scene.sequence_editor.sequences:
                            # ... (Rest of your strip logic)
                            if (
                                strip.type == "SOUND"
                                and strip.channel == target_channel
                            ):
                                # 1. Initialization
                                if "base_vol" not in strip:
                                    strip["base_vol"] = strip.volume
                                    print(
                                        f"[Init] Ch {target_channel} Base: {strip.volume}"
                                    )

                                # 2. Handle Manual Override (Blender UI moved)
                                # We check if the strip.volume is different from what we LAST set it to.
                                last_set_vol = strip.get(
                                    "last_applied_total", strip.volume
                                )

                                if abs(strip.volume - last_set_vol) > 0.001:
                                    # If the user changed the slider, adjust the anchor
                                    if multiplier > 0.01:
                                        strip["base_vol"] = strip.volume / multiplier
                                        print(
                                            f"[Blender] Manual Override! New Base: {strip['base_vol']:.3f}"
                                        )

                                # 3. Calculate and Apply
                                # Apply the current multiplier to the potentially NEW base_vol
                                new_total = strip["base_vol"] * multiplier
                                strip.volume = new_total

                                # 4. Update tracking properties
                                strip["last_applied_total"] = new_total
                                strip["last_int"] = incoming_int

                                # Debug Logging
                                if incoming_int != strip.get("prev_log_int"):
                                    print(
                                        f"[DAW -> Blender] Ch {target_channel} Val: {incoming_int}"
                                    )
                                    strip["prev_log_int"] = incoming_int

                        # Trigger UI Refresh
                        for area in context.screen.areas:
                            if area.type == "SEQUENCE_EDITOR":
                                area.tag_redraw()

                except Exception as e:
                    print(f"[Blender] Sync Error: {e}")

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
