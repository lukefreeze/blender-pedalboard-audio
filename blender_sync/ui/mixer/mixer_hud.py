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
import core.meters as _meters_mod  # import module not values — avoids stale refs after reload

# ---------------------------------------------------------------------------
# UI state — imported by interaction.py and Loader.py
# ---------------------------------------------------------------------------
UI_SCALE  = 1.0
SCROLL_X  = 0.0
SCROLL_Y  = 0.0

pb_ui_enabled = False
HUD_AREA_PTR  = None
_draw_diag_done = False  # print layer order once on first draw


# ---------------------------------------------------------------------------
# Lazy cached wrapper for draw_racks
# A plain module-level import silently binds to a stub because Racks.py is
# registered after mixer_hud imports. Defer lookup to first call and cache.
# ---------------------------------------------------------------------------
_draw_racks_fn = None

def _draw_racks(width, height, scroll_x, scroll_y, ui_scale):
    global _draw_racks_fn
    if _draw_racks_fn is None:
        from Racks import draw_racks as _fn
        _draw_racks_fn = _fn
    _draw_racks_fn(width, height, scroll_x, scroll_y, ui_scale)


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
    global _draw_diag_done; _draw_diag_done = False  # re-print layers on next draw
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
# Background image tuning
# BG_IMG_W / BG_IMG_H — match these to your PNG pixel dimensions.
# BG_X_OFFSET / BG_Y_OFFSET — unscaled px, multiplied by UI_SCALE at draw time.
#   Positive X moves right, negative moves left.
#   Positive Y moves up, negative moves down.
# ---------------------------------------------------------------------------
BG_IMG_W    = 1891.0   # Background.png width  in px  ← update when image changes
BG_IMG_H    = 5210.0   # Background.png height in px  ← update when image changes
BG_X_OFFSET = -300.0   # nudge background left/right
BG_Y_OFFSET = 0.0      # nudge background up/down

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

        # Background — table PNG scrolls with the UI.
        # Falls back to nothing (transparent canvas) if PNG not loaded.
        # Tune BG_X_OFFSET / BG_Y_OFFSET below to reposition the image.
        try:
            from ui.mixer.texture_cache import get_texture as _gt_bg
            from ui.mixer.draw_utils import blit_texture as _bt_bg
            _bg_tex = _gt_bg("background")
            if _bg_tex:
                _bg_w = BG_IMG_W * UI_SCALE
                _bg_h = BG_IMG_H * UI_SCALE
                _bg_x = SCROLL_X + BG_X_OFFSET * UI_SCALE
                _bg_y = height - _bg_h - SCROLL_Y + BG_Y_OFFSET * UI_SCALE
                _bt_bg(_bg_tex, _bg_x, _bg_y, _bg_w, _bg_h, key="background")
            else:
                draw_rect(0, 0, width, height, (0.01, 0.01, 0.01, 0.95))
        except Exception:
            draw_rect(0, 0, width, height, (0.01, 0.01, 0.01, 0.95))

        # Header label
        blf.color(0, 1, 1, 1, 1)
        blf.size(0, int(20 * UI_SCALE))
        blf.position(0, STRIP_LEFT_MARGIN*UI_SCALE + SCROLL_X,
                     height - 40*UI_SCALE - SCROLL_Y, 0)
        blf.draw(0, f"PEDALBOARD HUD | Scale: {round(UI_SCALE, 2)}")

        tracks = getattr(bpy.context.scene, "pb_sync_tracks", [])
        base_y = height - 150*UI_SCALE - SCROLL_Y

        # ── Mixer desk background ─────────────────────────────────────────
        # One PNG blit covering all 9 strips before individual strips draw.
        try:
            from ui.mixer.draw_utils import draw_element as _de
            from ui.mixer.channel_strip import send_section_height as _sh, SEND_MIN_SLOTS as _SMS
            from ui.mixer.channel_strip import STRIP_TOTAL_H as _STH
            from Racks import RACK_MARGIN_TOP as _RMT
            # Use fixed base height (SEND_MIN_SLOTS only) — PNG is never stretched.
            # Extra sends slots open downward and the PNG is tall enough to cover them.
            _base_send_h = _sh(_SMS, UI_SCALE)
            _strip_h_fixed = _STH * UI_SCALE + _base_send_h + _RMT * UI_SCALE
            _desk_x     = STRIP_LEFT_MARGIN * UI_SCALE + SCROLL_X
            _desk_y     = base_y - _strip_h_fixed   # anchored at top (base_y)
            _n_strips   = min(len(tracks), 9)
            _desk_w     = _n_strips * STRIP_STRIDE * UI_SCALE + (STRIP_W - STRIP_STRIDE) * UI_SCALE
            _de("mixer_desk_bg", _desk_x, _desk_y, _desk_w, _strip_h_fixed,
                draw_rect, (0.07, 0.07, 0.07, 1.0))
        except Exception:
            pass

        # ── 3-part strip skin backgrounds (drawn full-width, once for all 9 strips) ──
        # Drawn BEFORE strip elements so knobs/faders draw on top of the background.
        try:
            from ui.mixer.texture_cache import get_texture as _gt
            from ui.mixer.draw_utils import blit_texture as _bt
            from ui.mixer.channel_strip import (
                SEC_HEADER_H as _SHH, SEC_GAIN_H as _SGH,
                SEC_EQ_H as _SEQ, SEC_PAN_H as _SPAN,
                FADER_TOP_PAD as _FTP, FADER_HEIGHT as _FH,
                NUMBOX_H as _NBH, FADER_BOTTOM_PAD as _FBP,
                SENDS_LABEL_H as _SLH, SLOT_H as _SLOTH,
                SEND_MIN_SLOTS as _SMS,
                STRIP_LEFT_MARGIN as _SLM, STRIP_STRIDE as _SSTRIDE, STRIP_W as _SW,
            )
            scene2     = bpy.context.scene
            all_racks2 = getattr(scene2, "pb_racks", []) if scene2 else []
            n_racks2   = sum(1 for r in all_racks2 if getattr(r, "group_idx", 0) == 0)
            _slots2    = max(_SMS, n_racks2)
            _send_h2   = (_SLH + _slots2 * _SLOTH) * UI_SCALE
            _dx        = _SLM * UI_SCALE + SCROLL_X
            _n2        = min(len(tracks), 9)
            _dw        = _n2 * _SSTRIDE * UI_SCALE + (_SW - _SSTRIDE) * UI_SCALE

            # Top slice: HEADER + GAIN  (135px at scale 1)
            _top_h2 = (_SHH + _SGH) * UI_SCALE
            _tex = _gt("strip_top_bg")
            if _tex: _bt(_tex, _dx, base_y - _top_h2, _dw, _top_h2, key="strip_top_bg")

            # Send slot tile: tiled per slot row  (27px each at scale 1)
            _slot_h2   = _SLOTH * UI_SCALE
            _slot_top2 = base_y - (_SHH + _SGH + 12) * UI_SCALE - _SLH * UI_SCALE
            _stex = _gt("strip_send_slot_bg")
            if _stex:
                for _si in range(_slots2):
                    _sy = _slot_top2 - _si * _slot_h2 - _slot_h2
                    _bt(_stex, _dx, _sy, _dw, _slot_h2, key="strip_send_slot_bg")

            # Bottom slice: EQ + PAN + FADER  (523px at scale 1)
            _bot_h2  = (_SEQ + _SPAN + _FTP + _FH + _NBH + _FBP) * UI_SCALE
            _bot_top = base_y - (_SHH + _SGH + 12 + 12) * UI_SCALE - _send_h2
            _btex = _gt("strip_bottom_bg")
            if _btex: _bt(_btex, _dx, _bot_top - _bot_h2, _dw, _bot_h2, key="strip_bottom_bg")
        except Exception as _e3:
            pass


        # ── Draw layer diagnostic — prints once on first draw ────────────────
        global _draw_diag_done
        if not _draw_diag_done:
            _draw_diag_done = True
            from ui.mixer.texture_cache import get_texture as _gtd
            _layers = [
                ("1 (bottom)", "Background.png table surface (or dark rect fallback)", True),
                ("2",          "mixer_desk_bg PNG or grey fallback",             _gtd("mixer_desk_bg") is not None),
                ("3",          "strip_top_bg  (HEADER+GAIN, full width)",        _gtd("strip_top_bg") is not None),
                ("4",          "strip_send_slot_bg  (tiled rows, full width)",   _gtd("strip_send_slot_bg") is not None),
                ("5",          "strip_bottom_bg  (EQ+PAN+FADER, full width)",    _gtd("strip_bottom_bg") is not None),
                ("6",          "Channel strip elements (knobs/faders/buttons)",  True),
                ("7 (top)",    "Racks",                                          True),
            ]
            print("[HUD] ── Draw layer order ──────────────────────────────")
            for lvl, desc, loaded in _layers:
                status = "PNG" if loaded and lvl not in ("1 (bottom)", "6", "7 (top)") else ("FALLBACK" if not loaded and lvl not in ("1 (bottom)", "6", "7 (top)") else "")
                tag = f"  [{status}]" if status else ""
                print(f"[HUD]   Layer {lvl}: {desc}{tag}")
            print("[HUD] ─────────────────────────────────────────────────")

        draw_col = 0
        for i, track in enumerate(tracks):
            group_idx = i // 9
            sx = STRIP_LEFT_MARGIN*UI_SCALE + draw_col*STRIP_STRIDE*UI_SCALE + SCROLL_X
            draw_col += 1
            if sx + STRIP_W*UI_SCALE < 0 or sx > width:
                continue

            eng   = _meters_mod._engine_levels[i] if i < MAX_CHANNELS else 0.0
            peak  = _meters_mod._peak_hold[i]     if i < MAX_CHANNELS else 0.0
            draw_channel_strip(i, track, sx, base_y, UI_SCALE, tracks,
                               eng, peak, group_idx)

        # Racks
        try:
            _draw_racks(width, height, SCROLL_X, SCROLL_Y, UI_SCALE)
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
