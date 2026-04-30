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
    "fader_handle":  "fader_handle.png",
    "fader_rail":    "fader_rail.png",
    "knob":          "knob.png",
    "knob_gain":     "knob_gain.png",
    "knob_eq":       "knob_eq.png",
    "knob_pan":      "knob_pan.png",
    "btn_mute_off":  "btn_mute_off.png",
    "btn_mute_on":   "btn_mute_on.png",
    "btn_solo_off":  "btn_solo_off.png",
    "btn_solo_on":   "btn_solo_on.png",
    "strip_bg":      "strip_bg.png",
    "rack_chassis":  "rack_chassis.png",
    "rack_rail":     "rack_rail.png",
}

# ---------------------------------------------------------------------------
# Runtime cache: key -> gpu.types.GPUTexture
# ---------------------------------------------------------------------------
_texture_cache: dict = {}
_active_skin:   str  = "default"
_skin_dir:      str  = ""


def set_skin_dir(assets_dir: str, skin_name: str = "default") -> None:
    """Called once at startup from Loader.py with the assets path."""
    global _skin_dir, _active_skin
    _active_skin = skin_name
    _skin_dir    = os.path.join(assets_dir, skin_name)


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
                 alpha: float = 1.0) -> None:
    """Blit a gpu.GPUTexture into a screen-space rect (x,y = bottom-left).

    Uses IMAGE shader with a simple TRI_STRIP quad.
    Called by draw_utils.draw_element() when a texture is available.
    """
    if tex is None or w <= 0 or h <= 0:
        return
    try:
        shader = gpu.shader.from_builtin("IMAGE")
        verts  = [(x, y), (x+w, y), (x, y+h), (x+w, y+h)]
        uvs    = [(0, 0), (1, 0), (0, 1), (1, 1)]
        batch  = batch_for_shader(shader, "TRI_STRIP",
                                  {"pos": verts, "texCoord": uvs})
        shader.bind()
        shader.uniform_sampler("image", tex)
        batch.draw(shader)
    except Exception as e:
        print(f"[SKIN] blit error: {e}")


def clear_cache() -> None:
    """Flush the texture cache (call on file load or skin change)."""
    global _texture_cache
    _texture_cache = {}
