import json
import os
import queue
import socket
import time

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

        PedalboardState.is_connected = True

        while not PedalboardState.stop_signal:
            try:
                s.settimeout(0.5)
                try:
                    conn, addr = s.accept()
                except socket.timeout:
                    continue

                with conn:
                    print(f"--- NEW CONNECTION: {addr} ---")
                    time.sleep(0.1)

                    # --- RESTORE FADERS ON HANDSHAKE ---
                    if bpy.context.scene.sequence_editor:
                        for strip in bpy.context.scene.sequence_editor.sequences:
                            if strip.type == "SOUND" and "fader_pos" in strip:
                                print(
                                    f"RESTORE: Sending Ch {strip.channel} -> {strip['fader_pos']}"
                                )
                                restore_pkt = {
                                    "type": "RESTORE",
                                    "track_id": strip.channel,
                                    "volume": int(strip["fader_pos"] * 10000),
                                }
                                conn.sendall((json.dumps(restore_pkt) + "\n").encode())

                    # --- MAIN SYNC LOOP ---
                    while not PedalboardState.stop_signal:
                        try:
                            scene = bpy.context.scene
                            sync_packet = {
                                "type": "SYNC",
                                "frame_current": scene.frame_current,
                                "frame_end": scene.frame_end,
                                "fps": scene.render.fps / scene.render.fps_base,
                                "is_playing": any(
                                    scr.is_animation_playing for scr in bpy.data.screens
                                ),
                            }

                            conn.sendall((json.dumps(sync_packet) + "\n").encode())

                            conn.setblocking(False)
                            try:
                                data = conn.recv(4096).decode()
                                if data:
                                    PedalboardState.data_queue.put(data)
                                elif data == "":
                                    print("Connection closed by Tool.")
                                    break
                            except (BlockingIOError, socket.error):
                                pass

                            time.sleep(0.01)

                        except (socket.error, BrokenPipeError):
                            print("Socket Pipe Broken. Tool likely closed.")
                            break

            except Exception as e:
                print(f"Connection Reset: {e}")
                continue

        PedalboardState.is_connected = False
