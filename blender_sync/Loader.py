"""
Loader.py — slim orchestrator for the Pedalboard HUD addon.

Responsibilities:
  - sys.path bootstrap (adds blender_sync/ and subpackages)
  - Imports and wires together core/* and ui/mixer/*
  - Blender registration (classes, scene props, draw handler, app handlers)
  - VSE_OT_TogglePBGui — the launch button operator

Everything else lives in:
  core/constants.py    — every layout number
  core/engine.py       — .pyd import, pedalboard wheel install
  core/audio.py        — DSP, playback handlers, effect chain
  core/meters.py       — VU meter timer, envelope cache
  core/properties.py   — PB_TrackSettings, scene props
  ui/mixer/draw_utils.py    — GPU primitives + PNG bridge
  ui/mixer/texture_cache.py — PNG → gpu.texture loader
  ui/mixer/channel_strip.py — one fader strip
  ui/mixer/mixer_hud.py     — draw_callback_px, scrollbars, UI state
  ui/mixer/interaction.py   — modal operator, hit testing
  ui/racks/                 — one file per rack type
  Racks.py                  — rack data + public draw API
"""

import sys
import os

# ---------------------------------------------------------------------------
# Path bootstrap — must happen before any other import
# Only add blender_sync/ itself to sys.path, NOT subdirectories.
# Sub-packages (core/, ui/) use relative imports and must be loaded as packages,
# not as top-level modules — adding their directories breaks relative imports.
# ---------------------------------------------------------------------------
_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)

# ---------------------------------------------------------------------------
# Core imports
# ---------------------------------------------------------------------------
from core.engine import get_engine, reset_engine, PEDALBOARD_AVAILABLE, HIJACKER_AVAILABLE
from core.properties import register_properties, unregister_properties

# Re-export constants that Racks.py imports from Loader
from core.constants import FADER_TRACK_BOTTOM, NUMBOX_H, NUMBOX_Y_OFFSET
# _send_section_height is used by Racks.py
from ui.mixer.channel_strip import send_section_height as _send_section_height

# ---------------------------------------------------------------------------
# Audio engine imports — keep them lazy where possible to avoid
# circular imports at module load time
# ---------------------------------------------------------------------------
from core.audio import (
    _pb_engine_enable, _pb_engine_disable,
    _fft_timeline, _fft_timeline_full, _gr_timeline,
)

from core.meters import (
    _meter_timer, _ensure_meter_timer, _cancel_meter_timer,
    _engine_levels, _peak_hold, _peak_hold_timer,
)

# ---------------------------------------------------------------------------
# UI imports
# ---------------------------------------------------------------------------
from ui.mixer.mixer_hud import (
    draw_callback_px,
    UI_SCALE, SCROLL_X, SCROLL_Y,
    pb_ui_enabled, HUD_AREA_PTR,
    save_ui_state, load_ui_state, compute_autofit,
)
import ui.mixer.mixer_hud as _hud

from ui.mixer.interaction import (
    VSE_OT_SetFaderValue,
    VSE_OT_PB_Interaction,
    _sync_tracks_to_vse,
    _get_active_channel_count,
)
from ui.mixer.texture_cache import set_skin_dir, clear_cache as clear_texture_cache

# Racks public API
try:
    from Racks import (
        draw_racks, register_racks, unregister_racks,
        handle_click as racks_handle_click,
        hit_test as racks_hit_test,
        update_led_states,
        rack_knob_hit_test,
    )
    print("[RACKS] Racks.py loaded")
except ImportError as e:
    print(f"[RACKS] WARNING: could not import Racks.py: {e}")
    def draw_racks(*a, **kw): pass
    def register_racks(): pass
    def unregister_racks(): pass
    def racks_handle_click(*a, **kw): return False
    def racks_hit_test(*a, **kw): return None
    def update_led_states(*a, **kw): pass
    def rack_knob_hit_test(*a, **kw): return None

import bpy

# ---------------------------------------------------------------------------
# Helper: set global in mixer_hud module (avoids re-importing everywhere)
# ---------------------------------------------------------------------------
def _set_hud(key, value):
    setattr(_hud, key, value)

def _get_hud(key):
    return getattr(_hud, key)


