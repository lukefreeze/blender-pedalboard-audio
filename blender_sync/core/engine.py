# =============================================================================
# core/engine.py
# The Hijacker — loads hijacker_engine.pyd and exposes get_engine().
# =============================================================================

import os
import sys

_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ADDON_DIR not in sys.path:
    sys.path.append(_ADDON_DIR)

_engine = None


def _import_engine():
    import bpy
    major, minor, _ = bpy.app.version
    for name in (f"hijacker_engine_bl{major}_{minor}", "hijacker_engine"):
        try:
            mod = __import__(name)
            print(f"[HIJACKER] loaded '{name}' for Blender {major}.{minor}")
            return mod
        except ImportError:
            pass
    print("[HIJACKER] WARNING: hijacker_engine not found — compile with build.bat")
    return None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _import_engine()
    return _engine


def reset_engine():
    global _engine
    _engine = None


# Kept for import compatibility — no longer installs pedalboard wheel
PEDALBOARD_AVAILABLE = True
HIJACKER_AVAILABLE   = True
