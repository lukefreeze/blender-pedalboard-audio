# =============================================================================
# ui/mixer/texture_cache.py
# PNG skin texture loader — the bridge between GPU primitives and future PNG skins.
#
# HOW THE PNG REPLACEMENT WILL WORK
# ----------------------------------
# Every draw call in draw_utils.py routes through draw_element(key, x, y, w, h, ...).
# Right now "key" is ignored and it falls back to GPU primitive drawing.
#
# When you're ready to skin an element:
#   1. Drop a PNG into ui/assets/skins/default/  (e.g. knob.png)
#   2. The key "knob" will match SKIN_MAP below
#   3. load_skin() calls gpu.texture.from_image() once and caches the result
#   4. draw_element() blits the texture instead of drawing primitives
#   5. The call site in channel_strip.py / rack_*.py does NOT change at all
#
# The GPU blit path uses gpu.shader.from_builtin("IMAGE") with a TRI_STRIP
# quad mapped to the draw rect — same coordinate system as everything else.
#
# SKIN_MAP: element key -> filename in the active skin folder
# =============================================================================

import os
import gpu
from gpu_extras.batch import batch_for_shader

# ---------------------------------------------------------------------------
# Skin map — add an entry here when you have a PNG ready
# ---------------------------------------------------------------------------
SKIN_MAP = {
    # key            filename in skins/default/ (or skins/custom/)
    # LED meter tiles
    "meter_led_green":  "GreenLED.png",
    "meter_led_yellow": "YellowLED.png",
    "meter_led_red":    "RedLED.png",
    "meter_led_off":    "BlackLED.png",

    "fader_handle":  "fader_handle.png",  # generic fallback
    # Per-channel fader handles — fader_handle_1.png .. fader_handle_9.png
    # Drop any of these in skins/default/ to override that channel's handle.
    # Falls back to fader_handle.png if the per-channel file isn't present.
    "fader_handle_1":  "fader_handle_1.png",
    "fader_handle_2":  "fader_handle_2.png",
    "fader_handle_3":  "fader_handle_3.png",
    "fader_handle_4":  "fader_handle_4.png",
    "fader_handle_5":  "fader_handle_5.png",
    "fader_handle_6":  "fader_handle_6.png",
    "fader_handle_7":  "fader_handle_7.png",
    "fader_handle_8":  "fader_handle_8.png",
    "fader_handle_9":  "fader_handle_9.png",

    "fader_rail":    "fader_rail.png",
    "knob":          "knob.png",
    "knob_gain":     "knob_gain.png",
    "knob_eq":       "knob_eq.png",
    "knob_pan":      "knob_pan.png",
    # Send slot buttons
    "send_btn_off":  "SendOff.png",
    "send_btn_on":   "SendOn.png",

    "btn_mute_off":  "btn_mute_off.png",
    "btn_mute_on":   "btn_mute_on.png",
    "btn_solo_off":  "btn_solo_off.png",
    "btn_solo_on":   "btn_solo_on.png",
    # Mixer strip — three sections + fader section
    "strip_top_bg":        "strip_top_bg.png",       # label + mute/solo + gain (120x170px)
    "strip_send_slot_bg":  "strip_send_slot_bg.png", # one send row tile (120x23px, tiled)
    "strip_send_section_bg": "strip_send_section_bg.png", # full sends section background
    "strip_bottom_bg":     "strip_bottom_bg.png",    # EQ + pan knob section
    "strip_fader_bg":      "strip_fader_bg.png",     # fader + numbox + gap section
    "strip_bg":            "strip_bg.png",            # legacy full-strip (unused if above present)
    "mixer_desk_bg":       "mixer_desk_bg.png",
    # Rack body backgrounds
    "rack_comp_multi_bg":  "rack_comp_multi_bg.png",
    "rack_comp_single_bg": "rack_comp_single_bg.png",
    "rack_eq_bg":          "rack_eq_bg.png",
    "rack_reverb_bg":      "rack_reverb_bg.png",
    "rack_noisegate_bg":   "rack_noisegate_bg.png",
    "rack_delay_bg":       "rack_delay_bg.png",
    "rack_booster_bg":     "rack_booster_bg.png",
    "rack_mixdown_bg":     "rack_mixdown_bg.png",
    "rack_knnvc_bg":       "rack_knnvc_bg.png",
    "rack_demucs_bg":      "rack_demucs_bg.png",
    "add_rack_btn":        "add_rack_btn.png",
    "add_ai_rack_btn":     "add_ai_rack_btn.png",
    "rack_chassis":  "rack_chassis.png",
    "rack_rail":     "rack_rail.png",
}