# ---------------------------------------------------------------------------
# VSE_OT_TogglePBGui — the launch button
# ---------------------------------------------------------------------------
class VSE_OT_TogglePBGui(bpy.types.Operator):
    bl_idname = "vse.toggle_pb_gui"
    bl_label  = "Toggle The Hijacker"

    def execute(self, context):
        global pb_ui_enabled
        load_ui_state()
        new_state = not _get_hud('pb_ui_enabled')
        _set_hud('pb_ui_enabled', new_state)
        print(f"[TOGGLE] pb_ui_enabled={new_state}")

        if new_state:
            # Pick the largest Node Editor WINDOW as the HUD canvas
            best_ptr = context.area.as_pointer()
            best_w   = 0
            best_h   = 0
            for area in context.screen.areas:
                if area.type == 'NODE_EDITOR':
                    for rgn in area.regions:
                        if rgn.type == 'WINDOW':
                            if rgn.width * rgn.height > best_w * best_h:
                                best_w   = rgn.width
                                best_h   = rgn.height
                                best_ptr = area.as_pointer()

            _set_hud('HUD_AREA_PTR', best_ptr)
            print(f"[TOGGLE] HUD area ptr={best_ptr} "
                  f"NODE_EDITOR WINDOW={best_w}x{best_h}")

            # Auto-fit: scale + centre
            try:
                dw = best_w if best_w > 100 else context.region.width
                dh = best_h if best_h > 100 else context.region.height
                scale, scr_x, scr_y = compute_autofit(dw, dh)
                _set_hud('UI_SCALE',  scale)
                _set_hud('SCROLL_X',  scr_x)
                _set_hud('SCROLL_Y',  scr_y)
                print(f"[TOGGLE] auto-fit UI_SCALE={scale:.3f} "
                      f"canvas={dw}x{dh} "
                      f"scroll=({scr_x:.1f},{scr_y:.1f})")
            except Exception as fe:
                _set_hud('UI_SCALE', 1.0)
                _set_hud('SCROLL_X', 0.0)
                _set_hud('SCROLL_Y', 0.0)
                print(f"[TOGGLE] auto-fit failed: {fe}")

            save_ui_state()
            bpy.ops.vse.pb_interaction("INVOKE_DEFAULT")
            _ensure_meter_timer()
            _pb_engine_enable()
            # Ensure tracks exist even on blank scenes
            try:
                _sync_tracks_to_vse(context.scene, reset_values=False)
            except Exception as _se:
                print(f"[TOGGLE] track sync failed: {_se}")
        else:
            _set_hud('HUD_AREA_PTR', None)
            save_ui_state()
            _cancel_meter_timer()
            _pb_engine_disable()

        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# VSE_OT_RefreshPBTracks
# ---------------------------------------------------------------------------
class VSE_OT_RefreshPBTracks(bpy.types.Operator):
    bl_idname     = "vse.refresh_pb_tracks"
    bl_label      = "Refresh Tracks"
    bl_description = "Sync fader strips with current VSE channels"

    def execute(self, context):
        _sync_tracks_to_vse(context.scene, reset_values=False)
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------
class VSE_PT_Pedalboard_Panel(bpy.types.Panel):
    bl_label       = "The Hijacker"
    bl_space_type  = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category    = "Hijacker"

    def draw(self, context):
        self.layout.operator("vse.refresh_pb_tracks")
        self.layout.operator("vse.toggle_pb_gui")


def draw_header_buttons(self, context):
    self.layout.operator("vse.toggle_pb_gui", text="The Hijacker")


# ---------------------------------------------------------------------------
# Load handler
# ---------------------------------------------------------------------------
@bpy.app.handlers.persistent
def on_load_post(filepath, *args):
    from core.engine import reset_engine
    from core.audio  import _pb_engine_disable as _disable
    _disable()
    clear_texture_cache()
    reset_engine()
    load_ui_state()
    if _get_hud('pb_ui_enabled'):
        bpy.app.timers.register(_deferred_invoke, first_interval=0.1)


def _deferred_invoke():
    try:
        bpy.ops.vse.pb_interaction("INVOKE_DEFAULT")
        _ensure_meter_timer()
        _pb_engine_enable()
        # Ensure tracks are synced even on blank scenes
        try:
            scene = bpy.context.scene
            if scene:
                _sync_tracks_to_vse(scene, reset_values=False)
        except Exception as se:
            print(f"[STATE] track sync failed: {se}")
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception as e:
        print(f"[STATE] deferred invoke failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
from core.properties import PB_TrackSettings

classes = (
    VSE_OT_SetFaderValue,
    VSE_OT_TogglePBGui,
    VSE_OT_RefreshPBTracks,
    VSE_OT_PB_Interaction,
    VSE_PT_Pedalboard_Panel,
)

_handle = None


def register():
    global _handle
    from core.constants import ASSETS_DIR
    set_skin_dir(ASSETS_DIR, "default")

    register_properties()
    register_racks()
    for cls in classes:
        # Safely unregister first in case of hot-reload or double-registration
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
        bpy.utils.register_class(cls)

    bpy.types.NODE_HT_header.append(draw_header_buttons)
    _handle = bpy.types.SpaceNodeEditor.draw_handler_add(
        draw_callback_px, (None, None), "WINDOW", "POST_PIXEL")

    if on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)

    print("[REGISTER] The Hijacker registered")


def unregister():
    global _handle
    _cancel_meter_timer()
    _pb_engine_disable()
    unregister_racks()

    if on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)

    if _handle:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, "WINDOW")

    bpy.types.NODE_HT_header.remove(draw_header_buttons)

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    unregister_properties()
    print("[UNREGISTER] The Hijacker unregistered")


if __name__ == "__main__":
    register()
