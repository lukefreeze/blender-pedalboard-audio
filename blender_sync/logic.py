import json

import bpy


def process_command(msg):
    try:
        data = json.loads(msg.strip())
        scene = bpy.context.scene
        # --- NEW: Handle Transport Commands ---
        if "command" in data:
            cmd = data["command"]
            if cmd == "toggle_play":
                bpy.ops.screen.animation_play()
            elif cmd == "set_frame":
                # Check for "value" (new) or "frame" (old) to be safe
                new_frame = data.get("value") or data.get("frame") or 1
                scene.frame_set(int(new_frame))
            return  # Exit early if it was a command

        # 1. VOLUME SYNC LOGIC
        target_channel = data.get("track_id")
        # Safety: Default to 10000 if volume is missing to prevent crashes
        incoming_int = int(data.get("volume", 10000))
        multiplier = incoming_int / 10000.0

        if target_channel is not None and scene.sequence_editor:
            for strip in scene.sequence_editor.sequences:
                if strip.type == "SOUND" and strip.channel == target_channel:
                    if "base_vol" not in strip:
                        strip["base_vol"] = strip.volume

                    last_set_vol = strip.get("last_applied_total", strip.volume)
                    if abs(strip.volume - last_set_vol) > 0.001:
                        # Safety: Don't divide by zero if multiplier is 0
                        if multiplier > 0.001:
                            strip["base_vol"] = strip.volume / multiplier

                    new_total = strip["base_vol"] * multiplier
                    strip.volume = min(max(new_total, 0.0), 10.0)  # Clamp values
                    strip["last_applied_total"] = strip.volume

            # Refresh UI[cite: 1]
            for area in bpy.context.screen.areas:
                if area.type == "SEQUENCE_EDITOR":
                    area.tag_redraw()

    except Exception as e:
        print(f"Logic Error: {e}")