# ---------------------------------------------------------------------------
# Runtime cache: key -> gpu.types.GPUTexture
# ---------------------------------------------------------------------------
_texture_cache: dict = {}
_active_skin:   str  = "default"
_skin_dir:      str  = ""
_blit_logged:   set  = set()   # keys already logged to console


def set_skin_dir(assets_dir: str, skin_name: str = "default") -> None:
    """Called once at startup from Loader.py with the assets path."""
    global _skin_dir, _active_skin
    _active_skin = skin_name
    _skin_dir    = os.path.join(assets_dir, skin_name)
    print(f"[SKIN] skin dir: {_skin_dir}")
    print(f"[SKIN] dir exists: {os.path.isdir(_skin_dir)}")
    if os.path.isdir(_skin_dir):
        found = [f for f in os.listdir(_skin_dir) if f.endswith('.png')]
        print(f"[SKIN] PNGs found: {found if found else 'NONE'}")


def get_texture(key: str):
    """Return a cached gpu.GPUTexture for key, or None if not available.

    None means the caller should fall back to GPU primitive drawing.
    This is the only function draw_utils.py needs to call.
    """
    if key in _texture_cache:
        return _texture_cache[key]

    filename = SKIN_MAP.get(key)
    if not filename or not _skin_dir:
        return None

    filepath = os.path.join(_skin_dir, filename)
    if not os.path.exists(filepath):
        return None

    try:
        import bpy
        img = bpy.data.images.load(filepath, check_existing=True)
        img.gl_load()
        tex = gpu.texture.from_image(img)
        _texture_cache[key] = tex
        print(f"[SKIN] loaded '{key}' from {os.path.basename(filepath)}")
        return tex
    except Exception as e:
        print(f"[SKIN] failed to load '{key}': {e}")
        _texture_cache[key] = None   # don't retry
        return None


def blit_texture(tex, x: float, y: float, w: float, h: float,
                 alpha: float = 1.0, key: str = "") -> None:
    """Blit a gpu.GPUTexture into a screen-space rect (x,y = bottom-left)."""
    if tex is None or w <= 0 or h <= 0:
        return
    if key and key not in _blit_logged:
        _blit_logged.add(key)
        print(f"[SKIN] blitting '{key}' ({int(w)}x{int(h)}px)")
    try:
        # Explicitly set alpha blend — do not rely on caller having set it.
        # Blender images use premultiplied alpha internally after loading,
        # so ALPHA_PREMULT gives correct compositing over the background.
        gpu.state.blend_set("ALPHA_PREMULT")
        shader = gpu.shader.from_builtin("IMAGE")
        verts  = [(x, y), (x+w, y), (x, y+h), (x+w, y+h)]
        uvs    = [(0, 0), (1, 0), (0, 1), (1, 1)]
        batch  = batch_for_shader(shader, "TRI_STRIP",
                                  {"pos": verts, "texCoord": uvs})
        shader.bind()
        shader.uniform_sampler("image", tex)
        batch.draw(shader)
        gpu.state.blend_set("ALPHA")  # restore standard blend for everything else
    except Exception as e:
        print(f"[SKIN] blit error '{key}': {e}")


def clear_cache() -> None:
    """Flush the texture cache (call on file load or skin change)."""
    global _texture_cache
    _texture_cache = {}
