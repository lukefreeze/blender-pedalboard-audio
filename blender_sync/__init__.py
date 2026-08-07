"""
__init__.py — Blender addon entry point for The Hijacker.

This file exists ONLY so Blender's addon system (Preferences > Add-ons,
and "Install from File") has a top-level module to discover and register.

All real logic lives in Loader.py — this file just re-exposes its
register()/unregister() functions under bl_info, which is the standard
pattern when addon logic is organised into a sub-module rather than
written directly in __init__.py.

Do NOT add logic here. Add it to Loader.py instead, so the dev workflow
(running Loader.py directly, F8 hot-reload) and the packaged-install
workflow (this file) stay in sync automatically.
"""

bl_info = {
    "name":        "The Hijacker",
    "author":      "Luke Bareham",
    "version":     (0, 1, 0),
    "blender":     (4, 5, 0),
    "location":    "Video Sequence Editor > Sidebar > Hijacker",
    "description": "Professional audio mixing and AI processing for Blender's VSE",
    "category":    "Sequencer",
}

from .Loader import register, unregister
