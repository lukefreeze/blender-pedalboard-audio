# =============================================================================
# ui/mixer/mixer_hud.py
# The GPU draw callback that paints the entire HUD onto the Node Editor area.
# Also owns the UI state globals (UI_SCALE, SCROLL_X/Y) so other modules
# can import them without circular dependencies.
# =============================================================================

import bpy
import gpu
import blf

from ui.mixer.draw_utils import draw_rect, draw_rounded_rect
from ui.mixer.channel_strip import (
    draw_channel_strip, send_section_height,
    STRIP_LEFT_MARGIN, STRIP_W, STRIP_STRIDE,
)
from core.constants import (
    DEFAULT_CHANNELS, MAX_CHANNELS,
    AUTOFIT_CONTENT_W, AUTOFIT_CONTENT_H, AUTOFIT_PADDING,
    SB_TRACK_PX, SB_THUMB_PX, SB_INSET, SB_MARGIN, SB_RADIUS,
    SB_H_RANGE, SB_V_RANGE,
)

# ---------------------------------------------------------------------------
# UI state — imported by interaction.py and Loader.py
# ---------------------------------------------------------------------------
UI_SCALE  = 1.0
SCROLL_X  = 0.0
SCROLL_Y  = 0.0

pb_ui_enabled = False
HUD_AREA_PTR  = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def save_ui_state() -> None:
    scene = bpy.context.scene
    if not scene:
        return
    scene.pb_ui_scale    = UI_SCALE
    scene.pb_ui_scroll_x = SCROLL_X
    scene.pb_ui_scroll_y = SCROLL_Y
    scene.pb_ui_enabled  = pb_ui_enabled


def load_ui_state() -> None:
    global UI_SCALE, SCROLL_X, SCROLL_Y, pb_ui_enabled
    scene = bpy.context.scene
    if not scene:
        return
    UI_SCALE      = getattr(scene, "pb_ui_scale",    1.0)
    SCROLL_X      = getattr(scene, "pb_ui_scroll_x", 0.0)
    SCROLL_Y      = getattr(scene, "pb_ui_scroll_y", 0.0)
    pb_ui_enabled = getattr(scene, "pb_ui_enabled",  False)
    print(f"[STATE] scale={round(UI_SCALE,2)} "
          f"scroll=({round(SCROLL_X)},{round(SCROLL_Y)}) "
          f"enabled={pb_ui_enabled}")


def compute_autofit(draw_w: float, draw_h: float):
    """Return (UI_SCALE, SCROLL_X, SCROLL_Y) to centre the mixer in the canvas."""
    fit_x   = draw_w / AUTOFIT_CONTENT_W
    fit_y   = draw_h / AUTOFIT_CONTENT_H
    scale   = max(0.35, min(2.0, min(fit_x, fit_y) * AUTOFIT_PADDING))
    scr_x   = (draw_w - AUTOFIT_CONTENT_W * scale) / 2.0 - 30.0 * scale
    scr_y   = draw_h / 2.0 - 550.0 * scale
    return scale, scr_x, scr_y


# ---------------------------------------------------------------------------
# Main draw callback — registered on SpaceNodeEditor in Loader.py
# ---------------------------------------------------------------------------
def draw_callback_px(self, context) -> None:
    global pb_ui_enabled, UI_SCALE, SCROLL_X, SCROLL_Y

    if not pb_ui_enabled:
        return
    if HUD_AREA_PTR is not None and bpy.context.area is not None:
        if bpy.context.area.as_pointer() != HUD_AREA_PTR:
            return

    region = bpy.context.region
    if not region:
        return
    width, height = region.width, region.height

    try:
        gpu.state.blend_set("ALPHA")

        # Background
        draw_rect(0, 0, width, height, (0.01, 0.01, 0.01, 0.95))

        # Header label
        blf.color(0, 1, 1, 1, 1)
        blf.size(0, int(20 * UI_SCALE))
        blf.position(0, STRIP_LEFT_MARGIN*UI_SCALE + SCROLL_X,
                     height - 40*UI_SCALE - SCROLL_Y, 0)
        blf.draw(0, f"PEDALBOARD HUD | Scale: {round(UI_SCALE, 2)}")

        tracks = getattr(bpy.context.scene, "pb_sync_tracks", [])
        base_y = height - 150*UI_SCALE - SCROLL_Y

        # Import meter state from core/meters.py
        from core.meters import _engine_levels, _peak_hold

        draw_col = 0
        for i, track in enumerate(tracks):
            group_idx = i // 9
            sx = STRIP_LEFT_MARGIN*UI_SCALE + draw_col*STRIP_STRIDE*UI_SCALE + SCROLL_X
            draw_col += 1
            if sx + STRIP_W*UI_SCALE < 0 or sx > width:
                continue

            eng   = _engine_levels[i] if i < MAX_CHANNELS else 0.0
            peak  = _peak_hold[i]     if i < MAX_CHANNELS else 0.0
            draw_channel_strip(i, track, sx, base_y, UI_SCALE, tracks,
                               eng, peak, group_idx)

        # Racks
        try:
            from Racks import draw_racks
            draw_racks(width, height, SCROLL_X, SCROLL_Y, UI_SCALE)
        except Exception as e:
            print(f"[RACKS] draw error: {e}")

        # Scrollbars
        _draw_scrollbars(width, height)

    except Exception as e:
        print(f"DRAW ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        try:
            gpu.state.blend_set("NONE")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Scrollbars
# ---------------------------------------------------------------------------
def _draw_scrollbars(width: float, height: float) -> None:
    t   = SB_TRACK_PX
    th  = SB_THUMB_PX
    ins = SB_INSET
    mar = SB_MARGIN
    r   = SB_RADIUS

    # Horizontal
    h_track_y = mar
    h_track_w = width - t - mar * 2
    draw_rect(mar, h_track_y, h_track_w, t, (0.10, 0.10, 0.10, 0.55))
    h_thumb_w = max(30, int(h_track_w * 0.12))
    h_travel  = h_track_w - h_thumb_w
    h_frac    = min(1.0, abs(SCROLL_X) / max(1.0, SB_H_RANGE))
    h_thumb_x = mar + int(h_frac * h_travel)
    draw_rounded_rect(h_thumb_x, h_track_y + ins, h_thumb_w, th, r,
                      (0.50, 0.50, 0.50, 0.80))

    # Vertical
    v_track_x = width - t - mar
    v_track_h = height - t - mar * 2
    draw_rect(v_track_x, mar + t, t, v_track_h, (0.10, 0.10, 0.10, 0.55))
    v_thumb_h = max(20, int(v_track_h * 0.18))
    v_travel  = v_track_h - v_thumb_h
    v_frac    = min(1.0, abs(SCROLL_Y) / max(1.0, SB_V_RANGE))
    v_thumb_y = mar + t + v_travel - int(v_frac * v_travel)
    draw_rounded_rect(v_track_x + ins, v_thumb_y, th, v_thumb_h, r,
                      (0.50, 0.50, 0.50, 0.80))
