import os
import sys

import bpy

# Tell Python to look in the current folder for our .pyd file
dir = os.path.dirname(os.path.realpath(__file__))
if dir not in sys.path:
    sys.path.append(dir)

try:
    import pedalboard_engine

    ENGINE_LOADED = True
except ImportError:
    ENGINE_LOADED = False


class VSE_OT_TestCPPEngine(bpy.types.Operator):
    """Run a test buffer through the C++ Engine"""

    bl_idname = "vse.test_cpp_engine"
    bl_label = "Test C++ Engine"

    def execute(self, context):
        if not ENGINE_LOADED:
            self.report({"ERROR"}, "C++ Engine (.pyd) not found!")
            return {"CANCELLED"}

        # 1. Create a fake audio buffer
        fake_buffer = [0.1, 0.5, 0.9]

        # 2. Call your C++ wrapper.cpp function
        # This multiplies the buffer by 2.0
        processed = pedalboard_engine.process_buffer(fake_buffer, 2.0)

        # 3. Show result in Blender's UI and Console
        self.report({"INFO"}, f"C++ Success: {processed}")
        print(f"C++ Engine Output: {processed}")

        return {"FINISHED"}


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
            col.operator("vse.test_cpp_engine", icon="SOUND")
        else:
            col.label(text="Engine Status: Not Found", icon="ERROR")
            col.label(text="Check terminal for build errors.")


def register():
    bpy.utils.register_class(VSE_OT_TestCPPEngine)
    bpy.utils.register_class(VSE_PT_PedalboardPanel)


def unregister():
    bpy.utils.unregister_class(VSE_OT_TestCPPEngine)
    bpy.utils.unregister_class(VSE_PT_PedalboardPanel)


if __name__ == "__main__":
    register()
