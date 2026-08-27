# =============================================================================
# ui/mixer/interaction.py
# Modal operator (mouse drag, click, scroll, zoom), fader/knob hit testing,
# and the SetFaderValue popup operator.
# =============================================================================

import math
import time
import bpy

from core.constants import (
    FADER_MIN, FADER_MAX, FADER_HEIGHT, FADER_HANDLE_H, FADER_HANDLE_W,
    FADER_HANDLE_X_OFF, FADER_TRACK_BOTTOM, NUMBOX_H,
    GAIN_MIN, GAIN_MAX,
    DEFAULT_CHANNELS,
    DOUBLE_CLICK_TIME,
    SEND_BTN_H, SEND_BTN_GAP, SEND_MIN_SLOTS, SEND_START_Y,
)
from ui.mixer.channel_strip import (
    send_section_height as _send_section_height,
    FADER_BOTTOM_PAD,
    FADER_VISUAL_BOTTOM_PAD,
    SEC_HEADER_H as _CS_SHH, SEC_GAIN_H as _CS_SGH,
    SEC_EQ_H as _CS_SEQ, SEC_PAN_H as _CS_SPAN,
    FADER_TOP_PAD as _CS_FTP, DIVIDER_GAP as _CS_DG,
    SENDS_LABEL_H as _CS_SLH, SLOT_H as _CS_SLOTH,
    SEND_BTN_IMG_X as _CS_SBIX, SEND_BTN_IMG_Y as _CS_SBIY,
    SEND_BTN_IMG_W as _CS_SBIW, SEND_BTN_IMG_H as _CS_SBIH,
)
from core.audio import (
    apply_fader_to_channel, apply_gain_to_channel,
    _pb_rebuild_eq,
    sync_vse_mute, sync_vse_solo,
)
from core.meters import _meter_timer

def _get_racks_funcs():
    """Lazy import of Racks functions to avoid circular import at load time."""
    try:
        from Racks import (rack_knob_hit_test, hit_test as _racks_hit_test,
                           handle_click as _racks_handle_click,
                           get_rack_channels, set_rack_param)
        try:
            from Racks import _trigger_reprocess
        except ImportError:
            def _trigger_reprocess(*a, **kw): pass
        return (rack_knob_hit_test, _racks_hit_test, _racks_handle_click,
                get_rack_channels, set_rack_param, _trigger_reprocess)
    except Exception:
        def _noop(*a, **kw): return None
        return (_noop, _noop, _noop, _noop, _noop, _noop)


def _get_ai_racks_funcs():
    """Lazy import of AI rack hit test and click handler."""
    try:
        from Racks import hit_test_ai_racks, handle_ai_rack_click
        return hit_test_ai_racks, handle_ai_rack_click
    except Exception as e:
        print(f"[AI RACKS] WARNING: could not import AI rack funcs: {e}")
        def _noop(*a, **kw): return None
        return _noop, _noop

# ---------------------------------------------------------------------------

def _engine_active():
    """Live check of engine state — avoids stale bool from module-level import."""
    import core.audio as _a
    return _a._pb_engine_active


# Module-level drag / interaction state
# These were globals in the original Loader.py — kept here so the modal
# operator can reference them without importing from another module.
# ---------------------------------------------------------------------------
is_dragging_h      = False
is_dragging_v      = False
is_panning         = False
is_zooming         = False

# Text-selection drag — armed on LEFTMOUSE press inside the Piper script box
# (see handle_ai_rack_click's ai_piper_text branch), cleared on release.
# While True, MOUSEMOVE re-derives the cursor from the mouse position using
# the same word-wrap layout the text is drawn with (rack_piper.cursor_index_
# from_xy) and extends the selection from the click's drag_anchor.
is_dragging_text   = False

active_knob_track  = -1
active_knob_type   = ""
active_rack_knob   = None   # (rack_idx, param_idx) or None
active_ai_knob     = None   # (ai_idx, knob_idx) or None

# Active text field state — when set, keyboard events type into rack.ai_text
# Format: {'ai_idx': int, 'cursor': int}  or None
_active_text_field = None
active_fader_track = -1

_last_click_time   = 0.0
_last_click_track  = -1

class VSE_OT_SetFaderValue(bpy.types.Operator):
    bl_idname      = "vse.set_fader_value"
    bl_label       = "Set Fader Value"
    bl_description = "Type an exact fader value (0.001 – 1.25). Enter 1 for unity."
    bl_options     = {"REGISTER", "UNDO"}

    channel_idx: bpy.props.IntProperty()
    new_value:   bpy.props.FloatProperty(
        name="Fader Value", min=FADER_MIN, max=FADER_MAX,
        default=1.0, step=1, precision=3)

    def invoke(self, context, event):
        # Pre-fill with current value
        tracks = getattr(context.scene, "pb_sync_tracks", [])
        if self.channel_idx < len(tracks):
            self.new_value = tracks[self.channel_idx].volume
        return context.window_manager.invoke_props_dialog(self, width=200)

    def draw(self, context):
        self.layout.prop(self, "new_value")

    def execute(self, context):
        tracks = getattr(context.scene, "pb_sync_tracks", [])
        if self.channel_idx >= len(tracks):
            return {"CANCELLED"}
        track     = tracks[self.channel_idx]
        old_fader = track.volume
        new_fader = max(FADER_MIN, min(FADER_MAX, self.new_value))
        apply_fader_to_channel(self.channel_idx, old_fader, new_fader)
        track.volume = new_fader
        # Update meter immediately even if paused
        _meter_timer._last_frame = None
        _meter_timer()
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Modal operator
# ---------------------------------------------------------------------------

