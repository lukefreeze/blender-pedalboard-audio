# =============================================================================
# core/properties.py
# Blender PropertyGroup definitions and scene property registration.
#
# Constants are inlined here rather than imported from constants.py.
# This avoids any relative import issues regardless of how Python loads
# this file (as core.properties or as a top-level module).
# =============================================================================

import bpy

# Inlined from core/constants.py — kept in sync manually
FADER_MIN    = 0.001
FADER_MAX    = 1.25
GAIN_MIN     = 0.25
GAIN_MAX     = 4.0
GAIN_DEFAULT = 1.0


class PB_TrackSettings(bpy.types.PropertyGroup):
    mute:    bpy.props.BoolProperty(default=False)
    solo:    bpy.props.BoolProperty(default=False)
    volume:  bpy.props.FloatProperty(
        default=1.0, min=FADER_MIN, max=FADER_MAX,
        description="Channel fader (proportional multiplier on strip volumes)")
    gain:    bpy.props.FloatProperty(
        default=GAIN_DEFAULT, min=GAIN_MIN, max=GAIN_MAX)
    pan:     bpy.props.FloatProperty(
        default=0.5, min=0.0, max=1.0,
        description="Stereo pan (0=full L, 0.5=centre, 1=full R)")
    eq_high: bpy.props.FloatProperty(default=0.0, min=-24.0, max=24.0)
    eq_mid:  bpy.props.FloatProperty(default=0.0, min=-24.0, max=24.0)
    eq_low:  bpy.props.FloatProperty(default=0.0, min=-24.0, max=24.0)


def register_properties():
    bpy.utils.register_class(PB_TrackSettings)
    bpy.types.Scene.pb_sync_tracks = bpy.props.CollectionProperty(
        type=PB_TrackSettings)
    bpy.types.Scene.pb_ui_scale    = bpy.props.FloatProperty(default=1.0)
    bpy.types.Scene.pb_ui_scroll_x = bpy.props.FloatProperty(default=0.0)
    bpy.types.Scene.pb_ui_scroll_y = bpy.props.FloatProperty(default=0.0)
    bpy.types.Scene.pb_ui_enabled  = bpy.props.BoolProperty(default=False)


def unregister_properties():
    try:
        bpy.utils.unregister_class(PB_TrackSettings)
    except Exception:
        pass
    for attr in ("pb_sync_tracks", "pb_ui_scale",
                 "pb_ui_scroll_x", "pb_ui_scroll_y", "pb_ui_enabled"):
        try:
            delattr(bpy.types.Scene, attr)
        except Exception:
            pass
