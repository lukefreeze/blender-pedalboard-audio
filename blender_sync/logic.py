import json

import bpy


def process_command(msg):
    for raw_msg in msg.strip().split("\n"):
        if not raw_msg.strip(): continue
        try:
            data = json.loads(raw_msg)
            scene = bpy.context.scene
            tid = data.get("track_id") # Get track_id early

            # --- 1. HANDLE COMMANDS (Transport & Mute/Solo) ---
            if "command" in data:
                cmd = data["command"]

                # Transport
                if cmd == "toggle_play":
                    bpy.ops.screen.animation_play()
                elif cmd == "set_frame":
                    new_frame = data.get("value") or 1
                    scene.frame_set(int(new_frame))

                # MUTE LOGIC (Keep this!)
                elif cmd == "toggle_mute" and tid is not None:
                    for strip in scene.sequence_editor.sequences:
                        if strip.type == "SOUND" and strip.channel == tid:
                            strip.mute = data.get("value", False)

                # SOLO LOGIC (Add this!)
                elif cmd == "toggle_solo" and tid is not None:
                    target_strip = None
                    # Find our target first
                    for strip in scene.sequence_editor.sequences:
                        if strip.type == "SOUND" and strip.channel == tid:
                            target_strip = strip
                            break

                    if target_strip:
                        is_solo_active = data.get("value", False)
                        for strip in scene.sequence_editor.sequences:
                            if strip.type == "SOUND":
                                if is_solo_active:
                                    # Mute every strip EXCEPT the one we soloed
                                    strip.mute = (strip != target_strip)
                                else:
                                    # When un-soloing, we unmute everything
                                    strip.mute = False
                continue

            # --- 2. HANDLE VOLUME LOGIC ---
            if tid is not None and "volume" in data:
                # ... [Keep your existing volume multiplier logic here] ...
                #
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
                            # --- ADD THESE TWO LINES BELOW ---
                            strip["fader_pos"] = multiplier
                            # ---------------------------------

                            # Clamp at 100.0 (Blender's max)
                            strip.volume = min(max(new_vol, 0.0), 100.0)

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