class VSE_OT_PB_Interaction(bpy.types.Operator):
    bl_idname = "vse.pb_interaction"
    bl_label  = "PB Interaction"

    def modal(self, context, event):
        global is_panning, is_zooming, \
               active_knob_track, active_knob_type, active_fader_track, \
               active_rack_knob, \
               active_ai_knob, \
               _active_text_field, \
               is_dragging_h, is_dragging_v, is_dragging_text, \
               _last_click_time, _last_click_track

        import ui.mixer.mixer_hud as _hud
        pb_ui_enabled = _hud.pb_ui_enabled
        UI_SCALE      = _hud.UI_SCALE
        SCROLL_X      = _hud.SCROLL_X
        SCROLL_Y      = _hud.SCROLL_Y
        HUD_AREA_PTR  = _hud.HUD_AREA_PTR

        def save_ui_state():
            _hud.UI_SCALE  = UI_SCALE
            _hud.SCROLL_X  = SCROLL_X
            _hud.SCROLL_Y  = SCROLL_Y
            _hud.save_ui_state()

        (rack_knob_hit_test, racks_hit_test, racks_handle_click,
         get_rack_channels, set_rack_param, _trigger_reprocess) = _get_racks_funcs()
        if not pb_ui_enabled: return {"FINISHED"}
        if context.area is None or context.area.type != "NODE_EDITOR":
            return {"PASS_THROUGH"}

        region = context.region
        rx, ry  = event.mouse_region_x, event.mouse_region_y
        ry_top  = region.height - ry

        is_inside   = 0<=rx<=region.width and 0<=ry<=region.height
        mid_drag    = is_panning or is_zooming
        widget_drag = (active_fader_track != -1 or active_knob_track != -1
                       or active_rack_knob is not None
                       or active_ai_knob is not None
                       or _active_text_field is not None
                       or is_dragging_h or is_dragging_v or is_dragging_text)

        if not (is_inside or mid_drag or widget_drag):
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            if is_dragging_text and _active_text_field is not None:
                try:
                    from ui.racks.rack_piper import cursor_index_from_xy
                    text = ""
                    ai_idx_d = _active_text_field.get('ai_idx', -1)
                    ai_racks_d = getattr(context.scene, "pb_ai_racks", [])
                    if 0 <= ai_idx_d < len(ai_racks_d):
                        text = getattr(ai_racks_d[ai_idx_d], 'ai_text', '') or ''
                    idx = cursor_index_from_xy(
                        text,
                        _active_text_field.get('drag_sp_x', 0.0),
                        _active_text_field.get('drag_sp_y', 0.0),
                        _active_text_field.get('drag_sp_w', 0.0),
                        _active_text_field.get('drag_sp_h', 0.0),
                        _active_text_field.get('drag_scale', UI_SCALE),
                        rx, ry)
                    anchor = _active_text_field.get('drag_anchor', idx)
                    _active_text_field['cursor']    = idx
                    _active_text_field['sel_start'] = anchor
                    _active_text_field['sel_end']   = idx
                    context.area.tag_redraw()
                except Exception as _dse:
                    print(f"[PIPER] drag-select error: {_dse}")
                return {"RUNNING_MODAL"}
            if is_zooming:
                old_s    = UI_SCALE
                UI_SCALE = max(0.1, min(5.0, UI_SCALE +
                               (event.mouse_x-event.mouse_prev_x)*0.01))
                r        = UI_SCALE/old_s
                SCROLL_X = rx-(rx-SCROLL_X)*r
                SCROLL_Y = ry_top-(ry_top-SCROLL_Y)*r
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if is_panning:
                SCROLL_X += (event.mouse_x-event.mouse_prev_x)*2
                SCROLL_Y -= (event.mouse_y-event.mouse_prev_y)*2
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if is_dragging_h:
                # Derive ratio from actual scrollbar geometry so thumb tracks mouse.
                # Horizontal: thumb=150px wide, range=5000 content px over (width-150) track px.
                _h_track = max(1, region.width - 150)
                _h_ratio = 5000.0 / _h_track
                SCROLL_X -= (event.mouse_x - event.mouse_prev_x) * _h_ratio
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if is_dragging_v:
                # Vertical: thumb=100px tall, range=2000 content px over (height-100) track px.
                _v_track = max(1, region.height - 100)
                _v_ratio = 2000.0 / _v_track
                SCROLL_Y += (event.mouse_y - event.mouse_prev_y) * _v_ratio
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}

            if active_fader_track != -1:
                tracks = context.scene.pb_sync_tracks
                track  = tracks[active_fader_track]
                delta  = (event.mouse_y-event.mouse_prev_y) / ((FADER_HEIGHT - 2*FADER_VISUAL_BOTTOM_PAD)*UI_SCALE)
                fader_delta = delta * (FADER_MAX - FADER_MIN)
                old_fader   = track.volume
                new_fader   = max(FADER_MIN, min(FADER_MAX,
                                                  track.volume + fader_delta))
                apply_fader_to_channel(active_fader_track, old_fader, new_fader)
                track.volume = new_fader
                # Force meter to recalculate immediately so the level updates
                # while paused — without this it only updates on next play tick.
                _meter_timer._last_frame = None
                _meter_timer()
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}

            if active_knob_track != -1:
                track = context.scene.pb_sync_tracks[active_knob_track]
                delta = (event.mouse_y-event.mouse_prev_y)*0.005
                if   active_knob_type == "GAIN":
                    old_gain = track.gain
                    new_gain = max(GAIN_MIN, min(GAIN_MAX, track.gain + delta * (GAIN_MAX - GAIN_MIN)))
                    apply_gain_to_channel(active_knob_track, old_gain, new_gain)
                    track.gain = new_gain
                    _meter_timer._last_frame = None
                    _meter_timer()
                elif active_knob_type == "PAN":
                    track.pan = max(0.0, min(1.0, track.pan + delta))
                    if _engine_active():
                        try:
                            from core.engine import get_engine as _get_eng
                            _eng = _get_eng()
                            if _eng:
                                _hj = _eng.get_engine()
                                if _hj: _hj.set_pan(active_knob_track, track.pan)
                        except Exception: pass
                elif active_knob_type == "HIGH":
                    track.eq_high = max(-24.0, min(24.0, track.eq_high+delta*100))
                    if _engine_active(): _pb_rebuild_eq(active_knob_track)
                elif active_knob_type == "MID":
                    track.eq_mid  = max(-24.0, min(24.0, track.eq_mid+delta*100))
                    if _engine_active(): _pb_rebuild_eq(active_knob_track)
                elif active_knob_type == "LOW":
                    track.eq_low  = max(-24.0, min(24.0, track.eq_low+delta*100))
                    if _engine_active(): _pb_rebuild_eq(active_knob_track)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}

            if active_rack_knob is not None:
                rack_idx, param_idx = active_rack_knob
                racks = getattr(context.scene, "pb_racks", [])
                if rack_idx < len(racks):
                    rack   = racks[rack_idx]
                    from Racks import EFFECT_PARAMS, set_rack_param
                    delta  = (event.mouse_y - event.mouse_prev_y) * 0.004
                    if rack.effect_type == "COMP_MULTI":
                        if param_idx >= 16:
                            # Gain fader — larger delta so handle tracks mouse
                            from Racks import RACK_EXPANDED_H_MB, RACK_RAIL_H
                            rh_mb   = RACK_EXPANDED_H_MB * UI_SCALE
                            body_h  = rh_mb - RACK_RAIL_H * UI_SCALE
                            fdr_h   = max((body_h*0.52 - 8*UI_SCALE - 26*UI_SCALE - 26*UI_SCALE - 2*UI_SCALE), 40*UI_SCALE)
                            fdr_delta = (event.mouse_y - event.mouse_prev_y) / max(fdr_h, 1)
                            old_v = getattr(rack, f'p{param_idx}', 0.5)
                            new_v = max(0.0, min(1.0, old_v + fdr_delta))
                            set_rack_param(rack, param_idx, new_v)
                        else:
                            # Knobs — relative delta
                            old_v = getattr(rack, f'p{param_idx}', 0.0)
                            new_v = max(0.0, min(1.0, old_v + delta))
                            set_rack_param(rack, param_idx, new_v)
                    else:
                        if rack.effect_type == "EQ":
                            # EQ knobs: p0-p6=gain, p7-p13=freq, p14-p20=Q (7 bands)
                            # All stored 0-1 normalised, just clamp and set
                            old_v = getattr(rack, f'p{param_idx}', 0.0)
                            new_v = max(0.0, min(1.0, old_v + delta))
                            set_rack_param(rack, param_idx, new_v)
                        else:
                            params = EFFECT_PARAMS.get(rack.effect_type, [])
                            if param_idx < len(params):
                                old_v = getattr(rack, f'p{param_idx}', 0.0)
                                new_v = max(0.0, min(1.0, old_v + delta))
                                set_rack_param(rack, param_idx, new_v)
                    # Update engine with new params — takes effect next buffer
                    if _engine_active():
                        try:
                            from Racks import get_rack_channels
                            from core.audio import _hj_wire_effects
                            assigned = get_rack_channels(rack)
                            scene = context.scene
                            for ch in assigned:
                                _hj_wire_effects(ch, scene)
                        except Exception as e:
                            print(f"[WIRE] live update failed: {e}")
                    context.area.tag_redraw()
                return {"RUNNING_MODAL"}

            if active_ai_knob is not None:
                ai_idx, knob_idx = active_ai_knob
                try:
                    import Racks as _rk_ai2
                    ai_racks = getattr(context.scene, "pb_ai_racks", [])
                    if ai_idx < len(ai_racks):
                        rack_ai = ai_racks[ai_idx]
                        attr    = f'p{knob_idx}'
                        delta   = (event.mouse_y - event.mouse_prev_y) * 0.005
                        old_v   = getattr(rack_ai, attr, 0.5)
                        new_v   = max(0.0, min(1.0, old_v + delta))
                        setattr(rack_ai, attr, new_v)
                        context.area.tag_redraw()
                except Exception as _ae:
                    print(f"[AI RACKS] knob drag error: {_ae}")
                return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                # Any click outside a text field closes it
                is_dragging_text = False
                if _active_text_field is not None:
                    _active_text_field = None
                    context.area.tag_redraw()
                    # Don't return — let the click be processed normally below
                if ry < 14:   # horizontal scrollbar hit zone (8px track + margin)
                    is_dragging_h = True; return {"RUNNING_MODAL"}
                if rx > region.width - 14:   # vertical scrollbar hit zone
                    is_dragging_v = True; return {"RUNNING_MODAL"}

                import time
                now    = time.time()
                base_y = region.height-(150*UI_SCALE)-SCROLL_Y
                f_h    = FADER_HEIGHT * UI_SCALE
                # Must match draw loop exactly — include send section height
                n_racks_ht = len(getattr(context.scene, "pb_racks", []))
                send_h_ht  = _send_section_height(n_racks_ht, UI_SCALE)
                # Cursor-based f_y — mirrors channel_strip.py draw exactly
                # sec_top = base_y - (header+gain+divider*2+divider*2+eq+pan)*scale - sends
                _cs_sec_top = base_y - (_CS_SHH + _CS_SGH + _CS_DG*4 + _CS_SEQ + _CS_SPAN) * UI_SCALE - send_h_ht
                f_y    = _cs_sec_top - _CS_FTP * UI_SCALE - FADER_HEIGHT * UI_SCALE
                f_hw   = FADER_HANDLE_W * UI_SCALE
                f_hh   = FADER_HANDLE_H * UI_SCALE
                nb_h   = NUMBOX_H * UI_SCALE

                # Must mirror draw loop's draw_col logic exactly
                _hit_draw_col = 0
                for i, track in enumerate(context.scene.pb_sync_tracks):
                    sx  = (30*UI_SCALE)+(_hit_draw_col*135*UI_SCALE)+SCROLL_X
                    _hit_draw_col += 1
                    kx  = sx+(60*UI_SCALE)
                    f_hx = sx+(FADER_HANDLE_X_OFF*UI_SCALE)
                    nb_x = sx+(15*UI_SCALE)
                    nb_y = f_y - (FADER_BOTTOM_PAD // 2) * UI_SCALE - nb_h  # mirrors channel_strip draw
                    nb_w = 90*UI_SCALE
                    fader_norm = (track.volume-FADER_MIN)/(FADER_MAX-FADER_MIN)
                    fader_norm = max(0.0, min(1.0, fader_norm))
                    h_p  = f_y+(fader_norm*f_h)-(f_hh/2)
                    fhb  = f_y - (f_hh/2)
                    fht  = f_y + f_h + (f_hh/2)

                    # Knobs — derived from channel_strip.py cursor logic:
                    #   cursor after header(65)+gain(70)+div(12)+sends+div(12) = base_y - 159*s - send_h
                    #   EQ top = that cursor; knobs centred in each third of SEC_EQ_H(175)
                    #   PAN cy = EQ_bot - SEC_PAN_H + SEC_PAN_H*0.55 = EQ_bot - 54
                    n_racks_k  = len(getattr(context.scene, "pb_racks", []))
                    send_h_k   = _send_section_height(n_racks_k, UI_SCALE)
                    eq_top_k   = base_y - (159*UI_SCALE + send_h_k)
                    eq_sp_k    = (175*UI_SCALE) / 3.0
                    eq_high_k  = eq_top_k - 0.5 * eq_sp_k
                    eq_mid_k   = eq_top_k - 1.5 * eq_sp_k
                    eq_low_k   = eq_top_k - 2.5 * eq_sp_k
                    pan_ky_k   = eq_top_k - 175*UI_SCALE - 120*UI_SCALE + 120*UI_SCALE*0.55
                    if math.dist((rx,ry),(kx,base_y-100*UI_SCALE))<20*UI_SCALE:
                        active_knob_track,active_knob_type=i,"GAIN"; return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,eq_high_k))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"HIGH"; return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,eq_mid_k))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"MID";  return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,eq_low_k))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"LOW";  return {"RUNNING_MODAL"}
                    # Pan knob
                    if math.dist((rx,ry),(kx, pan_ky_k)) < 18*UI_SCALE:
                        # Double-click snaps pan to centre
                        if (now - _last_click_time < DOUBLE_CLICK_TIME
                                and _last_click_track == i):
                            context.scene.pb_sync_tracks[i].pan = 0.5
                            _last_click_time  = 0.0
                            _last_click_track = -1
                            context.area.tag_redraw()
                            return {"RUNNING_MODAL"}
                        _last_click_time  = now
                        _last_click_track = i
                        active_knob_track,active_knob_type=i,"PAN"; return {"RUNNING_MODAL"}

                    # Fader track — checked BEFORE numbox so handle at
                    # bottom position is always reachable
                    if f_hx < rx < f_hx+f_hw and fhb < ry < fht:
                        # Double-click on fader snaps to 1.0
                        if (now - _last_click_time < DOUBLE_CLICK_TIME
                                and _last_click_track == i):
                            old_fader = track.volume
                            apply_fader_to_channel(i, old_fader, 1.0)
                            track.volume      = 1.0
                            _last_click_time  = 0.0
                            _last_click_track = -1
                            context.area.tag_redraw()
                            return {"RUNNING_MODAL"}
                        _last_click_time  = now
                        _last_click_track = i
                        active_fader_track = i
                        return {"RUNNING_MODAL"}

                    # Number box — single click opens popup, double-click snaps to 1.0
                    if nb_x < rx < nb_x+nb_w and nb_y < ry < nb_y+nb_h:
                        if (now - _last_click_time < DOUBLE_CLICK_TIME
                                and _last_click_track == i):
                            # Double-click: snap to unity
                            old_fader = track.volume
                            apply_fader_to_channel(i, old_fader, 1.0)
                            track.volume = 1.0
                            _last_click_time  = 0.0
                            _last_click_track = -1
                        else:
                            # Single click: open value entry popup
                            _last_click_time  = now
                            _last_click_track = i
                            bpy.ops.vse.set_fader_value(
                                "INVOKE_DEFAULT", channel_idx=i,
                                new_value=track.volume)
                        context.area.tag_redraw()
                        return {"RUNNING_MODAL"}

                    # Mute / Solo
                    if sx<rx<sx+120*UI_SCALE and base_y-50*UI_SCALE<ry<base_y:
                        if rx < sx+60*UI_SCALE:
                            track.mute = not track.mute
                            sync_vse_mute(i, track.mute)
                        else:
                            track.solo = not track.solo
                            sync_vse_solo(i, track.solo)
                        context.area.tag_redraw()
                        return {"RUNNING_MODAL"}

                    # Send buttons — cursor arithmetic mirrors channel_strip.py draw exactly
                    # Draw flow: base_y -> header(65) -> gain(70) -> divider*2(12) -> sends
                    n_racks_s = len(getattr(context.scene, "pb_racks", []))
                    slots_s   = max(SEND_MIN_SLOTS, n_racks_s)
                    _snd_top  = base_y - (_CS_SHH + _CS_SGH + _CS_DG*2) * UI_SCALE
                    _slot_cur = _snd_top - _CS_SLH * UI_SCALE
                    btn_x_s   = sx + _CS_SBIX * UI_SCALE
                    btn_w_s   = _CS_SBIW * UI_SCALE
                    btn_h_s   = _CS_SBIH * UI_SCALE
                    if btn_x_s <= rx <= btn_x_s + btn_w_s:
                        for slot in range(slots_s):
                            tile_y_s = _slot_cur - (slot + 1) * _CS_SLOTH * UI_SCALE
                            by_s     = tile_y_s + _CS_SBIY * UI_SCALE
                            if by_s <= ry <= by_s + btn_h_s and slot < n_racks_s:
                                rack  = context.scene.pb_racks[slot]
                                attr  = f'ch{i}'
                                if hasattr(rack, attr):
                                    setattr(rack, attr,
                                            not getattr(rack, attr, False))
                                    try:
                                        from Racks import _trigger_reprocess
                                        _trigger_reprocess(slot, rack, context)
                                    except Exception as _sre:
                                        print(f"[ENGINE] send reprocess failed: {_sre}")
                                    context.area.tag_redraw()
                                    return {"RUNNING_MODAL"}

                # Check rack knob clicks
                rk_hit = rack_knob_hit_test(rx, ry, region.height,
                                            SCROLL_X, SCROLL_Y, UI_SCALE)
                if rk_hit is not None:
                    active_rack_knob = rk_hit
                    return {"RUNNING_MODAL"}

                # Check rack clicks (below fader section)
                hit = racks_hit_test(rx, ry, region.height,
                                     SCROLL_X, SCROLL_Y, UI_SCALE)
                if hit:
                    if racks_handle_click(hit, context):
                        context.area.tag_redraw()
                    return {"RUNNING_MODAL"}

                # Check AI rack clicks (below DSP rack section)
                # Use aliased imports — NEVER import FADER_TRACK_BOTTOM or NUMBOX_H
                # bare inside a function that already uses them as module-level names,
                # or Python will treat the module-level reference as unbound local.
                try:
                    import Racks as _rk_ai
                    _cs_st = base_y - (_CS_SHH + _CS_SGH + _CS_DG*4 + _CS_SEQ + _CS_SPAN) * UI_SCALE - send_h_ht
                    _FTB_f_y = _cs_st - _CS_FTP * UI_SCALE - FADER_HEIGHT * UI_SCALE  # correct fader bottom
                    _NBH  = NUMBOX_H             # already in scope from module import
                    from ui.mixer.channel_strip import send_section_height as _ssh_ai

                    scene_ai   = context.scene
                    base_y_ai  = region.height - 150*UI_SCALE - SCROLL_Y

                    # Group geometry — mirrors draw_racks exactly
                    _GW_UNSCALED = 9 * 135 - 15   # 1200px per group
                    _GW          = _GW_UNSCALED * UI_SCALE
                    num_tracks_ai = len(getattr(scene_ai, "pb_sync_tracks", []))
                    num_groups_ai = max(1, (num_tracks_ai + 8) // 9)

                    dsp_racks_all = getattr(scene_ai, "pb_racks", [])

                    ai_hit_test, ai_handle_click = _get_ai_racks_funcs()
                    ai_hit = None

                    for _g_ai in range(num_groups_ai):
                        rack_x_ai = (30 + _g_ai * (_GW_UNSCALED + 15)) * UI_SCALE + SCROLL_X

                        # Skip group if click is outside its X range
                        if not (rack_x_ai <= rx <= rack_x_ai + _GW):
                            continue

                        # Group-local DSP racks determine Y position
                        group_dsp = [r for r in dsp_racks_all
                                     if getattr(r, 'group_idx', 0) == _g_ai]
                        n_group_dsp = len(group_dsp)
                        send_h_ai   = _ssh_ai(n_group_dsp, UI_SCALE)

                        from ui.mixer.channel_strip import strip_bottom_y as _sbot_ai
                        rack_top_ai  = _sbot_ai(base_y_ai, n_group_dsp, UI_SCALE) - _rk_ai.RACK_MARGIN_TOP*UI_SCALE

                        # Walk down past this group's DSP racks.
                        # RACK_COLLAPSED_H must come from rack_base, NOT from
                        # Racks — Racks.RACK_COLLAPSED_H (36) is a legacy value
                        # kept only for AI-rack sizing; the real collapsed-rack
                        # draw height (rack_base._draw_rack_collapsed) is 48.
                        # Using the stale 36 here under-counts every collapsed
                        # DSP rack above the AI section by 12 unscaled px,
                        # pushing ai_section_top_y (and everything hit-tested
                        # below it — the AI popup, add button, and every AI
                        # rack's own hitboxes) down out of alignment with what's
                        # actually drawn. Same fix already applied inside
                        # Racks.py's own draw_racks()/rack_knob_hit_test()/hit_test().
                        from ui.racks.rack_base import RACK_COLLAPSED_H as _RB_COLLAPSED_H
                        cur_y_ai = rack_top_ai
                        for _ri in group_dsp:
                            if _ri.collapsed:
                                _rh = _RB_COLLAPSED_H * UI_SCALE
                            elif _ri.effect_type == "COMP_MULTI":
                                _rh = _rk_ai.RACK_EXPANDED_H_MB * UI_SCALE
                            elif _ri.effect_type == "EQ":
                                _rh = _rk_ai.RACK_EXPANDED_H_EQ * UI_SCALE
                            elif _ri.effect_type == "REVERB":
                                _rh = _rk_ai.RACK_EXPANDED_H_RV * UI_SCALE
                            elif _ri.effect_type == "NOISE_GATE":
                                _rh = _rk_ai.RACK_EXPANDED_H_NG * UI_SCALE
                            elif _ri.effect_type == "DELAY":
                                _rh = _rk_ai.RACK_EXPANDED_H_DL * UI_SCALE
                            elif _ri.effect_type == "BOOSTER":
                                _rh = _rk_ai.RACK_EXPANDED_H_DL * UI_SCALE
                            elif _ri.effect_type == "MIXDOWN":
                                _rh = _rk_ai.RACK_EXPANDED_H_MX * UI_SCALE
                            else:
                                _rh = _rk_ai.RACK_EXPANDED_H * UI_SCALE
                            cur_y_ai -= _rh + _rk_ai.RACK_GAP * UI_SCALE

                        # ai_section_top_y = bottom of DSP add-rack button
                        ai_section_top_y = cur_y_ai - 28*UI_SCALE

                        ai_hit = ai_hit_test(rx, ry, ai_section_top_y,
                                             rack_x_ai, UI_SCALE, _GW_UNSCALED,
                                             _g_ai)
                        if ai_hit:
                            break   # found a hit in this group

                    if ai_hit:
                        # Inject scale and region width so popup can clamp its x
                        ai_hit['scale']    = UI_SCALE
                        ai_hit['region_w'] = region.width
                        if ai_hit.get('zone') in ('ai_dnf_knob', 'ai_piper_knob', 'ai_rvc_knob'):
                            active_ai_knob = (ai_hit['ai_idx'], ai_hit['knob_idx'])
                            return {"RUNNING_MODAL"}
                        if ai_hit.get('zone') == 'ai_piper_text':
                            # Arm drag-select — handle_ai_rack_click() below
                            # places the cursor and stashes the geometry
                            # MOUSEMOVE needs to keep extending the selection
                            # while the button stays down.
                            is_dragging_text = True
                        if ai_handle_click(ai_hit, context):
                            context.area.tag_redraw()
                        return {"RUNNING_MODAL"}
                except Exception as _ai_e:
                    print(f"[AI RACKS] hit test error: {_ai_e}")

            elif event.value == "RELEASE":
                active_knob_track  = -1
                active_knob_type   = ""
                active_fader_track = -1
                active_rack_knob   = None
                active_ai_knob     = None
                is_dragging_h      = False
                is_dragging_v      = False
                is_dragging_text   = False

        if event.type == "MIDDLEMOUSE":
            if event.value == "PRESS":
                is_zooming = event.ctrl; is_panning = not event.ctrl
            else:
                is_zooming = is_panning = False; save_ui_state()
            return {"RUNNING_MODAL"}

        if event.type in {"WHEELUPMOUSE","WHEELDOWNMOUSE"}:
            if is_inside:
                step = 150 if event.type=="WHEELUPMOUSE" else -150
                if event.shift: SCROLL_X += step
                else:           SCROLL_Y += step
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}

        # Trackpad two-finger pan
        if event.type == "TRACKPADPAN" and is_inside:
            SCROLL_X += event.mouse_x - event.mouse_prev_x
            SCROLL_Y -= event.mouse_y - event.mouse_prev_y
            save_ui_state(); context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        # Trackpad two-finger pinch zoom
        # Delta is in X axis: prev_x > mouse_x = pinching in (zoom out)
        #                      prev_x < mouse_x = pinching out (zoom in)
        if event.type == "TRACKPADZOOM" and is_inside:
            zoom_delta = (event.mouse_x - event.mouse_prev_x) * 0.003
            new_scale  = max(0.3, min(3.0, UI_SCALE + zoom_delta))
            cx = region.width  / 2
            cy = region.height / 2
            SCROLL_X  = cx - (cx - SCROLL_X) * (new_scale / max(UI_SCALE, 0.001))
            SCROLL_Y  = cy - (cy - SCROLL_Y) * (new_scale / max(UI_SCALE, 0.001))
            UI_SCALE  = new_scale
            save_ui_state(); context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        # ── Mixdown frame box text input — must come before HOME/other key handlers
        try:
            from ui.racks.rack_mixdown import _mx_state as _mxst
        except Exception:
            _mxst = None
        if (_mxst is not None
                and _mxst.get('text_focus') is not None
                and _mxst.get('text_rack', -1) >= 0
                and event.value == "PRESS"):
            print(f"[MIXDOWN DEBUG] keyboard event: type={event.type!r} unicode={event.unicode!r} focus={_mxst['text_focus']} buf={_mxst['text_buf']!r}")
            _buf = _mxst['text_buf']
            _commit = False
            _handled = True
            if event.type == "BACK_SPACE":
                _mxst['text_buf'] = _buf[:-1]
                print(f"[MIXDOWN DEBUG] BACKSPACE → buf now {_mxst['text_buf']!r}")
            elif event.type == "DEL":
                _mxst['text_buf'] = ''
            elif event.type in {"RET", "NUMPAD_ENTER"}:
                _commit = True
            elif event.type == "ESC":
                _mxst['text_focus'] = None
                _mxst['text_buf']   = ''
                _mxst['text_rack']  = -1
            elif event.unicode and event.unicode.isdigit():
                if len(_buf) < 6:
                    _mxst['text_buf'] = _buf + event.unicode
                print(f"[MIXDOWN DEBUG] digit {event.unicode!r} → buf now {_mxst['text_buf']!r}")
            else:
                _handled = False
                print(f"[MIXDOWN DEBUG] unhandled key type={event.type!r}")
            if _commit:
                _param = _mxst['text_focus']
                _buf2  = _mxst['text_buf']
                _racks = getattr(context.scene, "pb_racks", [])
                _ri    = _mxst['text_rack']
                print(f"[MIXDOWN DEBUG] COMMIT param={_param} buf={_buf2!r}")
                if _buf2.isdigit() and _ri < len(_racks):
                    setattr(_racks[_ri], _param, float(max(1, int(_buf2))))
                _mxst['text_focus'] = None
                _mxst['text_buf']   = ''
                _mxst['text_rack']  = -1
            if _handled or _commit:
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}

        # Also print if we have focus but event.value != PRESS to see what's arriving
        if (_mxst is not None
                and _mxst.get('text_focus') is not None
                and event.value != "PRESS"
                and event.type not in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "TIMER"}):
            print(f"[MIXDOWN DEBUG] non-PRESS event while focused: type={event.type!r} value={event.value!r}")

        if event.type == "HOME" and event.value == "PRESS":
            SCROLL_X = 0.0
            SCROLL_Y = 0.0
            save_ui_state()
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        # ── Text field keyboard handling ─────────────────────────────────────
        if _active_text_field is not None and event.value == "PRESS":
            ai_idx  = _active_text_field.get('ai_idx', -1)

            # ── Mixdown frame box branch ──────────────────────────────────────
            if _active_text_field.get('mx_frame'):
                text      = _active_text_field.get('text', '')
                cursor    = _active_text_field.get('cursor', len(text))
                consumed  = True
                if event.type == "BACK_SPACE" and cursor > 0:
                    text   = text[:cursor-1] + text[cursor:]
                    cursor -= 1
                elif event.type == "DEL" and cursor < len(text):
                    text = text[:cursor] + text[cursor+1:]
                elif event.type in {"RET", "NUMPAD_ENTER"}:
                    # Commit — write value back to rack property
                    try:
                        commit_fn = _active_text_field.get('commit_fn')
                        if commit_fn:
                            commit_fn(text)
                    except Exception as _cfe:
                        print(f"[MIXDOWN] commit error: {_cfe}")
                    _active_text_field = None
                    context.area.tag_redraw()
                    return {"RUNNING_MODAL"}
                elif event.type == "ESC":
                    _active_text_field = None
                    context.area.tag_redraw()
                    return {"RUNNING_MODAL"}
                elif event.unicode and event.unicode.isdigit() and not event.ctrl:
                    if len(text) < 6:
                        text   = text[:cursor] + event.unicode + text[cursor:]
                        cursor += 1
                else:
                    consumed = False
                if consumed:
                    _active_text_field['text']   = text
                    _active_text_field['cursor'] = cursor
                    context.area.tag_redraw()
                    return {"RUNNING_MODAL"}
                return {"PASS_THROUGH"}

            ai_racks = getattr(context.scene, "pb_ai_racks", []) if context.scene else []
            if ai_idx < 0 or ai_idx >= len(ai_racks):
                _active_text_field = None
                return {"PASS_THROUGH"}

            rack      = ai_racks[ai_idx]
            _tf_field = _active_text_field.get('field', 'ai_text')
            if _tf_field == 'ai_text':
                text = getattr(rack, 'ai_text', '') or ''
            else:
                text = str(rack.get(_tf_field, '') or '')
            cursor   = _active_text_field.get('cursor', len(text))
            sel_start = _active_text_field.get('sel_start', -1)
            sel_end   = _active_text_field.get('sel_end', -1)
            cursor    = max(0, min(len(text), cursor))

            def _has_sel():
                return sel_start >= 0 and sel_end >= 0 and sel_start != sel_end

            def _sel_range():
                return min(sel_start, sel_end), max(sel_start, sel_end)

            def _clear_sel():
                _active_text_field['sel_start'] = -1
                _active_text_field['sel_end']   = -1

            def _delete_sel():
                lo, hi = _sel_range()
                return text[:lo] + text[hi:], lo

            consumed = True

            if event.type == "BACK_SPACE":
                if _has_sel():
                    text, cursor = _delete_sel(); _clear_sel()
                elif cursor > 0:
                    text = text[:cursor-1] + text[cursor:]
                    cursor -= 1
            elif event.type == "DEL":
                if _has_sel():
                    text, cursor = _delete_sel(); _clear_sel()
                elif cursor < len(text):
                    text = text[:cursor] + text[cursor+1:]
            elif event.type == "LEFT_ARROW":
                if event.shift:
                    # Extend/start selection
                    if not _has_sel():
                        _active_text_field['sel_start'] = cursor
                    cursor = max(0, cursor - 1)
                    _active_text_field['sel_end'] = cursor
                else:
                    if _has_sel():
                        cursor = _sel_range()[0]
                    else:
                        cursor = max(0, cursor - 1)
                    _clear_sel()
            elif event.type == "RIGHT_ARROW":
                if event.shift:
                    if not _has_sel():
                        _active_text_field['sel_start'] = cursor
                    cursor = min(len(text), cursor + 1)
                    _active_text_field['sel_end'] = cursor
                else:
                    if _has_sel():
                        cursor = _sel_range()[1]
                    else:
                        cursor = min(len(text), cursor + 1)
                    _clear_sel()
            elif event.type == "HOME":
                if event.shift:
                    if not _has_sel(): _active_text_field['sel_start'] = cursor
                    cursor = 0
                    _active_text_field['sel_end'] = cursor
                else:
                    cursor = 0; _clear_sel()
            elif event.type == "END":
                if event.shift:
                    if not _has_sel(): _active_text_field['sel_start'] = cursor
                    cursor = len(text)
                    _active_text_field['sel_end'] = cursor
                else:
                    cursor = len(text); _clear_sel()
            elif event.type == "A" and event.ctrl:
                # Ctrl+A select all
                _active_text_field['sel_start'] = 0
                _active_text_field['sel_end']   = len(text)
                cursor = len(text)
            elif event.type == "C" and event.ctrl:
                # Ctrl+C — copy selection (or the whole field if nothing
                # selected) to the SYSTEM clipboard, so it can be pasted
                # into another application. Text/cursor are unchanged.
                if _has_sel():
                    lo, hi = _sel_range()
                    context.window_manager.clipboard = text[lo:hi]
                else:
                    context.window_manager.clipboard = text
            elif event.type == "X" and event.ctrl:
                # Ctrl+X — cut selection to the system clipboard
                if _has_sel():
                    context.window_manager.clipboard = text[_sel_range()[0]:_sel_range()[1]]
                    text, cursor = _delete_sel(); _clear_sel()
                else:
                    consumed = False
            elif event.type == "V" and event.ctrl:
                # Ctrl+V — paste from the system clipboard (e.g. text copied
                # from another application), replacing any selection.
                paste = context.window_manager.clipboard or ""
                paste = paste.replace("\r\n", "\n").replace("\r", "\n")
                if _tf_field != 'ai_text':
                    # Single-line fields — collapse any newlines to spaces
                    paste = paste.replace("\n", " ")
                if _has_sel():
                    text, cursor = _delete_sel(); _clear_sel()
                paste = paste[:max(0, 4096 - len(text))]   # respect the same 4096 cap as typing
                if paste:
                    text   = text[:cursor] + paste + text[cursor:]
                    cursor += len(paste)
            elif event.type == "RET" or event.type == "NUMPAD_ENTER":
                if event.shift and _tf_field == 'ai_text':
                    if _has_sel():
                        text, cursor = _delete_sel(); _clear_sel()
                    text   = text[:cursor] + "\n" + text[cursor:]
                    cursor += 1
                else:
                    _active_text_field = None
                    if _tf_field == 'ai_text':
                        rack.ai_text = text
                    else:
                        rack[_tf_field] = text
                    context.area.tag_redraw()
                    return {"RUNNING_MODAL"}
            elif event.type == "ESC":
                _active_text_field = None
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            elif event.unicode and len(event.unicode) == 1 and not event.ctrl:
                ch = event.unicode
                if _has_sel():
                    text, cursor = _delete_sel(); _clear_sel()
                if len(text) < 4096:
                    text   = text[:cursor] + ch + text[cursor:]
                    cursor += 1
            else:
                consumed = False

            if consumed:
                if _tf_field == 'ai_text':
                    rack.ai_text = text
                else:
                    rack[_tf_field] = text
                _active_text_field['cursor'] = cursor
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}


