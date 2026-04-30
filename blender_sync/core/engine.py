# =============================================================================
# core/engine.py
# Handles importing pedalboard_engine.pyd and exposes a single get_engine()
# accessor used throughout the codebase.  Also owns the pedalboard wheel
# install so it stays in one place.
# =============================================================================

import os
import sys
import glob
import subprocess

# ---------------------------------------------------------------------------
# Path bootstrap — blender_sync/ must be on sys.path so the .pyd can load
# ---------------------------------------------------------------------------
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ADDON_DIR not in sys.path:
    sys.path.append(_ADDON_DIR)


# ---------------------------------------------------------------------------
# Pedalboard wheel install (library is bundled, not pip-installed globally)
# ---------------------------------------------------------------------------
def _ensure_pedalboard():
    """Install pedalboard from bundled wheel into addon lib/ if not present."""
    lib_dir = os.path.join(_ADDON_DIR, "lib")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)

    try:
        import pedalboard
        print(f"[PEDALBOARD] v{pedalboard.__version__} ready")
        return True
    except ImportError:
        pass

    wheels = glob.glob(os.path.join(_ADDON_DIR, "wheels", "pedalboard*.whl"))
    if not wheels:
        print("[PEDALBOARD] WARNING: no wheel found in wheels/ folder")
        return False

    print(f"[PEDALBOARD] Installing from {os.path.basename(wheels[0])}...")
    os.makedirs(lib_dir, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--target", lib_dir, "--no-deps", wheels[0]],
        capture_output=True, text=True)

    if result.returncode == 0:
        try:
            import pedalboard
            print(f"[PEDALBOARD] v{pedalboard.__version__} ready")
            return True
        except ImportError as e:
            print(f"[PEDALBOARD] Import failed after install: {e}")
            return False
    else:
        print(f"[PEDALBOARD] Install failed: {result.stderr[-500:]}")
        return False


PEDALBOARD_AVAILABLE = _ensure_pedalboard()


# ---------------------------------------------------------------------------
# Engine import — tries versioned name first (e.g. pedalboard_engine_bl4_5)
# ---------------------------------------------------------------------------
_engine = None


def _import_engine():
    import bpy
    major, minor, _ = bpy.app.version
    for name in (f"pedalboard_engine_bl{major}_{minor}", "pedalboard_engine"):
        try:
            mod = __import__(name)
            print(f"[ENGINE] loaded '{name}' for Blender {major}.{minor}")
            return mod
        except ImportError:
            pass
    print("[ENGINE] WARNING: pedalboard_engine not found")
    return None


def get_engine():
    """Return the engine module, importing it on first call."""
    global _engine
    if _engine is None:
        _engine = _import_engine()
    return _engine


def reset_engine():
    """Force a re-import on next get_engine() call (used after file load)."""
    global _engine
    _engine = None
