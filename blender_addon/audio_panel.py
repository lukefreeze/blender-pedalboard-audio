import importlib
import os
import sys

import bpy

# 1. DYNAMIC PATH ALIGNMENT
# Get the directory where this script lives
addon_dir = os.path.dirname(os.path.realpath(__file__))

# Add it to sys.path so we can find the .pyd file
if addon_dir not in sys.path:
    sys.path.append(addon_dir)

# 2. ENGINE INITIALIZATION WITH CACHE CLEARING
ENGINE_LOADED = False

try:
    # If we already tried to import it and failed,
    # we force Python to look again.
    if "pedalboard_engine" in sys.modules:
        importlib.reload(sys.modules["pedalboard_engine"])
    import pedalboard_engine

    ENGINE_LOADED = True
except ImportError as e:
    print(f"Pedalboard Engine Error: {e}")
    ENGINE_LOADED = False


# --- OPERATOR ---
class VSE_OT_TestCPPEngine(bpy.types.Operator):
    """Run a test buffer through the C++ Engine"""

    bl_idname = "vse.test_cpp_engine"
    bl_label = "Test C++ Engine"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not ENGINE_LOADED:
            self.report({"ERROR"}, "C++ Engine (.pyd) not found! Check System Console.")
            return {"CANCELLED"}

        # Simulate a 10ms audio chunk (at 48kHz, this would be 480 samples)
        fake_buffer = [0.1, 0.5, 0.9, -0.2, -0.8]

        # Call the C++ function (Multiplies buffer by gain)
        # In Phase 2, this becomes: pedalboard_engine.denoise(fake_buffer)
        processed = pedalboard_engine.process_buffer(fake_buffer, 2.0)

        self.report({"INFO"}, f"C++ Result: {processed}")
        print(f"C++ Engine Output: {processed}")

        return {"FINISHED"}


# --- PANEL ---
class VSE_PT_PedalboardPanel(bpy.types.Panel):
    bl_label = "Pedalboard Audio"
    bl_idname = "VSE_PT_pedalboard_panel"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Audio Tools"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)

        if ENGINE_LOADED:
            col.label(text="Engine Status: Active", icon="CHECKMARK")
            col.separator()
            col.operator(
                "vse.test_cpp_engine", icon="SOUND", text="Run C++ Denoise Test"
            )
        else:
            col.alert = True
            col.label(text="Engine Status: Not Found", icon="ERROR")
            col.label(text="Missing: pedalboard_engine.pyd", icon="FILE_BACKUP")
            col.operator("wm.console_toggle", text="Open Terminal for Logs")


# --- REGISTRATION ---
classes = (
    VSE_OT_TestCPPEngine,
    VSE_PT_PedalboardPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
