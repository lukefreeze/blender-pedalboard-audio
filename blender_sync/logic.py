import json

import bpy


def process_command(msg):
    # Split to handle clumped packets and prevent 'Extra Data' errors
    for raw_msg in msg.strip().split("\n"):
        if not raw_msg.strip():
            continue

        try:
            data = json.loads(raw_msg)
            scene = bpy.context.scene

            # --- 1. HANDLE TRANSPORT COMMANDS (Play/Stop/Scrub) ---
            if "command" in data:
                cmd = data["command"]
                if cmd == "toggle_play":
                    bpy.ops.screen.animation_play()
                elif cmd == "set_frame":
                    new_frame = data.get("value") or data.get("frame") or 1
                    scene.frame_set(int(new_frame))
                continue  # Move to next message, don't run volume logic on a transport command

            # --- 2. HANDLE VOLUME LOGIC ---
            if "track_id" in data:
                tid = data["track_id"]

                # Convert 0-10000 back to 0.0-1.0
                raw_in = data.get("volume", 10000)
                multiplier = float(raw_in) / 10000.0

                # EMERGENCY CRITICAL FIX:
                # If C++ sends 99990000, multiplier becomes 9999.0.
                # We force it to 1.0 (100%) so it doesn't snap the audio to 100.0 (Max)
                if multiplier > 1.0:
                    multiplier = 1.0

                if scene.sequence_editor:
                    for strip in scene.sequence_editor.sequences:
                        if strip.type == "SOUND" and strip.channel == tid:
                            # A) INITIAL CAPTURE: Store original volume on first touch
                            if "base_vol" not in strip:
                                strip["base_vol"] = strip.volume
                                print(f"DEBUG: Captured Base Vol: {strip['base_vol']}")

                            # B) MANUAL OVERRIDE: If you moved the slider in Blender UI
                            last_applied = strip.get("last_applied_total", strip.volume)
                            if abs(strip.volume - last_applied) > 0.001:
                                if multiplier > 0.01:
                                    strip["base_vol"] = strip.volume / multiplier
                                else:
                                    strip["base_vol"] = strip.volume

                            # C) APPLY CALCULATION: (Original/Base) * (Fader Position %)
                            new_vol = strip["base_vol"] * multiplier

                            # Clamp at 100.0 (Blender's max)
                            strip.volume = min(max(new_vol, 0.0), 100.0)
                            strip["last_applied_total"] = strip.volume

                            # Keep debugs on for now so you can verify the fix
                            print(
                                f"DEBUG MATH: {strip['base_vol']:.2f} * {multiplier:.4f} = {strip.volume:.2f}"
                            )

                # Refresh Blender UI to show fader movement
                for area in bpy.context.screen.areas:
                    if area.type == "SEQUENCE_EDITOR":
                        area.tag_redraw()

        except Exception as e:
            print(f"LOGIC ERROR: {e} | String: {raw_msg}")
