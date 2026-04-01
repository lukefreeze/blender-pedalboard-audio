import json
import os
import queue
import socket

import bpy


class PedalboardState:
    host = "127.0.0.1"
    port = 65432
    is_connected = False
    stop_signal = False
    data_queue = queue.Queue()
    executable_path = r"C:\Users\lukeb\Documents\BlenderTool\build\PedalboardDAW.exe"


def socket_server_loop():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((PedalboardState.host, PedalboardState.port))
        s.listen()

        # FIXED: Removed the cite tags
        PedalboardState.is_connected = True

        while not PedalboardState.stop_signal:
            try:
                s.settimeout(0.5)
                conn, addr = s.accept()
                with conn:
                    while not PedalboardState.stop_signal:
                        scene = bpy.data.scenes[0]
                        sync_packet = {
                            "type": "SYNC",
                            "frame_current": scene.frame_current,
                            "frame_end": scene.frame_end,
                            "fps": scene.render.fps / scene.render.fps_base,
                            "is_playing": any(
                                screen.is_animation_playing
                                for screen in bpy.data.screens
                            ),
                        }
                        try:
                            conn.sendall((json.dumps(sync_packet) + "\n").encode())
                            conn.setblocking(False)
                            data = conn.recv(1024).decode()
                            if data:
                                PedalboardState.data_queue.put(data)
                        except (BlockingIOError, socket.error):
                            import time

                            time.sleep(0.01)
                            pass
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Socket Error: {e}")
                break

        # FIXED: Removed the cite tags
        PedalboardState.is_connected = False
