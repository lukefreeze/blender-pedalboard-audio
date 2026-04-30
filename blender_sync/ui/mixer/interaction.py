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
    MAX_CHANNELS, DEFAULT_CHANNELS,
    DOUBLE_CLICK_TIME,
    SB_TRACK_PX, SB_MARGIN,
    SEND_BTN_H, SEND_BTN_GAP, SEND_MIN_SLOTS, SEND_START_Y,
)
from ui.mixer.channel_strip import (
    send_section_height as _send_section_height,
    STRIP_LEFT_MARGIN, STRIP_W, STRIP_STRIDE,
    M_BTN_X_OFF, M_BTN_W, M_BTN_H, M_BTN_Y_OFF, S_BTN_X_OFF,
    KNOB_GAIN_X_OFF, KNOB_GAIN_Y_OFF, KNOB_GAIN_R,
    EQ_KNOB_X_OFF, EQ_KNOB_START_Y, EQ_KNOB_SPACING, KNOB_EQ_R,
    KNOB_PAN_X_OFF, KNOB_PAN_R, KNOB_PAN_CLEARANCE,
)
from core.audio import (
    apply_fader_to_channel, apply_gain_to_channel,
    _pb_rebuild_eq, _pb_reprocess_channel,
    _pb_wire_rack_to_engine, sync_vse_mute, sync_vse_solo,
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
    except Exception as e:
        print(f"[INTERACTION] WARNING: failed to import Racks functions: {e}")
        def _noop(*a, **kw): return None
        return (_noop, _noop, _noop, _noop, _noop, _noop)

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

active_knob_track  = -1
active_knob_type   = ""
active_rack_knob   = None   # (rack_idx, param_idx) or None
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
               is_dragging_h, is_dragging_v, \
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
                       or is_dragging_h or is_dragging_v)

        if not (is_inside or mid_drag or widget_drag):
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
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
                delta  = (event.mouse_y-event.mouse_prev_y) / (FADER_HEIGHT*UI_SCALE)
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
                    if _engine_active(): _pb_reprocess_channel(active_knob_track)
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
                    # Reprocess audio with new settings
                    if _engine_active():
                        try:
                            from Racks import get_rack_channels
                            assigned = get_rack_channels(rack)
                            for ch in assigned:
                                _pb_wire_rack_to_engine(ch)
                                _pb_reprocess_channel(ch)
                        except Exception as e:
                            print(f"[WIRE] live update failed: {e}")
                    context.area.tag_redraw()
                return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
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
                f_y    = base_y - (FADER_TRACK_BOTTOM*UI_SCALE) - send_h_ht
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
                    nb_y = f_y - (nb_h + 4*UI_SCALE)
                    nb_w = 90*UI_SCALE
                    fader_norm = (track.volume-FADER_MIN)/(FADER_MAX-FADER_MIN)
                    fader_norm = max(0.0, min(1.0, fader_norm))
                    h_p  = f_y+(fader_norm*f_h)-(f_hh/2)
                    fhb  = f_y - (f_hh/2)
                    fht  = f_y + f_h + (f_hh/2)

                    # Knobs — positions must match draw loop exactly
                    n_racks_k  = len(getattr(context.scene, "pb_racks", []))
                    send_h_k   = _send_section_height(n_racks_k, UI_SCALE)
                    eq_start_k = 175*UI_SCALE + send_h_k
                    if math.dist((rx,ry),(kx,base_y-100*UI_SCALE))<20*UI_SCALE:
                        active_knob_track,active_knob_type=i,"GAIN"; return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,base_y-eq_start_k))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"HIGH"; return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,base_y-(eq_start_k+50*UI_SCALE)))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"MID";  return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,base_y-(eq_start_k+100*UI_SCALE)))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"LOW";  return {"RUNNING_MODAL"}
                    # Pan knob — between EQ LOW and fader top, matches draw position
                    pan_ky_k = f_y + f_h + 42*UI_SCALE
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

                    # Send buttons
                    n_racks_s = len(getattr(context.scene, "pb_racks", []))
                    slots_s   = max(SEND_MIN_SLOTS, n_racks_s)
                    btn_x_s   = sx + 10*UI_SCALE
                    btn_w_s   = 100*UI_SCALE
                    btn_h_s   = SEND_BTN_H*UI_SCALE
                    start_y_s = base_y - SEND_START_Y*UI_SCALE
                    if btn_x_s <= rx <= btn_x_s+btn_w_s:
                        for slot in range(slots_s):
                            by_s = start_y_s - slot*(btn_h_s+SEND_BTN_GAP*UI_SCALE)
                            if by_s <= ry <= by_s+btn_h_s and slot < n_racks_s:
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

            elif event.value == "RELEASE":
                active_knob_track  = -1
                active_knob_type   = ""
                active_fader_track = -1
                active_rack_knob   = None
                is_dragging_h      = False
                is_dragging_v      = False

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

        if event.type == "HOME" and event.value == "PRESS":
            SCROLL_X = 0.0
            SCROLL_Y = 0.0
            save_ui_state()
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

    # Always show at least DEFAULT_CHANNELS faders
    needed = max(DEFAULT_CHANNELS, highest_strip_channel)

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