# ---------------------------------------------------------------------------
# Operators & panel
# ---------------------------------------------------------------------------

def _sync_tracks_to_vse(scene, reset_values=False):
    """Sync pb_sync_tracks to VSE channel layout.

    Always maintains at least DEFAULT_CHANNELS (9) faders so the mixer
    matches Blender's default VSE layout even when channels are empty.
    Auto-expands beyond 9 when strips appear on higher channels.
    Metastrips (type=META) are treated as a single channel — their
    interior strips are not recursed into.
    Preserves existing fader/EQ values when reset_values=False.
    """
    if not scene: return

    # Find the highest channel that has a sound strip (non-meta, top-level)
    highest_strip_channel = 0
    if scene.sequence_editor:
        for s in scene.sequence_editor.sequences_all:
            if s.type == "SOUND" and s.sound:
                highest_strip_channel = max(highest_strip_channel, s.channel)

    # Always show at least DEFAULT_CHANNELS faders, rounded up to next group of 9
    needed = max(DEFAULT_CHANNELS, ((highest_strip_channel + 8) // 9) * 9)

    existing = len(scene.pb_sync_tracks)

    # Add any missing tracks
    for i in range(existing, needed):
        track = scene.pb_sync_tracks.add()
        track.volume = 1.0
        if scene.sequence_editor:
            for s in scene.sequence_editor.sequences_all:
                if s.channel == (i + 1) and s.type == "SOUND":
                    track.mute = s.mute
                    break

    # If reset_values, reset faders to unity but preserve mute from strips
    if reset_values:
        for i, track in enumerate(scene.pb_sync_tracks):
            track.volume = 1.0
            track.gain   = 1.0
            track.eq_low = track.eq_mid = track.eq_high = 0.0
            if scene.sequence_editor:
                for s in scene.sequence_editor.sequences_all:
                    if s.channel == (i + 1) and s.type == "SOUND":
                        track.mute = s.mute
                        break

    active = [s.channel for s in scene.sequence_editor.sequences_all
              if s.type == "SOUND" and s.sound] if scene.sequence_editor else []
    print(f"[TRACKS] {len(scene.pb_sync_tracks)} tracks "
          f"(default={DEFAULT_CHANNELS}, "
          f"highest strip ch={highest_strip_channel}, "
          f"active={sorted(set(active))[:12]}{'...' if len(set(active))>12 else ''})")


def _get_active_channel_count(scene):
    """Return the number of VSE channels that have sound strips."""
    if not scene or not scene.sequence_editor:
        return 0
    return len(set(
        s.channel - 1
        for s in scene.sequence_editor.sequences_all
        if s.type == "SOUND" and s.sound
    ))


class VSE_OT_RefreshPBTracks(bpy.types.Operator):
    bl_idname = "vse.refresh_pb_tracks"