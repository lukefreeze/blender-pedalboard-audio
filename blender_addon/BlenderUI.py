import bpy
import socket
import threading
import queue
import subprocess
import os

# --- 1. GLOBAL STATE ---
data_queue = queue.Queue()

class PedalboardState:
    is_connected = False
    stop_signal = False
    host = '127.0.0.1'
    port = 65432
    # CHANGE THIS to your compiled C++ binary path
    executable_path = "C:/Path/To/Your/Project/build/PedalboardDAW.exe"

# --- 2. THE SOCKET THREAD ---
def socket_server_loop():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind((PedalboardState.host, PedalboardState.port))
            s.listen()
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
        if PedalboardState.stop_signal or not PedalboardState.is_connected:
            self.cancel(context)
            return {'FINISHED'}

        if event.type == 'TIMER':
            while not data_queue.empty():
                msg = data_queue.get()
                # Example protocol: "VOL:0.75"
                if msg.startswith("VOL:"):
                    try:
                        val = float(msg.split(":")[1])
                        strip = context.scene.sequence_editor.active_strip
                        if strip and strip.type == 'SOUND':
                            strip.volume = val
                            context.area.tag_redraw()
                    except: pass

        return {'PASS_THROUGH'}

    def execute(self, context):
        PedalboardState.stop_signal = False
        PedalboardState.is_connected = True
        threading.Thread(target=socket_server_loop, daemon=True).start()
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        context.window_manager.event_timer_remove(self._timer)
        PedalboardState.stop_signal = True
        PedalboardState.is_connected = False

class VSE_OT_LaunchExternal(bpy.types.Operator):
    bl_idname = "vse.pedalboard_launch"
    bl_label = "Launch External DAW"
    
    def execute(self, context):
        if os.path.exists(PedalboardState.executable_path):
            subprocess.Popen([PedalboardState.executable_path])
            self.report({'INFO'}, "DAW Launched")
        else:
            self.report({'ERROR'}, f"Binary not found at {PedalboardState.executable_path}")
        return {'FINISHED'}

class VSE_OT_StopConnection(bpy.types.Operator):
    bl_idname = "vse.pedalboard_stop"
    bl_label = "Stop Connection"
    def execute(self, context):
        PedalboardState.stop_signal = True
        return {'FINISHED'}

# --- 4. UI PANEL ---
class VSE_PT_PedalboardBridge(bpy.types.Panel):
    bl_label = "Pedalboard Bridge"
    bl_idname = "VSE_PT_pedalboard_bridge"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Pedalboard"

    def draw(self, context):
        layout = self.layout
        
        # Connection Status Display
        box = layout.box()
        if PedalboardState.is_connected:
            box.label(text="CONNECTED", icon='CHECKMARK')
            layout.operator("vse.pedalboard_stop", text="Stop Service", icon='PAUSE')
        else:
            box.label(text="DISCONNECTED", icon='CANCEL')
            layout.operator("vse.pedalboard_modal", text="Start Service", icon='PLAY')

        layout.separator(factor=1.5)
        
        # Launch Button
        col = layout.column()
        col.scale_y = 1.5
        col.operator("vse.pedalboard_launch", icon='WINDOW')

def register():
    bpy.utils.register_class(VSE_OT_Pedalboard_Modal)
    bpy.utils.register_class(VSE_OT_LaunchExternal)
    bpy.utils.register_class(VSE_OT_StopConnection)
    bpy.utils.register_class(VSE_PT_PedalboardBridge)

if __name__ == "__main__":
    register()