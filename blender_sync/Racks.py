"""
Racks.py — Pedalboard effects rack UI
Imported by Loader.py.

Draws a Reason-style rack of effect units below the mixer fader section.
Each rack unit can be expanded (full UI) or collapsed (single row).
No audio processing here — this file is UI only.
Audio wiring happens in Loader.py at play-start.
"""

import math
import bpy
import gpu
import blf
from gpu_extras.batch import batch_for_shader
# ---------------------------------------------------------------------------
# Shader singleton — gpu.shader.from_builtin() is expensive; reuse one instance.
# ---------------------------------------------------------------------------
_shader = None

def _get_shader():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _shader


def _mx_frame_blink_timer():
    """Forces a redraw every 0.5s so the mixdown frame box cursor blinks."""
    try:
        from ui.racks.rack_mixdown import _mx_state
        if _mx_state.get('text_focus') is None:
            return None  # cancel — no longer focused
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'NODE_EDITOR':
                    area.tag_redraw()
    except Exception:
        return None  # cancel on error
    return 0.5  # reschedule



# ---------------------------------------------------------------------------
# Shared state — set by Loader.py before calling draw_racks()
# ---------------------------------------------------------------------------
_UI_SCALE  = 1.0

# =============================================================================
# AI RACK RAIL — CHANNEL NUMBER LABEL TUNING
# The rail channel buttons are baked into a rack's full-unit skin art (see
# _ai_bg_key below), so only the number text draws in code once a skin is
# loaded — this nudges that text against the art. Unscaled px, + = right/up.
# Only applied when a full-rack skin texture is present; the flat-rect
# fallback (no skin yet) still centres the label on the GPU-drawn box as
# before, so this has no effect until a rack's art is wired in.
# =============================================================================
AI_RAIL_CH_LABEL_X_OFFSET = 1.0
AI_RAIL_CH_LABEL_Y_OFFSET = -3.0

# Rail channel-number text colour, unassigned state — brighter than the old
# (0.35, 0.12, 0.08) so it reads against the baked button art (same fix as
# the body-level "PLACE ON CHANNEL" numbers in rack_piper.py).
AI_RAIL_CH_LABEL_OFF_COLOR = (0.65, 0.25, 0.16, 1.0)

# GR levels written by the audio engine — rack reads these for GR meters
# gr_levels[rack_idx][channel_idx] = float 0.0-1.0 (0=no reduction, 1=max)
_gr_levels = {}

# LED pulse state — toggled by meter timer for "actively compressing" blink
_led_states      = {}   # rack_idx -> bool
# _gr_smooth_state removed — GR meters now use real GR timeline

# Popup state — effect selector
_popup_open     = False
_popup_x        = 0.0
_popup_y        = 0.0
_popup_group_idx = 0   # which fader group the Add Rack popup belongs to

# Reorder dropdown state
_reorder_open     = False   # whether the reorder dropdown is open
_reorder_rack_idx = -1      # which rack's badge was clicked
_reorder_x        = 0.0
_reorder_y        = 0.0

# AI popup state — for the + ADD AI RACK selector
_ai_popup_open    = False
_ai_popup_x       = 0.0
_ai_popup_y       = 0.0
_ai_popup_group_idx = 0   # which fader group the ADD AI RACK button belongs to

# ---------------------------------------------------------------------------
# Rack dimensions (in unscaled pixels, multiplied by UI_SCALE at draw time)
# ---------------------------------------------------------------------------
# Rack width matches the fader section exactly:
# left margin=30px, each fader strip=120px wide, stride=135px
# right edge of 9th fader = 30 + 8*135 + 120 = 1230px
# rack starts at 30px → rack width = 1230 - 30 = 1200px
RACK_WIDTH          = 1200         # matches 9-fader section width exactly
RACK_EXPANDED_H     = 260
RACK_EXPANDED_H_MB  = 400
RACK_EXPANDED_H_EQ  = 580
RACK_EXPANDED_H_RV  = 340
RACK_EXPANDED_H_NG  = 320   # noise gate — display + 5-knob row
RACK_EXPANDED_H_DL  = 320   # delay      — waveform display + 5-knob row
RACK_EXPANDED_H_MX  = 320   # mixdown    — 4-column layout
RACK_COLLAPSED_H    = 36
# ^ AI-rack collapsed row height ONLY (see _get_ai_rack_height below). The
# standard (DSP) rack collapse/stacking code shadows this name locally with
# rack_base.RACK_COLLAPSED_H (=48) instead — see the comment in draw_racks()
# for why. Do NOT "fix" standard racks by changing this value; it'll change
# the AI rack row height too. If you ever unify these, update both.
RACK_MARGIN_TOP     = 40           # gap between fader section and racks
RACK_GAP            = 0            # gap between rack units
RACK_RAIL_H         = 32           # top rail height

# Knob layout (left section of expanded rack)
KNOB_SECTION_W      = 360
KNOB_START_X        = 55
KNOB_SPACING        = 68

# Spectrum display
SPEC_X              = 430
SPEC_W              = 480
SPEC_H              = 200
SPEC_BANDS          = 48           # number of frequency bars

# GR meter section
GR_BAR_W            = 12
GR_BAR_SPACING      = 16

# Channel buttons (right section)
CH_BTN_SIZE         = 26

# ---------------------------------------------------------------------------
# Effect type definitions
# ---------------------------------------------------------------------------
EFFECT_TYPES = [
    ("COMP_SINGLE", "Single Band Compressor"),
    ("COMP_MULTI",  "Multiband Compressor"),
    ("EQ",          "Parametric EQ"),
    ("REVERB",      "Reverb"),
    ("NOISE_GATE",  "Noise Gate"),
    ("DELAY",       "Delay"),
    ("BOOSTER",     "The BOOSTER!!"),
    ("MIXDOWN",     "Mixdown"),
]

# AI rack type identifiers — separate namespace from DSP racks
AI_RACK_TYPES = [
    ("PIPER_TTS",     "Piper TTS — Text to Speech"),
    ("RVC",           "Voice Conversion kNN-VC (Beta)"),
    ("RESEMBLE",      "VoiceFixer — Speech Restoration (Beta)"),
    ("WHISPER",       "Whisper — Speech to Text (Beta)"),
    ("DEMUCS",        "Demucs — Source Separation (Beta)"),
]

EFFECT_PARAMS = {
    # COMP_SINGLE: p0=thr, p1=ratio, p2=attack, p3=release, p4=makeup, p5=knee
    "COMP_SINGLE": [
        ("THRESHOLD", "Threshold", -40.0,  0.0,  -18.0, "{:.0f}dB"),
        ("RATIO",     "Ratio",       1.0, 20.0,    4.0, "{:.1f}:1"),
        ("ATTACK",    "Attack",      0.1,100.0,   10.0, "{:.0f}ms"),
        ("RELEASE",   "Release",    10.0,1000.0,  80.0, "{:.0f}ms"),
        ("MAKEUP",    "Makeup",      0.0, 24.0,    0.0, "+{:.1f}dB"),
        ("KNEE",      "Knee",        0.5, 24.0,    4.0, "{:.1f}dB"),
    ],
    # COMP_MULTI: p0-p3=thr, p4-p7=ratio, p8-p11=attack,
    #             p12-p15=release, p16-p19=gain, p20-p23=knee
    "COMP_MULTI": [
        ("THR_LOW",   "Low Thr",   -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("THR_LMD",   "LMid Thr",  -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("THR_HMD",   "HMid Thr",  -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("THR_HIGH",  "High Thr",  -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("RAT_LOW",   "Low Rat",     1.0,  20.0,    4.0, "{:.1f}:1"),
        ("RAT_LMD",   "LMid Rat",    1.0,  20.0,    4.0, "{:.1f}:1"),
        ("RAT_HMD",   "HMid Rat",    1.0,  20.0,    4.0, "{:.1f}:1"),
        ("RAT_HIGH",  "High Rat",    1.0,  20.0,    4.0, "{:.1f}:1"),
        ("ATK_LOW",   "Low Atk",     0.1, 100.0,   10.0, "{:.0f}ms"),
        ("ATK_LMD",   "LMid Atk",    0.1, 100.0,   10.0, "{:.0f}ms"),
        ("ATK_HMD",   "HMid Atk",    0.1, 100.0,   10.0, "{:.0f}ms"),
        ("ATK_HIGH",  "High Atk",    0.1, 100.0,   10.0, "{:.0f}ms"),
        ("REL_LOW",   "Low Rel",    10.0,1000.0,   80.0, "{:.0f}ms"),
        ("REL_LMD",   "LMid Rel",   10.0,1000.0,   80.0, "{:.0f}ms"),
        ("REL_HMD",   "HMid Rel",   10.0,1000.0,   80.0, "{:.0f}ms"),
        ("REL_HIGH",  "High Rel",   10.0,1000.0,   80.0, "{:.0f}ms"),
        ("GAIN_LOW",  "Low Gain",  -12.0,  12.0,    0.0, "{:+.1f}dB"),
        ("GAIN_LMD",  "LMid Gain", -12.0,  12.0,    0.0, "{:+.1f}dB"),
        ("GAIN_HMD",  "HMid Gain", -12.0,  12.0,    0.0, "{:+.1f}dB"),
        ("GAIN_HIGH", "High Gain", -12.0,  12.0,    0.0, "{:+.1f}dB"),
        ("KNEE_LOW",  "Low Knee",    0.5,  24.0,    4.0, "{:.1f}dB"),
        ("KNEE_LMD",  "LMid Knee",   0.5,  24.0,    4.0, "{:.1f}dB"),
        ("KNEE_HMD",  "HMid Knee",   0.5,  24.0,    4.0, "{:.1f}dB"),
        ("KNEE_HIGH", "High Knee",   0.5,  24.0,    4.0, "{:.1f}dB"),
    ],
    # "EQ" entry removed — EQ rack uses its own inline EQ7_BANDS (7-band);
    # this 5-band entry was unreachable and is intentionally omitted.
    "REVERB": [
        # Order MUST match audio engine: p0=room, p1=damp, p2=wet, p3=pre_delay, p4=width
        ("ROOM",      "Room",         0.0,  1.0,   0.5, "{:.0%}"),
        ("DAMP",      "Damp",         0.0,  1.0,   0.5, "{:.0%}"),
        ("WET",       "Wet",          0.0,  1.0,   0.3, "{:.0%}"),
        ("PRE_DELAY", "Pre-dly",      0.0,  1.0,   0.0, "{:.0%}"),
        ("WIDTH",     "Width",        0.0,  1.0,   1.0, "{:.0%}"),
    ],
    "NOISE_GATE": [
        ("THRESHOLD", "Threshold",  -80.0,  0.0, -40.0, "{:.0f}dB"),
        ("ATTACK",    "Attack",       0.1,100.0,   5.0, "{:.0f}ms"),
        ("HOLD",      "Hold",         0.0,500.0,  50.0, "{:.0f}ms"),
        ("RELEASE",   "Release",     10.0,1000.0,100.0, "{:.0f}ms"),
        ("RANGE",     "Range",      -90.0,  0.0, -90.0, "{:.0f}dB"),
    ],
    "DELAY": [
        ("TIME",      "Time",         1.0,2000.0,250.0, "{:.0f}ms"),
        ("FEEDBACK",  "Feedback",     0.0,  1.0,  0.4, "{:.0%}"),
        ("MIX",       "Mix",          0.0,  1.0,  0.3, "{:.0%}"),
        ("SPREAD",    "Spread",       0.0,  1.0,  0.5, "{:.0%}"),
        ("FILTER",    "Filter",       0.0,  1.0,  0.5, "{:.0%}"),
    ],
    "BOOSTER": [
        ("BOOST",   "Boost",   0.0, 40.0, 12.0, "+{:.1f}dB"),
        ("LIMITER", "Limiter", 0.0,  1.0,  1.0, "on/off"),
    ],
}

# PRESET_DATA: dict of effect_type -> list of (name, [normalised_params])
# COMP_SINGLE: [thr, ratio, atk, rel, makeup, knee]  (p0-p5)
# COMP_MULTI:  [thr*4, ratio*4, atk*4, rel*4, gain*4, knee*4] (p0-p23)
PRESET_DATA = {'COMP_SINGLE': [('Vocal Compress', [0.075, 0.10526315789473684, 0.0990990990990991, 0.0707070707070707, 0.16666666666666666, 0.14893617021276595]), ('Gentle Glue', [0.0875, 0.02631578947368421, 0.29929929929929927, 0.1919191919191919, 0.08333333333333333, 0.3191489361702128]), ('Drum Bus', [0.1, 0.2631578947368421, 0.009009009009009009, 0.09090909090909091, 0.125, 0.06382978723404255]), ('Heavy Squash', [0.025, 0.47368421052631576, 0.04904904904904905, 0.04040404040404041, 0.3333333333333333, 0.02127659574468085]), ('Transparent', [0.0875, 0.05263157894736842, 0.49949949949949946, 0.494949494949495, 0.041666666666666664, 0.40425531914893614]), ('Broadcast Voice', [0.075, 0.15789473684210525, 0.04904904904904905, 0.050505050505050504, 0.25, 0.06382978723404255]), ('Intimate Whisper', [0.0875, 0.07894736842105263, 0.39939939939939934, 0.29292929292929293, 0.125, 0.3191489361702128]), ('Telephone', [0.0, 1.0, 0.0, 0.010101010101010102, 0.4166666666666667, 0.0]), ('Radio Ready', [0.05, 0.3684210526315789, 0.019019019019019017, 0.030303030303030304, 0.3333333333333333, 0.0425531914893617]), ('Vintage Tape', [0.0625, 0.10526315789473684, 0.19919919919919918, 0.1919191919191919, 0.125, 0.23404255319148937]), ('Dark Presence', [0.0375, 0.5789473684210527, 0.009009009009009009, 0.020202020202020204, 0.4166666666666667, 0.02127659574468085]), ('Robot Voice', [0.0, 1.0, 0.0, 0.0, 0.5, 0.0]), ('Underwater', [0.025, 0.15789473684210525, 0.7997997997997998, 0.898989898989899, 0.16666666666666666, 0.48936170212765956]), ('Announcer', [0.075, 0.13157894736842105, 0.07907907907907907, 0.09090909090909091, 0.20833333333333334, 0.10638297872340426]), ('Whisper to Shout', [0.0125, 0.7368421052631579, 0.04904904904904905, 0.1414141414141414, 0.3333333333333333, 0.14893617021276595])], 'COMP_MULTI': [('Voice Over Clean', [0.45, 0.5, 0.55, 0.6, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.19919919919919918, 0.14914914914914915, 0.0990990990990991, 0.07907907907907907, 0.1919191919191919, 0.1414141414141414, 0.09090909090909091, 0.0707070707070707, 0.5, 0.5, 0.5, 0.5, 0.3191489361702128, 0.23404255319148937, 0.19148936170212766, 0.14893617021276595]), ('Voice Over Warm', [0.5, 0.55, 0.6, 0.65, 0.07894736842105263, 0.10526315789473684, 0.10526315789473684, 0.05263157894736842, 0.14914914914914915, 0.0990990990990991, 0.07907907907907907, 0.04904904904904905, 0.1414141414141414, 0.09090909090909091, 0.0707070707070707, 0.050505050505050504, 0.5833333333333334, 0.4583333333333333, 0.4166666666666667, 0.5, 0.23404255319148937, 0.19148936170212766, 0.14893617021276595, 0.10638297872340426]), ('Voice Over Bright', [0.45, 0.5, 0.55, 0.65, 0.05263157894736842, 0.07894736842105263, 0.10526315789473684, 0.10526315789473684, 0.19919919919919918, 0.11911911911911911, 0.07907907907907907, 0.04904904904904905, 0.1717171717171717, 0.1111111111111111, 0.0707070707070707, 0.050505050505050504, 0.4583333333333333, 0.5, 0.5416666666666666, 0.5833333333333334, 0.2765957446808511, 0.19148936170212766, 0.14893617021276595, 0.10638297872340426]), ('Male Voice', [0.55, 0.65, 0.5, 0.45, 0.10526315789473684, 0.15789473684210525, 0.07894736842105263, 0.05263157894736842, 0.0990990990990991, 0.07907907907907907, 0.14914914914914915, 0.19919919919919918, 0.09090909090909091, 0.0707070707070707, 0.1414141414141414, 0.1919191919191919, 0.5416666666666666, 0.5, 0.4583333333333333, 0.4166666666666667, 0.14893617021276595, 0.10638297872340426, 0.23404255319148937, 0.3191489361702128]), ('Female Voice', [0.45, 0.55, 0.65, 0.6, 0.05263157894736842, 0.10526315789473684, 0.15789473684210525, 0.10526315789473684, 0.19919919919919918, 0.11911911911911911, 0.05905905905905906, 0.07907907907907907, 0.1717171717171717, 0.09090909090909091, 0.050505050505050504, 0.0707070707070707, 0.4583333333333333, 0.5, 0.5416666666666666, 0.5, 0.3191489361702128, 0.19148936170212766, 0.10638297872340426, 0.14893617021276595]), ('Narration', [0.4, 0.45, 0.5, 0.55, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.29929929929929927, 0.2492492492492492, 0.19919919919919918, 0.14914914914914915, 0.29292929292929293, 0.24242424242424243, 0.1919191919191919, 0.1414141414141414, 0.5, 0.5, 0.5, 0.5, 0.40425531914893614, 0.3191489361702128, 0.23404255319148937, 0.19148936170212766]), ('Podcast Ready', [0.55, 0.6, 0.65, 0.65, 0.10526315789473684, 0.10526315789473684, 0.15789473684210525, 0.10526315789473684, 0.0990990990990991, 0.07907907907907907, 0.05905905905905906, 0.04904904904904905, 0.09090909090909091, 0.0707070707070707, 0.050505050505050504, 0.050505050505050504, 0.5416666666666666, 0.5416666666666666, 0.5416666666666666, 0.5, 0.19148936170212766, 0.14893617021276595, 0.10638297872340426, 0.10638297872340426]), ('Broadcast', [0.7, 0.75, 0.7, 0.65, 0.2631578947368421, 0.3684210526315789, 0.2631578947368421, 0.21052631578947367, 0.039039039039039033, 0.029029029029029027, 0.039039039039039033, 0.04904904904904905, 0.04040404040404041, 0.030303030303030304, 0.04040404040404041, 0.050505050505050504, 0.625, 0.5833333333333334, 0.5833333333333334, 0.5416666666666666, 0.06382978723404255, 0.06382978723404255, 0.06382978723404255, 0.10638297872340426]), ('Dark Presence', [0.0375, 0.6, 0.45, 0.4, 0.47368421052631576, 0.15789473684210525, 0.05263157894736842, 0.05263157894736842, 0.019019019019019017, 0.0990990990990991, 0.19919919919919918, 0.29929929929929927, 0.030303030303030304, 0.09090909090909091, 0.1919191919191919, 0.29292929292929293, 0.6666666666666666, 0.5416666666666666, 0.4583333333333333, 0.4166666666666667, 0.02127659574468085, 0.10638297872340426, 0.23404255319148937, 0.3191489361702128]), ('Whisper Voice', [0.3, 0.35, 0.4, 0.45, 0.02631578947368421, 0.02631578947368421, 0.05263157894736842, 0.05263157894736842, 0.39939939939939934, 0.3493493493493493, 0.2492492492492492, 0.19919919919919918, 0.3434343434343434, 0.29292929292929293, 0.24242424242424243, 0.1919191919191919, 0.5, 0.5, 0.5, 0.5, 0.48936170212765956, 0.40425531914893614, 0.3191489361702128, 0.23404255319148937]), ('Telephone MB', [0.85, 0.9, 0.9, 0.75, 1.0, 0.7368421052631579, 0.7368421052631579, 0.47368421052631576, 0.0, 0.0, 0.0, 0.009009009009009009, 0.0, 0.0, 0.0, 0.04040404040404041, 0.0, 0.6666666666666666, 0.6666666666666666, 0.0, 0.0, 0.0, 0.0, 0.0]), ('Loud & Proud', [0.8, 0.75, 0.75, 0.7, 0.47368421052631576, 0.3684210526315789, 0.3684210526315789, 0.2631578947368421, 0.009009009009009009, 0.019019019019019017, 0.019019019019019017, 0.029029029029029027, 0.020202020202020204, 0.030303030303030304, 0.030303030303030304, 0.04040404040404041, 0.7083333333333334, 0.625, 0.625, 0.5833333333333334, 0.02127659574468085, 0.02127659574468085, 0.06382978723404255, 0.06382978723404255]), ('Gentle Master', [0.3, 0.35, 0.4, 0.45, 0.02631578947368421, 0.02631578947368421, 0.02631578947368421, 0.02631578947368421, 0.49949949949949946, 0.39939939939939934, 0.3493493493493493, 0.29929929929929927, 0.3939393939393939, 0.3434343434343434, 0.29292929292929293, 0.24242424242424243, 0.5, 0.5, 0.5, 0.5, 0.48936170212765956, 0.40425531914893614, 0.3191489361702128, 0.23404255319148937]), ('Drum Glue', [0.65, 0.6, 0.5, 0.4, 0.21052631578947367, 0.15789473684210525, 0.07894736842105263, 0.05263157894736842, 0.019019019019019017, 0.04904904904904905, 0.14914914914914915, 0.2492492492492492, 0.050505050505050504, 0.0707070707070707, 0.1414141414141414, 0.1919191919191919, 0.5833333333333334, 0.5416666666666666, 0.5, 0.4583333333333333, 0.06382978723404255, 0.10638297872340426, 0.19148936170212766, 0.2765957446808511]), ('Master Bus', [0.45, 0.5, 0.55, 0.6, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.2492492492492492, 0.19919919919919918, 0.14914914914914915, 0.0990990990990991, 0.24242424242424243, 0.1919191919191919, 0.1414141414141414, 0.09090909090909091, 0.5, 0.5, 0.5, 0.5, 0.3191489361702128, 0.23404255319148937, 0.19148936170212766, 0.14893617021276595]), ('EXTREME Crush', [0.85, 0.85, 0.85, 0.85, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8333333333333334, 0.8333333333333334, 0.8333333333333334, 0.8333333333333334, 0.0, 0.0, 0.0, 0.0]), ('Robot Voice MB', [0.8, 0.85, 0.85, 0.7, 0.8947368421052632, 1.0, 1.0, 0.3684210526315789, 0.0, 0.0, 0.0, 0.0, 0.005050505050505051, 0.0, 0.0, 0.020202020202020204, 0.25, 0.75, 0.8333333333333334, 0.16666666666666666, 0.0, 0.0, 0.0, 0.0]), ('Underwater MB', [0.5, 0.4, 0.75, 0.85, 0.10526315789473684, 0.05263157894736842, 0.3684210526315789, 0.7368421052631579, 0.49949949949949946, 0.39939939939939934, 0.04904904904904905, 0.0, 0.494949494949495, 0.3939393939393939, 0.04040404040404041, 0.0, 0.9166666666666666, 0.5833333333333334, 0.3333333333333333, 0.08333333333333333, 0.48936170212765956, 0.3191489361702128, 0.06382978723404255, 0.0])]}
# EQ presets — 21 params: p0-p6=gain(norm 0-1, 0.5=0dB ±24dB),
#                          p7-p13=freq(log-norm 0-1, 20Hz-20kHz),
#                          p14-p20=Q(log-norm 0-1, 0.1-10.0)
PRESET_DATA['EQ'] = [
    ('Presence Boost', [0.5, 0.5, 0.5, 0.5625, 0.604167, 0.5625, 0.5, 0.200687, 0.365637, 0.514689, 0.725364, 0.825707, 0.92605, 0.967697, 0.422549, 0.5, 0.5, 0.539591, 0.5, 0.5, 0.422549]),
    ('Low Cut',        [0.125, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.23299, 0.365637, 0.514689, 0.666667, 0.799313, 0.899657, 0.967697, 0.422549, 0.5, 0.5, 0.5, 0.5, 0.5, 0.422549]),
    ('Air',            [0.5, 0.5, 0.458333, 0.5, 0.5, 0.583333, 0.625, 0.200687, 0.39203, 0.514689, 0.666667, 0.799313, 0.92605, 0.967697, 0.422549, 0.539591, 0.5, 0.5, 0.5, 0.5, 0.422549]),
    ('Mud Remove',     [0.5, 0.416667, 0.4375, 0.541667, 0.541667, 0.5, 0.5, 0.200687, 0.333333, 0.433677, 0.725364, 0.799313, 0.899657, 0.967697, 0.422549, 0.588046, 0.588046, 0.5, 0.5, 0.5, 0.422549]),
    ('Telephone',      [0.0, 0.5625, 0.583333, 0.5625, 0.375, 0.25, 0.125, 0.23299, 0.39203, 0.53402, 0.69897, 0.799313, 0.899657, 0.967697, 0.422549, 0.650515, 0.588046, 0.588046, 0.588046, 0.588046, 0.422549]),
]

# REVERB presets — 5 params: [room_size, damping, wet, pre_delay, width]
PRESET_DATA['REVERB'] = [
    # ── Rooms ────────────────────────────────────────────────────────────────
    ('Bathroom Tiles',       [0.2,  0.1,  0.3,  0.0,  0.6 ]),
    ('Small Room',           [0.3,  0.25, 0.28, 0.01, 0.7 ]),
    ('Medium Room',          [0.45, 0.35, 0.3,  0.02, 0.8 ]),
    ('Large Room',           [0.6,  0.4,  0.32, 0.03, 0.85]),
    ('Drum Room',            [0.4,  0.2,  0.35, 0.01, 0.9 ]),
    ('Studio Live Room',     [0.35, 0.45, 0.25, 0.01, 0.75]),
    ('Garage',               [0.5,  0.15, 0.38, 0.02, 0.8 ]),
    # ── Halls ────────────────────────────────────────────────────────────────
    ('Small Hall',           [0.65, 0.5,  0.35, 0.04, 0.85]),
    ('Concert Hall',         [0.78, 0.55, 0.38, 0.06, 0.9 ]),
    ('Cathedral',            [0.9,  0.3,  0.45, 0.1,  0.95]),
    ('Church',               [0.75, 0.4,  0.4,  0.07, 0.9 ]),
    ('Stadium',              [0.95, 0.2,  0.4,  0.12, 1.0 ]),
    ('Outdoor Amphitheatre', [0.7,  0.6,  0.32, 0.08, 0.85]),
    # ── Plates ───────────────────────────────────────────────────────────────
    ('Bright Plate',         [0.55, 0.05, 0.35, 0.0,  0.8 ]),
    ('Dark Plate',           [0.55, 0.7,  0.35, 0.0,  0.8 ]),
    ('Vintage Plate',        [0.6,  0.45, 0.38, 0.01, 0.75]),
    ('Vocal Plate',          [0.5,  0.35, 0.3,  0.01, 0.7 ]),
    # ── Springs ──────────────────────────────────────────────────────────────
    ('Guitar Spring',        [0.38, 0.55, 0.32, 0.0,  0.4 ]),
    ('Vintage Spring',       [0.42, 0.6,  0.35, 0.0,  0.35]),
    # ── Chambers ─────────────────────────────────────────────────────────────
    ('Echo Chamber',         [0.65, 0.5,  0.42, 0.05, 0.85]),
    ('Vocal Chamber',        [0.48, 0.55, 0.28, 0.02, 0.7 ]),
    # ── Special ──────────────────────────────────────────────────────────────
    ('Slap Room',            [0.25, 0.3,  0.28, 0.03, 0.75]),
    ('Tunnel',               [0.8,  0.1,  0.45, 0.06, 0.5 ]),
    ('Cave',                 [0.85, 0.25, 0.48, 0.08, 0.6 ]),
    ('Parking Garage',       [0.55, 0.1,  0.4,  0.05, 0.7 ]),
    ('Arena',                [0.88, 0.35, 0.42, 0.1,  0.95]),
    ('Club',                 [0.52, 0.3,  0.35, 0.03, 0.85]),
    ('Long Ambient',         [0.92, 0.65, 0.5,  0.12, 1.0 ]),
    ('Dry Ambience',         [0.15, 0.7,  0.15, 0.0,  0.5 ]),
]

PRESET_DATA['NOISE_GATE'] = [
    # ── Dialogue / vocals ──────────────────────────────────────────────────
    ('Dialogue Clean',   [0.50, 0.05, 0.12, 0.09, 0.0 ]),
    ('Dialogue Natural', [0.44, 0.08, 0.20, 0.15, 0.0 ]),
    ('Vocal Gate',       [0.50, 0.05, 0.16, 0.18, 0.0 ]),
    ('Breathy Vocal',    [0.38, 0.10, 0.24, 0.25, 0.0 ]),
    ('Hard Vocal Cut',   [0.60, 0.03, 0.08, 0.08, 0.0 ]),
    # ── Drums ──────────────────────────────────────────────────────────────
    ('Drum Room',        [0.56, 0.02, 0.10, 0.06, 0.0 ]),
    ('Snare Gate',       [0.63, 0.01, 0.06, 0.05, 0.0 ]),
    ('Kick Gate',        [0.56, 0.02, 0.14, 0.07, 0.0 ]),
    ('Overhead Gate',    [0.44, 0.05, 0.20, 0.12, 0.0 ]),
    ('Drum Bleed',       [0.50, 0.03, 0.08, 0.05, 0.0 ]),
    # ── Instruments ────────────────────────────────────────────────────────
    ('Guitar Amp',       [0.44, 0.05, 0.16, 0.12, 0.0 ]),
    ('Bass Gate',        [0.44, 0.03, 0.18, 0.10, 0.0 ]),
    ('Piano Room',       [0.38, 0.08, 0.30, 0.20, 0.0 ]),
    # ── Special / utility ──────────────────────────────────────────────────
    ('Tight Gate',       [0.56, 0.02, 0.06, 0.04, 0.0 ]),
    ('Soft Gate',        [0.38, 0.15, 0.30, 0.30, 0.0 ]),
    ('Room Noise Kill',  [0.56, 0.04, 0.10, 0.08, 0.0 ]),
    ('Expander Light',   [0.44, 0.12, 0.24, 0.25, 0.44]),
    ('Expander Hard',    [0.50, 0.05, 0.10, 0.10, 0.0 ]),
    ('De-breath',        [0.44, 0.05, 0.12, 0.10, 0.0 ]),
    ('Natural',          [0.44, 0.10, 0.20, 0.20, 0.0 ]),
]


PRESET_DATA['DELAY'] = [
    # name,            [time_norm, feedback, mix,  spread, filter]
    # time_norm: 0-1 → 1-2000ms
    ('Slapback',       [0.038, 0.00,  0.30, 0.00,  0.70]),
    ('Quarter Note',   [0.121, 0.40,  0.30, 0.50,  0.60]),
    ('Dotted 8th',     [0.091, 0.45,  0.28, 0.50,  0.55]),
    ('Ping Pong',      [0.121, 0.50,  0.30, 1.00,  0.55]),
    ('Tape Echo',      [0.075, 0.55,  0.35, 0.20,  0.30]),
    ('Vocal Doubler',  [0.018, 0.00,  0.50, 0.40,  0.80]),
    ('Room Slap',      [0.025, 0.10,  0.25, 0.30,  0.65]),
    ('Long Ambient',   [0.249, 0.60,  0.25, 0.80,  0.40]),
]

PRESET_DATA['BOOSTER'] = [
    # name,       [boost_norm (0-1 → 0-40dB), limiter_on (0 or 1)]
    ('+6 dB',  [6/40,  1.0]),
    ('+12 dB', [12/40, 1.0]),
    ('+18 dB', [18/40, 1.0]),
    ('+24 dB', [24/40, 1.0]),
    ('+6 Raw',  [6/40,  0.0]),
    ('+12 Raw', [12/40, 0.0]),
]

# AI rack presets — keyed by ai_type
# DeepFilterNet params: [p0=atten(0-1), p1=sensitiv(0-1), p2=postgain_norm(0-1)]
# p0: 0=no attenuation, 1=max attenuation (-40dB limit)
# p1: 0=always process, 1=only process silence gaps
# p2: 0.5=0dB post gain, 0=−12dB, 1=+12dB
AI_PRESET_DATA = {}
# Piper TTS presets are voice names — discovered at runtime from voices/ folder
# These are fallback display names if the folder can't be scanned
AI_PRESET_DATA['PIPER_TTS'] = [
    # (name, [p0_speed_norm, p1_noise_scale, p2_noise_w])
    ('Narration',      [0.50, 0.667, 0.80]),  # Normal pace, natural expression
    ('Audiobook',      [0.45, 0.500, 0.70]),  # Slightly slower, consistent tone
    ('Fast Read',      [0.75, 0.667, 0.80]),  # Quick, for subtitles/temp tracks
    ('Slow & Clear',   [0.25, 0.500, 0.60]),  # Deliberate, great for instruction
    ('Expressive',     [0.50, 0.900, 0.90]),  # High variation, dramatic
    ('Monotone',       [0.50, 0.100, 0.20]),  # Robotic, low variation
    ('Whisper',        [0.40, 0.800, 0.95]),  # Soft, breathy quality
    ('Broadcaster',    [0.55, 0.400, 0.65]),  # Professional, even delivery
    ('Character',      [0.50, 1.000, 1.00]),  # Max variation, for characters
    ('Subtitle Pace',  [0.80, 0.667, 0.80]),  # Fast, matches subtitle timing
]
AI_PRESETS = {k: [p[0] for p in v] for k, v in AI_PRESET_DATA.items()}



def _load_ai_preset(rack, preset_idx):
    """Load a DeepFilterNet preset by index into rack params."""
    atype   = rack.ai_type
    presets = AI_PRESET_DATA.get(atype)
    if presets and 0 <= preset_idx < len(presets):
        _, params = presets[preset_idx]
        for i, v in enumerate(params):
            attr = f'p{i}'
            if hasattr(rack, attr):
                setattr(rack, attr, float(v))
        rack.preset_idx = preset_idx


# Legacy name lists for rack preset display
PRESETS = {
    "COMP_SINGLE": [p[0] for p in PRESET_DATA["COMP_SINGLE"]],
    "COMP_MULTI":  [p[0] for p in PRESET_DATA["COMP_MULTI"]],
    "EQ":          [p[0] for p in PRESET_DATA["EQ"]],
    "REVERB":      [p[0] for p in PRESET_DATA["REVERB"]],
    "NOISE_GATE":  [p[0] for p in PRESET_DATA["NOISE_GATE"]],
    "DELAY":       [p[0] for p in PRESET_DATA["DELAY"]],
    "MIXDOWN":     ["settings"],   # single entry — no presets but list must be non-empty
    "BOOSTER":     [p[0] for p in PRESET_DATA["BOOSTER"]],
}

# ---------------------------------------------------------------------------
# Property group — one rack instance
# ---------------------------------------------------------------------------
class PB_RackSettings(bpy.types.PropertyGroup):
    effect_type:      bpy.props.StringProperty(default="COMP_SINGLE")
    enabled:          bpy.props.BoolProperty(default=True)
    collapsed:        bpy.props.BoolProperty(default=False)
    preset_idx:       bpy.props.IntProperty(default=0)
    # Which group of 9 faders this rack belongs to.
    # group 0 = VSE channels 1-9, group 1 = 10-18, group 2 = 19-27, etc.
    # Defaults to 0 so all existing racks survive migration unchanged.
    group_idx:        bpy.props.IntProperty(default=0)
    # Mixdown rack output path — stored as file path string
    mixdown_output_path: bpy.props.StringProperty(default="", subtype='FILE_PATH')
    # Mixdown place-on-channel target: 0 = auto, 1-32 = specific channel
    mixdown_place_ch: bpy.props.IntProperty(default=0, min=0, max=32)
    # 32 channel assignment booleans (matches MAX_CHANNELS in Loader.py)
    ch0:  bpy.props.BoolProperty(default=False)
    ch1:  bpy.props.BoolProperty(default=False)
    ch2:  bpy.props.BoolProperty(default=False)
    ch3:  bpy.props.BoolProperty(default=False)
    ch4:  bpy.props.BoolProperty(default=False)
    ch5:  bpy.props.BoolProperty(default=False)
    ch6:  bpy.props.BoolProperty(default=False)
    ch7:  bpy.props.BoolProperty(default=False)
    ch8:  bpy.props.BoolProperty(default=False)
    # 8 parameter values (covers all effect types)
    p0: bpy.props.FloatProperty(default=0.0)
    p1: bpy.props.FloatProperty(default=0.0)
    p2: bpy.props.FloatProperty(default=0.0)
    p3: bpy.props.FloatProperty(default=0.0)
    p4: bpy.props.FloatProperty(default=0.0)
    p5: bpy.props.FloatProperty(default=0.0)
    p6: bpy.props.FloatProperty(default=0.0)
    p7: bpy.props.FloatProperty(default=0.0)
    # Extended params (p8-p23)
    # p0-p3:  thr, p4-p7:  ratio, p8-p11: attack, p12-p15: release
    # p16-p19: gain (0.5=unity), p20-p23: knee
    # COMP_SINGLE uses p0-p5 only
    p8:  bpy.props.FloatProperty(default=0.0)
    p9:  bpy.props.FloatProperty(default=0.0)
    p10: bpy.props.FloatProperty(default=0.0)
    p11: bpy.props.FloatProperty(default=0.0)
    p12: bpy.props.FloatProperty(default=0.0)
    p13: bpy.props.FloatProperty(default=0.0)
    p14: bpy.props.FloatProperty(default=0.0)
    p15: bpy.props.FloatProperty(default=0.0)
    p16: bpy.props.FloatProperty(default=0.5)
    p17: bpy.props.FloatProperty(default=0.5)
    p18: bpy.props.FloatProperty(default=0.5)
    p19: bpy.props.FloatProperty(default=0.5)
    p20: bpy.props.FloatProperty(default=0.14)  # knee default ~4dB
    p21: bpy.props.FloatProperty(default=0.14)
    p22: bpy.props.FloatProperty(default=0.14)
    p23: bpy.props.FloatProperty(default=0.14)
    # Processing state — used by BOOSTER and any future offline DSP racks
    ai_status: bpy.props.StringProperty(default="READY")


# ---------------------------------------------------------------------------
# Property group — one AI rack instance
# ---------------------------------------------------------------------------
class PB_AIRackSettings(bpy.types.PropertyGroup):
    """Property group for a single AI rack instance."""
    ai_type:   bpy.props.StringProperty(default="WHISPER")
    enabled:   bpy.props.BoolProperty(default=True)
    collapsed: bpy.props.BoolProperty(default=False)
    # Which fader group (0=CH1-9, 1=CH10-18, …) this rack belongs to.
    # Controls which column it appears in and which 9 channels its rail shows.
    group_idx: bpy.props.IntProperty(default=0)
    # Channel assignment — which channels feed into this AI rack
    ch0:  bpy.props.BoolProperty(default=False)
    ch1:  bpy.props.BoolProperty(default=False)
    ch2:  bpy.props.BoolProperty(default=False)
    ch3:  bpy.props.BoolProperty(default=False)
    ch4:  bpy.props.BoolProperty(default=False)
    ch5:  bpy.props.BoolProperty(default=False)
    ch6:  bpy.props.BoolProperty(default=False)
    ch7:  bpy.props.BoolProperty(default=False)
    ch8:  bpy.props.BoolProperty(default=False)
    # Float params
    # p0 = Attenuation limit (norm 0-1, maps -40..0 dB)
    # p1 = Sensitivity / voice activity threshold (0-1)
    # p2 = Post gain (norm 0-1, maps -12..+12 dB)
    # p3-p7 reserved for Whisper/Demucs/Piper/Matchering
    p0: bpy.props.FloatProperty(default=1.0)   # Whisper model default = Base (index 1)
    p1: bpy.props.FloatProperty(default=0.75)  # Sensitivity default = 0.75
    p2: bpy.props.FloatProperty(default=0.5)   # Post gain default = 0dB
    p3: bpy.props.FloatProperty(default=0.0)
    p4: bpy.props.FloatProperty(default=40.0)  # Whisper font size default = 40
    p5: bpy.props.FloatProperty(default=0.0)
    p6: bpy.props.FloatProperty(default=2.0)   # Whisper position default = BOT
    p7: bpy.props.FloatProperty(default=1.0)   # Whisper VAD default = on
    # Processing state — updated by the processing thread
    ai_status: bpy.props.StringProperty(default="READY")
    # Text field for Piper TTS script input
    ai_text: bpy.props.StringProperty(default="", maxlen=4096)
    # Path to last processed output file (for reload/bypass)
    ai_output_path:    bpy.props.StringProperty(default="", subtype='FILE_PATH')
    preset_idx:        bpy.props.IntProperty(default=0)
    # Whisper-specific properties
    wsp_font_path:     bpy.props.StringProperty(default="", subtype='FILE_PATH')
    wsp_srt_path:      bpy.props.StringProperty(default="")
    wsp_chunk_length:  bpy.props.IntProperty(default=10, min=5, max=30)


# ---------------------------------------------------------------------------
# EQ band constants and helpers — defined early so _load_preset can use them
# p0-p4 = gain (0.5 = 0dB, range -24..+24dB)
# p5-p9 = freq (log-normalised 0-1 over 20Hz..20kHz)
# p10-p14 = Q   (log-normalised 0-1 over 0.1..10.0)
# ---------------------------------------------------------------------------
EQ_BANDS = [
    # (name, colour, filter_type, default_freq_hz, default_Q)
    ("Low",    (0.25, 0.55, 1.0),  "low_shelf",  100.0,  0.7),
    ("L-Mid",  (0.25, 0.85, 0.45), "peak",       300.0,  1.0),
    ("Mid",    (0.85, 0.75, 0.15), "peak",      1000.0,  1.0),
    ("H-Mid",  (1.0,  0.45, 0.15), "peak",      4000.0,  1.0),
    ("High",   (0.9,  0.25, 0.7),  "high_shelf",10000.0, 0.7),
]
EQ_FREQ_MIN_LOG = math.log10(20.0)
EQ_FREQ_MAX_LOG = math.log10(20000.0)
EQ_Q_MIN_LOG    = math.log10(0.1)
EQ_Q_MAX_LOG    = math.log10(10.0)


def _eq_freq_from_norm(norm):
    """Convert 0-1 norm to Hz (log scale)."""
    return 10.0 ** (EQ_FREQ_MIN_LOG + norm * (EQ_FREQ_MAX_LOG - EQ_FREQ_MIN_LOG))


def _eq_freq_to_norm(hz):
    return max(0.0, min(1.0,
        (math.log10(max(20.0, hz)) - EQ_FREQ_MIN_LOG) /
        (EQ_FREQ_MAX_LOG - EQ_FREQ_MIN_LOG)))


def _eq_q_from_norm(norm):
    return 10.0 ** (EQ_Q_MIN_LOG + norm * (EQ_Q_MAX_LOG - EQ_Q_MIN_LOG))


def _eq_q_to_norm(q):
    return max(0.0, min(1.0,
        (math.log10(max(0.1, q)) - EQ_Q_MIN_LOG) /
        (EQ_Q_MAX_LOG - EQ_Q_MIN_LOG)))


def _eq_get_band(rack, band_idx):
    """Return (gain_db, freq_hz, q, freq_norm, q_norm) for a band."""
    gain_norm = getattr(rack, f'p{band_idx}',      0.5)
    freq_norm = getattr(rack, f'p{band_idx + 5}', -1.0)
    q_norm    = getattr(rack, f'p{band_idx + 10}',-1.0)
    gain_db   = (gain_norm - 0.5) * 48.0   # -24..+24 dB
    _, _, _, def_freq, def_q = EQ_BANDS[band_idx]
    if freq_norm < 0.0:
        freq_norm = _eq_freq_to_norm(def_freq)
    if q_norm < 0.0:
        q_norm = _eq_q_to_norm(def_q)
    return gain_db, _eq_freq_from_norm(freq_norm), _eq_q_from_norm(q_norm), freq_norm, q_norm


def _rp(rack, idx, default=0.0):
    """Safely get rack param by index, returning default if not yet registered."""
    try:
        return getattr(rack, f'p{idx}', default)
    except Exception:
        return default


def get_rack_channels(rack):
    """Return absolute VSE channel indices (0-based) assigned to this rack.

    ch0–ch8 are LOCAL within the rack's group of 9.
    Absolute = rack.group_idx * 9 + local.
    """
    offset = getattr(rack, 'group_idx', 0) * 9
    return [offset + i for i in range(9) if getattr(rack, f'ch{i}', False)]


def get_ai_rack_channels(rack):
    """Return absolute 0-based VSE channel indices assigned to an AI rack.
    ch0-ch8 are local to the rack's group; absolute = group_idx * 9 + local.
    """
    offset = getattr(rack, 'group_idx', 0) * 9
    return [offset + i for i in range(9) if getattr(rack, f'ch{i}', False)]


def get_rack_params(rack):
    """Return list of 8 param values for a rack."""
    return [getattr(rack, f'p{i}', 0.0) for i in range(8)]


def set_rack_param(rack, idx, value):
    setattr(rack, f'p{idx}', value)


def get_param_value(rack, param_idx):
    """Get a parameter value scaled to its actual range."""
    etype  = rack.effect_type
    params = EFFECT_PARAMS.get(etype, [])
    if param_idx >= len(params): return 0.0
    _, _, pmin, pmax, pdefault, _ = params[param_idx]
    raw = getattr(rack, f'p{param_idx}', 0.0)
    # raw is stored as 0-1 normalised, convert to actual range
    return pmin + raw * (pmax - pmin)


def normalise_param(rack, param_idx, actual_value):
    """Convert actual value to 0-1 normalised storage."""
    etype  = rack.effect_type
    params = EFFECT_PARAMS.get(etype, [])
    if param_idx >= len(params): return 0.0
    _, _, pmin, pmax, _, _ = params[param_idx]
    return max(0.0, min(1.0, (actual_value - pmin) / (pmax - pmin)))


def init_rack_defaults(rack):
    """Set parameter values to defaults for the effect type — loads preset 0."""
    rack.preset_idx = 0
    if rack.effect_type == "REVERB":
        rack.preset_idx = 2   # Medium Room
    elif rack.effect_type == "NOISE_GATE":
        rack.preset_idx = 0   # Dialogue Clean
    elif rack.effect_type == "MIXDOWN":
        # p0=mode(0=mix), p1=fmt(0=WAV), p2=sr(0.5=48k), p3=bd(0.5=24bit)
        # p4=range(0=full), p5=custom_start, p6=custom_end, p7=import_mode(0=mute+free)
        rack.p0 = 0.0   # mix mode
        rack.p1 = 0.0   # WAV
        rack.p2 = 0.5   # 48000 Hz
        rack.p3 = 0.5   # 24-bit
        rack.p4 = 0.0   # full timeline
        rack.p5 = 0.0   # custom start (frame 1)
        rack.p6 = 1.0   # custom end (frame 10000)
        rack.p7 = 0.0   # mute originals + place on free channel
        return
    _load_preset(rack, rack.preset_idx)


def _load_preset(rack, preset_idx):
    """Load a preset by index into rack params."""
    etype    = rack.effect_type
    presets  = PRESET_DATA.get(etype)
    if presets and preset_idx < len(presets):
        _, params = presets[preset_idx]
        for i, val in enumerate(params):
            attr = f'p{i}'
            if hasattr(rack, attr):
                setattr(rack, attr, float(max(0.0, min(1.0, val))))
        return
    # EQ: 7 bands — gain p0-p6, freq p7-p13, Q p14-p20
    if etype == "EQ":
        EQ7_DEFAULTS = [
            ("low_shelf",   80.0, 0.7),
            ("peak",       250.0, 1.0),
            ("peak",       700.0, 1.0),
            ("peak",      2000.0, 1.0),
            ("peak",      5000.0, 1.0),
            ("peak",     10000.0, 1.0),
            ("high_shelf",16000.0, 0.7),
        ]
        for bi, (_, df, dq) in enumerate(EQ7_DEFAULTS):
            setattr(rack, f'p{bi}',      0.5)
            setattr(rack, f'p{bi + 7}',  _eq_freq_to_norm(df))
            setattr(rack, f'p{bi + 14}', _eq_q_to_norm(dq))
        return
    # Fallback to EFFECT_PARAMS defaults for other types
    params = EFFECT_PARAMS.get(etype, [])
    for i, (_, _, pmin, pmax, pdefault, _) in enumerate(params):
        if i < 8:
            setattr(rack, f'p{i}', normalise_param(rack, i, pdefault))


# ---------------------------------------------------------------------------
# Drawing helpers (local versions — don't depend on Loader.py globals)
# ---------------------------------------------------------------------------
def _draw_rect(x, y, w, h, color):
    if w <= 0 or h <= 0: return
    shader = _get_shader()
    batch  = batch_for_shader(shader, "TRI_STRIP",
                              {"pos": [(x,y),(x+w,y),(x,y+h),(x+w,y+h)]})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line(x1, y1, x2, y2, color, width=1.0):
    shader = _get_shader()
    batch  = batch_for_shader(shader, "LINES",
                              {"pos": [(x1,y1),(x2,y2)]})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def _draw_circle(cx, cy, r, color, filled=True):
    shader = _get_shader()
    segs   = 20
    if filled:
        verts = [(cx, cy)]
        for i in range(segs+1):
            a = 2*math.pi*i/segs
            verts.append((cx+math.cos(a)*r, cy+math.sin(a)*r))
        batch = batch_for_shader(shader, "TRI_FAN", {"pos": verts})
    else:
        verts = []
        for i in range(segs+1):
            a = 2*math.pi*i/segs
            verts.append((cx+math.cos(a)*r, cy+math.sin(a)*r))
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_text(text, x, y, size, color=(1,1,1,1)):
    blf.size(0, max(1, int(size)))
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _text_width(text, size):
    blf.size(0, max(1, int(size)))
    return blf.dimensions(0, text)[0]


def _draw_knob(cx, cy, radius, norm_value, color, label, value_str, scale, label_above=False):
    """Draw a single rotary knob. label_above=True draws labels above the knob."""
    # Outer ring
    _draw_circle(cx, cy, radius, (0.13, 0.13, 0.13, 1.0))
    _draw_circle(cx, cy, radius, (0.33, 0.33, 0.33, 1.0), filled=False)
    # Inner cap
    inner_r = radius * 0.7
    _draw_circle(cx, cy, inner_r, (0.1, 0.1, 0.1, 1.0))
    # Arc background (270 degrees, from ~7 o'clock to ~5 o'clock)
    arc_start = -225.0
    arc_total = 270.0
    shader = _get_shader()
    segs = 24
    # Active arc
    active_angle = arc_start + norm_value * arc_total
    arc_pts = []
    a0 = math.radians(arc_start)
    a1 = math.radians(active_angle)
    steps = max(2, int(abs(a1-a0)/(2*math.pi)*segs))
    for i in range(steps+1):
        t = i/steps
        a = a0 + t*(a1-a0)
        arc_pts.append((cx+math.cos(a)*(radius*0.85),
                        cy+math.sin(a)*(radius*0.85)))
    if len(arc_pts) >= 2:
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": arc_pts})
        gpu.state.line_width_set(max(2.0, scale*2))
        shader.bind()
        c = color[:3] if len(color) >= 3 else color
        shader.uniform_float("color", (*c, 1.0))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
    # Indicator line
    angle = math.radians(arc_start + norm_value * arc_total)
    lx = cx + math.cos(angle) * inner_r * 0.75
    ly = cy + math.sin(angle) * inner_r * 0.75
    _draw_line(cx, cy, lx, ly, (1,1,1,0.9), max(1.5, scale*1.5))
    # Labels always below the knob — scale with zoom
    fs = max(1, int(7*scale))
    tw = _text_width(label, fs)
    _draw_text(label, cx - tw/2, cy - radius - 12*scale, fs,
               (0.5, 0.5, 0.5, 1.0))
    vw = _text_width(value_str, fs)
    _draw_text(value_str, cx - vw/2, cy - radius - 22*scale, fs,
               (0.8, 0.8, 0.8, 1.0))


def _draw_spectrum(rx, ry, rw, rh, rack_idx, scale):
    """Draw the spectrum analyser display."""
    # Background
    _draw_rect(rx, ry, rw, rh, (0.04, 0.04, 0.04, 1.0))
    # Border
    shader = _get_shader()
    verts = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind(); shader.uniform_float("color", (0.15,0.15,0.15,1.0))
    batch.draw(shader)

    # Grid lines horizontal (every 6dB)
    for i in range(1, 6):
        gy = ry + (i/6)*rh
        _draw_rect(rx, gy, rw, max(0.5, scale*0.5), (0.1,0.1,0.1,1.0))

    # Grid lines vertical
    for i in range(1, 8):
        gx = rx + (i/8)*rw
        _draw_rect(gx, ry, max(0.5, scale*0.5), rh, (0.1,0.1,0.1,1.0))

    # Spectrum bars — clear when rack is bypassed, no static fallback
    fft_flat = None
    _rack_on = True
    try:
        import bpy as _bpys0
        _sc0   = _bpys0.context.scene
        _rs0   = getattr(_sc0, "pb_racks", []) if _sc0 else []
        _rack_on = _rs0[rack_idx].enabled if rack_idx < len(_rs0) else True
    except Exception:
        pass

    if _rack_on:
        try:
            from Loader import _fft_timeline
            import bpy as _bpys
            scene_s   = _bpys.context.scene
            racks_s   = getattr(scene_s, "pb_racks", [])
            if rack_idx < len(racks_s):
                assigned_s = get_rack_channels(racks_s[rack_idx])
                if assigned_s:
                    ch_s = list(assigned_s)[0]
                    tl   = _fft_timeline.get(ch_s)
                    if tl is not None and len(tl['snapshots']) > 0:
                        cur_f    = scene_s.frame_current if scene_s else 0
                        snap_sec = tl['snap_frames'] / tl['sr']
                        elap_sec = (cur_f - tl['start_frame']) / tl['fps']
                        snap_f   = elap_sec / snap_sec
                        snap_idx = int(snap_f)
                        frac     = snap_f - snap_idx
                        snap_idx = max(0, min(len(tl['snapshots'])-1, snap_idx))
                        import numpy as _np
                        frame_d  = tl['snapshots'][snap_idx]
                        # Interpolate toward next snapshot for smooth bar motion
                        if frac > 0.0 and snap_idx + 1 < len(tl['snapshots']):
                            next_d  = tl['snapshots'][snap_idx + 1]
                            frame_d = frame_d * (1.0 - frac) + next_d * frac
                        fft_flat = _np.concatenate([frame_d[b] for b in range(4)])
        except Exception:
            fft_flat = None

    bar_w = (rw - 4*scale) / SPEC_BANDS
    if fft_flat is not None and _rack_on:
        import math as _mth
        n_bins = len(fft_flat)

        # Log-spaced centre bin for each bar
        centres = [_mth.pow(n_bins, b / SPEC_BANDS) - 1.0
                   for b in range(SPEC_BANDS)]

        # Sigma = half the gap to adjacent centres, wider for low-freq bars
        # that share only a few real FFT bins between many display bars
        sigmas = []
        for b in range(SPEC_BANDS):
            if b == 0:
                gap = max(0.5, centres[1] - centres[0])
            elif b == SPEC_BANDS - 1:
                gap = max(0.5, centres[-1] - centres[-2])
            else:
                gap = max(0.5, (centres[b + 1] - centres[b - 1]) * 0.5)
            sigmas.append(max(1.5, gap * 1.5))

        for b in range(SPEC_BANDS):
            t      = b / SPEC_BANDS
            centre = centres[b]
            sigma  = sigmas[b]
            # Gaussian-weighted average — each bar blends across its neighbourhood
            lo = max(0, int(centre - 3.0 * sigma))
            hi = min(n_bins - 1, int(centre + 3.0 * sigma) + 1)
            total_w = 0.0
            total_v = 0.0
            for i in range(lo, hi + 1):
                w = _mth.exp(-0.5 * ((i - centre) / sigma) ** 2)
                total_w += w
                total_v += w * float(fft_flat[i])
            h_frac = (total_v / total_w) if total_w > 0 else float(fft_flat[max(0, min(n_bins - 1, int(centre)))])
            h_frac = max(0.04, min(0.95, h_frac))
            bar_h  = h_frac * rh
            bx     = rx + 2*scale + b * bar_w
            if t < 0.3:   col = (0.0, 0.7,  0.45, 0.75)
            elif t < 0.6: col = (0.0, 0.85, 0.55, 0.85)
            elif t < 0.8: col = (0.9, 0.65, 0.0,  0.7)
            else:         col = (0.7, 0.35, 0.0,  0.5)
            _draw_rect(bx, ry, max(bar_w - scale, 1.0), bar_h, col)

    # GR curve — classic soft knee transfer function (input→output diagonal).
    # X = input level -60..0 dB, Y = output level -60..0 dB.
    # 1:1 slope below threshold, compressed slope above, soft knee join.
    import math as _math
    scene = bpy.context.scene
    racks = getattr(scene, "pb_racks", [])
    if rack_idx < len(racks):
        rack = racks[rack_idx]
        if rack.effect_type == "COMP_SINGLE":
            thr_db  = -40.0 + rack.p0 * 40.0
            ratio   =  1.0  + rack.p1 * 19.0
            knee_db =  0.5  + rack.p5 * 23.5

            pts = []
            for s in range(129):
                t      = s / 128.0
                in_db  = -60.0 + t * 60.0
                half_k = knee_db * 0.5
                if in_db <= thr_db - half_k:
                    out_db = in_db
                elif in_db <= thr_db + half_k and knee_db > 0:
                    x      = in_db - thr_db + half_k
                    out_db = in_db + (1.0/ratio - 1.0)*(x*x)/(2.0*knee_db)
                else:
                    out_db = thr_db + (in_db - thr_db) / ratio
                out_norm = (out_db + 60.0) / 60.0
                pts.append((rx + t*rw, ry + out_norm*rh))

            # Thick red diagonal line — clearly visible over bars
            for i in range(len(pts)-1):
                _draw_line(pts[i][0], pts[i][1],
                           pts[i+1][0], pts[i+1][1],
                           (0.9, 0.2, 0.2, 0.9), max(2.5, scale*2.5))

            # Threshold vertical marker
            thr_norm = (thr_db + 60.0) / 60.0
            thr_x    = rx + thr_norm * rw
            _draw_line(thr_x, ry, thr_x, ry+rh,
                       (0.6, 0.2, 0.2, 0.4), max(1.0, scale*1.0))

    # Frequency labels
    freq_labels = [("20", 0.0), ("200", 0.22), ("1k", 0.44),
                   ("4k", 0.63), ("10k", 0.8), ("20k", 0.95)]
    fs = max(1, int(7*scale))
    for label, t in freq_labels:
        lx = rx + t*rw
        _draw_text(label, lx, ry - 12*scale, fs, (0.3,0.3,0.3,1.0))

    # dB scale on left
    db_labels = [("0", 1.0), ("-12", 0.66), ("-24", 0.33), ("-36", 0.0)]
    for label, t in db_labels:
        ly = ry + t*rh - 3*scale
        tw = _text_width(label, fs)
        _draw_text(label, rx - tw - 4*scale, ly, fs, (0.3,0.3,0.3,1.0))

    # Legend
    lfs = max(1, int(7*scale))
    _draw_rect(rx + 4*scale, ry + rh - 12*scale, 12*scale, 2*scale,
               (0.0, 0.8, 0.5, 0.8))
    _draw_text("signal", rx + 18*scale, ry + rh - 14*scale, lfs,
               (0.4, 0.4, 0.4, 1.0))
    _draw_rect(rx + 60*scale, ry + rh - 12*scale, 12*scale, 2*scale,
               (0.9, 0.2, 0.2, 0.7))
    _draw_text("GR curve", rx + 74*scale, ry + rh - 14*scale, lfs,
               (0.4, 0.4, 0.4, 1.0))


def _draw_gr_meters(rx, ry, rh, rack_idx, assigned_channels, scale):
    """Draw one slim GR meter per assigned channel.
    GR meter: 0dB at TOP, reduction fills downward from top.
    Like a VU meter — full bar = heavy compression, empty = no compression.
    """
    if not assigned_channels: return
    bar_w   = GR_BAR_W * scale
    spacing = GR_BAR_SPACING * scale
    fs      = max(1, int(7*scale))

    import bpy as _bpy_gr
    scene_gr = _bpy_gr.context.scene
    racks_gr = getattr(scene_gr, "pb_racks", []) if scene_gr else []
    rack_gr  = racks_gr[rack_idx] if rack_idx < len(racks_gr) else None
    is_enabled = rack_gr.enabled if rack_gr else True

    for i, ch_idx in enumerate(assigned_channels[:6]):
        bx = rx + i * spacing
        tw = _text_width(str(ch_idx+1), fs)
        _draw_text(str(ch_idx+1), bx + bar_w/2 - tw/2,
                   ry - 12*scale, fs, (0.4,0.4,0.4,1.0))
        _draw_rect(bx, ry, bar_w, rh, (0.04, 0.04, 0.04, 1.0))

        # Premier Pro style: green signal bar + red GR cap.
        # Signal from FFT timeline. GR computed from signal + rack params
        # (COMP_SINGLE has no separate GR timeline — compute it here).
        sig_norm  = 0.0
        gr_db_val = 0.0
        if is_enabled:
            try:
                import math as _sgm
                from Loader import _gr_timeline, _fft_timeline
                cur_f = scene_gr.frame_current if scene_gr else 0

                # Signal level — average across all bands
                tl_f  = _fft_timeline.get(ch_idx)
                if tl_f is not None and len(tl_f['snapshots']) > 0:
                    sec_f  = tl_f['snap_frames'] / tl_f['sr']
                    elap_f = (cur_f - tl_f['start_frame']) / tl_f['fps']
                    idx_f  = max(0, min(len(tl_f['snapshots'])-1, int(elap_f/sec_f)))
                    sig_norm = float(tl_f['snapshots'][idx_f].mean())

                # GR: try timeline first (COMP_MULTI populates it)
                tl_g  = _gr_timeline.get(ch_idx)
                if tl_g is not None and len(tl_g['snapshots']) > 0:
                    sec_g  = tl_g['snap_frames'] / tl_g['sr']
                    elap_g = (cur_f - tl_g['start_frame']) / tl_g['fps']
                    idx_g  = max(0, min(len(tl_g['snapshots'])-1, int(elap_g/sec_g)))
                    gr_db_val = float(tl_g['snapshots'][idx_g].mean())
                elif rack_gr and rack_gr.effect_type == "COMP_SINGLE":
                    # Read GR directly from C++ engine — most accurate source.
                    # gr_levels[ch][0] is updated each process_buffer call.
                    # Value is positive dB of gain reduction (e.g. 3.5 = 3.5dB GR).
                    try:
                        from Loader import get_engine as _get_eng_sb
                        _eng_sb = _get_eng_sb()
                        if _eng_sb and assigned_gr2:
                            _ch_sb = list(assigned_gr2)[0]
                            _gv_sb = _eng_sb.get_state().get_gr_levels(_ch_sb)
                            gr_db_val = min(12.0, max(0.0, float(_gv_sb[0])))
                    except Exception:
                        gr_db_val = 0.0
            except Exception:
                pass

        sig_h   = max(0.0, min(1.0, sig_norm)) * rh
        if sig_h > 0.5:
            gr_h    = max(0.0, min(1.0, gr_db_val/12.0)) * sig_h
            green_h = sig_h - gr_h
            if green_h > 0.5:
                _draw_rect(bx, ry, bar_w, green_h, (0.05, 0.55, 0.25, 0.85))
                if green_h > 3*scale:
                    _draw_rect(bx, ry+green_h-2*scale, bar_w, 2*scale,
                               (0.1, 0.9, 0.4, 0.95))
            if gr_h > 0.5:
                _draw_rect(bx, ry+green_h, bar_w, gr_h, (0.85, 0.15, 0.15, 0.9))
                if gr_h > 2*scale:
                    _draw_rect(bx, ry+green_h+gr_h-2*scale, bar_w, 2*scale,
                               (1.0, 0.35, 0.35, 1.0))

        for db_t in [0.25, 0.5, 0.75]:
            _draw_rect(bx, ry + db_t*rh, bar_w, max(0.5, scale*0.5),
                       (0.2, 0.2, 0.2, 1.0))
        _draw_text("GR", bx + bar_w/2 - _text_width("GR",fs)/2,
                   ry + rh + 2*scale, fs, (0.3,0.3,0.3,1.0))


def _draw_gr_meter_band(bx, by, bw, bh, gr_db, scale, signal_norm=0.0):
    """Premier Pro style: green signal bar rising from bottom + red GR cap on top."""
    _draw_rect(bx, by, bw, bh, (0.04, 0.04, 0.04, 1.0))
    sig_h = max(0.0, min(1.0, signal_norm)) * bh
    if sig_h > 0.5:
        gr_h    = max(0.0, min(1.0, gr_db / 12.0)) * sig_h
        green_h = sig_h - gr_h
        if green_h > 0.5:
            _draw_rect(bx, by, bw, green_h, (0.05, 0.55, 0.25, 0.85))
            if green_h > 3*scale:
                _draw_rect(bx, by + green_h - 2*scale, bw, 2*scale,
                           (0.1, 0.9, 0.4, 0.95))
        if gr_h > 0.5:
            _draw_rect(bx, by + green_h, bw, gr_h, (0.85, 0.15, 0.15, 0.9))
            if gr_h > 2*scale:
                _draw_rect(bx, by + green_h + gr_h - 2*scale, bw, 2*scale,
                           (1.0, 0.35, 0.35, 1.0))
    for t in [0.25, 0.5, 0.75]:
        _draw_rect(bx, by + t*bh, bw, max(0.5, scale*0.5), (0.2, 0.2, 0.2, 1.0))


def _draw_channel_buttons(rx, ry, rack, scale):
    """Draw channel assignment buttons — always exactly 9, local to the rack's group.

    ch0–ch8 are LOCAL indices. Label shows absolute VSE channel number.
    """
    btn_s     = CH_BTN_SIZE * scale
    gap       = 4 * scale
    fs        = max(1, int(10*scale))
    group_idx = getattr(rack, 'group_idx', 0)
    offset    = group_idx * 9

    shader = _get_shader()
    for local_idx in range(9):
        row = local_idx // 3
        col = local_idx % 3
        bx  = rx + col * (btn_s + gap)
        by  = ry - row * (btn_s + gap) - btn_s

        attr     = f'ch{local_idx}'
        assigned = getattr(rack, attr, False)

        if assigned:
            bg = (0.0, 0.18, 0.10, 1.0)
            bc = (0.0, 0.75, 0.45, 1.0)
            tc = (0.0, 0.85, 0.55, 1.0)
        else:
            bg = (0.07, 0.07, 0.07, 1.0)
            bc = (0.2,  0.2,  0.2,  1.0)
            tc = (0.2,  0.2,  0.2,  1.0)

        _draw_rect(bx, by, btn_s, btn_s, bg)
        verts = [(bx,by),(bx+btn_s,by),(bx+btn_s,by+btn_s),(bx,by+btn_s),(bx,by)]
        batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
        shader.bind(); shader.uniform_float("color", bc); batch.draw(shader)

        label = str(offset + local_idx + 1)
        tw    = _text_width(label, fs)
        _draw_text(label, bx + btn_s/2 - tw/2,
                   by + btn_s/2 - fs/2, fs, tc)


# Band colours for multiband display (matching C6-style)
BAND_COLORS = [
    (0.2, 0.5, 1.0, 0.85),   # Low      — blue
    (0.2, 0.9, 0.4, 0.85),   # Low-Mid  — green
    (1.0, 0.7, 0.1, 0.85),   # High-Mid — amber
    (1.0, 0.3, 0.3, 0.85),   # High     — red
]
BAND_NAMES  = ["Low", "L-Mid", "H-Mid", "High"]
BAND_FREQS  = ["<120Hz", "120-800Hz", "800Hz-5kHz", ">5kHz"]


def _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Draw multiband compressor body.
    Layout matches agreed sketch:
    - Top 48%: spectrum display with 4 equal-width band curves
    - Bottom 52%: 4 equal columns, each with gain fader left + 2x2 knobs right
    - No coloured backgrounds, only elements carry band colour
    - Equal margins on left and right of band area
    """
    rail_h       = RACK_RAIL_H * scale
    body_h       = rh - rail_h
    spec_zone_h  = body_h * 0.48
    fader_zone_h = body_h * 0.52

    # Channel buttons take 108px on right — equal margin on left
    ch_btn_w   = 108 * scale
    side_margin = ch_btn_w / 2   # 54px each side

    # Total content width available
    content_w   = rw - ch_btn_w - side_margin
    band_w      = content_w / 4
    content_x   = rx + side_margin / 2

    shader = _get_shader()

    # ----------------------------------------------------------------
    # SPECTRUM DISPLAY — FabFilter style full-width GR curve
    # Background: grey FFT bars showing audio content
    # Foreground: smooth coloured GR curve dipping at compressed bands
    # ----------------------------------------------------------------
    spec_x = content_x
    spec_y = ry + fader_zone_h + 4*scale
    spec_w = content_w
    spec_h = spec_zone_h - 8*scale

    # Background
    _draw_rect(spec_x, spec_y, spec_w, spec_h, (0.04, 0.04, 0.04, 1.0))
    bv = [(spec_x,spec_y),(spec_x+spec_w,spec_y),
          (spec_x+spec_w,spec_y+spec_h),(spec_x,spec_y+spec_h),(spec_x,spec_y)]
    bb = batch_for_shader(shader,"LINE_STRIP",{"pos":bv})
    shader.bind(); shader.uniform_float("color",(0.15,0.15,0.15,1.0)); bb.draw(shader)

    # Horizontal grid lines (dB scale)
    for gi in range(1, 5):
        gy = spec_y + gi/5 * spec_h
        _draw_rect(spec_x, gy, spec_w, max(0.5,scale*0.5), (0.09,0.09,0.09,1.0))

    # dB scale labels on left
    fs_db = max(1, int(7*scale))
    for label, frac in [("+6",0.1),("0",0.3),("-6",0.5),("-12",0.7),("-24",0.9)]:
        ly = spec_y + frac * spec_h
        tw = _text_width(label, fs_db)
        _draw_text(label, spec_x - tw - 3*scale, ly - fs_db/2,
                   fs_db, (0.3,0.3,0.3,1.0))

    # Get FFT data — clear when rack is bypassed
    fft_data = None
    gr_data  = [0.0, 0.0, 0.0, 0.0]
    if not rack.enabled:
        pass
    else:
     try:
        from Loader import _fft_timeline
        import bpy as _bpy2
        assigned = get_rack_channels(rack)
        if assigned:
            ch = list(assigned)[0]
            tl = _fft_timeline.get(ch)
            if tl is not None and len(tl['snapshots']) > 0:
                scene2      = _bpy2.context.scene
                cur_frame   = scene2.frame_current if scene2 else 0
                start_frame = tl['start_frame']
                fps         = tl['fps']
                snap_sec    = tl['snap_frames'] / tl['sr']
                elapsed_sec = (cur_frame - start_frame) / fps
                snap_f      = elapsed_sec / snap_sec
                snap_idx    = int(snap_f)
                frac        = snap_f - snap_idx
                snaps       = tl['snapshots']
                snap_idx    = max(0, min(len(snaps)-1, snap_idx))
                # snaps is (n_snaps, 4, 8) numpy array — interpolate for smooth motion
                import numpy as _np2
                frame_data  = snaps[snap_idx]
                if frac > 0.0 and snap_idx + 1 < len(snaps):
                    next_data  = snaps[snap_idx + 1]
                    frame_data = frame_data * (1.0 - frac) + next_data * frac
                fft_data    = [frame_data[b].tolist() for b in range(4)]
     except Exception as _fe:
        import traceback as _tb
        _tb.print_exc()
        fft_data = None

    # Also get GR levels for the GR bar
    try:
        from Loader import get_engine
        engine = get_engine()
        assigned2 = get_rack_channels(rack)
        if engine and assigned2:
            ch2 = list(assigned2)[0]
            if 0 <= ch2 < 32:
                gr_data = engine.get_state().get_gr_levels(ch2)
    except Exception:
        pass

    # --- Background: grey FFT bars (full width, all bands combined) ---
    FFT_BINS = 32
    total_bars = FFT_BINS * 4
    bar_w_full = spec_w / total_bars
    for band in range(4):
        col = BAND_COLORS[band]
        if fft_data and fft_data[band]:
            base_bins = list(fft_data[band])
        else:
            base_bins = [0.04] * FFT_BINS

        # Use real FFT timeline data — no fake animation
        for bi, base_val in enumerate(base_bins):
            val     = max(0.0, min(1.0, base_val))
            bar_idx = band * FFT_BINS + bi
            bar_h   = val * spec_h * 0.85
            bx      = spec_x + bar_idx * bar_w_full
            r,g,b_c,a = col
            _draw_rect(bx, spec_y, max(bar_w_full-0.5, 0.5), bar_h,
                       (r*0.2+0.04, g*0.2+0.04, b_c*0.2+0.04, 0.9))

    # --- Band divider lines ---
    for b in range(1, 4):
        dx = spec_x + b * band_w
        _draw_rect(dx, spec_y, max(0.5,scale*0.5), spec_h, (0.2,0.2,0.2,1.0))
        # Crossover frequency labels
        cross_labels = ["120hz", "800hz", "5khz"]
        fs_cr = max(1, int(7*scale))
        tw_cr = _text_width(cross_labels[b-1], fs_cr)
        _draw_text(cross_labels[b-1], dx - tw_cr/2,
                   spec_y + spec_h + 2*scale,
                   fs_cr, (0.35,0.35,0.35,1.0))

    # --- Settings-driven frequency response curve ---
    # Shows the effect of current knob settings on the frequency spectrum.
    # Each band's gain setting + compression depth shapes the curve.
    # Updates instantly as knobs move — no animation, pure representation.
    #
    # Curve logic per band:
    #   - At 0dB gain with no threshold hit: flat at 0dB
    #   - Gain knob shifts band up/down
    #   - Threshold + ratio creates a soft-knee dip based on a nominal
    #     input level (we use -18dB RMS as the reference signal level)
    #     This shows how much the compressor would affect a typical signal
    #
    # Y axis: -12dB (bottom) to +12dB (top), 0dB = centre
    # X axis: full spectrum left to right across all 4 bands

    zero_db_y  = spec_y + spec_h * 0.5      # 0dB at vertical centre
    db_per_px  = 12.0 / (spec_h * 0.5)      # 12dB maps to half height
    scale_px   = (spec_h * 0.5) / 12.0      # pixels per dB

    # Draw 0dB reference line
    _draw_rect(spec_x, zero_db_y, spec_w, max(0.5, scale*0.5),
               (0.35, 0.35, 0.35, 0.6))

    # dB grid lines and labels
    fs_db = max(1, int(7*scale))
    for db_val, label in [(12,"+12"),(6,"+6"),(0,"0"),(-6,"-6"),(-12,"-12")]:
        gy = zero_db_y - db_val * scale_px
        if spec_y <= gy <= spec_y + spec_h:
            _draw_rect(spec_x, gy, spec_w, max(0.5,scale*0.3),
                       (0.12,0.12,0.12,1.0))
            tw = _text_width(label, fs_db)
            _draw_text(label, spec_x - tw - 3*scale, gy - fs_db*0.5,
                       fs_db, (0.3,0.3,0.3,1.0))

    # Compute per-band gain offset from knob settings
    # Reference input: -18dB RMS — represents typical programme level
    REF_INPUT_DB = -18.0

    def band_output_db(b):
        """Net dB change this band applies to the reference signal."""
        thr_db  = -40.0 + _rp(rack, b)    * 40.0   # threshold
        ratio   =  1.0  + _rp(rack, b+4)  * 19.0   # ratio
        knee_db =  0.5  + _rp(rack, b+20) * 23.5   # knee
        gain_db = (_rp(rack, b+16, 0.5) - 0.5) * 24.0  # band gain

        # Soft knee gain reduction at reference input
        half_k = knee_db * 0.5
        in_db  = REF_INPUT_DB
        if in_db <= thr_db - half_k:
            gr_db = 0.0
        elif in_db <= thr_db + half_k and knee_db > 0:
            x     = in_db - thr_db + half_k
            gr_db = (1.0/ratio - 1.0) * (x*x) / (2.0*knee_db)
        else:
            gr_db = (in_db - thr_db) * (1.0/ratio - 1.0)

        return gain_db + gr_db  # total net effect on signal

    # Calculate net dB per band
    band_db = [band_output_db(b) for b in range(4)]

    # Build smooth curve — cubic smooth-step between band centres
    # with flat regions within each band and smooth transitions at crossovers
    N_PTS = 200
    curve_pts = []
    for i in range(N_PTS + 1):
        fx     = i / N_PTS
        band_f = fx * 4.0
        band_i = min(3, int(band_f))
        band_t = band_f - band_i

        # Smooth blend at band boundaries
        db_this = band_db[band_i]
        db_next = band_db[min(3, band_i + 1)]
        # Sigmoid transition — flat in band centre, smooth at edges
        s       = band_t * band_t * (3.0 - 2.0 * band_t)
        db_here = db_this + (db_next - db_this) * s

        px = spec_x + fx * spec_w
        py = zero_db_y - db_here * scale_px
        py = max(spec_y + 2*scale, min(spec_y + spec_h - 2*scale, py))
        curve_pts.append((px, py))

    # Draw filled area between curve and 0dB line
    for i in range(len(curve_pts) - 1):
        px1, py1 = curve_pts[i]
        px2, py2 = curve_pts[i+1]
        band_here = min(3, int((px1 - spec_x) / band_w))
        col       = BAND_COLORS[band_here]
        r,g,b_c,a = col
        y_top  = min(py1, zero_db_y)
        y_bot  = max(py1, zero_db_y)
        fill_h = y_bot - y_top
        if fill_h > 0.5:
            _draw_rect(px1, y_top, max(px2-px1, 0.5), fill_h,
                       (r*0.35, g*0.35, b_c*0.35, 0.4))

    # Draw the curve line
    for i in range(len(curve_pts) - 1):
        px1, py1 = curve_pts[i]
        px2, py2 = curve_pts[i+1]
        band_here = min(3, int((px1 - spec_x) / band_w))
        col       = BAND_COLORS[band_here]
        r,g,b_c,a = col
        _draw_line(px1, py1, px2, py2,
                   (min(1,r*1.4), min(1,g*1.4), min(1,b_c*1.4), 1.0),
                   max(2.0, scale*2.0))

    # Band centre dots (like IK Quad Comp)
    for band in range(4):
        bx_c = spec_x + (band + 0.5) * band_w
        db_b = band_db[band]
        py_c = zero_db_y - db_b * scale_px
        py_c = max(spec_y + 4*scale, min(spec_y + spec_h - 4*scale, py_c))
        col  = BAND_COLORS[band]
        r,g,b_c,a = col
        _draw_circle(bx_c, py_c, 5*scale,
                     (min(1,r*1.5), min(1,g*1.5), min(1,b_c*1.5), 1.0))
        _draw_circle(bx_c, py_c, 5*scale, (0.1,0.1,0.1,0.6), filled=False)
        # Value label
        fs_lbl = max(1, int(7*scale))
        lbl    = f"{db_b:+.1f}dB"
        tw_lbl = _text_width(lbl, fs_lbl)
        _draw_text(lbl, bx_c - tw_lbl/2, py_c + 8*scale,
                   fs_lbl, (r, g, b_c, 0.9))

    # Band name labels
    for band in range(4):
        col   = BAND_COLORS[band]
        bx_c  = spec_x + (band + 0.5) * band_w
        fs_bn = max(1, int(8*scale))
        tw_bn = _text_width(BAND_NAMES[band], fs_bn)
        _draw_text(BAND_NAMES[band], bx_c - tw_bn/2,
                   spec_y + spec_h - 14*scale,
                   fs_bn, (col[0]*0.8, col[1]*0.8, col[2]*0.8, 0.8))

    # ----------------------------------------------------------------
    # FADER + KNOB ZONE — bottom portion, 4 equal columns
    # Layout per column:
    #   Left ~30px: vertical gain fader + "GAIN" label
    #   Thin divider
    #   Right section: 2x2 knob grid (Thr, Ratio top row; Atk, Rel bottom row)
    #   Bottom: band name + freq range
    # ----------------------------------------------------------------
    fader_area_y = ry + 4*scale
    fader_area_h = fader_zone_h - 8*scale

    label_h  = 26*scale   # band name + freq at bottom
    ctrl_y   = fader_area_y + label_h
    ctrl_h   = fader_area_h - label_h

    # Knob layout: 2 rows, 2 cols within right section
    # Calculated so labels (22px below each knob) never overlap adjacent row:
    #   Bottom row centre: ctrl_y + 4 + 22 + r
    #   Top row centre:    bottom_row + r + 6 + 22 + r
    fader_strip_w = 38*scale
    knob_area_w   = band_w - fader_strip_w - 8*scale
    knob_r        = min(knob_area_w * 0.15, 16*scale)
    knob_r        = max(knob_r, 10*scale)
    knob_col_gap  = knob_area_w / 2
    knob_label_h  = 22*scale   # space needed below each knob for labels
    knob_gap      = 6*scale    # gap between top-row label and bottom-row knob

    for band in range(4):
      try:
        bx  = content_x + band * band_w
        col = BAND_COLORS[band]

        # Band name + freq label at bottom
        fs_bn = max(1, int(9*scale))
        tw_bn = _text_width(BAND_NAMES[band], fs_bn)
        _draw_text(BAND_NAMES[band],
                   bx + band_w/2 - tw_bn/2,
                   fader_area_y + 14*scale, fs_bn, col)
        fs_fr = max(1, int(7*scale))
        tw_fr = _text_width(BAND_FREQS[band], fs_fr)
        _draw_text(BAND_FREQS[band],
                   bx + band_w/2 - tw_fr/2,
                   fader_area_y + 3*scale, fs_fr,
                   (col[0]*0.55, col[1]*0.55, col[2]*0.55, 1.0))

        # --- GAIN FADER (left strip) ---
        fdr_x  = bx + 6*scale
        fdr_w  = 10*scale
        fdr_cx = fdr_x + fdr_w/2 - 2*scale   # rail centre
        gain_norm = _rp(rack, band+16, 0.5)
        # dB label above fader
        gain_db  = (gain_norm - 0.5) * 24.0  # -12 to +12 dB
        fs_g     = max(1, int(7*scale))
        gain_str = f"{gain_db:+.0f}"
        tw_g     = _text_width(gain_str, fs_g)
        _draw_text(gain_str, bx+6*scale + fdr_w/2 - tw_g/2,
                   ctrl_y + ctrl_h - 12*scale, fs_g, (0.75,0.75,0.75,1.0))
        fs_gl    = max(1, int(7*scale))
        _draw_text("GAIN", bx+6*scale, ctrl_y + ctrl_h - 22*scale,
                   fs_gl, (col[0]*0.7,col[1]*0.7,col[2]*0.7,1.0))

        fdr_h  = ctrl_h - 26*scale
        fdr_y  = ctrl_y + 2*scale
        # Rail
        _draw_rect(fdr_cx, fdr_y, 4*scale, fdr_h, (0.06,0.06,0.06,1.0))
        # 0dB mark
        unity_y = fdr_y + 0.5 * fdr_h
        _draw_rect(fdr_cx - 2*scale, unity_y, 8*scale,
                   max(0.5, scale*0.5), (0.3,0.3,0.3,1.0))
        # Handle
        handle_h = max(8*scale, fdr_h * 0.07)
        handle_y = fdr_y + gain_norm * (fdr_h - handle_h)
        _draw_rect(fdr_x, handle_y, fdr_w, handle_h,
                   (col[0]*0.85, col[1]*0.85, col[2]*0.85, 1.0))
        _draw_rect(fdr_x, handle_y + handle_h/2 - max(0.5,scale*0.5),
                   fdr_w, max(1.0, scale),
                   (min(1.0,col[0]*1.4), min(1.0,col[1]*1.4), min(1.0,col[2]*1.4), 1.0))

        # GR meter — slim bar to the right of the gain fader
        # Shows live gain reduction for this band, 0dB at top filling downward
        gr_meter_w = 6*scale
        gr_meter_x = fdr_x + fdr_w + 3*scale
        gr_meter_y = fdr_y
        gr_meter_h = fdr_h

        # Read GR + signal from timelines — zero when rack is bypassed
        gr_db_band = 0.0
        sig_norm   = 0.0
        if rack.enabled:
            try:
                from Loader import _gr_timeline, _fft_timeline
                import bpy as _grbpy2
                scene_gr2    = _grbpy2.context.scene
                assigned_gr2 = get_rack_channels(rack)
                if assigned_gr2 and scene_gr2:
                    ch_gr2 = list(assigned_gr2)[0]
                    cur_f2 = scene_gr2.frame_current
                    tl_gr2 = _gr_timeline.get(ch_gr2)
                    if tl_gr2 is not None and len(tl_gr2['snapshots']) > 0:
                        snap_sec2 = tl_gr2['snap_frames'] / tl_gr2['sr']
                        elap_sec2 = (cur_f2 - tl_gr2['start_frame']) / tl_gr2['fps']
                        snap_idx2 = max(0, min(len(tl_gr2['snapshots'])-1,
                                              int(elap_sec2 / snap_sec2)))
                        gr_db_band = float(tl_gr2['snapshots'][snap_idx2][band])
                    tl_fft2 = _fft_timeline.get(ch_gr2)
                    if tl_fft2 is not None and len(tl_fft2['snapshots']) > 0:
                        snap_sec3 = tl_fft2['snap_frames'] / tl_fft2['sr']
                        elap_sec3 = (cur_f2 - tl_fft2['start_frame']) / tl_fft2['fps']
                        snap_idx3 = max(0, min(len(tl_fft2['snapshots'])-1,
                                              int(elap_sec3 / snap_sec3)))
                        sig_norm  = float(tl_fft2['snapshots'][snap_idx3][band].mean())
            except Exception:
                pass

        _draw_gr_meter_band(gr_meter_x, gr_meter_y, gr_meter_w,
                            gr_meter_h, gr_db_band, scale,
                            signal_norm=sig_norm)

        # Thin divider after fader strip
        div_x = bx + fader_strip_w
        _draw_rect(div_x, ctrl_y, max(0.5,scale*0.5), ctrl_h, (0.18,0.18,0.18,1.0))

        # --- 3x2 KNOB GRID (Thr/Ratio/Knee top, Atk/Rel/Gain bottom) ---
        knob_base_x = div_x + 4*scale
        knob_col_gap3 = knob_area_w / 3
        kx0 = knob_base_x + knob_col_gap3 * 0.5
        kx1 = knob_base_x + knob_col_gap3 * 1.5
        kx2 = knob_base_x + knob_col_gap3 * 2.5
        ky1 = ctrl_y + 4*scale + knob_label_h + knob_r
        ky0 = ky1 + knob_r + knob_gap + knob_label_h + knob_r

        # Threshold (p0-p3)
        thr_n   = _rp(rack, band)
        thr_db  = -40.0 + thr_n*40.0
        _draw_knob(kx0, ky0, knob_r, thr_n, col,
                   "Thr", f"{thr_db:.0f}dB", scale)

        # Ratio (p4-p7)
        rat_n  = _rp(rack, band+4)
        ratio  = 1.0 + rat_n*19.0
        _draw_knob(kx1, ky0, knob_r, rat_n, col,
                   "Ratio", f"{ratio:.1f}:1", scale)

        # Knee (p20-p23)
        kne_n  = _rp(rack, band+20, 0.14)
        kne_db = 0.5 + kne_n*23.5
        _draw_knob(kx2, ky0, knob_r, kne_n, col,
                   "Knee", f"{kne_db:.1f}dB", scale)

        # Attack (p8-p11)
        atk_n  = _rp(rack, band+8)
        atk_ms = 0.1 + atk_n*99.9
        _draw_knob(kx0, ky1, knob_r, atk_n, col,
                   "Atk", f"{atk_ms:.0f}ms", scale)

        # Release (p12-p15)
        rel_n  = _rp(rack, band+12)
        rel_ms = 10.0 + rel_n*990.0
        _draw_knob(kx1, ky1, knob_r, rel_n, col,
                   "Rel", f"{rel_ms:.0f}ms", scale)

        # Gain (p16-p19)
        gain_n  = _rp(rack, band+16, 0.5)
        gain_db = (gain_n - 0.5) * 24.0
        _draw_knob(kx2, ky1, knob_r, gain_n, col,
                   "Gain", f"{gain_db:+.0f}dB", scale)

        # Column divider (not after last band)
        if band < 3:
            _draw_rect(bx + band_w, fader_area_y,
                       max(0.5,scale*0.5), fader_area_h,
                       (0.18,0.18,0.18,1.0))
      except Exception as e:
        print(f"[MB] band {band} draw error: {e}")



# ---------------------------------------------------------------------------
# EQ rack body
# ---------------------------------------------------------------------------


def _eq_biquad_response(freq_hz, gain_db, band_filter_type, q, f_test):
    """
    Compute magnitude response in dB at f_test Hz for one EQ band.
    Uses Audio EQ Cookbook biquad formulae (same as Loader.py).
    sample_rate assumed 48000 for display purposes.
    """
    sr = 48000.0
    w0 = 2.0 * math.pi * freq_hz / sr
    wt = 2.0 * math.pi * f_test  / sr
    cw0, sw0 = math.cos(w0), math.sin(w0)
    cwt       = math.cos(wt)
    swt       = math.sin(wt)
    alpha     = sw0 / (2.0 * q)
    A         = 10.0 ** (gain_db / 40.0)

    if band_filter_type == "low_shelf":
        sq = 2.0 * math.sqrt(A) * alpha
        b0 = A*((A+1) - (A-1)*cw0 + sq)
        b1 = 2*A*((A-1) - (A+1)*cw0)
        b2 = A*((A+1) - (A-1)*cw0 - sq)
        a0 = (A+1) + (A-1)*cw0 + sq
        a1 = -2*((A-1) + (A+1)*cw0)
        a2 = (A+1) + (A-1)*cw0 - sq
    elif band_filter_type == "high_shelf":
        sq = 2.0 * math.sqrt(A) * alpha
        b0 = A*((A+1) + (A-1)*cw0 + sq)
        b1 = -2*A*((A-1) + (A+1)*cw0)
        b2 = A*((A+1) + (A-1)*cw0 - sq)
        a0 = (A+1) - (A-1)*cw0 + sq
        a1 = 2*((A-1) - (A+1)*cw0)
        a2 = (A+1) - (A-1)*cw0 - sq
    else:  # peak
        alpha_a = sw0 / (2.0 * q)
        b0 = 1 + alpha_a * A
        b1 = -2 * cw0
        b2 = 1 - alpha_a * A
        a0 = 1 + alpha_a / A
        a1 = -2 * cw0
        a2 = 1 - alpha_a / A

    # Evaluate H(e^jwt) via the bilinear s→z substitution
    # |H(z)| at z=e^jwt:  num = b0 + b1*e^-jwt + b2*e^-2jwt
    #                      den = a0 + a1*e^-jwt + a2*e^-2jwt
    try:
        nr = b0/a0 + (b1/a0)*cwt + (b2/a0)*math.cos(2*wt)
        ni = -(b1/a0)*swt - (b2/a0)*math.sin(2*wt)
        dr = 1.0   + (a1/a0)*cwt + (a2/a0)*math.cos(2*wt)
        di = -(a1/a0)*swt - (a2/a0)*math.sin(2*wt)
        mag_sq = (nr*nr + ni*ni) / max(1e-30, dr*dr + di*di)
        return 10.0 * math.log10(max(1e-10, mag_sq))
    except Exception:
        return 0.0


def _draw_eq_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """
    Parametric EQ rack body — Adobe Premiere Pro style.

    Layout:
      Top 62% : frequency display
                - Audio waveform silhouette (mirrored, from RMS envelope)
                - dB grid +/-18dB, octave frequency grid
                - Per-band dim coloured response curves
                - Combined cyan response curve + filled teal area
                - 7 draggable band handle dots
      Bottom 38%: 7-column knob strip  L | 1 | 2 | 3 | 4 | 5 | H
                  Each column: band label, Gain knob, Freq knob, Q knob

    Parameter storage (7-band layout):
      p0-p6  : gain (0.5 = 0dB, range -24..+24dB)
      p7-p13 : freq (log-normalised 0-1)
      p14-p20: Q    (log-normalised 0-1)
    """
    import math as _m

    EQ7_BANDS = [
        ("L",  (0.30, 0.60, 1.00), "low_shelf",   80.0,  0.7),
        ("1",  (0.25, 0.90, 0.55), "peak",        250.0,  1.0),
        ("2",  (0.50, 0.90, 0.20), "peak",        700.0,  1.0),
        ("3",  (0.95, 0.85, 0.10), "peak",       2000.0,  1.0),
        ("4",  (1.00, 0.55, 0.10), "peak",       5000.0,  1.0),
        ("5",  (0.95, 0.30, 0.55), "peak",      10000.0,  1.0),
        ("H",  (0.80, 0.30, 1.00), "high_shelf", 16000.0, 0.7),
    ]
    N_BANDS = 7

    def _get_b(bi):
        gn  = getattr(rack, f"p{bi}",      0.5)
        fn  = getattr(rack, f"p{bi + 7}", -1.0)
        qn  = getattr(rack, f"p{bi + 14}",-1.0)
        gdb = (gn - 0.5) * 48.0
        _, _, bft, df, dq = EQ7_BANDS[bi]
        if fn < 0.0: fn = _eq_freq_to_norm(df)
        if qn < 0.0: qn = _eq_q_to_norm(dq)
        return gdb, _eq_freq_from_norm(fn), _eq_q_from_norm(qn), gn, fn, qn

    # -----------------------------------------------------------------------
    # Geometry
    # -----------------------------------------------------------------------
    rail_h   = RACK_RAIL_H * scale
    body_h   = rh - rail_h
    ch_btn_w = 108 * scale
    margin_l = 42 * scale
    margin_r = ch_btn_w + 8 * scale

    disp_x = rx + margin_l
    disp_w = rw - margin_l - margin_r
    # 54% display / 44% knobs — more knob room for larger controls + visible labels
    disp_h = body_h * 0.54
    disp_y = ry + body_h - disp_h - 2 * scale   # top of body (display at top)

    knob_h = body_h * 0.44 - 6 * scale
    knob_y = ry + 2 * scale                      # knob strip at bottom of body

    db_range  = 18.0
    zero_db_y = disp_y + disp_h * 0.5
    px_per_db = (disp_h * 0.5) / db_range

    shader = _get_shader()

    # -----------------------------------------------------------------------
    # DISPLAY BACKGROUND + GRID
    # -----------------------------------------------------------------------
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.035, 0.035, 0.040, 1.0))

    for db_val in (18, 12, 6, 0, -6, -12, -18):
        gy = zero_db_y + db_val * px_per_db
        if not (disp_y <= gy <= disp_y + disp_h):
            continue
        bright = 0.22 if db_val == 0 else 0.09
        lw     = max(1.0, scale) if db_val == 0 else max(0.5, scale * 0.5)
        _draw_rect(disp_x, gy, disp_w, lw, (bright, bright, bright, 0.9))
        lbl   = "0dB" if db_val == 0 else f"{db_val:+d}"
        fs_db = max(1, int(7 * scale))
        tw_db = _text_width(lbl, fs_db)
        _draw_text(lbl, disp_x - tw_db - 4 * scale,
                   gy - fs_db * 0.5, fs_db, (0.30, 0.30, 0.30, 1.0))

    freq_marks = [
        (20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"),
        (1000, "1k"), (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k"),
    ]
    for fhz_m, lbl_m in freq_marks:
        t_m  = (_m.log10(fhz_m) - EQ_FREQ_MIN_LOG) / (EQ_FREQ_MAX_LOG - EQ_FREQ_MIN_LOG)
        gx_m = disp_x + t_m * disp_w
        if not (disp_x <= gx_m <= disp_x + disp_w):
            continue
        _draw_rect(gx_m, disp_y, max(0.5, scale * 0.5), disp_h,
                   (0.11, 0.11, 0.11, 1.0))
        fs_f = max(1, int(7 * scale))
        tw_f = _text_width(lbl_m, fs_f)
        _draw_text(lbl_m, gx_m - tw_f * 0.5,
                   disp_y - 11 * scale, fs_f, (0.28, 0.28, 0.28, 1.0))

    # -----------------------------------------------------------------------
    # SPECTRUM ANALYSER — FabFilter Pro-Q style
    # Reads _fft_timeline built by Loader.py during batch processing.
    # Shape: (n_snaps, 4, 32) — 4 bands × 32 log-spaced bins = 128 points.
    # Bands: 0=20-120Hz  1=120-800Hz  2=800-5000Hz  3=5000-20000Hz
    #
    # Two filled silhouette layers, bottom-to-top (NOT bars):
    #   Layer 1 — pre-EQ:  dark grey filled polygon from floor up
    #   Layer 2 — post-EQ: same data with current EQ gain curve applied
    # Jagged/spiky look comes naturally from 128 narrow adjacent bins.
    # Silent / dark until audio has been processed at least once.
    # -----------------------------------------------------------------------
    try:
        from Loader import _fft_timeline
        import math as _ms

        assigned_sp = get_rack_channels(rack)
        if assigned_sp:
            ch_sp = list(assigned_sp)[0]
            tl_sp = _fft_timeline.get(ch_sp)
            if tl_sp is not None and len(tl_sp['snapshots']) > 0:
                scene_sp    = bpy.context.scene
                cur_frame   = scene_sp.frame_current if scene_sp else 0
                start_frame = tl_sp['start_frame']
                fps_sp      = tl_sp['fps']
                snap_sec    = tl_sp['snap_frames'] / tl_sp['sr']
                elapsed_sec = max(0.0, (cur_frame - start_frame) / fps_sp)
                snap_f      = elapsed_sec / snap_sec
                snap_idx    = max(0, min(len(tl_sp['snapshots']) - 1, int(snap_f)))
                frac        = snap_f - int(snap_f)

                import numpy as _nps
                frame_data = tl_sp['snapshots'][snap_idx].astype(float)
                if frac > 0.0 and snap_idx + 1 < len(tl_sp['snapshots']):
                    frame_data = (frame_data * (1.0 - frac) +
                                  tl_sp['snapshots'][snap_idx + 1].astype(float) * frac)

                # Build (x_norm 0-1, amplitude 0-1) pairs for all 128 bins
                CROSSOVERS = [20, 120, 800, 5000, 20000]
                BINS_PER   = 32
                LOG_MIN    = _ms.log10(20.0)
                LOG_RANGE  = _ms.log10(20000.0) - LOG_MIN

                freq_amp = []   # (t_x, amp) sorted by frequency
                import numpy as _nps2
                for band in range(4):
                    f_lo = CROSSOVERS[band]
                    f_hi = CROSSOVERS[band + 1]
                    bins = frame_data[band]
                    freqs = _nps2.logspace(_ms.log10(max(f_lo, 1.0)),
                                           _ms.log10(f_hi), BINS_PER)
                    for bi in range(BINS_PER):
                        t_x = (_ms.log10(max(float(freqs[bi]), 20.0)) - LOG_MIN) / LOG_RANGE
                        freq_amp.append((t_x, max(0.0, min(1.0, float(bins[bi])))))

                freq_amp.sort(key=lambda p: p[0])

                def _spectrum_fill(pairs, color, h_scale=0.90):
                    """Draw filled silhouette from floor up as a single polygon."""
                    if len(pairs) < 2:
                        return
                    verts = []
                    for t_x, amp in pairs:
                        bx = disp_x + t_x * disp_w
                        verts.append((bx, disp_y))
                        verts.append((bx, disp_y + amp * disp_h * h_scale))
                    if len(verts) >= 4:
                        bf = batch_for_shader(shader, "TRI_STRIP", {"pos": verts})
                        shader.bind()
                        shader.uniform_float("color", color)
                        bf.draw(shader)

                def _spectrum_edge(pairs, color, h_scale=0.90):
                    """Draw the top edge of the spectrum as a LINE_STRIP."""
                    if len(pairs) < 2:
                        return
                    verts = [(disp_x + t_x * disp_w,
                              disp_y + amp * disp_h * h_scale)
                             for t_x, amp in pairs]
                    bt = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
                    gpu.state.line_width_set(max(1.0, scale * 0.7))
                    shader.bind()
                    shader.uniform_float("color", color)
                    bt.draw(shader)
                    gpu.state.line_width_set(1.0)

                # --- Layer 1: pre-EQ spectrum (dark grey) ---
                _spectrum_fill(freq_amp, (0.17, 0.17, 0.19, 0.82))
                _spectrum_edge(freq_amp, (0.32, 0.32, 0.36, 0.55))

                # --- Layer 2: post-EQ spectrum (EQ curve applied) ---
                # Multiply each bin's amplitude by the linear gain the current
                # EQ settings produce at that frequency — shows shaping live.
                try:
                    band_params_sp = [_get_b(bi) for bi in range(N_BANDS)]
                    post_pairs = []
                    for t_x, amp in freq_amp:
                        freq_hz = 20.0 * (10.0 ** (t_x * LOG_RANGE))
                        eq_db = 0.0
                        for bi in range(N_BANDS):
                            gdb_s, fhz_s, q_s, _, _, _ = band_params_sp[bi]
                            eq_db += _eq_biquad_response(
                                fhz_s, gdb_s, EQ7_BANDS[bi][2], q_s, freq_hz)
                        lin = 10.0 ** (eq_db / 20.0)
                        post_pairs.append((t_x, max(0.0, min(1.0, amp * lin))))

                    _spectrum_fill(post_pairs, (0.28, 0.30, 0.35, 0.72))
                    _spectrum_edge(post_pairs, (0.52, 0.58, 0.68, 0.90))
                except Exception:
                    pass  # post-EQ layer is bonus — never block pre-EQ draw

    except Exception:
        pass  # spectrum is decorative — never crash the draw callback

    # -----------------------------------------------------------------------
    # EQ CURVES
    # -----------------------------------------------------------------------
    N_PTS = 300
    try:
        band_params = [_get_b(bi) for bi in range(N_BANDS)]

        # Combined response
        combined_db = []
        for pi in range(N_PTS):
            t      = pi / (N_PTS - 1)
            f_test = _eq_freq_from_norm(t)
            total  = 0.0
            for bi in range(N_BANDS):
                gdb_b, fhz_b, q_b, _, _, _ = band_params[bi]
                total += _eq_biquad_response(fhz_b, gdb_b, EQ7_BANDS[bi][2], q_b, f_test)
            combined_db.append(total)

        # Curve points (clamped)
        curve_pts = []
        for pi in range(N_PTS):
            t   = pi / (N_PTS - 1)
            db  = max(-db_range * 1.1, min(db_range * 1.1, combined_db[pi]))
            cpx = disp_x + t * disp_w
            cpy = zero_db_y + db * px_per_db
            cpy = max(disp_y + 1, min(disp_y + disp_h - 1, cpy))
            curve_pts.append((cpx, cpy))

        # Filled teal area between curve and 0dB
        y_zero  = max(disp_y, min(disp_y + disp_h, zero_db_y))
        fill_v2 = []
        for i in range(N_PTS):
            fill_v2.append(curve_pts[i])
            fill_v2.append((curve_pts[i][0], y_zero))
        if len(fill_v2) >= 4:
            bfill = batch_for_shader(shader, "TRI_STRIP", {"pos": fill_v2})
            shader.bind()
            shader.uniform_float("color", (0.05, 0.35, 0.52, 0.18))
            bfill.draw(shader)

        # Per-band dim curves
        for bi in range(N_BANDS):
            gdb_b, fhz_b, q_b, _, _, _ = band_params[bi]
            if abs(gdb_b) < 0.5:
                continue
            bcol_b = EQ7_BANDS[bi][1]
            bftype_b = EQ7_BANDS[bi][2]
            bpts = []
            for pi in range(N_PTS):
                t   = pi / (N_PTS - 1)
                db  = _eq_biquad_response(fhz_b, gdb_b, bftype_b, q_b,
                                          _eq_freq_from_norm(t))
                db  = max(-db_range * 1.1, min(db_range * 1.1, db))
                bpx  = disp_x + t * disp_w
                bpyv = zero_db_y + db * px_per_db
                bpyv = max(disp_y + 1, min(disp_y + disp_h - 1, bpyv))
                bpts.append((bpx, bpyv))
            if len(bpts) >= 2:
                bb_b = batch_for_shader(shader, "LINE_STRIP", {"pos": bpts})
                gpu.state.line_width_set(max(1.0, scale))
                shader.bind()
                shader.uniform_float("color", (*bcol_b, 0.35))
                bb_b.draw(shader)
                gpu.state.line_width_set(1.0)

        # Combined curve (bright cyan)
        if len(curve_pts) >= 2:
            bc = batch_for_shader(shader, "LINE_STRIP", {"pos": curve_pts})
            gpu.state.line_width_set(max(2.0, scale * 2.0))
            shader.bind()
            shader.uniform_float("color", (0.15, 0.75, 1.00, 0.95))
            bc.draw(shader)
            gpu.state.line_width_set(1.0)

        # Band handle dots — positioned on the combined curve at each band freq
        for bi in range(N_BANDS):
            gdb_b, fhz_b, q_b, gn_b, fn_b, qn_b = band_params[bi]
            bname_b, bcol_b = EQ7_BANDS[bi][0], EQ7_BANDS[bi][1]
            dot_x = disp_x + fn_b * disp_w
            f_here = _eq_freq_from_norm(fn_b)
            dot_db = sum(
                _eq_biquad_response(band_params[b][1], band_params[b][0],
                                    EQ7_BANDS[b][2], band_params[b][2], f_here)
                for b in range(N_BANDS)
            )
            dot_db = max(-db_range, min(db_range, dot_db))
            dot_y  = zero_db_y + dot_db * px_per_db
            dot_y  = max(disp_y + 5*scale, min(disp_y + disp_h - 5*scale, dot_y))
            dot_r  = max(6*scale, 7*scale)
            _draw_circle(dot_x, dot_y, dot_r + 2*scale, (*bcol_b, 0.20))
            _draw_circle(dot_x, dot_y, dot_r,           (*bcol_b, 1.00))
            _draw_circle(dot_x, dot_y, dot_r * 0.35,    (1.0, 1.0, 1.0, 0.80))
            fs_dot = max(1, int(8 * scale))
            tw_dot = _text_width(bname_b, fs_dot)
            _draw_text(bname_b, dot_x - tw_dot * 0.5,
                       dot_y - dot_r - 11 * scale, fs_dot, (*bcol_b, 0.9))

    except Exception:
        import traceback; traceback.print_exc()

    # Display border
    bv3 = [(disp_x, disp_y), (disp_x + disp_w, disp_y),
           (disp_x + disp_w, disp_y + disp_h),
           (disp_x, disp_y + disp_h), (disp_x, disp_y)]
    bb3 = batch_for_shader(shader, "LINE_STRIP", {"pos": bv3})
    shader.bind(); shader.uniform_float("color", (0.20, 0.20, 0.20, 1.0))
    bb3.draw(shader)

    # -----------------------------------------------------------------------
    # KNOB STRIP  — 7 equal columns: L | 1 | 2 | 3 | 4 | 5 | H
    # Each column (top to bottom): label, Gain knob, Freq knob, Q knob
    # -----------------------------------------------------------------------
    col_w    = disp_w / N_BANDS
    kr_gain  = min(max(16 * scale, col_w * 0.18), 26 * scale)
    kr_small = min(max(11 * scale, col_w * 0.13), 18 * scale)

    # Row layout: place rows evenly within knob_h, top-to-bottom:
    # label → gain knob → freq knob → Q knob
    # Divide available height across 4 rows with equal spacing.
    row_slot = knob_h / 4.0
    row_lbl  = knob_y + knob_h - row_slot * 0.28
    row_gain = knob_y + knob_h - row_slot * 1.2
    row_freq = knob_y + knob_h - row_slot * 2.3
    row_q    = knob_y + knob_h - row_slot * 3.35
    # Clamp knob radii so they fit within a slot
    kr_gain  = min(kr_gain,  row_slot * 0.42)
    kr_small = min(kr_small, row_slot * 0.32)

    fs_lbl = max(1, int(11 * scale))

    for bi in range(N_BANDS):
        gdb_b, fhz_b, q_b, gn_b, fn_b, qn_b = band_params[bi]
        bname_b, bcol_b, bftype_b = EQ7_BANDS[bi][0], EQ7_BANDS[bi][1], EQ7_BANDS[bi][2]
        col_cx = disp_x + (bi + 0.5) * col_w

        # Band label
        tw_l = _text_width(bname_b, fs_lbl)
        _draw_text(bname_b, col_cx - tw_l * 0.5,
                   row_lbl - fs_lbl, fs_lbl, (*bcol_b, 1.0))

        # Gain knob
        gain_str = f"{gdb_b:+.1f}dB"
        _draw_knob(col_cx, row_gain, kr_gain, gn_b, bcol_b,
                   "Gain", gain_str, scale)

        # Freq knob
        freq_str = (f"{fhz_b/1000:.2f}k" if fhz_b >= 1000
                    else f"{fhz_b:.0f}Hz")
        _draw_knob(col_cx, row_freq, kr_small, fn_b, bcol_b,
                   "Freq", freq_str, scale)

        # Q knob (peaks only)
        if bftype_b == "peak":
            q_str = f"{q_b:.2f}"
            _draw_knob(col_cx, row_q, kr_small, qn_b, bcol_b,
                       "Q", q_str, scale)
        else:
            _draw_circle(col_cx, row_q, kr_small, (0.09, 0.09, 0.09, 1.0))
            _draw_circle(col_cx, row_q, kr_small, (0.18, 0.18, 0.18, 1.0),
                         filled=False)
            fs_sh = max(1, int(8 * scale))
            lbl_sh = "shelf"
            tw_sh  = _text_width(lbl_sh, fs_sh)
            _draw_text(lbl_sh, col_cx - tw_sh * 0.5,
                       row_q - fs_sh * 0.5, fs_sh, (0.22, 0.22, 0.22, 1.0))

        # Column divider
        if bi < N_BANDS - 1:
            div_xd = disp_x + (bi + 1) * col_w
            _draw_rect(div_xd - max(0.5, scale * 0.5),
                       knob_y, max(0.5, scale * 0.5), knob_h,
                       (0.16, 0.16, 0.16, 1.0))

def _draw_reverb_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Draw the reverb rack body — Option C style.

    Display area (upper 60% of body):
      Left zone  — dry waveform from _fft_timeline (same as EQ pre-EQ layer)
      Divider    — dashed vertical line at the pre-delay position
      Right zone — computed reverb tail silhouette, exponentially decaying,
                   shape driven entirely by room_size and damping knobs

    Knob strip (lower 40%):
      Room | Damp | Wet | Pre-dly | Width
    """
    import math as _mr
    ui_scale    = scale
    rail_h      = RACK_RAIL_H * ui_scale
    body_h      = rh - rail_h

    # --- Display geometry: display at TOP of body, knobs at BOTTOM ---
    margin_l    = 42 * ui_scale
    ch_btn_w    = 108 * ui_scale
    margin_r    = ch_btn_w + 8 * ui_scale
    disp_x      = rx + margin_l
    disp_w      = rw - margin_l - margin_r
    disp_prop   = 0.56          # display takes 56% of body height
    disp_h      = body_h * disp_prop - 4 * ui_scale
    disp_y      = ry + body_h - disp_h - 2 * ui_scale  # top of body

    # Knob strip sits at the bottom of the body
    knob_h      = body_h * 0.42 - 4 * ui_scale
    knob_y      = ry + 2 * ui_scale                     # bottom of body

    shader = _get_shader()

    # Display background
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.07, 0.07, 0.09, 1.0))

    # Grid lines
    for db_frac in [0.25, 0.5, 0.75]:
        ly = disp_y + db_frac * disp_h
        gl = batch_for_shader(shader, "LINES",
                               {"pos": [(disp_x, ly), (disp_x + disp_w, ly)]})
        shader.bind()
        shader.uniform_float("color", (0.18, 0.18, 0.20, 1.0))
        gl.draw(shader)

    # --- Read knob params ---
    room_sz  = getattr(rack, 'p0', 0.5)
    damping  = getattr(rack, 'p1', 0.5)
    wet      = getattr(rack, 'p2', 0.3)
    pre_d    = getattr(rack, 'p3', 0.0)
    width    = getattr(rack, 'p4', 1.0)

    # Pre-delay position as fraction of display width (0–20% of display)
    pre_frac = pre_d * 0.20
    div_x    = disp_x + pre_frac * disp_w

    # --- LEFT ZONE: dry waveform from FFT timeline ---
    if rack.enabled:
     try:
        from Loader import _fft_timeline
        assigned_rv = get_rack_channels(rack)
        if assigned_rv:
            ch_rv = list(assigned_rv)[0]
            tl_rv = _fft_timeline.get(ch_rv)
            if tl_rv is not None and len(tl_rv['snapshots']) > 0:
                import bpy as _bpy_rv
                scene_rv    = _bpy_rv.context.scene
                cur_frame   = scene_rv.frame_current if scene_rv else 0
                start_frame = tl_rv['start_frame']
                fps_rv      = tl_rv['fps']
                snap_sec    = tl_rv['snap_frames'] / tl_rv['sr']
                elapsed     = max(0.0, (cur_frame - start_frame) / fps_rv)
                snap_f      = elapsed / snap_sec
                snap_idx    = max(0, min(len(tl_rv['snapshots'])-1, int(snap_f)))
                frac_rv     = snap_f - int(snap_f)

                import numpy as _np_rv
                frame_data = tl_rv['snapshots'][snap_idx].astype(float)
                if frac_rv > 0.0 and snap_idx+1 < len(tl_rv['snapshots']):
                    frame_data = (frame_data*(1.0-frac_rv) +
                                  tl_rv['snapshots'][snap_idx+1].astype(float)*frac_rv)

                # Flatten 4 bands × 32 bins into 128 amplitude points
                CROSSOVERS = [20, 120, 800, 5000, 20000]
                BINS_PER   = 32
                LOG_MIN    = _mr.log10(20.0)
                LOG_RNG    = _mr.log10(20000.0) - LOG_MIN
                import numpy as _np_rv2
                wf_pairs = []
                for band in range(4):
                    f_lo = CROSSOVERS[band]; f_hi = CROSSOVERS[band+1]
                    freqs = _np_rv2.logspace(_mr.log10(max(f_lo,1.0)),
                                              _mr.log10(f_hi), BINS_PER)
                    for bi in range(BINS_PER):
                        t_x = (_mr.log10(max(float(freqs[bi]),20.0)) - LOG_MIN) / LOG_RNG
                        # Clamp to left zone (pre-delay divider)
                        bx  = disp_x + t_x * pre_frac * disp_w
                        amp = max(0.0, min(1.0, float(frame_data[band][bi])))
                        wf_pairs.append((bx, amp))

                wf_pairs.sort(key=lambda p: p[0])

                # Draw dry waveform silhouette — same style as EQ pre-EQ layer
                if len(wf_pairs) >= 2:
                    verts = []
                    for bx, amp in wf_pairs:
                        verts.append((bx, disp_y))
                        verts.append((bx, disp_y + amp * disp_h * 0.88))
                    if len(verts) >= 4:
                        bf = batch_for_shader(shader, "TRI_STRIP", {"pos": verts})
                        shader.bind()
                        shader.uniform_float("color", (0.17, 0.17, 0.19, 0.82))
                        bf.draw(shader)
                    edge = [(bx, disp_y + amp * disp_h * 0.88)
                            for bx, amp in wf_pairs]
                    if len(edge) >= 2:
                        be = batch_for_shader(shader, "LINE_STRIP", {"pos": edge})
                        gpu.state.line_width_set(max(1.0, ui_scale*0.7))
                        shader.bind()
                        shader.uniform_float("color", (0.32, 0.32, 0.36, 0.55))
                        be.draw(shader)
                        gpu.state.line_width_set(1.0)
     except Exception:
        pass  # waveform is decorative — never crash

    # --- Pre-delay divider ---
    if pre_frac > 0.005:
        div_verts = [(div_x, disp_y), (div_x, disp_y + disp_h)]
        div_batch = batch_for_shader(shader, "LINES", {"pos": div_verts})
        shader.bind()
        shader.uniform_float("color", (0.55, 0.55, 0.60, 0.50))
        div_batch.draw(shader)
        # Label
        fs_pd = max(1, int(8*ui_scale))
        _draw_text(f"{int(pre_d*100)}ms", div_x + 2*ui_scale,
                   disp_y + disp_h - fs_pd - 2*ui_scale, fs_pd, (0.55, 0.55, 0.60, 0.80))

    # --- RIGHT ZONE: reverb tail silhouette ---
    # Exponential decay: y(t) = exp(-t * decay_rate)
    # decay_rate is derived from room_size and damping
    # RT60 (60dB decay time) = -60 / (20*log10(e) * decay_rate)
    # We map room_size → feedback (0.28-0.98), damping → HF rolloff
    feedback     = 0.28 + room_sz * 0.70
    # Approximate RT60 in display-space: larger room = longer tail
    if feedback < 0.9999:
        rt60_frac = -0.05 / _mr.log10(max(feedback, 1e-9))  # in display width units
    else:
        rt60_frac = 2.0
    rt60_frac = min(rt60_frac, 2.0)

    # HF curve decays faster by damping factor
    hf_rt60_frac = rt60_frac * (1.0 - damping * 0.7)

    tail_start_x = div_x
    tail_w       = disp_x + disp_w - tail_start_x
    N_TAIL       = 128
    centre_y     = disp_y + disp_h * 0.5
    peak_h       = disp_h * 0.45 * wet  # taller tail = more wet

    # Full-band tail (grey)
    tail_verts = []
    for i in range(N_TAIL + 1):
        t = i / N_TAIL
        x = tail_start_x + t * tail_w
        if rt60_frac > 0:
            amp = _mr.exp(-t * 3.0 / max(rt60_frac, 0.01))
        else:
            amp = 0.0
        h = amp * peak_h
        tail_verts.append((x, centre_y))
        tail_verts.append((x, centre_y + h))

    if len(tail_verts) >= 4:
        bt = batch_for_shader(shader, "TRI_STRIP", {"pos": tail_verts})
        shader.bind()
        shader.uniform_float("color", (0.28, 0.32, 0.38, 0.65))
        bt.draw(shader)

    # Mirror lower half
    tail_lower = []
    for i in range(N_TAIL + 1):
        t = i / N_TAIL
        x = tail_start_x + t * tail_w
        amp = _mr.exp(-t * 3.0 / max(rt60_frac, 0.01)) if rt60_frac > 0 else 0.0
        h = amp * peak_h
        tail_lower.append((x, centre_y))
        tail_lower.append((x, centre_y - h))

    if len(tail_lower) >= 4:
        bl = batch_for_shader(shader, "TRI_STRIP", {"pos": tail_lower})
        shader.bind()
        shader.uniform_float("color", (0.28, 0.32, 0.38, 0.65))
        bl.draw(shader)

    # HF tail overlay (lighter, decays faster — shows damping effect)
    hf_verts_top = []; hf_verts_bot = []
    for i in range(N_TAIL + 1):
        t = i / N_TAIL
        x = tail_start_x + t * tail_w
        amp = _mr.exp(-t * 3.0 / max(hf_rt60_frac, 0.01)) if hf_rt60_frac > 0 else 0.0
        h = amp * peak_h * 0.65
        hf_verts_top.append((x, centre_y + h))
        hf_verts_bot.append((x, centre_y - h))

    for hf_verts in [hf_verts_top, hf_verts_bot]:
        if len(hf_verts) >= 2:
            bh = batch_for_shader(shader, "LINE_STRIP", {"pos": hf_verts})
            gpu.state.line_width_set(max(1.0, ui_scale * 0.7))
            shader.bind()
            shader.uniform_float("color", (0.50, 0.60, 0.72, 0.70))
            bh.draw(shader)
            gpu.state.line_width_set(1.0)

    # RT60 label
    if rt60_frac > 0:
        rt60_ms = rt60_frac * 1000
        rt60_str = f"{rt60_ms:.0f}ms" if rt60_ms < 1000 else f"{rt60_ms/1000:.1f}s"
        fs_rt = max(1, int(9*ui_scale))
        _draw_text(f"RT60 {rt60_str}", disp_x + disp_w - 60*ui_scale,
                   disp_y + 6*ui_scale, fs_rt, (0.50, 0.60, 0.72, 0.85))

    # Labels
    fs_lbl = max(1, int(8*ui_scale))
    _draw_text("dry", disp_x + 3*ui_scale, disp_y + 5*ui_scale,
               fs_lbl, (0.45, 0.45, 0.48, 0.80))
    _draw_text("tail", tail_start_x + 4*ui_scale, disp_y + 5*ui_scale,
               fs_lbl, (0.50, 0.60, 0.72, 0.80))

    # --- KNOB STRIP (5 knobs: Room, Damp, Wet, Pre-dly, Width) ---
    N_KNOBS  = 5
    col_w    = disp_w / N_KNOBS
    row_slot = knob_h / 3.0
    row_knob = knob_y + knob_h - row_slot * 1.3
    kr       = min(max(13*ui_scale, col_w*0.16), 20*ui_scale)
    kr       = min(kr, row_slot * 0.42)

    RV_KNOB_PARAMS = ["Room", "Damp", "Wet", "Pre-dly", "Width"]
    rv_vals = [room_sz, damping, wet, pre_d, width]
    rv_col  = (0.35, 0.65, 0.90)

    for ki in range(N_KNOBS):
        cx = disp_x + (ki + 0.5) * col_w
        val = rv_vals[ki]
        pct_str = f"{int(val*100)}%"
        _draw_knob(cx, row_knob, kr, val, rv_col,
                   RV_KNOB_PARAMS[ki], pct_str, ui_scale)



def _draw_noisegate_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Noise gate display.

    Draws exactly the mockup design:
    - Grey waveform from _fft_timeline_full (real audio, mirrored top+bottom)
    - Green gate envelope line drawn from knob values (attack slope, hold flat,
      release slope) — triggered wherever waveform crosses threshold
    - Amber dashed threshold line
    - Colour-coded atk/hold/rel bracket annotations over first event
    - Gate open/closed readout top-right
    - Knob strip with colour-coded arcs matching bracket colours
    """
    import math as _mg
    ui     = scale
    rail_h = RACK_RAIL_H * ui
    body_h = rh - rail_h

    # Display area (left of channel buttons)
    disp_x = rx + 42 * ui
    disp_w = rw - 42 * ui - 108 * ui - 8 * ui
    disp_h = body_h * 0.56 - 4 * ui
    disp_y = ry + body_h - disp_h - 2 * ui

    # Knob area (above display)
    knob_h = body_h * 0.42 - 4 * ui
    knob_y = ry + 2 * ui

    # Gate state Y positions
    open_y   = disp_y + disp_h * 0.92   # gate open = high signal = near bottom of display
    closed_y = disp_y + disp_h * 0.10   # gate closed = muted = near top of display
    centre_y = disp_y + disp_h * 0.50   # waveform centre

    # Knob values
    thr_norm  = getattr(rack, 'p0', 0.50)
    atk_norm  = getattr(rack, 'p1', 0.05)
    hold_norm = getattr(rack, 'p2', 0.16)
    rel_norm  = getattr(rack, 'p3', 0.14)
    rng_norm  = getattr(rack, 'p4', 0.00)
    thr_db    = -60.0 + thr_norm * 60.0
    atk_ms    = 0.1   + atk_norm  * 99.9
    hold_ms   = hold_norm * 500.0
    rel_ms    = 10.0  + rel_norm  * 990.0
    rng_db    = -90.0 + rng_norm  * 90.0

    # Threshold in linear amplitude (for RMS comparison)
    thr_lin = 10.0 ** (thr_db / 20.0)

    shader = _get_shader()

    # ── Display background ────────────────────────────────────────────────────
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.035, 0.042, 0.050, 1.0))

    # ── Grey waveform from real audio ─────────────────────────────────────────
    rms_vals = None
    gate_events = []   # list of (close_x, attack_x, hold_x, release_x) in pixels
    gate_open_now = True
    gr_db_cur = 0.0

    try:
        import bpy as _bpy, numpy as _np
        from Loader import _fft_timeline_full, _fft_timeline
        assigned = get_rack_channels(rack)
        if not assigned:
            raise ValueError("no ch")
        ch = list(assigned)[0]
        tl = _fft_timeline_full.get(ch) or _fft_timeline.get(ch)
        if tl is None or not len(tl["snapshots"]):
            raise ValueError("no data")

        # Always prefer the full-track timeline — it has consistent start_frame=1
        # and covers the whole track, so cur_snap is always comparable.
        tl_full = _fft_timeline_full.get(ch)
        if tl_full and len(tl_full["snapshots"]):
            tl = tl_full

        n_tl     = len(tl["snapshots"])
        scene    = _bpy.context.scene
        cur_f    = scene.frame_current if scene else 0
        snap_sec = tl["snap_frames"] / float(tl["sr"])
        elapsed  = max(0.0, (cur_f - tl["start_frame"]) / float(tl["fps"]))
        cur_snap = max(0, min(n_tl - 1, int(elapsed / snap_sec)))

        # Show a fixed N_WIN window of snapshots.
        # Window slides so cur_snap is always visible:
        #   - If track fits in N_WIN, show all of it (0..n_tl-1) + silence pad.
        #   - Otherwise, centre the window on cur_snap.
        N_WIN    = 80
        if n_tl <= N_WIN:
            # Short track — show everything, silence-pad the right
            ws       = 0
            n_real   = n_tl
            n_pad_r  = N_WIN - n_real
            raw_vals = ([float(_np.mean(tl["snapshots"][i])) for i in range(n_real)]
                        + [0.0] * n_pad_r)
        else:
            # Long track — slide window to keep cur_snap near right edge (2/3 in)
            ws = max(0, cur_snap - (N_WIN * 2 // 3))
            we = ws + N_WIN
            if we > n_tl:
                we = n_tl
                ws = max(0, we - N_WIN)
            raw_vals = [float(_np.mean(tl["snapshots"][ws + i]))
                        for i in range(we - ws)]
            if len(raw_vals) < N_WIN:
                raw_vals = raw_vals + [0.0] * (N_WIN - len(raw_vals))
        rms_vals  = raw_vals  # always N_WIN entries
        # cur_snap position within the displayed window (for playhead line)
        playhead_slot = max(0, min(N_WIN - 1, cur_snap - ws))

        # Current gate state from RMS vs threshold
        rms_cur = rms_vals[-1] if rms_vals else 0.0
        gate_open_now = rms_cur >= thr_lin

        # Pixel widths for attack / hold / release — based on fixed N_WIN spacing
        ms_per_snap = snap_sec * 1000.0
        px_per_snap = disp_w / max(N_WIN - 1, 1)
        atk_px  = max(2.0, (atk_ms  / ms_per_snap) * px_per_snap)
        hold_px = max(2.0, (hold_ms / ms_per_snap) * px_per_snap)
        rel_px  = max(2.0, (rel_ms  / ms_per_snap) * px_per_snap)

    except Exception:
        rms_vals = None
        atk_px  = max(2.0, disp_w * 0.025)
        hold_px = max(2.0, disp_w * 0.15)
        rel_px  = max(2.0, disp_w * 0.055)

    # Draw waveform if we have data
    if rms_vals:
        max_rms = max(rms_vals + [0.01])
        half_h  = disp_h * 0.34
        wf_top  = []
        wf_bot  = []
        _N = 79  # N_WIN - 1, constant so x-spacing never changes
        for i, rms in enumerate(rms_vals):
            bx  = disp_x + (i / _N) * disp_w
            amp = (rms / max_rms) * half_h
            wf_top.append((bx, centre_y - amp))
            wf_bot.append((bx, centre_y + amp))

        # Fill between top and bottom
        fill_verts = []
        for (bx, ty), (_, by) in zip(wf_top, wf_bot):
            fill_verts += [(bx, ty), (bx, by)]
        if len(fill_verts) >= 4:
            bf = batch_for_shader(shader, "TRI_STRIP", {"pos": fill_verts})
            shader.bind(); shader.uniform_float("color", (0.15, 0.16, 0.20, 0.85))
            bf.draw(shader)
        for pts in [wf_top, wf_bot]:
            if len(pts) >= 2:
                bl = batch_for_shader(shader, "LINE_STRIP", {"pos": pts})
                gpu.state.line_width_set(max(1.0, ui * 0.8))
                shader.bind(); shader.uniform_float("color", (0.24, 0.26, 0.32, 0.70))
                bl.draw(shader)
        gpu.state.line_width_set(1.0)

    # ── Playhead cursor line ─────────────────────────────────────────────────
    # White vertical line showing current frame position in the waveform window
    if rms_vals:
        try:
            _ph_x = disp_x + (playhead_slot / max(N_WIN - 1, 1)) * disp_w
            _ph_verts = [(_ph_x, disp_y + 2*ui), (_ph_x, disp_y + disp_h - 2*ui)]
            _ph_batch = batch_for_shader(shader, "LINES", {"pos": _ph_verts})
            gpu.state.line_width_set(max(1.5, ui))
            shader.bind()
            shader.uniform_float("color", (0.85, 0.85, 0.90, 0.60))
            _ph_batch.draw(shader)
            gpu.state.line_width_set(1.0)
        except Exception:
            pass

    # ── Gate envelope line ────────────────────────────────────────────────────
    # Built from knob values: find where waveform crosses threshold → draw
    # attack slope up, hold flat, release slope down, then closed again.
    # Range floor: where the line sits when closed
    rng_y = closed_y - rng_norm * (closed_y - open_y)

    gate_pts = []
    first_event = None   # (close_x, atk_x, hold_x, rel_x) for annotations

    if rms_vals:
        count    = len(rms_vals)
        px_per_i = disp_w / 79  # always N_WIN-1 = 79 so spacing is constant
        in_gate = False
        hold_remaining = 0.0

        i = 0
        while i < count:
            bx  = disp_x + i * px_per_i
            rms = rms_vals[i]
            is_above = rms >= thr_lin

            if not in_gate and is_above:
                # Transition: closed → open (attack)
                close_x = bx
                atk_end = min(bx + atk_px, disp_x + disp_w)
                if gate_pts and gate_pts[-1][1] != rng_y:
                    gate_pts.append((bx, rng_y))
                else:
                    gate_pts.append((bx, rng_y))
                gate_pts.append((atk_end, open_y))
                in_gate = True
                hold_remaining = hold_px
                if first_event is None:
                    first_event = (close_x, atk_end, None, None)
            elif in_gate and is_above:
                # Still open — flat at open_y, consume hold
                gate_pts.append((bx, open_y))
                hold_remaining -= px_per_i
                if first_event and first_event[2] is None:
                    first_event = (first_event[0], first_event[1], bx, None)
            elif in_gate and not is_above:
                # Below threshold — hold then release
                if hold_remaining > 0:
                    gate_pts.append((bx, open_y))
                    hold_remaining -= px_per_i
                    if first_event and first_event[2] is None:
                        first_event = (first_event[0], first_event[1], bx, None)
                else:
                    # Release slope
                    rel_end = min(bx + rel_px, disp_x + disp_w)
                    gate_pts.append((bx, open_y))
                    gate_pts.append((rel_end, rng_y))
                    in_gate = False
                    if first_event and first_event[3] is None:
                        if first_event[2] is None:
                            first_event = (first_event[0], first_event[1], bx, rel_end)
                        else:
                            first_event = (first_event[0], first_event[1], first_event[2], rel_end)
            else:
                # Closed — flat at range floor
                gate_pts.append((bx, rng_y))
            i += 1
    else:
        # No data — draw flat at open
        gate_pts = [(disp_x, open_y), (disp_x + disp_w, open_y)]

    # Fill under gate line
    if len(gate_pts) >= 2:
        fv2 = []
        for gx, gy in gate_pts:
            fv2 += [(gx, disp_y + disp_h - 2), (gx, gy)]
        if len(fv2) >= 4:
            bf2 = batch_for_shader(shader, "TRI_STRIP", {"pos": fv2})
            shader.bind(); shader.uniform_float("color", (0.04, 0.18, 0.07, 0.45))
            bf2.draw(shader)
        bg = batch_for_shader(shader, "LINE_STRIP", {"pos": gate_pts})
        gpu.state.line_width_set(max(2.0, ui * 1.5))
        shader.bind(); shader.uniform_float("color", (0.20, 0.88, 0.38, 1.0))
        bg.draw(shader); gpu.state.line_width_set(1.0)

    # ── Amber dashed threshold line ───────────────────────────────────────────
    thr_y = rng_y - thr_norm * (rng_y - open_y)
    dash = 7*ui; gap = 4*ui; x = disp_x; tog = True; segs = []
    while x < disp_x + disp_w:
        xe = min(x + (dash if tog else gap), disp_x + disp_w)
        if tog: segs += [(x, thr_y), (xe, thr_y)]
        x = xe; tog = not tog
    if segs:
        bd = batch_for_shader(shader, "LINES", {"pos": segs})
        gpu.state.line_width_set(max(1.8, ui * 1.2))
        shader.bind(); shader.uniform_float("color", (0.96, 0.62, 0.08, 1.0))
        bd.draw(shader); gpu.state.line_width_set(1.0)
    _draw_text(f"thr {thr_db:.0f}dB", disp_x + 4*ui, thr_y - 2*ui - 9*ui,
               max(1, int(8*ui)), (0.96, 0.62, 0.08, 1.0))

    # ── open/closed labels ────────────────────────────────────────────────────
    fs7 = max(1, int(7*ui))
    _draw_text("open",   disp_x + 4*ui, open_y + 2*ui,       fs7, (0.28, 0.55, 0.28, 0.70))
    _draw_text("closed", disp_x + 4*ui, closed_y - fs7 - 2*ui, fs7, (0.55, 0.28, 0.28, 0.70))

    # ── Bracket annotations (atk/hold/rel) on first gate event ───────────────
    ann_y = disp_y + 8*ui
    fs8   = max(1, int(8*ui))
    if first_event:
        cx, ax, hx, rx = first_event
        hx = hx or ax
        rx = rx or min(hx + rel_px, disp_x + disp_w)
        # attack bracket (blue)
        if ax > cx + 2:
            _draw_rect(cx, ann_y - 1, ax - cx, 1, (0.42, 0.55, 1.0, 0.9))
            _draw_rect(cx, ann_y - 3, 1, 5, (0.42, 0.55, 1.0, 0.9))
            _draw_rect(ax, ann_y - 3, 1, 5, (0.42, 0.55, 1.0, 0.9))
            _draw_text("atk", cx + 2, ann_y - fs8 - 3, fs8, (0.42, 0.55, 1.0, 1.0))
        # hold bracket (purple)
        if hx > ax + 2:
            _draw_rect(ax, ann_y - 1, hx - ax, 1, (0.65, 0.55, 0.98, 0.9))
            _draw_rect(ax, ann_y - 3, 1, 5, (0.65, 0.55, 0.98, 0.9))
            _draw_rect(hx, ann_y - 3, 1, 5, (0.65, 0.55, 0.98, 0.9))
            mid = ax + (hx - ax) / 2
            _draw_text("hold", mid - 12*ui, ann_y - fs8 - 3, fs8, (0.65, 0.55, 0.98, 1.0))
        # release bracket (orange)
        if rx > hx + 2:
            _draw_rect(hx, ann_y - 1, rx - hx, 1, (0.98, 0.45, 0.08, 0.9))
            _draw_rect(hx, ann_y - 3, 1, 5, (0.98, 0.45, 0.08, 0.9))
            _draw_rect(rx, ann_y - 3, 1, 5, (0.98, 0.45, 0.08, 0.9))
            _draw_text("rel", hx + 2, ann_y - fs8 - 3, fs8, (0.98, 0.45, 0.08, 1.0))

    # ── Gate open/closed readout ──────────────────────────────────────────────
    try:
        col = (0.20, 0.88, 0.32, 1.0) if gate_open_now else (0.88, 0.18, 0.18, 1.0)
        lbl = "gate open" if gate_open_now else "gate closed"
        fs9 = max(1, int(9*ui)); fs8b = max(1, int(8*ui)); pad = 4*ui
        tw  = _text_width(lbl, fs9)
        bw  = tw + pad*2; bh = fs9 + fs8b + pad*2 + 2*ui
        bx2 = disp_x + disp_w - bw - 4*ui
        by2 = disp_y + 4*ui
        bg_col = (0.05, 0.18, 0.07, 1.0) if gate_open_now else (0.18, 0.05, 0.05, 1.0)
        _draw_rect(bx2, by2, bw, bh, bg_col)
        _draw_text(lbl, bx2 + pad, by2 + bh - fs9 - pad, fs9, col)
        _draw_text(f"GR  {gr_db_cur:.1f}dB", bx2 + pad, by2 + pad, fs8b, (0.55, 0.65, 0.55, 1.0))
    except Exception:
        pass

    # ── Knob strip ────────────────────────────────────────────────────────────
    N  = 5
    cw = disp_w / N
    rk = knob_y + knob_h * 0.68
    kr = min(max(13*ui, cw * 0.16), 20*ui)
    kr = min(kr, knob_h * 0.42 * 0.42)

    knob_defs = [
        ("Threshold", thr_norm, f"{thr_db:.0f}dB",  (0.88, 0.30, 0.30)),
        ("Attack",    atk_norm, f"{atk_ms:.0f}ms",  (0.42, 0.55, 1.00)),
        ("Hold",      hold_norm,f"{hold_ms:.0f}ms", (0.65, 0.55, 0.98)),
        ("Release",   rel_norm, f"{rel_ms:.0f}ms",  (0.98, 0.45, 0.08)),
        ("Range",     rng_norm, f"{rng_db:.0f}dB",  (0.50, 0.50, 0.55)),
    ]
    for ki, (label, val, vstr, col_k) in enumerate(knob_defs):
        cx = disp_x + (ki + 0.5) * cw
        _draw_knob(cx, rk, kr, val, col_k, label, vstr, ui)



def _draw_delay_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Delay rack — impulse response curve display.

    Upper display (~56% of body height):
      Impulse response envelope: a smooth mirrored decay curve built entirely
      from the five knob values — no audio timeline data needed.

      The curve simulates what a single impulse sounds like through the delay:
        - A dry impulse spike at x=0
        - A series of echo peaks at x = n * delay_ms, each with amplitude
          feedback^n * mix, smoothed into a continuous envelope
        - The LP filter darkens (attenuates) each successive echo by
          multiplying the amplitude by filter^n (fully open = no attenuation)
        - Ping-pong: even echoes drawn on the upper half of the display,
          odd echoes on the lower half, with a magenta centre divider
        - The overall shape updates instantly as any knob moves

    Lower knob strip (~42% of body height):
      Time (cyan) | Feedback (orange) | Mix (green) | Spread (magenta) | Filter (warm-white)
    """
    import math as _md
    ui     = scale
    rail_h = RACK_RAIL_H * ui
    body_h = rh - rail_h

    # ── Geometry — identical to previous version ──────────────────────────────
    margin_l  = 42 * ui
    ch_btn_w  = 108 * ui
    margin_r  = ch_btn_w + 8 * ui
    disp_x    = rx + margin_l
    disp_w    = rw - margin_l - margin_r
    disp_prop = 0.56
    disp_h    = body_h * disp_prop - 4 * ui
    disp_y    = ry + body_h - disp_h - 2 * ui
    knob_h    = body_h * 0.42 - 4 * ui
    knob_y    = ry + 2 * ui

    # ── Knob values — identical to previous version ───────────────────────────
    time_norm  = getattr(rack, 'p0', 0.121)
    fb_norm    = getattr(rack, 'p1', 0.40)
    mix_norm   = getattr(rack, 'p2', 0.30)
    spread_n   = getattr(rack, 'p3', 0.50)
    filt_norm  = getattr(rack, 'p4', 0.60)

    delay_ms   = 1.0 + time_norm * 1999.0
    feedback   = fb_norm
    mix        = mix_norm
    ping_pong  = spread_n > 0.5

    filt_hz    = 200.0 * (100.0 ** filt_norm)
    filt_str   = (f"LP {filt_hz:.0f}Hz" if filt_hz < 10000
                  else f"LP {filt_hz/1000:.1f}kHz")
    delay_str  = (f"{delay_ms:.0f}ms" if delay_ms < 1000
                  else f"{delay_ms/1000:.2f}s")

    shader = _get_shader()

    # ── Display background ────────────────────────────────────────────────────
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.035, 0.040, 0.055, 1.0))

    # Subtle grid lines
    for _gfrac in (0.25, 0.50, 0.75):
        _gly = disp_y + _gfrac * disp_h
        _ggb = batch_for_shader(shader, "LINES",
                                {"pos": [(disp_x, _gly), (disp_x + disp_w, _gly)]})
        shader.bind()
        shader.uniform_float("color", (0.10, 0.11, 0.16, 1.0))
        _ggb.draw(shader)

    # ── Impulse response curve ────────────────────────────────────────────────
    # Build the envelope by sampling at N_PTS points across the display.
    # For each x position we work out which delay tap it falls under and what
    # the envelope amplitude is, including LP darkening across taps.
    #
    # Envelope model per tap n (1-based):
    #   peak_amp  = mix * 0.9 * feedback^(n-1)
    #   lp_factor = filt_norm^n  (1.0 = no darkening, 0.0 = silent)
    #   amplitude = peak_amp * lp_factor
    #   The shape within each tap is a Gaussian bell centred on the tap's x,
    #   with sigma = delay_px * 0.18 (narrows/widens with delay time naturally).
    #
    # The dry impulse (tap 0) is always drawn as a sharp spike at x=0.

    N_PTS    = 512
    MAX_TAPS = 12
    cy       = disp_y + disp_h * 0.5   # vertical centre of display

    # Map delay_ms to pixels using the full display width = 2500ms of "view time"
    # so the curve always fits: at max delay (2000ms) the first echo is at 80% width.
    VIEW_MS    = 2500.0
    px_per_ms  = disp_w / VIEW_MS

    # LP per-tap attenuation: each echo is multiplied by lp_atten once more
    # filt_norm=1.0 → lp_atten=1.0 (fully open, no roll-off)
    # filt_norm=0.0 → lp_atten=0.2 (very dark, heavy roll-off)
    lp_atten = 0.2 + filt_norm * 0.8

    # Gaussian sigma in pixels — scales with delay so shape looks natural
    sigma_px  = max(8.0 * ui, delay_ms * px_per_ms * 0.18)

    def _gaussian(x_px, centre_px):
        d = (x_px - centre_px) / sigma_px
        return _md.exp(-0.5 * d * d)

    # Sample the full envelope across the display width
    pts_top = []   # upper half (or full if no ping-pong)
    pts_bot = []   # lower half (mirrored)
    pts_top_pp_even = []   # ping-pong even taps (upper)
    pts_bot_pp_even = []
    pts_top_pp_odd  = []   # ping-pong odd taps (lower)
    pts_bot_pp_odd  = []

    half_h = disp_h * 0.43   # max half-height the curve can reach

    for _pi in range(N_PTS):
        _xf  = _pi / (N_PTS - 1)
        _x   = disp_x + _xf * disp_w
        _xms = _xf * VIEW_MS

        # Dry spike — sharp Gaussian centred at 0
        dry_amp = _gaussian(_xms, 0.0) * (half_h * 0.92)

        # Sum echo contributions across taps
        echo_amp_even = 0.0   # ping-pong even taps
        echo_amp_odd  = 0.0   # ping-pong odd taps
        echo_amp_full = 0.0   # non-ping-pong

        tap_lp = lp_atten
        for _tn in range(1, MAX_TAPS + 1):
            tap_centre_ms = _tn * delay_ms
            tap_centre_px = tap_centre_ms   # we're working in ms-space for gaussian
            peak = mix * 0.9 * (feedback ** (_tn - 1)) * tap_lp
            if peak < 0.005:
                break
            g = _gaussian(_xms, tap_centre_ms)
            contribution = g * peak * half_h

            if ping_pong:
                if _tn % 2 == 0:
                    echo_amp_even += contribution
                else:
                    echo_amp_odd  += contribution
            else:
                echo_amp_full += contribution

            tap_lp *= lp_atten

        # Build point lists
        if ping_pong:
            # Dry impulse goes on both halves
            pts_top_pp_even.append((_x, cy - dry_amp * 0.5 - echo_amp_even))
            pts_bot_pp_even.append((_x, cy + dry_amp * 0.5 + echo_amp_even))
            pts_top_pp_odd.append( (_x, cy - dry_amp * 0.5 - echo_amp_odd))
            pts_bot_pp_odd.append( (_x, cy + dry_amp * 0.5 + echo_amp_odd))
        else:
            pts_top.append((_x, cy - dry_amp - echo_amp_full))
            pts_bot.append((_x, cy + dry_amp + echo_amp_full))

    def _draw_envelope(top_pts, bot_pts, fill_col, line_col):
        """Draw a filled envelope from two point lists (top and bottom)."""
        if len(top_pts) < 2:
            return
        try:
            # Filled interior — TRI_STRIP interleaved top/bottom
            _fv = []
            for (_tx, _ty), (_bx, _by) in zip(top_pts, bot_pts):
                _fv += [(_tx, _ty), (_bx, _by)]
            if len(_fv) >= 4:
                _bf = batch_for_shader(shader, "TRI_STRIP", {"pos": _fv})
                shader.bind()
                shader.uniform_float("color", fill_col)
                _bf.draw(shader)
            # Top edge line
            gpu.state.line_width_set(max(1.5, ui))
            _bl = batch_for_shader(shader, "LINE_STRIP", {"pos": top_pts})
            shader.bind()
            shader.uniform_float("color", line_col)
            _bl.draw(shader)
            # Bottom edge line (mirrored)
            _bb = batch_for_shader(shader, "LINE_STRIP", {"pos": bot_pts})
            shader.bind()
            shader.uniform_float("color", line_col)
            _bb.draw(shader)
            gpu.state.line_width_set(1.0)
        except Exception:
            pass

    if ping_pong:
        centre_x = disp_x + disp_w * 0.5

        # Clip even taps to upper half, odd taps to lower half
        def _clamp_half(pts, upper):
            out = []
            for (x, y) in pts:
                if upper:
                    out.append((x, max(disp_y + 1*ui, min(cy, y))))
                else:
                    out.append((x, max(cy, min(disp_y + disp_h - 1*ui, y))))
            return out

        top_e = _clamp_half(pts_top_pp_even, True)
        bot_e = _clamp_half(pts_bot_pp_even, True)
        top_o = _clamp_half(pts_top_pp_odd,  False)
        bot_o = _clamp_half(pts_bot_pp_odd,  False)

        # Even taps (upper half) — cyan tint
        _draw_envelope(top_e, bot_e,
                       (0.04, 0.22, 0.40, 0.45),
                       (0.15, 0.55, 0.90, 0.80))
        # Odd taps (lower half) — magenta tint
        _draw_envelope(top_o, bot_o,
                       (0.28, 0.04, 0.35, 0.40),
                       (0.72, 0.20, 0.88, 0.75))

        # Centre divider
        _ppv = [(centre_x, disp_y + 2*ui), (centre_x, disp_y + disp_h - 2*ui)]
        _ppb = batch_for_shader(shader, "LINES", {"pos": _ppv})
        gpu.state.line_width_set(max(1.2, ui))
        shader.bind()
        shader.uniform_float("color", (0.75, 0.20, 0.85, 0.50))
        _ppb.draw(shader)
        gpu.state.line_width_set(1.0)
        _fs_pp = max(1, int(7 * ui))
        _draw_text("L", disp_x + 3*ui, disp_y + disp_h * 0.25 - _fs_pp*0.5,
                   _fs_pp, (0.30, 0.65, 0.95, 0.65))
        _draw_text("R", disp_x + 3*ui, disp_y + disp_h * 0.75 - _fs_pp*0.5,
                   _fs_pp, (0.72, 0.20, 0.88, 0.65))
    else:
        # Single stereo envelope — cyan fill with brighter edge
        _draw_envelope(pts_top, pts_bot,
                       (0.04, 0.22, 0.42, 0.40),
                       (0.15, 0.58, 0.92, 0.85))

    # ── Tap marker lines — thin verticals at each echo centre ────────────────
    # Shows exactly where each tap lands, colour fades with amplitude
    tap_lp2 = lp_atten
    _fs_tap = max(1, int(7 * ui))
    for _tn in range(1, MAX_TAPS + 1):
        _tap_ms = _tn * delay_ms
        if _tap_ms > VIEW_MS:
            break
        _tap_x  = disp_x + (_tap_ms / VIEW_MS) * disp_w
        _tap_amp = mix * 0.9 * (feedback ** (_tn - 1)) * tap_lp2
        if _tap_amp < 0.005:
            break
        _alpha = min(0.70, _tap_amp * 1.4)
        _tv2 = [(_tap_x, disp_y + 2*ui), (_tap_x, disp_y + disp_h - 2*ui)]
        _tb2 = batch_for_shader(shader, "LINES", {"pos": _tv2})
        gpu.state.line_width_set(max(1.0, ui * 0.7))
        shader.bind()
        if ping_pong and _tn % 2 == 0:
            shader.uniform_float("color", (0.72, 0.20, 0.88, _alpha * 0.55))
        else:
            shader.uniform_float("color", (0.15, 0.58, 0.92, _alpha * 0.55))
        _tb2.draw(shader)
        gpu.state.line_width_set(1.0)
        # Tap number label on first few taps
        if _tn <= 5:
            _draw_text(str(_tn), _tap_x + 2*ui, disp_y + 3*ui,
                       _fs_tap,
                       (0.72, 0.20, 0.88, _alpha * 0.7) if (ping_pong and _tn % 2 == 0)
                       else (0.15, 0.58, 0.92, _alpha * 0.7))
        tap_lp2 *= lp_atten

    # ── LP filter cutoff marker — amber dashed vertical ───────────────────────
    # Position maps the cutoff frequency to the x position of the tap whose
    # centre frequency would be most attenuated — simpler: place it as a
    # fraction of the display width scaled by filt_norm so it visually moves
    # with the Filter knob in an intuitive left=dark, right=bright direction.
    _fc_x = disp_x + filt_norm * disp_w * 0.88 + disp_w * 0.06
    _dy_fc = disp_y; _segs_fc = []; _tog_fc = True
    _dash_fc = 5*ui; _gap_fc = 3*ui
    while _dy_fc < disp_y + disp_h:
        _ye_fc = min(_dy_fc + (_dash_fc if _tog_fc else _gap_fc), disp_y + disp_h)
        if _tog_fc:
            _segs_fc += [(_fc_x, _dy_fc), (_fc_x, _ye_fc)]
        _dy_fc = _ye_fc; _tog_fc = not _tog_fc
    if _segs_fc:
        _bfc = batch_for_shader(shader, "LINES", {"pos": _segs_fc})
        gpu.state.line_width_set(max(1.2, ui))
        shader.bind()
        shader.uniform_float("color", (0.90, 0.60, 0.12, 0.65))
        _bfc.draw(shader)
        gpu.state.line_width_set(1.0)

    # ── Info labels ───────────────────────────────────────────────────────────
    _fs8 = max(1, int(8 * ui))
    _draw_text(f"t = {delay_str}", disp_x + 4*ui,
               disp_y + disp_h - _fs8 - 3*ui, _fs8, (0.25, 0.68, 0.95, 0.85))
    _tw_f = _text_width(filt_str, _fs8)
    _draw_text(filt_str, disp_x + disp_w - _tw_f - 4*ui,
               disp_y + disp_h - _fs8 - 3*ui, _fs8, (0.90, 0.60, 0.12, 0.80))
    _draw_text(f"fb {int(feedback * 100)}%", disp_x + 4*ui,
               disp_y + 3*ui, _fs8, (0.25, 0.68, 0.95, 0.75))

    # ── Knob strip — identical to previous version ────────────────────────────
    _N_dl  = 5
    _cw_dl = disp_w / _N_dl
    _rk_dl = knob_y + knob_h * 0.68
    _kr_dl = min(max(13*ui, _cw_dl * 0.16), 20*ui)
    _kr_dl = min(_kr_dl, knob_h * 0.42 * 0.42)

    _knob_defs_dl = [
        ("Time",     time_norm, delay_str,              (0.25, 0.72, 0.95)),
        ("Feedback", fb_norm,   f"{int(feedback*100)}%",(0.95, 0.48, 0.12)),
        ("Mix",      mix_norm,  f"{int(mix*100)}%",     (0.30, 0.85, 0.45)),
        ("Spread",   spread_n,  f"{int(spread_n*100)}%",(0.82, 0.22, 0.92)),
        ("Filter",   filt_norm, filt_str,               (0.88, 0.78, 0.55)),
    ]
    for _ki_dl, (_lbl, _val, _vstr, _col_k) in enumerate(_knob_defs_dl):
        _cx_dl = disp_x + (_ki_dl + 0.5) * _cw_dl
        _draw_knob(_cx_dl, _rk_dl, _kr_dl, _val, _col_k, _lbl, _vstr, ui)


def _draw_rack_expanded(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a fully expanded rack unit."""
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = (RACK_EXPANDED_H_MB  if rack.effect_type == "COMP_MULTI"
           else RACK_EXPANDED_H_EQ if rack.effect_type == "EQ"
           else RACK_EXPANDED_H_RV if rack.effect_type == "REVERB"
           else RACK_EXPANDED_H_NG if rack.effect_type == "NOISE_GATE"
           else RACK_EXPANDED_H_DL if rack.effect_type == "DELAY"
           else RACK_EXPANDED_H_DL if rack.effect_type == "BOOSTER"
           else RACK_EXPANDED_H_MX if rack.effect_type == "MIXDOWN"
           else RACK_EXPANDED_H) * scale

    # --- CHASSIS ---
    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = _get_shader()
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.28,0.28,0.28,1.0))
    batch.draw(shader)

    # etype needed throughout — define once here before anything uses it
    etype = rack.effect_type

    rail_h = RACK_RAIL_H * scale

    # --- BODY CONTENT — dispatch by effect type ---
    body_h = rh - rail_h
    spec_h = min(SPEC_H * scale, body_h - 50*scale)

    if etype == "COMP_MULTI":
        _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "EQ":
        _draw_eq_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "REVERB":
        _draw_reverb_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "NOISE_GATE":
        _draw_noisegate_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "DELAY":
        _draw_delay_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "MIXDOWN":
        try:
            from ui.racks.rack_mixdown import _draw_mixdown_body
            _draw_mixdown_body(rx, ry, rw, rh, rack, rack_idx, scale)
        except ImportError as e:
            _draw_rect(rx, ry, rw, rh - RACK_RAIL_H*scale, (0.02, 0.08, 0.05, 1.0))
            _draw_text("MISSING: blender_sync/ui/racks/rack_mixdown.py",
                       rx + 10*scale, ry + (rh - RACK_RAIL_H*scale)/2,
                       max(1, int(10*scale)), (0.3, 1.0, 0.5, 1.0))
            print(f"[MIXDOWN] ImportError — rack_mixdown.py not found: {e}")
    elif etype == "BOOSTER":
        try:
            from ui.racks.rack_booster import _draw_booster_body
            _draw_booster_body(rx, ry, rw, rh, rack, rack_idx, scale)
        except ImportError as e:
            _draw_rect(rx, ry, rw, rh - RACK_RAIL_H*scale, (0.15, 0.02, 0.02, 1.0))
            _draw_text("MISSING: blender_sync/ui/racks/rack_booster.py",
                       rx + 10*scale, ry + (rh - RACK_RAIL_H*scale)/2,
                       max(1, int(10*scale)), (1.0, 0.3, 0.3, 1.0))
            print(f"[BOOSTER] ImportError — rack_booster.py not found: {e}")
        except Exception as e:
            print(f"[BOOSTER] draw error: {e}")
            import traceback; traceback.print_exc()
    else:
        # Single band: 2x3 knob grid + spectrum + GR meters
        params  = EFFECT_PARAMS.get(etype, [])
        col     = (0.0, 0.65, 0.4)
        knob_r  = 18 * scale
        knob_kx  = [rx + (KNOB_START_X + c*KNOB_SPACING) * scale for c in range(3)]
        body_top = ry
        body_bot = ry + rh - RACK_RAIL_H*scale
        mid_y    = (body_top + body_bot) * 0.5
        ky0      = mid_y + knob_r + 14*scale
        ky1      = mid_y - knob_r - 14*scale

        param_order = [0,1,5, 2,3,4]
        for idx, pi in enumerate(param_order):
            col_i = idx % 3
            row_i = idx // 3
            kx    = knob_kx[col_i]
            ky    = ky0 if row_i == 0 else ky1
            if pi < len(params):
                pkey, plabel, pmin, pmax, pdef, pfmt = params[pi]
                norm   = getattr(rack, f'p{pi}', 0.0)
                actual = pmin + norm*(pmax-pmin)
                try:    val_str = pfmt.format(actual)
                except: val_str = f"{actual:.1f}"
                _draw_knob(kx, ky, knob_r, norm, col, plabel, val_str, scale)

        div_x = rx + KNOB_SECTION_W * scale
        _draw_rect(div_x, ry+4*scale, max(1.0, scale),
                   rh-RACK_RAIL_H*scale-8*scale, (0.2, 0.2, 0.2, 1.0))

        spec_x = rx + SPEC_X * scale
        spec_y = ry + (body_h - spec_h) / 2 - 5*scale
        spec_w = SPEC_W * scale
        _draw_spectrum(spec_x, spec_y, spec_w, spec_h, rack_idx, scale)

        assigned = get_rack_channels(rack)
        gr_x     = spec_x + spec_w + 16*scale
        _draw_gr_meters(gr_x, spec_y, spec_h, rack_idx, assigned, scale)

    # Channel buttons always on right
    ch_right_x = rx + rw - 100*scale
    ch_top_y   = ry + rh - RACK_RAIL_H*scale - 20*scale
    _draw_channel_buttons(ch_right_x, ch_top_y, rack, scale)
    fs_ch = max(1, int(8*scale))
    _draw_text("CHANNELS", ch_right_x + 10*scale,
               ch_top_y + 8*scale, fs_ch, (0.35,0.35,0.35,1.0))


    # --- TOP RAIL ---

    # Corner screws
    for sx2, sy2 in [(rx+14*scale, ry+rh-16*scale),
                     (rx+rw-14*scale, ry+rh-16*scale),
                     (rx+14*scale, ry+14*scale),
                     (rx+rw-14*scale, ry+14*scale)]:
        _draw_circle(sx2, sy2, 4*scale, (0.07,0.07,0.07,1.0))
        _draw_circle(sx2, sy2, 4*scale, (0.3,0.3,0.3,1.0), filled=False)
        _draw_line(sx2-3*scale, sy2, sx2+3*scale, sy2, (0.3,0.3,0.3,0.8))
        _draw_line(sx2, sy2-3*scale, sx2, sy2+3*scale, (0.3,0.3,0.3,0.8))

    # --- COLLAPSE ARROW — dedicated button, far left of rail ---
    # Clear ▲ icon in its own 28px zone so it's always visible and clickable
    col_btn_x = rx + 4*scale
    col_btn_y = ry + rh - 28*scale
    col_btn_w = 24*scale
    col_btn_h = 20*scale
    _draw_rect(col_btn_x, col_btn_y, col_btn_w, col_btn_h, (0.10, 0.10, 0.12, 1.0))
    col_bverts = [(col_btn_x, col_btn_y), (col_btn_x+col_btn_w, col_btn_y),
                  (col_btn_x+col_btn_w, col_btn_y+col_btn_h),
                  (col_btn_x, col_btn_y+col_btn_h), (col_btn_x, col_btn_y)]
    col_bb = batch_for_shader(shader, "LINE_STRIP", {"pos": col_bverts})
    shader.bind(); shader.uniform_float("color", (0.35, 0.35, 0.40, 1.0))
    col_bb.draw(shader)
    # ▲ triangle pointing up — indicates click to collapse
    ax = col_btn_x + col_btn_w * 0.5
    ay = col_btn_y + col_btn_h * 0.5
    arrow = [(ax - 5*scale, ay - 3*scale),
             (ax + 5*scale, ay - 3*scale),
             (ax,           ay + 5*scale)]
    batch = batch_for_shader(shader, "TRIS", {"pos": arrow})
    shader.uniform_float("color", (0.65, 0.65, 0.70, 1.0)); batch.draw(shader)

    # --- RACK NUMBER BADGE + EFFECT NAME ---
    # Badge starts after the collapse button — no overlap
    enames = dict(EFFECT_TYPES)
    ename  = enames.get(etype, etype)
    fs_name = max(1, int(11*scale))

    badge_label = str(rack_idx + 1)
    badge_fs    = max(1, int(13*scale))
    badge_x     = col_btn_x + col_btn_w + 4*scale   # starts after collapse button
    badge_y     = ry + rh - 28*scale
    badge_w     = max(22*scale, _text_width(badge_label, badge_fs) + 12*scale)
    badge_h     = 20*scale

    badge_open  = _reorder_open and _reorder_rack_idx == rack_idx
    badge_bg    = (0.2, 0.45, 0.75, 1.0) if badge_open else (0.12, 0.25, 0.45, 1.0)
    _draw_rect(badge_x, badge_y, badge_w, badge_h, badge_bg)
    bverts = [(badge_x, badge_y), (badge_x+badge_w, badge_y),
              (badge_x+badge_w, badge_y+badge_h),
              (badge_x, badge_y+badge_h), (badge_x, badge_y)]
    bb2 = batch_for_shader(shader, "LINE_STRIP", {"pos": bverts})
    shader.bind()
    shader.uniform_float("color", (0.35, 0.7, 1.0, 0.7))
    bb2.draw(shader)
    tw_b = _text_width(badge_label, badge_fs)
    _draw_text(badge_label, badge_x + badge_w/2 - tw_b/2,
               badge_y + badge_h/2 - badge_fs/2,
               badge_fs, (0.35, 0.7, 1.0, 1.0))
    arr_fs = max(1, int(8*scale))
    _draw_text("▾", badge_x + badge_w - 10*scale,
               badge_y + 2*scale, arr_fs, (0.35, 0.7, 1.0, 0.8))
    badge_w = badge_w + 6*scale

    _draw_text(ename.upper(),
               badge_x + badge_w,
               ry+rh-22*scale, fs_name, (0.75,0.75,0.75,1.0))

    # --- PRESET SELECTOR ---
    presets   = PRESETS.get(etype, ["Default"])
    p_idx     = rack.preset_idx % max(1, len(presets))
    p_name    = presets[p_idx]
    try:
        from ui.racks.rack_base import COMP_SINGLE_PRESET_X as _CSPX2
    except Exception:
        _CSPX2 = 380
    _pb = _CSPX2 if etype == "COMP_SINGLE" else 280
    p_box_x   = rx + _pb*scale
    p_box_w   = 160*scale
    p_box_y   = ry + rh - 26*scale
    p_box_h   = 16*scale

    lax = p_box_x - 14*scale
    lay = ry + rh - 18*scale
    la  = [(lax, lay), (lax+10*scale, lay+6*scale), (lax+10*scale, lay-6*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":la})
    shader.uniform_float("color",(0.4,0.4,0.4,1.0)); batch.draw(shader)

    _draw_rect(p_box_x, p_box_y, p_box_w, p_box_h, (0.07,0.07,0.07,1.0))
    fs_p = max(1, int(9*scale))
    tw   = _text_width(p_name, fs_p)
    _draw_text(p_name, p_box_x + p_box_w/2 - tw/2,
               p_box_y + p_box_h/2 - fs_p/2 + 1, fs_p, (0.7,0.7,0.7,1.0))

    rax = p_box_x + p_box_w + 4*scale
    ray = ry + rh - 18*scale
    ra  = [(rax+10*scale, ray), (rax, ray+6*scale), (rax, ray-6*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":ra})
    shader.uniform_float("color",(0.4,0.4,0.4,1.0)); batch.draw(shader)

    # --- DELETE BUTTON ---
    del_x = rx + rw - 26*scale
    del_y = ry + rh - 27*scale
    del_w = 18*scale
    del_h = 16*scale
    _draw_rect(del_x, del_y, del_w, del_h, (0.18, 0.04, 0.04, 1.0))
    shader2 = _get_shader()
    dv = [(del_x,del_y),(del_x+del_w,del_y),
          (del_x+del_w,del_y+del_h),(del_x,del_y+del_h),(del_x,del_y)]
    db = batch_for_shader(shader2,"LINE_STRIP",{"pos":dv})
    shader2.bind(); shader2.uniform_float("color",(0.6,0.1,0.1,1.0)); db.draw(shader2)
    fs_del = max(1, int(9*scale))
    tw_del = _text_width("X", fs_del)
    _draw_text("X", del_x+del_w/2-tw_del/2, del_y+del_h/2-fs_del/2+1,
               fs_del, (0.8, 0.15, 0.15, 1.0))

    # --- ON/BYPASS BUTTON ---
    on_x = rx + rw - 68*scale
    on_y = ry + rh - 27*scale
    on_w = 40*scale
    on_h = 16*scale
    if rack.enabled:
        _draw_rect(on_x, on_y, on_w, on_h, (0.0, 0.13, 0.0, 1.0))
        on_col = (0.0, 0.65, 0.3, 1.0)
        on_txt = "ON"
    else:
        _draw_rect(on_x, on_y, on_w, on_h, (0.13, 0.0, 0.0, 1.0))
        on_col = (0.65, 0.0, 0.0, 1.0)
        on_txt = "OFF"
    verts = [(on_x,on_y),(on_x+on_w,on_y),
             (on_x+on_w,on_y+on_h),(on_x,on_y+on_h),(on_x,on_y)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.uniform_float("color", on_col); batch.draw(shader)
    fs_on = max(1, int(9*scale))
    tw    = _text_width(on_txt, fs_on)
    _draw_text(on_txt, on_x+on_w/2-tw/2, on_y+on_h/2-fs_on/2+1,
               fs_on, on_col)


def _draw_rack_collapsed(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a collapsed rack unit — single row."""
    rw = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh = RACK_COLLAPSED_H * scale

    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = _get_shader()
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.25,0.25,0.25,1.0))
    batch.draw(shader)

    for sx2, sy2 in [(rx+12*scale, ry+rh/2),
                     (rx+rw-12*scale, ry+rh/2)]:
        _draw_circle(sx2, sy2, 3*scale, (0.07,0.07,0.07,1.0))
        _draw_circle(sx2, sy2, 3*scale, (0.28,0.28,0.28,1.0), filled=False)
        _draw_line(sx2-2*scale, sy2, sx2+2*scale, sy2, (0.28,0.28,0.28,0.8))
        _draw_line(sx2, sy2-2*scale, sx2, sy2+2*scale, (0.28,0.28,0.28,0.8))

    ax = rx + 26*scale
    ay = ry + rh/2
    arrow = [(ax-5*scale, ay+6*scale),
             (ax-5*scale, ay-6*scale),
             (ax+5*scale, ay)]
    batch = batch_for_shader(shader,"TRIS",{"pos":arrow})
    shader.uniform_float("color",(0.45,0.45,0.45,1.0)); batch.draw(shader)

    etype  = rack.effect_type
    enames = dict(EFFECT_TYPES)
    ename  = enames.get(etype, etype)
    fs     = max(1, int(11*scale))

    badge_label = str(rack_idx + 1)
    badge_fs    = max(1, int(13*scale))
    badge_x     = rx + 40*scale
    badge_y     = ry + rh/2 - badge_fs/2
    _draw_text(badge_label, badge_x, badge_y,
               badge_fs, (0.35, 0.7, 1.0, 1.0))
    badge_w     = _text_width(badge_label, badge_fs) + 6*scale

    _draw_text(ename.upper(),
               rx + 40*scale + badge_w,
               ry + rh/2 - fs/2, fs, (0.6,0.6,0.6,1.0))

    assigned = get_rack_channels(rack)
    btn_x    = rx + 250*scale
    btn_y    = ry + rh/2 - 8*scale
    btn_w    = 20*scale
    btn_h    = 16*scale
    btn_gap  = 4*scale
    fs_b     = max(1, int(9*scale))

    for i, ch_idx in enumerate(assigned):
        bx = btn_x + i*(btn_w+btn_gap)
        _draw_rect(bx, btn_y, btn_w, btn_h, (0.0, 0.15, 0.08, 1.0))
        verts = [(bx,btn_y),(bx+btn_w,btn_y),
                 (bx+btn_w,btn_y+btn_h),(bx,btn_y+btn_h),(bx,btn_y)]
        batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
        shader.uniform_float("color",(0.0,0.65,0.38,1.0)); batch.draw(shader)
        tw = _text_width(str(ch_idx+1), fs_b)
        _draw_text(str(ch_idx+1),
                   bx+btn_w/2-tw/2, btn_y+btn_h/2-fs_b/2+1,
                   fs_b, (0.0,0.8,0.5,1.0))
        led_x = bx + btn_w + 3*scale
        led_y = btn_y + btn_h/2
        is_lit = _led_states.get((rack_idx, ch_idx), False)
        led_col = (0.0, 1.0, 0.55, 1.0) if is_lit else (0.0, 0.25, 0.14, 1.0)
        _draw_circle(led_x, led_y, 4*scale, led_col)

    presets = PRESETS.get(etype, ["Default"])
    p_idx   = rack.preset_idx % max(1, len(presets))
    p_name  = presets[p_idx]
    fs_p    = max(1, int(9*scale))
    _draw_text(p_name, rx + rw/2 - _text_width(p_name, fs_p)/2,
               ry + rh/2 - fs_p/2, fs_p, (0.3,0.3,0.3,1.0))

    cdel_x = rx + rw - 28*scale
    cdel_y = ry + rh/2 - 7*scale
    cdel_w = 18*scale
    cdel_h = 14*scale
    _draw_rect(cdel_x, cdel_y, cdel_w, cdel_h, (0.18,0.04,0.04,1.0))
    shader_d = _get_shader()
    dv2 = [(cdel_x,cdel_y),(cdel_x+cdel_w,cdel_y),
           (cdel_x+cdel_w,cdel_y+cdel_h),(cdel_x,cdel_y+cdel_h),(cdel_x,cdel_y)]
    db2 = batch_for_shader(shader_d,"LINE_STRIP",{"pos":dv2})
    shader_d.bind(); shader_d.uniform_float("color",(0.5,0.1,0.1,1.0)); db2.draw(shader_d)
    fs_d2 = max(1, int(8*scale))
    tw_d2 = _text_width("X", fs_d2)
    _draw_text("X", cdel_x+cdel_w/2-tw_d2/2, cdel_y+cdel_h/2-fs_d2/2+1,
               fs_d2, (0.7,0.1,0.1,1.0))

    on_x  = rx + rw - 50*scale
    on_y  = ry + rh/2 - 7*scale
    on_w  = 28*scale
    on_h  = 14*scale
    if rack.enabled:
        _draw_rect(on_x, on_y, on_w, on_h, (0.0, 0.1, 0.0, 1.0))
        _draw_text("ON", on_x+4*scale, on_y+3*scale,
                   max(1, int(8*scale)), (0.0,0.6,0.3,1.0))
    else:
        _draw_rect(on_x, on_y, on_w, on_h, (0.1, 0.0, 0.0, 1.0))
        _draw_text("OFF", on_x+2*scale, on_y+3*scale,
                   max(1, int(8*scale)), (0.5,0.0,0.0,1.0))


def _draw_add_rack_button(rx, ry, scale, rack_width=None):
    """Draw the + ADD RACK EFFECT button."""
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = 28*scale
    fs  = max(1, int(10*scale))

    _draw_rect(rx, ry, rw, rh, (0.07, 0.07, 0.07, 1.0))

    # Dashed border
    shader = _get_shader()
    segs   = 60
    verts  = []
    for i in range(segs+1):
        t   = i/segs
        if i % 2 == 0:
            px = rx + t*rw
            py = ry
            verts.append((px, py))
        else:
            if verts:
                px2 = rx + t*rw
                verts.append((px2, ry))
    # Simple rect border instead of true dashed (GPU line dashing is complex)
    border_verts = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":border_verts})
    shader.bind()
    shader.uniform_float("color",(0.2,0.2,0.2,1.0))
    batch.draw(shader)

    label = "+  ADD RACK EFFECT"
    tw    = _text_width(label, fs)
    _draw_text(label, rx + rw/2 - tw/2,
               ry + rh/2 - fs/2, fs, (0.3,0.3,0.3,1.0))


def _draw_add_popup(px, py, scale):
    """Draw the effect type selector popup."""
    popup_w  = 200*scale
    title_h  = 24*scale
    popup_h  = (len(EFFECT_TYPES) * 28 + 10) * scale + title_h
    popup_x  = px
    popup_y  = py

    # Shadow
    _draw_rect(popup_x+3*scale, popup_y-3*scale,
               popup_w, popup_h, (0.0,0.0,0.0,0.5))
    # Background
    _draw_rect(popup_x, popup_y, popup_w, popup_h, (0.15,0.15,0.15,1.0))
    shader = _get_shader()
    verts  = [(popup_x, popup_y),
              (popup_x+popup_w, popup_y),
              (popup_x+popup_w, popup_y+popup_h),
              (popup_x, popup_y+popup_h),
              (popup_x, popup_y)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.35,0.35,0.35,1.0))
    batch.draw(shader)

    # Title bar at top of popup
    _draw_rect(popup_x, popup_y+popup_h-title_h,
               popup_w, title_h, (0.18,0.18,0.18,1.0))
    fs_t = max(1, int(9*scale))
    _draw_text("SELECT EFFECT", popup_x+8*scale,
               popup_y+popup_h-title_h+6*scale, fs_t, (0.6,0.6,0.6,1.0))
    _draw_rect(popup_x, popup_y+popup_h-title_h,
               popup_w, max(0.5,scale*0.5), (0.3,0.3,0.3,1.0))

    # Effect type buttons — start below title bar
    fs_b = max(1, int(10*scale))
    for i, (etype, ename) in enumerate(EFFECT_TYPES):
        by = popup_y + popup_h - title_h - (i+1)*28*scale - 4*scale
        bh = 24*scale
        # Hover highlight — simple alternating for now
        bg = (0.18,0.18,0.18,1.0) if i % 2 == 0 else (0.14,0.14,0.14,1.0)
        _draw_rect(popup_x+2*scale, by, popup_w-4*scale, bh, bg)
        _draw_text(ename, popup_x+12*scale, by+bh/2-fs_b/2,
                   fs_b, (0.75,0.75,0.75,1.0))


# ---------------------------------------------------------------------------
# Main draw entry point — called by Loader.py draw_callback_px
# ---------------------------------------------------------------------------
def draw_racks(region_width, region_height, scroll_x, scroll_y, ui_scale):
    """
    Draw all rack units below the fader section.
    Called from Loader.py draw_callback_px after drawing faders.
    """
    # rack_base owns the maintained chassis/shell renderers.
    # Import inside function to avoid circular imports at module load time.
    # RACK_COLLAPSED_H is imported here too — Racks.py has its own stale
    # module-level copy of this constant (used only by the AI-rack section
    # below) that drifted out of sync with rack_base's value. Shadowing it
    # locally with rack_base's live value keeps the space reserved for a
    # collapsed row in this stacking loop matching what _draw_rack_collapsed
    # (rack_base.py, the version that actually runs) draws — otherwise the
    # row renders taller than its reserved slot and paints over the rack
    # above it.
    from ui.racks.rack_base import (
        _draw_rack_expanded, _draw_rack_collapsed,
        _draw_add_rack_button, _draw_add_popup,
        RACK_COLLAPSED_H,
    )
    global _UI_SCALE
    _UI_SCALE = ui_scale

    scene = bpy.context.scene
    if not scene: return

    all_racks = getattr(scene, "pb_racks", [])

    # Calculate base Y position — same anchor as before
    from ui.mixer.channel_strip import strip_bottom_y as _strip_bot
    base_y = region_height - 150*ui_scale - scroll_y

    # Number of fader groups — always in multiples of 9
    num_tracks = len(getattr(scene, "pb_sync_tracks", []))
    num_groups = max(1, (num_tracks + 8) // 9)

    # Each group is 9 strips wide: 9 × 135 - 15 = 1200px unscaled
    GROUP_STRIP_COUNT = 9
    group_w_unscaled  = GROUP_STRIP_COUNT * 135 - 15   # 1200px
    group_w           = group_w_unscaled * ui_scale
    # Gap between groups (matches the strip stride gap: 135-120=15px)
    group_gap         = 15 * ui_scale

    for g in range(num_groups):
        # X position of this group's rack section
        rack_x = (30 + g * (group_w_unscaled + 15)) * ui_scale + scroll_x

        # Racks belonging to this group
        group_racks = [(i, r) for i, r in enumerate(all_racks)
                       if getattr(r, 'group_idx', 0) == g]

        # send_h for this group's racks
        try:
            from Loader import _send_section_height
            send_h = _send_section_height(len(group_racks), ui_scale)
        except Exception:
            send_h = 0.0

        rack_top_y  = _strip_bot(base_y, len(group_racks), ui_scale) - RACK_MARGIN_TOP*ui_scale

        cur_y = rack_top_y

        for i, rack in group_racks:
            if rack.collapsed:
                rh = RACK_COLLAPSED_H * ui_scale
            elif rack.effect_type == "COMP_MULTI":
                rh = RACK_EXPANDED_H_MB * ui_scale
            elif rack.effect_type == "EQ":
                rh = RACK_EXPANDED_H_EQ * ui_scale
            elif rack.effect_type == "REVERB":
                rh = RACK_EXPANDED_H_RV * ui_scale
            elif rack.effect_type == "NOISE_GATE":
                rh = RACK_EXPANDED_H_NG * ui_scale
            elif rack.effect_type == "DELAY":
                rh = RACK_EXPANDED_H_DL * ui_scale
            elif rack.effect_type == "BOOSTER":
                rh = RACK_EXPANDED_H_DL * ui_scale
            elif rack.effect_type == "MIXDOWN":
                rh = RACK_EXPANDED_H_MX * ui_scale
            else:
                rh = RACK_EXPANDED_H * ui_scale

            # Cull if completely off screen vertically
            if cur_y - rh > region_height or cur_y < -rh:
                cur_y -= rh + RACK_GAP*ui_scale
                continue

            try:
                if rack.collapsed:
                    _draw_rack_collapsed(rack_x, cur_y - rh, rack, i,
                                         ui_scale, group_w_unscaled)
                else:
                    _draw_rack_expanded(rack_x, cur_y - rh, rack, i,
                                        ui_scale, group_w_unscaled)
            except Exception as e:
                import traceback
                print(f"[RACKS] draw error rack {i}: {e}")
                traceback.print_exc()

            cur_y -= rh + RACK_GAP*ui_scale

        # Add rack button for this group
        _draw_add_rack_button(rack_x, cur_y - 28*ui_scale, ui_scale,
                              group_w_unscaled)

        # AI processing section — drawn below DSP racks for this group
        ai_section_top_y = cur_y - 28*ui_scale
        try:
            draw_ai_racks(rack_x, ai_section_top_y, ui_scale, group_w_unscaled, g)
        except Exception as e:
            print(f"[AI RACKS] draw error: {e}")

    # Popup (drawn on top of everything — not group-specific)
    if _popup_open:
        _draw_add_popup(_popup_x, _popup_y, ui_scale)

    # AI rack popup (drawn on top of everything — not group-specific)
    if _ai_popup_open:
        _draw_ai_add_popup(_ai_popup_x, _ai_popup_y, ui_scale)

    # Reorder dropdown (drawn on top of everything)
    if _reorder_open and _reorder_rack_idx >= 0:
        import bpy as _bpy_ro
        scene_ro = _bpy_ro.context.scene
        racks_ro = getattr(scene_ro, "pb_racks", [])
        n_racks  = len(racks_ro)
        if n_racks > 1:
            row_h = 22 * ui_scale
            pad   = 6  * ui_scale
            dw    = 100 * ui_scale
            dh    = row_h * (n_racks + 1) + pad * 2  # +1 for title row

            # Anchor below the badge that was clicked
            dx = _reorder_x
            dy = _reorder_y - dh

            shader2 = _get_shader()

            # Shadow
            _draw_rect(dx+3*ui_scale, dy-3*ui_scale, dw, dh,
                       (0.0, 0.0, 0.0, 0.5))
            # Background
            _draw_rect(dx, dy, dw, dh, (0.13, 0.13, 0.13, 0.97))
            # Border
            bv = [(dx,dy),(dx+dw,dy),(dx+dw,dy+dh),
                  (dx,dy+dh),(dx,dy)]
            bb = batch_for_shader(shader2, "LINE_STRIP", {"pos": bv})
            shader2.bind()
            shader2.uniform_float("color", (0.35, 0.7, 1.0, 0.6))
            bb.draw(shader2)

            # Title row
            fs_t = max(1, int(8*ui_scale))
            _draw_rect(dx, dy+dh-row_h, dw, row_h, (0.1, 0.2, 0.35, 1.0))
            _draw_text("MOVE TO POSITION",
                       dx + pad, dy + dh - row_h + row_h*0.25,
                       fs_t, (0.4, 0.7, 1.0, 0.9))

            # One row per position
            fs_r = max(1, int(11*ui_scale))
            for pos in range(n_racks):
                row_y   = dy + dh - row_h*(pos + 2)
                is_cur  = (pos == _reorder_rack_idx)
                if is_cur:
                    _draw_rect(dx + pad*0.5, row_y,
                               dw - pad, row_h,
                               (0.2, 0.45, 0.75, 0.35))
                lbl = f"  {pos + 1}  {'←' if is_cur else ''}"
                col = (0.35, 0.7, 1.0, 1.0) if is_cur else (0.75, 0.75, 0.75, 1.0)
                _draw_text(lbl, dx + pad,
                           row_y + row_h * 0.2,
                           fs_r, col)


# ---------------------------------------------------------------------------
# Hit testing — returns (rack_idx, zone, sub_idx) or None
# Called from Loader.py modal operator LEFTMOUSE handler
# ---------------------------------------------------------------------------
def rack_knob_hit_test(rx, ry, region_height, scroll_x, scroll_y, ui_scale):
    """Check if a rack knob was clicked.
    Returns (rack_idx, param_idx) or None.
    Only checks expanded racks — collapsed racks have no knobs.
    Group-aware: mirrors draw_racks geometry exactly for multi-group layouts.
    """
    import math
    try:
        from ui.mixer.channel_strip import strip_bottom_y as _strip_bot
    except Exception:
        return None
    # Local shadow of the stale module-level RACK_COLLAPSED_H — see the
    # comment in draw_racks() for why this must match rack_base's value.
    from ui.racks.rack_base import RACK_COLLAPSED_H

    scene = bpy.context.scene
    if not scene: return None
    racks = getattr(scene, "pb_racks", [])

    base_y = region_height - 150*ui_scale - scroll_y

    # Group geometry — mirrors draw_racks exactly
    GROUP_STRIP_COUNT = 9
    group_w_unscaled  = GROUP_STRIP_COUNT * 135 - 15   # 1200px
    rw                = group_w_unscaled * ui_scale

    num_tracks = len(getattr(scene, "pb_sync_tracks", []))
    num_groups = max(1, (num_tracks + 8) // 9)

    knob_r = 20 * ui_scale

    for g in range(num_groups):
        rack_x = (30 + g * (group_w_unscaled + 15)) * ui_scale + scroll_x

        # Skip if click is outside this group's X range
        if not (rack_x <= rx <= rack_x + rw):
            continue

        group_racks = [(i, r) for i, r in enumerate(racks)
                       if getattr(r, 'group_idx', 0) == g]

        try:
            send_h = _send_section_height(len(group_racks), ui_scale)
        except Exception:
            send_h = 0.0

        rack_top_y  = _strip_bot(base_y, len(group_racks), ui_scale) - RACK_MARGIN_TOP*ui_scale

        cur_y = rack_top_y

        for i, rack in group_racks:
            if rack.collapsed:
                rh = RACK_COLLAPSED_H * ui_scale
                cur_y -= rh + RACK_GAP * ui_scale
                continue

            rh = (RACK_EXPANDED_H_MB  if rack.effect_type == "COMP_MULTI"
                  else RACK_EXPANDED_H_EQ if rack.effect_type == "EQ"
                  else RACK_EXPANDED_H_RV if rack.effect_type == "REVERB"
                  else RACK_EXPANDED_H_NG if rack.effect_type == "NOISE_GATE"
                  else RACK_EXPANDED_H_DL if rack.effect_type == "DELAY"
                  else RACK_EXPANDED_H_DL if rack.effect_type == "BOOSTER"
                  else RACK_EXPANDED_H) * ui_scale
            rack_y = cur_y - rh

            if rack.effect_type == "COMP_MULTI":
                # Multiband: hit test gain faders and 3x2 knob grid
                # Must exactly mirror _draw_multiband_body geometry
                body_h       = rh - RACK_RAIL_H*ui_scale
                fader_zone_h = body_h * 0.52
                ch_btn_w     = 108*ui_scale
                side_margin  = ch_btn_w / 2
                content_w    = rw - ch_btn_w - side_margin
                band_w       = content_w / 4
                content_x    = rack_x + side_margin / 2
                fader_area_y = rack_y + 4*ui_scale
                fader_area_h = fader_zone_h - 8*ui_scale
                label_h      = 26*ui_scale
                ctrl_y       = fader_area_y + label_h
                ctrl_h       = fader_area_h - label_h
                fader_strip_w = 38*ui_scale
                knob_area_w  = band_w - fader_strip_w - 8*ui_scale
                knob_r_mb    = max(min(knob_area_w*0.15, 16*ui_scale), 10*ui_scale)
                knob_label_h = 22*ui_scale
                knob_gap     = 6*ui_scale

                for band in range(4):
                    bx = content_x + band * band_w

                    # Gain fader (p16-p19) — left strip
                    fdr_x = bx + 6*ui_scale
                    fdr_w = 10*ui_scale
                    fdr_h = ctrl_h - 26*ui_scale
                    fdr_y = ctrl_y + 2*ui_scale
                    if fdr_x <= rx <= fdr_x+fdr_w and fdr_y <= ry <= fdr_y+fdr_h:
                        return (i, band + 16)

                    # 3x2 knob grid — must match draw exactly
                    div_x         = bx + fader_strip_w
                    knob_base_x   = div_x + 4*ui_scale
                    knob_col_gap3 = knob_area_w / 3
                    kx0 = knob_base_x + knob_col_gap3 * 0.5
                    kx1 = knob_base_x + knob_col_gap3 * 1.5
                    kx2 = knob_base_x + knob_col_gap3 * 2.5
                    ky1 = ctrl_y + 4*ui_scale + knob_label_h + knob_r_mb   # bottom row
                    ky0 = ky1 + knob_r_mb + knob_gap + knob_label_h + knob_r_mb  # top row

                    kr_mb = knob_r_mb + 4*ui_scale  # hit radius with tolerance
                    if math.dist((rx,ry),(kx0,ky0)) < kr_mb: return (i, band)
                    if math.dist((rx,ry),(kx1,ky0)) < kr_mb: return (i, band+4)
                    if math.dist((rx,ry),(kx2,ky0)) < kr_mb: return (i, band+20)
                    if math.dist((rx,ry),(kx0,ky1)) < kr_mb: return (i, band+8)
                    if math.dist((rx,ry),(kx1,ky1)) < kr_mb: return (i, band+12)
                    if math.dist((rx,ry),(kx2,ky1)) < kr_mb: return (i, band+16)

            elif rack.effect_type == "EQ":
                # 7-band layout: p0-p6=gain, p7-p13=freq, p14-p20=Q
                EQ7_BANDS_HT = [
                    ("L","low_shelf",80.0,0.7), ("1","peak",250.0,1.0),
                    ("2","peak",700.0,1.0),     ("3","peak",2000.0,1.0),
                    ("4","peak",5000.0,1.0),    ("5","peak",10000.0,1.0),
                    ("H","high_shelf",16000.0,0.7),
                ]
                N_BANDS_HT   = 7
                body_h_eq    = rh - RACK_RAIL_H * ui_scale
                ch_btn_w_eq  = 108 * ui_scale
                margin_l_eq  = 42 * ui_scale
                margin_r_eq  = ch_btn_w_eq + 8 * ui_scale
                disp_x_eq    = rack_x + margin_l_eq
                disp_w_eq    = rw - margin_l_eq - margin_r_eq
                knob_y_eq    = rack_y + 4 * ui_scale
                knob_h_eq    = body_h_eq * 0.38 - 8 * ui_scale
                col_w_eq     = disp_w_eq / N_BANDS_HT
                kr_gain_eq   = min(max(13*ui_scale, col_w_eq*0.16), 20*ui_scale)
                kr_small_eq  = min(max( 9*ui_scale, col_w_eq*0.11), 14*ui_scale)
                kz_top_eq    = knob_y_eq + knob_h_eq
                row_lbl_eq   = kz_top_eq  -  8 * ui_scale
                row_gain_eq  = row_lbl_eq  - 16 * ui_scale - kr_gain_eq
                row_freq_eq  = row_gain_eq - kr_gain_eq - 10 * ui_scale - kr_small_eq
                row_q_eq     = row_freq_eq - kr_small_eq - 8  * ui_scale - kr_small_eq
                tol_eq       = 6 * ui_scale
                for bi in range(N_BANDS_HT):
                    col_cx_eq = disp_x_eq + (bi + 0.5) * col_w_eq
                    if math.dist((rx, ry), (col_cx_eq, row_gain_eq)) < kr_gain_eq + tol_eq:
                        return (i, bi)
                    if math.dist((rx, ry), (col_cx_eq, row_freq_eq)) < kr_small_eq + tol_eq:
                        return (i, bi + 7)
                    _, bftype_ht, _, _ = EQ7_BANDS_HT[bi]
                    if bftype_ht == "peak":
                        if math.dist((rx, ry), (col_cx_eq, row_q_eq)) < kr_small_eq + tol_eq:
                            return (i, bi + 14)

            elif rack.effect_type == "REVERB":
                # 5 knobs: p0=Room, p1=Damp, p2=Wet, p3=Pre-dly, p4=Width
                body_h_rv   = rh - RACK_RAIL_H * ui_scale
                ch_btn_w_rv = 108 * ui_scale
                margin_l_rv = 42 * ui_scale
                margin_r_rv = ch_btn_w_rv + 8 * ui_scale
                disp_x_rv   = rack_x + margin_l_rv
                disp_w_rv   = rw - margin_l_rv - margin_r_rv
                N_KNOBS_RV  = 5
                col_w_rv    = disp_w_rv / N_KNOBS_RV
                knob_h_rv   = body_h_rv * 0.42 - 4 * ui_scale
                knob_y_rv   = rack_y + 2 * ui_scale
                row_slot_rv = knob_h_rv / 3.0
                row_knob_rv = knob_y_rv + knob_h_rv - row_slot_rv * 1.3
                kr_rv       = min(max(13*ui_scale, col_w_rv*0.16), 20*ui_scale)
                kr_rv       = min(kr_rv, row_slot_rv * 0.42)
                for ki in range(N_KNOBS_RV):
                    cx_rv = disp_x_rv + (ki + 0.5) * col_w_rv
                    if math.dist((rx, ry), (cx_rv, row_knob_rv)) < kr_rv + 8*ui_scale:
                        return (i, ki)

            elif rack.effect_type == "NOISE_GATE":
                # 5 knobs: p0=Threshold, p1=Attack, p2=Hold, p3=Release, p4=Range
                body_h_ng   = rh - RACK_RAIL_H * ui_scale
                disp_x_ng   = rack_x + 42 * ui_scale
                disp_w_ng   = rw - 42*ui_scale - 108*ui_scale - 8*ui_scale
                col_w_ng    = disp_w_ng / 5
                knob_h_ng   = body_h_ng * 0.42 - 4 * ui_scale
                knob_y_ng   = rack_y + 2 * ui_scale
                row_slot_ng = knob_h_ng / 3.0
                row_knob_ng = knob_y_ng + knob_h_ng - row_slot_ng * 1.3
                kr_ng       = min(max(13*ui_scale, col_w_ng*0.16), 20*ui_scale)
                kr_ng       = min(kr_ng, row_slot_ng * 0.42)
                for ki in range(5):
                    cx_ng = disp_x_ng + (ki + 0.5) * col_w_ng
                    if math.dist((rx, ry), (cx_ng, row_knob_ng)) < kr_ng + 8*ui_scale:
                        return (i, ki)

            elif rack.effect_type == "DELAY":
                # 5 knobs: p0=Time, p1=Feedback, p2=Mix, p3=Spread, p4=Filter
                body_h_dl   = rh - RACK_RAIL_H * ui_scale
                disp_x_dl   = rack_x + 42 * ui_scale
                disp_w_dl   = rw - 42*ui_scale - 108*ui_scale - 8*ui_scale
                col_w_dl    = disp_w_dl / 5
                knob_h_dl   = body_h_dl * 0.42 - 4 * ui_scale
                knob_y_dl   = rack_y + 2 * ui_scale
                row_slot_dl = knob_h_dl / 3.0
                row_knob_dl = knob_y_dl + knob_h_dl - row_slot_dl * 1.3
                kr_dl       = min(max(13*ui_scale, col_w_dl*0.16), 20*ui_scale)
                kr_dl       = min(kr_dl, row_slot_dl * 0.42)
                for ki in range(5):
                    cx_dl = disp_x_dl + (ki + 0.5) * col_w_dl
                    if math.dist((rx, ry), (cx_dl, row_knob_dl)) < kr_dl + 8*ui_scale:
                        return (i, ki)

            elif rack.effect_type == "BOOSTER":
                body_h_bo = rh - RACK_RAIL_H * ui_scale
                left_w_bo = rw * 0.25
                knob_cx   = rack_x + left_w_bo * 0.5
                knob_cy   = rack_y + body_h_bo * 0.52
                knob_r_bo = min(left_w_bo * 0.30, body_h_bo * 0.35)
                if math.dist((rx, ry), (knob_cx, knob_cy)) < knob_r_bo + 6*ui_scale:
                    return (i, 0)

            else:
                # Single band compressor — 2x3 knob grid
                # param_order = [0,1,5, 2,3,4] → Thr,Ratio,Knee / Atk,Rel,Makeup
                body_top  = rack_y
                body_bot  = rack_y + rh - RACK_RAIL_H*ui_scale
                mid_y     = (body_top + body_bot) * 0.5
                ky0       = mid_y + knob_r + 14*ui_scale   # top row
                ky1       = mid_y - knob_r - 14*ui_scale   # bottom row
                kxs       = [rack_x + (KNOB_START_X + c*KNOB_SPACING)*ui_scale
                             for c in range(3)]
                param_order = [0, 1, 5,  2, 3, 4]
                for idx, pi in enumerate(param_order):
                    col_i = idx % 3
                    row_i = idx // 3
                    kx    = kxs[col_i]
                    ky    = ky0 if row_i == 0 else ky1
                    if math.dist((rx, ry), (kx, ky)) < knob_r + 4*ui_scale:
                        return (i, pi)

            cur_y -= rh + RACK_GAP * ui_scale

    return None


def hit_test(rx, ry, region_height, scroll_x, scroll_y, ui_scale):
    """
    Return what was clicked.
    Returns dict with 'zone' key, or None if nothing hit.
    Zones: 'collapse', 'preset_left', 'preset_right', 'on_off',
           'channel_btn', 'add_rack', 'popup_effect'
    """
    global _popup_open

    try:
        from ui.mixer.channel_strip import strip_bottom_y as _strip_bot
    except Exception:
        return None
    # Local shadow of the stale module-level RACK_COLLAPSED_H — see the
    # comment in draw_racks() for why this must match rack_base's value.
    from ui.racks.rack_base import RACK_COLLAPSED_H

    scene = bpy.context.scene
    if not scene: return None
    racks = getattr(scene, "pb_racks", [])

    # Reorder dropdown check — must happen before everything else
    if _reorder_open and _reorder_rack_idx >= 0 and len(racks) > 1:
        row_h = 22 * ui_scale
        pad   = 6  * ui_scale
        dw    = 100 * ui_scale
        dh    = row_h * (len(racks) + 1) + pad * 2
        dx    = _reorder_x
        dy    = _reorder_y - dh
        if dx <= rx <= dx+dw and dy <= ry <= dy+dh:
            for pos in range(len(racks)):
                row_y = dy + dh - row_h*(pos + 2)
                if row_y <= ry <= row_y + row_h:
                    return {'zone': 'reorder_select',
                            'rack_idx': _reorder_rack_idx,
                            'target_pos': pos}
            return {'zone': 'reorder_dismiss'}
        else:
            return {'zone': 'reorder_dismiss'}

    # Check popup first (drawn on top — not group-specific)
    if _popup_open:
        pw  = 200*ui_scale
        ph  = (len(EFFECT_TYPES)*28 + 10)*ui_scale + 24*ui_scale
        if _popup_x <= rx <= _popup_x+pw and _popup_y <= ry <= _popup_y+ph:
            title_h = 24*ui_scale
            for idx, (etype, _) in enumerate(EFFECT_TYPES):
                by = _popup_y + ph - title_h - (idx+1)*28*ui_scale - 4*ui_scale
                bh = 24*ui_scale
                if by <= ry <= by+bh:
                    return {'zone': 'popup_effect', 'effect_type': etype,
                            'group_idx': _popup_group_idx}
            return {'zone': 'popup_dismiss'}
        else:
            return {'zone': 'popup_dismiss'}

    base_y = region_height - 150*ui_scale - scroll_y

    # Group geometry constants
    GROUP_STRIP_COUNT = 9
    group_w_unscaled  = GROUP_STRIP_COUNT * 135 - 15   # 1200px
    group_w           = group_w_unscaled * ui_scale

    num_tracks = len(getattr(scene, "pb_sync_tracks", []))
    num_groups = max(1, (num_tracks + 8) // 9)

    for g in range(num_groups):
        rack_x = (30 + g * (group_w_unscaled + 15)) * ui_scale + scroll_x
        rw     = group_w

        # Skip this group if click is outside its X range
        if not (rack_x <= rx <= rack_x + rw):
            continue

        # Group racks
        group_racks = [(i, r) for i, r in enumerate(racks)
                       if getattr(r, 'group_idx', 0) == g]

        try:
            from Loader import _send_section_height
            send_h = _send_section_height(len(group_racks), ui_scale)
        except Exception:
            send_h = 0.0

        rack_top_y  = _strip_bot(base_y, len(group_racks), ui_scale) - RACK_MARGIN_TOP*ui_scale
        cur_y       = rack_top_y

        for i, rack in group_racks:
            if rack.collapsed:
                rh = RACK_COLLAPSED_H * ui_scale
            elif rack.effect_type == "COMP_MULTI":
                rh = RACK_EXPANDED_H_MB * ui_scale
            elif rack.effect_type == "EQ":
                rh = RACK_EXPANDED_H_EQ * ui_scale
            elif rack.effect_type == "REVERB":
                rh = RACK_EXPANDED_H_RV * ui_scale
            elif rack.effect_type == "NOISE_GATE":
                rh = RACK_EXPANDED_H_NG * ui_scale
            elif rack.effect_type == "DELAY":
                rh = RACK_EXPANDED_H_DL * ui_scale
            elif rack.effect_type == "BOOSTER":
                rh = RACK_EXPANDED_H_DL * ui_scale
            elif rack.effect_type == "MIXDOWN":
                rh = RACK_EXPANDED_H_MX * ui_scale
            else:
                rh = RACK_EXPANDED_H * ui_scale

            rack_y = cur_y - rh

            if rack_x <= rx <= rack_x+rw and rack_y <= ry <= rack_y+rh:
                # Hit in this rack — determine zone (all existing zone logic unchanged)
                rail_top = rack_y + rh - RACK_RAIL_H*ui_scale

                # Collapse button
                col_btn_x2 = rack_x + 4*ui_scale
                col_btn_y2 = rack_y + rh - 28*ui_scale
                col_btn_w2 = 24*ui_scale
                col_btn_h2 = 20*ui_scale
                if (col_btn_x2 <= rx <= col_btn_x2 + col_btn_w2 and
                        col_btn_y2 <= ry <= col_btn_y2 + col_btn_h2):
                    return {'zone': 'collapse', 'rack_idx': i}

                # Delegate full zone detection to the existing helper
                # by re-using the existing code path below
                # We set rack_x/rw/cur_y and fall through to the zone block
                return _hit_test_rack_zones(
                    rx, ry, i, rack, rack_x, rack_y, rw, rh, ui_scale, g)

            cur_y -= rh + RACK_GAP*ui_scale

        # Add rack button for this group
        add_y = cur_y - 28*ui_scale
        if add_y <= ry <= add_y + 28*ui_scale:
            return {'zone': 'add_rack', 'click_x': rx, 'click_y': ry,
                    'group_idx': g}

    # NOTE: AI rack section hits are NOT handled here.
    # interaction.py calls hit_test_ai_racks() directly.
    return None

def _hit_test_rack_zones(rx, ry, i, rack, rack_x, rack_y, rw, rh, ui_scale, group_idx=0):
    """Determine the exact zone within a rack that was clicked.
    Called from hit_test once a click is confirmed to be inside a rack's bounding box.
    Returns a hit dict or {'zone': 'rack_body', 'rack_idx': i} as fallback.
    """
    rail_top = rack_y + rh - RACK_RAIL_H*ui_scale

    # Collapse button — dedicated 24px button at far left of rail
    col_btn_x2 = rack_x + 4*ui_scale
    col_btn_y2 = rack_y + rh - 28*ui_scale
    col_btn_w2 = 24*ui_scale
    col_btn_h2 = 20*ui_scale
    if (col_btn_x2 <= rx <= col_btn_x2 + col_btn_w2 and
            col_btn_y2 <= ry <= col_btn_y2 + col_btn_h2):
        return {'zone': 'collapse', 'rack_idx': i}

    # Delete / ON-OFF hitboxes — derived from PNG blit geometry
    try:
        from ui.racks.rack_base import (RACK_BTN_W as _RBW, RACK_BTN_H as _RBH,
                                        RACK_BTN_X_OFFSET as _RBXO, RACK_BTN_Y_OFFSET as _RBYO,
                                        RACK_BTN_ONOFF_SPLIT as _RBSP)
    except Exception:
        _RBW, _RBH, _RBXO, _RBYO, _RBSP = 60.0, 22.8, 0.0, 0.0, 0.60
    _rbw = _RBW * ui_scale
    _rbh = _RBH * ui_scale
    _rbx = rack_x + rw - _rbw + _RBXO * ui_scale
    _rby = rack_y + rh - (RACK_RAIL_H * ui_scale + _rbh) / 2 + _RBYO * ui_scale
    # Close X = right portion of PNG
    del_x = _rbx + _rbw * _RBSP
    del_y = _rby
    if del_x <= rx <= _rbx + _rbw and del_y <= ry <= _rby + _rbh:
        return {'zone': 'delete_rack', 'rack_idx': i}

    # Rack number badge
    badge_x2 = rack_x + 4*ui_scale + 24*ui_scale + 4*ui_scale
    badge_y2 = rack_y + rh - 28*ui_scale
    badge_w2 = 38*ui_scale
    badge_h2 = 20*ui_scale
    if (badge_x2 <= rx <= badge_x2 + badge_w2 and
            badge_y2 <= ry <= badge_y2 + badge_h2):
        return {'zone': 'rack_badge', 'rack_idx': i,
                'bx': badge_x2, 'by': badge_y2+badge_h2}

    # ON/OFF = left portion of PNG
    on_x = _rbx
    on_y = _rby
    if on_x <= rx <= _rbx + _rbw * _RBSP and on_y <= ry <= _rby + _rbh:
        return {'zone': 'on_off', 'rack_idx': i}

    # Delete button (collapsed) — right portion of PNG, centred on collapsed rail
    if rack.collapsed:
        try:
            from ui.racks.rack_base import (RACK_BTN_W as _RBW2, RACK_BTN_H as _RBH2,
                                            RACK_BTN_X_OFFSET as _RBXO2, RACK_BTN_Y_OFFSET as _RBYO2,
                                            RACK_BTN_ONOFF_SPLIT as _RBSP2)
        except Exception:
            _RBW2, _RBH2, _RBXO2, _RBYO2, _RBSP2 = 60.0, 22.8, 0.0, 0.0, 0.60
        _rbw2 = _RBW2 * ui_scale
        _rbh2 = _RBH2 * ui_scale
        _rbx2 = rack_x + rw - _rbw2 + _RBXO2 * ui_scale
        _rby2 = rack_y + rh/2 - _rbh2/2 + _RBYO2 * ui_scale
        cdel_x = _rbx2 + _rbw2 * _RBSP2
        cdel_y = _rby2
        if cdel_x <= rx <= _rbx2 + _rbw2 and cdel_y <= ry <= _rby2 + _rbh2:
            return {'zone': 'delete_rack', 'rack_idx': i}

    # Preset arrows — box is centred in the rack's top bar (see
    # rack_base._draw_rack_expanded). Hitboxes must use the exact same
    # formula as the draw call or they drift out of sync with the visible
    # box whenever the centering offset changes.
    try:
        from ui.racks.rack_base import PRESET_BOX_CENTER_X_OFFSET as _PBXO
    except Exception:
        _PBXO = 0.0
    p_box_w = 160*ui_scale
    p_box_x = rack_x + (rw - p_box_w) / 2.0 + _PBXO*ui_scale
    if rack_y+rh-30*ui_scale <= ry <= rack_y+rh-12*ui_scale:
        if p_box_x-18*ui_scale <= rx <= p_box_x:
            return {'zone': 'preset_left', 'rack_idx': i}
        if p_box_x+p_box_w <= rx <= p_box_x+p_box_w+18*ui_scale:
            return {'zone': 'preset_right', 'rack_idx': i}

    # Channel buttons (expanded only) — ch0-ch8 are LOCAL to the group
    if not rack.collapsed:
        ch_right_x = rack_x + rw - 100*ui_scale
        ch_top_y   = rack_y + rh - RACK_RAIL_H*ui_scale - 20*ui_scale
        btn_s      = CH_BTN_SIZE * ui_scale
        btn_gap    = 4*ui_scale
        # Always exactly 9 buttons — local indices 0-8 for this group
        for local_idx in range(9):
            row = local_idx // 3
            col = local_idx % 3
            bx  = ch_right_x + col*(btn_s+btn_gap)
            by  = ch_top_y - row*(btn_s+btn_gap) - btn_s
            if bx <= rx <= bx+btn_s and by <= ry <= by+btn_s:
                return {'zone': 'channel_btn',
                        'rack_idx': i, 'ch_idx': local_idx,
                        'group_idx': group_idx}

    # Collapsed channel buttons
    if rack.collapsed:
        assigned = get_rack_channels(rack)
        btn_x    = rack_x + 250*ui_scale
        btn_y    = rack_y + rh/2 - 8*ui_scale
        btn_w    = 20*ui_scale
        btn_h    = 16*ui_scale
        btn_gap  = 4*ui_scale
        offset   = group_idx * 9
        for j, abs_ch in enumerate(assigned):
            bx = btn_x + j*(btn_w+btn_gap)
            if bx <= rx <= bx+btn_w and btn_y <= ry <= btn_y+btn_h:
                return {'zone': 'channel_btn',
                        'rack_idx': i, 'ch_idx': abs_ch - offset,
                        'group_idx': group_idx}

    # BOOSTER rack hit zones
    if not rack.collapsed and rack.effect_type == "BOOSTER":
        try:
            from ui.racks.rack_booster import (
                BST_APPLY_X, BST_APPLY_Y, BST_APPLY_W, BST_APPLY_H,
                BST_LIM_W, BST_LIM_X, BST_LIM_Y, BST_LIM_H,
                BST_GRID_X, BST_GRID_Y, BST_GRID_W, BST_GRID_H,
                BST_BTN_X, BST_BTN_Y, BST_BTN_W, BST_BTN_H,
                BST_STEPPER_X, BST_STEPPER_Y, BST_STEPPER_W, BST_STEPPER_H,
            )
        except Exception:
            (BST_APPLY_X, BST_APPLY_Y, BST_APPLY_W, BST_APPLY_H,
             BST_LIM_W, BST_LIM_X, BST_LIM_Y, BST_LIM_H,
             BST_GRID_X, BST_GRID_Y, BST_GRID_W, BST_GRID_H,
             BST_STEPPER_X, BST_STEPPER_Y, BST_STEPPER_W, BST_STEPPER_H) = (
                0,0,0,0, 88,0,0,0, 0,0,0,0, 0,0,0,0)
            BST_BTN_X = BST_BTN_Y = BST_BTN_W = BST_BTN_H = [0]*6

        s           = ui_scale
        body_h_bo   = rh - RACK_RAIL_H * s
        body_bot_bo = rack_y
        left_w_bo   = rw * 0.25
        centre_w_bo = rw * 0.50
        centre_x_bo = rack_x + left_w_bo
        _cx  = centre_x_bo
        _cw  = centre_w_bo
        _pad = 8 * s

        # Apply + Limiter — mirrors draw code exactly
        apply_x = _cx + _pad             + BST_APPLY_X * s
        apply_y = body_bot_bo + body_h_bo*0.06 + BST_APPLY_Y * s
        ctrl_h  = body_h_bo * 0.10       + BST_APPLY_H * s
        lim_w   = BST_LIM_W * s
        apply_w = _cw - _pad*2           + BST_APPLY_W * s - lim_w - 6*s
        lim_x   = apply_x + apply_w + 6*s + BST_LIM_X * s
        lim_y   = apply_y                  + BST_LIM_Y * s
        lim_h   = ctrl_h                   + BST_LIM_H * s

        if (apply_x <= rx <= apply_x + apply_w and
                apply_y <= ry <= apply_y + ctrl_h):
            return {'zone': 'booster_apply', 'rack_idx': i}
        if (lim_x <= rx <= lim_x + lim_w and
                lim_y <= ry <= lim_y + lim_h):
            return {'zone': 'booster_limiter', 'rack_idx': i}

        # Preset grid — mirrors draw code exactly
        grid_x   = _cx + _pad             + BST_GRID_X * s
        grid_w   = _cw - _pad*2           + BST_GRID_W * s
        grid_bot = body_bot_bo + body_h_bo*0.28 + BST_GRID_Y * s
        grid_h   = body_h_bo * 0.22       + BST_GRID_H * s
        _bw_base = (grid_w - 2*3*s) / 3
        _bh_base = (grid_h - 3*s)   / 2
        presets_bo = [6/40, 12/40, 18/40, 24/40, 30/40, 1.0]
        for pi, norm in enumerate(presets_bo):
            col_i = pi % 3
            row_i = pi // 3
            btn_w = _bw_base + BST_BTN_W[pi] * s
            btn_h = _bh_base + BST_BTN_H[pi] * s
            bx    = grid_x + col_i * (_bw_base + 3*s) + BST_BTN_X[pi] * s
            by    = grid_bot + row_i * (_bh_base + 3*s) + BST_BTN_Y[pi] * s
            if bx <= rx <= bx + btn_w and by <= ry <= by + btn_h:
                return {'zone': 'booster_preset', 'rack_idx': i,
                        'boost_norm': norm}

        # Stepper — mirrors draw code exactly
        right_x_bo = rack_x + left_w_bo + centre_w_bo
        _rpad      = 6 * s
        _rw_base   = left_w_bo - _rpad * 2
        rx2_bo     = right_x_bo + _rpad  + BST_STEPPER_X * s
        rw2_bo     = _rw_base             + BST_STEPPER_W * s
        stepper_h  = body_h_bo * 0.08    + BST_STEPPER_H * s
        stepper_y  = apply_y + ctrl_h + 6*s + BST_STEPPER_Y * s
        if (rx2_bo <= rx <= rx2_bo + rw2_bo and
                stepper_y <= ry <= stepper_y + stepper_h):
            third = rw2_bo / 3.0
            if rx <= rx2_bo + third:
                return {'zone': 'booster_ch_minus', 'rack_idx': i}
            elif rx >= rx2_bo + rw2_bo - third:
                return {'zone': 'booster_ch_plus', 'rack_idx': i}

    # MIXDOWN rack hit zones
    if not rack.collapsed and rack.effect_type == "MIXDOWN":
        s         = ui_scale
        pad_mx    = 10 * s
        CH_W      = 100 * s
        C_W       = 200 * s
        A_W       = 160 * s
        A_X       = rack_x
        B_X       = rack_x + A_W
        C_X_abs   = rack_x + rw - CH_W - C_W
        a_xi      = A_X + pad_mx;  a_wi = A_W - 2*pad_mx
        b_xi      = B_X + pad_mx;  b_wi = C_X_abs - B_X - 2*pad_mx
        body_top  = rack_y + rh - RACK_RAIL_H * s
        tog_h     = 20 * s
        row_h     = 20 * s
        btn_h2    = 26 * s

        mode_btn_h = 24 * s
        mix_y  = body_top - pad_mx - 17*s - mode_btn_h
        bake_y = mix_y - mode_btn_h - 4*s
        if a_xi <= rx <= a_xi + a_wi:
            if mix_y <= ry <= mix_y + mode_btn_h:
                return {'zone': 'mixdown_set', 'rack_idx': i,
                        'param': 'p0', 'value': 0.0}
            if bake_y <= ry <= bake_y + mode_btn_h:
                return {'zone': 'mixdown_set', 'rack_idx': i,
                        'param': 'p0', 'value': 1.0}

        is_bake_ht   = rack.p0 > 0.5
        b_top = body_top - pad_mx

        def bnxt(h):
            nonlocal b_top; b_top -= h; return b_top

        bnxt(4*s); bnxt(13*s)
        path_h2   = 22*s; path_y2 = bnxt(path_h2 + 3*s)
        browse_w2 = 60*s
        path_bw2  = b_wi - browse_w2 - 4*s
        br_x2     = b_xi + path_bw2 + 4*s

        if path_y2 <= ry <= path_y2 + path_h2:
            if br_x2 <= rx <= br_x2 + browse_w2:
                return {'zone': 'mixdown_browse', 'rack_idx': i}
            if b_xi <= rx <= b_xi + b_wi:
                return {'zone': 'mixdown_browse', 'rack_idx': i}

        try:
            from ui.racks.rack_mixdown import (
                MX_WAV_X,  MX_WAV_Y,  MX_WAV_W,  MX_WAV_H,
                MX_FLAC_X, MX_FLAC_Y, MX_FLAC_W, MX_FLAC_H,
                MX_SR44_X, MX_SR44_Y, MX_SR44_W, MX_SR44_H,
                MX_SR48_X, MX_SR48_Y, MX_SR48_W, MX_SR48_H,
                MX_SR96_X, MX_SR96_Y, MX_SR96_W, MX_SR96_H,
                MX_BD16_X, MX_BD16_Y, MX_BD16_W, MX_BD16_H,
                MX_BD24_X, MX_BD24_Y, MX_BD24_W, MX_BD24_H,
                MX_BD32_X, MX_BD32_Y, MX_BD32_W, MX_BD32_H,
                MX_FULL_X, MX_FULL_Y, MX_FULL_W, MX_FULL_H,
                MX_CUST_X, MX_CUST_Y, MX_CUST_W, MX_CUST_H,
                MX_FSTART_X, MX_FSTART_Y, MX_FSTART_W, MX_FSTART_H,
                MX_FEND_X,   MX_FEND_Y,   MX_FEND_W,   MX_FEND_H,
            )
        except Exception:
            MX_WAV_X=0;MX_WAV_Y=95;MX_WAV_W=135;MX_WAV_H=20
            MX_FLAC_X=143;MX_FLAC_Y=95;MX_FLAC_W=135;MX_FLAC_H=20
            MX_SR44_X=290;MX_SR44_Y=95;MX_SR44_W=90;MX_SR44_H=20
            MX_SR48_X=388;MX_SR48_Y=95;MX_SR48_W=90;MX_SR48_H=20
            MX_SR96_X=486;MX_SR96_Y=95;MX_SR96_W=90;MX_SR96_H=20
            MX_BD16_X=590;MX_BD16_Y=95;MX_BD16_W=90;MX_BD16_H=20
            MX_BD24_X=688;MX_BD24_Y=95;MX_BD24_W=90;MX_BD24_H=20
            MX_BD32_X=786;MX_BD32_Y=95;MX_BD32_W=90;MX_BD32_H=20
            MX_FULL_X=0;MX_FULL_Y=136;MX_FULL_W=180;MX_FULL_H=20
            MX_CUST_X=188;MX_CUST_Y=136;MX_CUST_W=180;MX_CUST_H=20
            MX_FSTART_X=390;MX_FSTART_Y=112;MX_FSTART_W=195;MX_FSTART_H=20
            MX_FEND_X=590;MX_FEND_Y=112;MX_FEND_W=195;MX_FEND_H=20

        def _mxb(cx, cy, cw, ch):
            """Convert MX constants to screen rect using body_top."""
            return b_xi+cx*s, body_top-cy*s, cw*s, ch*s

        # Format buttons
        _wx, _wy, _ww, _wh = _mxb(MX_WAV_X,  MX_WAV_Y,  MX_WAV_W,  MX_WAV_H)
        _fx, _fy, _fw, _fh = _mxb(MX_FLAC_X, MX_FLAC_Y, MX_FLAC_W, MX_FLAC_H)
        if _wx <= rx <= _wx+_ww and _wy <= ry <= _wy+_wh:
            return {'zone': 'mixdown_set', 'rack_idx': i, 'param': 'p1', 'value': 0.0}
        if _fx <= rx <= _fx+_fw and _fy <= ry <= _fy+_fh:
            return {'zone': 'mixdown_set', 'rack_idx': i, 'param': 'p1', 'value': 1.0}

        # Sample rate buttons
        for _val, _cons in [(0.0,(MX_SR44_X,MX_SR44_Y,MX_SR44_W,MX_SR44_H)),
                             (0.5,(MX_SR48_X,MX_SR48_Y,MX_SR48_W,MX_SR48_H)),
                             (1.0,(MX_SR96_X,MX_SR96_Y,MX_SR96_W,MX_SR96_H))]:
            _bx,_by,_bw,_bh = _mxb(*_cons)
            if _bx <= rx <= _bx+_bw and _by <= ry <= _by+_bh:
                return {'zone': 'mixdown_set', 'rack_idx': i, 'param': 'p2', 'value': _val}

        # Bit depth buttons
        for _val, _cons in [(0.0,(MX_BD16_X,MX_BD16_Y,MX_BD16_W,MX_BD16_H)),
                             (0.5,(MX_BD24_X,MX_BD24_Y,MX_BD24_W,MX_BD24_H)),
                             (1.0,(MX_BD32_X,MX_BD32_Y,MX_BD32_W,MX_BD32_H))]:
            _bx,_by,_bw,_bh = _mxb(*_cons)
            if _bx <= rx <= _bx+_bw and _by <= ry <= _by+_bh:
                return {'zone': 'mixdown_set', 'rack_idx': i, 'param': 'p3', 'value': _val}

        # Range buttons
        _ftx,_fty,_ftw,_fth = _mxb(MX_FULL_X, MX_FULL_Y, MX_FULL_W, MX_FULL_H)
        _ctx,_cty,_ctw,_cth = _mxb(MX_CUST_X, MX_CUST_Y, MX_CUST_W, MX_CUST_H)
        if _ftx <= rx <= _ftx+_ftw and _fty <= ry <= _fty+_fth:
            return {'zone': 'mixdown_set', 'rack_idx': i, 'param': 'p4', 'value': 0.0}
        if _ctx <= rx <= _ctx+_ctw and _cty <= ry <= _cty+_cth:
            return {'zone': 'mixdown_set', 'rack_idx': i, 'param': 'p4', 'value': 1.0}

        # Frame boxes handled below with text input focus zones
        is_custom_ht = rack.p4 > 0.5

        # Frame info text row (always present)
        bnxt(2*s); bnxt(8*s + 3*s)

        # Place on channel + after render — import MX constants
        try:
            from ui.racks.rack_mixdown import (
                MX_PLACE_X, MX_PLACE_Y, MX_PLACE_W, MX_PLACE_H,
                MX_AFTER_X, MX_AFTER_Y, MX_AFTER_W, MX_AFTER_H,
                MX_STEPPER_MINUS_X, MX_STEPPER_MINUS_W,
                MX_STEPPER_PLUS_X, MX_STEPPER_PLUS_W,
            )
        except Exception:
            MX_PLACE_X=-24;MX_PLACE_Y=189;MX_PLACE_W=390;MX_PLACE_H=20
            MX_AFTER_X=335;MX_AFTER_Y=189;MX_AFTER_W=390;MX_AFTER_H=20
            MX_STEPPER_MINUS_X=0.0;   MX_STEPPER_MINUS_W=136.5
            MX_STEPPER_PLUS_X=253.5;  MX_STEPPER_PLUS_W=136.5

        _plx = b_xi+MX_PLACE_X*s;  _ply = body_top-MX_PLACE_Y*s
        _plw = MX_PLACE_W*s;        _plh = MX_PLACE_H*s
        _afx = b_xi+MX_AFTER_X*s;  _afy = body_top-MX_AFTER_Y*s
        _afw = MX_AFTER_W*s;        _afh = MX_AFTER_H*s

        # Place on channel stepper
        if _plx <= rx <= _plx+_plw and _ply <= ry <= _ply+_plh:
            if not is_bake_ht:
                minus_lo = _plx + MX_STEPPER_MINUS_X*s
                minus_hi = minus_lo + MX_STEPPER_MINUS_W*s
                plus_lo  = _plx + MX_STEPPER_PLUS_X*s
                plus_hi  = plus_lo + MX_STEPPER_PLUS_W*s
                if minus_lo <= rx <= minus_hi:
                    return {'zone': 'mixdown_place_ch_minus', 'rack_idx': i}
                elif plus_lo <= rx <= plus_hi:
                    return {'zone': 'mixdown_place_ch_plus', 'rack_idx': i}
            else:
                cur = rack.p7
                return {'zone': 'mixdown_set', 'rack_idx': i,
                        'param': 'p7', 'value': 0.0 if cur > 0.5 else 1.0}

        # After render stepper (mix only)
        if not is_bake_ht:
            if _afx <= rx <= _afx+_afw and _afy <= ry <= _afy+_afh:
                minus_lo = _afx + MX_STEPPER_MINUS_X*s
                minus_hi = minus_lo + MX_STEPPER_MINUS_W*s
                plus_lo  = _afx + MX_STEPPER_PLUS_X*s
                plus_hi  = plus_lo + MX_STEPPER_PLUS_W*s
                cur_val = rack.p7
                if minus_lo <= rx <= minus_hi:
                    nxt = (0.0 if cur_val > 0.75 else 0.5 if cur_val < 0.25 else 1.0)
                elif plus_lo <= rx <= plus_hi:
                    nxt = (1.0 if cur_val < 0.25 else 0.0 if cur_val > 0.75 else 1.0)
                else:
                    nxt = cur_val
                return {'zone': 'mixdown_set', 'rack_idx': i,
                        'param': 'p7', 'value': nxt}

        # Frame boxes — click to focus for text input (only when custom frames active)
        if is_custom_ht:
            _fsx2,_fsy2,_fsw2,_fsh2 = b_xi+MX_FSTART_X*s, body_top-MX_FSTART_Y*s, MX_FSTART_W*s, MX_FSTART_H*s
            _fex2,_fey2,_few2,_feh2 = b_xi+MX_FEND_X*s,   body_top-MX_FEND_Y*s,   MX_FEND_W*s,   MX_FEND_H*s
            if _fsx2 <= rx <= _fsx2+_fsw2 and _fsy2 <= ry <= _fsy2+_fsh2:
                try:
                    from ui.racks.rack_mixdown import _frame_from_norm as _ffn
                    _cur_start = _ffn(rack.p5)
                except Exception:
                    _cur_start = int(rack.p5)
                return {'zone': 'mixdown_frame_focus', 'rack_idx': i, 'param': 'p5', 'current': _cur_start}
            if _fex2 <= rx <= _fex2+_few2 and _fey2 <= ry <= _fey2+_feh2:
                try:
                    from ui.racks.rack_mixdown import _frame_from_norm as _ffn
                    _cur_end = _ffn(rack.p6)
                except Exception:
                    _cur_end = int(rack.p6)
                return {'zone': 'mixdown_frame_focus', 'rack_idx': i, 'param': 'p6', 'current': _cur_end}
            # Click elsewhere while focused → commit
            try:
                import ui.mixer.interaction as _inter
                atf = _inter._active_text_field
                if atf and atf.get('mx_frame') and atf.get('rack_idx') == i:
                    return {'zone': 'mixdown_frame_commit', 'rack_idx': i}
            except Exception:
                pass

        # Render button
        try:
            from ui.racks.rack_mixdown import MX_RENDER_X, MX_RENDER_Y, MX_RENDER_W, MX_RENDER_H
        except Exception:
            MX_RENDER_X=-44; MX_RENDER_Y=252; MX_RENDER_W=790; MX_RENDER_H=26
        _rnx = b_xi+MX_RENDER_X*s;  _rny = body_top-MX_RENDER_Y*s
        _rnw = MX_RENDER_W*s;        _rnh = MX_RENDER_H*s
        if _rnx <= rx <= _rnx+_rnw and _rny <= ry <= _rny+_rnh:
            return {'zone': 'mixdown_render', 'rack_idx': i}

    return {'zone': 'rack_body', 'rack_idx': i}




# ---------------------------------------------------------------------------
# Handle a click result from hit_test
# ---------------------------------------------------------------------------
def _trigger_reprocess(rack_idx, rack, context):
    """Reprocess all channels affected by a rack change.
    Handles: preset change, ON/OFF toggle, channel assign/deassign.
    With the Hijacker engine, just rewires effect slots — no restart needed.
    """
    try:
        from core.audio import _hj_wire_effects, _pb_rebuild_eq
        import bpy as _bpy
        scene = _bpy.context.scene
        if not scene: return

        assigned = get_rack_channels(rack)

        # Rewire effects for assigned channels (new settings take effect ~5ms)
        for ch in assigned:
            _pb_rebuild_eq(ch)

        # Also rewire channels that are active but no longer assigned
        # so they revert to clean audio without this rack's DSP
        try:
            from core.engine import get_engine as _get_eng
            _eng = _get_eng()
            if _eng:
                _hj = _eng.get_engine()
                if _hj:
                    for ch in range(32):
                        if ch not in assigned and _hj.get_meter_rms(ch) > 0:
                            _pb_rebuild_eq(ch)
        except Exception:
            pass

    except Exception as e:
        print(f"[RACKS] reprocess failed: {e}")
        import traceback; traceback.print_exc()


def handle_click(hit, context):
    """Process a hit_test result. Returns True if redraw needed."""
    global _popup_open, _popup_x, _popup_y
    global _reorder_open, _reorder_rack_idx, _reorder_x, _reorder_y

    if hit is None:
        if _popup_open:
            _popup_open = False
            return True
        return False

    zone = hit.get('zone')

    if zone == 'popup_dismiss':
        _popup_open = False
        return True

    if zone == 'popup_effect':
        _popup_open = False
        etype = hit['effect_type']
        g     = hit.get('group_idx', 0)
        rack  = context.scene.pb_racks.add()
        rack.effect_type = etype
        rack.collapsed   = False
        rack.group_idx   = g
        init_rack_defaults(rack)
        print(f"[RACKS] added {etype} rack (group {g})")
        return True

    if zone == 'add_rack':
        global _popup_group_idx
        # Open popup — store click position and which group was clicked
        _popup_open      = True
        _popup_group_idx = hit.get('group_idx', 0)
        click_y = hit.get('click_y', 200.0)
        click_x = hit.get('click_x', 200.0)
        popup_h = (len(EFFECT_TYPES) * 28 + 10) * _UI_SCALE + 24 * _UI_SCALE
        _popup_x = click_x - 100*_UI_SCALE
        _popup_y = click_y - popup_h - 4*_UI_SCALE
        return True

    if zone == 'collapse':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            racks[i].collapsed = not racks[i].collapsed
            print(f"[RACKS] rack {i} collapsed={racks[i].collapsed}")
        return True

    if zone == 'delete_rack':
        i     = hit['rack_idx']
        racks = context.scene.pb_racks
        if i < len(racks):
            racks.remove(i)
            print(f"[RACKS] deleted rack {i}")
        return True

    if zone == 'on_off':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            racks[i].enabled = not racks[i].enabled
            state = 'ON' if racks[i].enabled else 'BYPASSED'
            print(f"[RACKS] rack {i} {state} — reprocessing")
            _trigger_reprocess(i, racks[i], context)
        return True

    if zone == 'booster_apply':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            rack = racks[i]
            if rack.ai_status != 'PROCESSING':
                from core.booster import process_booster
                process_booster(i, context)
        return True

    # ── Mixdown rack interactions ─────────────────────────────────────────
    if zone == 'mixdown_render':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            rack = racks[i]
            try:
                from ui.racks.rack_mixdown import _start_render, _mx_state
                if not _mx_state['running']:
                    _start_render(i, rack, context.scene)
            except Exception as e:
                print(f"[MIXDOWN] start_render error: {e}")
        return True

    if zone == 'mixdown_frame_focus':
        i     = hit['rack_idx']
        param = hit['param']
        cur   = hit.get('current', 1)
        try:
            import ui.mixer.interaction as _inter
            from ui.racks.rack_mixdown import _norm_from_frame
            racks = getattr(context.scene, "pb_racks", [])
            def _commit(text, _i=i, _param=param):
                if text.isdigit() and _i < len(racks):
                    frame_num = max(1, int(text))
                    norm_val  = _norm_from_frame(frame_num)
                    setattr(racks[_i], _param, norm_val)
            _inter._active_text_field = {
                'mx_frame':  True,
                'text':      str(cur),
                'cursor':    len(str(cur)),
                'param':     param,
                'rack_idx':  i,
                'commit_fn': _commit,
            }
        except Exception as e:
            print(f"[MIXDOWN] frame_focus error: {e}")
        return True

    if zone == 'mixdown_frame_commit':
        i = hit['rack_idx']
        try:
            import ui.mixer.interaction as _inter
            atf = _inter._active_text_field
            if atf and atf.get('mx_frame') and atf.get('rack_idx') == i:
                commit_fn = atf.get('commit_fn')
                if commit_fn:
                    commit_fn(atf.get('text', ''))
                _inter._active_text_field = None
        except Exception as e:
            print(f"[MIXDOWN] frame_commit error: {e}")
        return True

    if zone == 'mixdown_place_ch_minus':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            racks[i].mixdown_place_ch = max(0, racks[i].mixdown_place_ch - 1)
        return True

    if zone == 'mixdown_place_ch_plus':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            racks[i].mixdown_place_ch = min(32, racks[i].mixdown_place_ch + 1)
        return True
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            param  = hit['param']
            values = hit['values']   # list of float values to cycle through
            cur    = getattr(racks[i], param, 0.0)
            # Find next value in cycle
            dists  = [abs(cur - v) for v in values]
            cur_i  = dists.index(min(dists))
            nxt    = values[(cur_i + 1) % len(values)]
            setattr(racks[i], param, nxt)
        return True

    if zone == 'mixdown_set':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            setattr(racks[i], hit['param'], hit['value'])
        return True

    if zone == 'mixdown_browse':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            is_bake = racks[i].p0 > 0.5
            try:
                if is_bake:
                    bpy.ops.vse.mixdown_browse_folder('INVOKE_DEFAULT',
                                                      rack_idx=i)
                else:
                    bpy.ops.vse.mixdown_save_as('INVOKE_DEFAULT',
                                                rack_idx=i)
            except Exception as e:
                print(f"[MIXDOWN] browse error: {e}")
        return True

    if zone == 'booster_limiter':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            cur = getattr(racks[i], 'p1', 1.0)
            racks[i].p1 = 0.0 if cur > 0.5 else 1.0
        return True

    if zone == 'booster_preset':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            racks[i].p0 = hit['boost_norm']
            if racks[i].ai_status == 'DONE':
                racks[i].ai_status = 'READY'
        return True

    if zone == 'booster_ch_minus':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            cur = int(getattr(racks[i], 'p2', 0))
            racks[i].p2 = float(max(0, cur - 1))
        return True

    if zone == 'booster_ch_plus':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            cur = int(getattr(racks[i], 'p2', 0))
            racks[i].p2 = float(min(32, cur + 1))
        return True

    if zone == 'rack_badge':
        i = hit['rack_idx']
        if _reorder_open and _reorder_rack_idx == i:
            # Already open for this rack — close it
            _reorder_open = False
            _reorder_rack_idx = -1
        else:
            _reorder_open     = True
            _reorder_rack_idx = i
            _reorder_x        = hit['bx']
            _reorder_y        = hit['by']
        return True

    if zone == 'reorder_dismiss':
        _reorder_open     = False
        _reorder_rack_idx = -1
        return True

    if zone == 'reorder_select':
        _reorder_open = False
        i          = hit['rack_idx']
        target_pos = hit['target_pos']
        racks      = getattr(context.scene, "pb_racks", [])
        if i != target_pos and 0 <= i < len(racks) and 0 <= target_pos < len(racks):
            # Build reordered list by moving rack i to target_pos
            # Remove from current position and insert at target
            indices = list(range(len(racks)))
            indices.pop(i)
            indices.insert(target_pos, i)

            # Snapshot all rack data before modifying
            def snap(r):
                return {
                    'effect_type': r.effect_type,
                    'enabled':     r.enabled,
                    'collapsed':   r.collapsed,
                    'preset_idx':  r.preset_idx,
                    'params': {f'p{j}': getattr(r, f'p{j}', 0.0)
                               for j in range(24)},
                    'channels': {f'ch{j}': getattr(r, f'ch{j}', False)
                                 for j in range(9)},
                }
            snapshots = [snap(racks[k]) for k in range(len(racks))]

            # Write reordered data back
            for new_i, old_i in enumerate(indices):
                r  = racks[new_i]
                s  = snapshots[old_i]
                r.effect_type = s['effect_type']
                r.enabled     = s['enabled']
                r.collapsed   = s['collapsed']
                r.preset_idx  = s['preset_idx']
                for attr, val in s['params'].items():
                    setattr(r, attr, val)
                for attr, val in s['channels'].items():
                    setattr(r, attr, val)

            _reorder_rack_idx = -1
            print(f"[RACKS] rack reordered: was {i+1} → now position {target_pos+1}")
        return True

    if zone == 'preset_left':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            rack    = racks[i]
            presets = PRESETS.get(rack.effect_type, ["Default"])
            rack.preset_idx = (rack.preset_idx - 1) % len(presets)
            _load_preset(rack, rack.preset_idx)
            print(f"[RACKS] preset → {presets[rack.preset_idx]}")
            _trigger_reprocess(i, rack, context)
        return True

    if zone == 'preset_right':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            rack    = racks[i]
            presets = PRESETS.get(rack.effect_type, ["Default"])
            rack.preset_idx = (rack.preset_idx + 1) % len(presets)
            _load_preset(rack, rack.preset_idx)
            print(f"[RACKS] preset → {presets[rack.preset_idx]}")
            _trigger_reprocess(i, rack, context)
        return True

    if zone == 'channel_btn':
        i        = hit['rack_idx']
        ch_idx   = hit['ch_idx']   # LOCAL index 0-8 within the group
        racks    = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            attr  = f'ch{ch_idx}'
            rack  = racks[i]
            setattr(rack, attr, not getattr(rack, attr, False))
            state = 'assigned' if getattr(rack, attr) else 'removed'
            abs_ch = getattr(rack, 'group_idx', 0) * 9 + ch_idx
            print(f"[RACKS] ch{abs_ch+1} {state} from rack {i} — reprocessing")
            # Reset BOOSTER status when a channel is assigned after NO_CHANNEL
            if (getattr(rack, 'effect_type', '') == 'BOOSTER'
                    and state == 'assigned'
                    and getattr(rack, 'ai_status', '') == 'NO_CHANNEL'):
                rack.ai_status = 'READY'
            _trigger_reprocess(i, rack, context)
        return True

    return False


# ---------------------------------------------------------------------------
# LED pulse update — called by meter timer in Loader.py
# ---------------------------------------------------------------------------
def update_led_states(is_playing):
    """Toggle LEDs for channels where compression is active."""
    global _led_states
    if not is_playing:
        _led_states.clear()
        return
    scene = bpy.context.scene
    if not scene: return
    racks = getattr(scene, "pb_racks", [])
    for i, rack in enumerate(racks):
        if not rack.enabled: continue
        assigned = get_rack_channels(rack)
        for ch_idx in assigned:
            gr = _gr_levels.get(i, {}).get(ch_idx, 0.0)
            # Lit when actively compressing (GR > 5%)
            _led_states[(i, ch_idx)] = (gr > 0.05 and is_playing)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register_racks():
    bpy.utils.register_class(PB_RackSettings)
    bpy.types.Scene.pb_racks = bpy.props.CollectionProperty(
        type=PB_RackSettings)
    # Register mixdown file browser operators
    _register_mixdown_operators()
    print("[RACKS] registered")
    register_ai_racks()


def _register_mixdown_operators():
    """Register file browser operators for the mixdown rack."""
    import bpy

    class VSE_OT_MixdownSaveAs(bpy.types.Operator):
        """Open save-as file browser for mixdown output path."""
        bl_idname   = "vse.mixdown_save_as"
        bl_label    = "Save Mixdown As"
        rack_idx: bpy.props.IntProperty(default=0)
        filepath: bpy.props.StringProperty(subtype='FILE_PATH', default="")
        filter_glob: bpy.props.StringProperty(
            default="*.wav;*.flac", options={'HIDDEN'})

        def invoke(self, context, event):
            racks = getattr(context.scene, "pb_racks", [])
            if self.rack_idx < len(racks):
                rack = racks[self.rack_idx]
                cur  = getattr(rack, 'mixdown_output_path', '')
                if cur:
                    self.filepath = cur
                else:
                    import os
                    fmt   = 'flac' if rack.p1 > 0.5 else 'wav'
                    base  = os.path.splitext(os.path.basename(
                                bpy.data.filepath))[0] if bpy.data.filepath \
                                else 'untitled'
                    self.filepath = os.path.join(
                        os.path.dirname(bpy.data.filepath) if bpy.data.filepath
                        else bpy.app.tempdir,
                        f"{base}_mixdown.{fmt}")
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}

        def execute(self, context):
            racks = getattr(context.scene, "pb_racks", [])
            if self.rack_idx < len(racks):
                racks[self.rack_idx].mixdown_output_path = self.filepath
                print(f"[MIXDOWN] output path set: {self.filepath}")
            return {'FINISHED'}

    class VSE_OT_MixdownBrowseFolder(bpy.types.Operator):
        """Open folder browser for per-channel bake output directory."""
        bl_idname   = "vse.mixdown_browse_folder"
        bl_label    = "Choose Bake Output Folder"
        rack_idx: bpy.props.IntProperty(default=0)
        directory: bpy.props.StringProperty(subtype='DIR_PATH', default="")

        def invoke(self, context, event):
            racks = getattr(context.scene, "pb_racks", [])
            if self.rack_idx < len(racks):
                cur = getattr(racks[self.rack_idx], 'mixdown_output_path', '')
                if cur:
                    import os
                    self.directory = cur if os.path.isdir(cur) \
                                     else os.path.dirname(cur)
                else:
                    import os
                    self.directory = (os.path.dirname(bpy.data.filepath)
                                      if bpy.data.filepath else bpy.app.tempdir)
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}

        def execute(self, context):
            racks = getattr(context.scene, "pb_racks", [])
            if self.rack_idx < len(racks):
                racks[self.rack_idx].mixdown_output_path = self.directory
                print(f"[MIXDOWN] bake folder set: {self.directory}")
            return {'FINISHED'}

    for cls in [VSE_OT_MixdownSaveAs, VSE_OT_MixdownBrowseFolder]:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
        bpy.utils.register_class(cls)


def unregister_racks():
    try:
        bpy.utils.unregister_class(PB_RackSettings)
        del bpy.types.Scene.pb_racks
    except Exception:
        pass
    unregister_ai_racks()
    print("[RACKS] unregistered")


# ---------------------------------------------------------------------------
# AI rack draw helpers
# ---------------------------------------------------------------------------
def _get_ai_rack_height(rack, scale):
    """Return the pixel height of an AI rack at given scale."""
    if rack.collapsed:
        return RACK_COLLAPSED_H * scale
    heights = {
        "WHISPER":       280,
        "DEMUCS":        300,
        "PIPER_TTS":     320,
        "RVC":           360,
        "RESEMBLE":      340,
    }
    return heights.get(rack.ai_type, 300) * scale


def _draw_ai_channel_buttons(rx, ry, rw, rh, rack, scale):
    """Draw channel assignment buttons on the AI rack rail (right side).
    Always shows exactly 9 buttons — the 9 channels belonging to this rack's group.
    Labels show absolute VSE channel numbers (e.g. 10-18 for group 1).
    """
    try:
        from ui.mixer.draw_utils import (
            draw_rect  as _draw_rect,
            draw_text  as _draw_text,
            text_width as _text_width,
        )
    except ImportError:
        from ui.racks.rack_base import _draw_rect, _draw_text, _text_width

    btn_s = CH_BTN_SIZE * scale
    gap   = 4 * scale
    fs    = max(1, int(10 * scale))

    # Always exactly 9 buttons for this group; absolute label = group offset + local + 1
    group_off   = getattr(rack, 'group_idx', 0) * 9
    num_buttons = 9

    ch_area_x = rx + rw - 100*scale
    ch_area_y = ry + rh - RACK_RAIL_H*scale - 20*scale

    shader = _get_shader()
    for col_idx in range(num_buttons):
        row = col_idx // 3
        col = col_idx % 3
        bx  = ch_area_x + col * (btn_s + gap)
        by  = ch_area_y - row * (btn_s + gap) - btn_s
        attr     = f'ch{col_idx}'
        assigned = getattr(rack, attr, False)
        bg = (0.0, 0.18, 0.10, 1.0) if assigned else (0.07, 0.07, 0.07, 1.0)
        bc = (0.0, 0.75, 0.45, 1.0) if assigned else (0.2, 0.2, 0.2, 1.0)
        _draw_rect(bx, by, btn_s, btn_s, bg)
        verts = [(bx,by),(bx+btn_s,by),(bx+btn_s,by+btn_s),(bx,by+btn_s),(bx,by)]
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
        shader.bind(); shader.uniform_float("color", bc); batch.draw(shader)
        label = str(group_off + col_idx + 1)   # absolute VSE channel number
        tw    = _text_width(label, fs)
        _draw_text(label, bx+btn_s/2-tw/2, by+btn_s/2-fs/2, fs, bc)


def _draw_ai_rack_collapsed(rx, ry, rw, rh, rack, ai_idx, scale):
    """Draw AI rack in collapsed single-row form."""
    try:
        from ui.mixer.draw_utils import (
            draw_rect   as _draw_rect,
            draw_text   as _draw_text,
            text_width  as _text_width,
            draw_circle as _draw_circle,
            draw_line   as _draw_line,
        )
    except ImportError:
        from ui.racks.rack_base import (
            _draw_rect, _draw_text, _text_width, _draw_circle, _draw_line,
        )

    HAL_BG     = (0.05, 0.04, 0.04, 1.0)
    HAL_BORDER = (0.20, 0.08, 0.08, 1.0)
    HAL_TEXT   = (0.80, 0.22, 0.14, 1.0)

    cy = ry + rh / 2
    _draw_rect(rx, ry, rw, rh, HAL_BG)
    shader = _get_shader()
    bv = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": bv})
    shader.bind(); shader.uniform_float("color", HAL_BORDER); batch.draw(shader)

    for sx, sy in [(rx+12*scale, cy), (rx+rw-12*scale, cy)]:
        _draw_circle(sx, sy, 3*scale, (0.07, 0.03, 0.03, 1.0))
        _draw_circle(sx, sy, 3*scale, (0.25, 0.10, 0.08, 1.0), filled=False)
        _draw_line(sx-2*scale, sy, sx+2*scale, sy, (0.25, 0.10, 0.08, 0.8))
        _draw_line(sx, sy-2*scale, sx, sy+2*scale, (0.25, 0.10, 0.08, 0.8))

    ax = rx + 26*scale
    arrow = [(ax-5*scale, cy+5*scale), (ax-5*scale, cy-5*scale), (ax+5*scale, cy)]
    abat = batch_for_shader(shader, "TRIS", {"pos": arrow})
    shader.uniform_float("color", (0.50, 0.18, 0.12, 1.0)); abat.draw(shader)

    ai_type_names = dict(AI_RACK_TYPES)
    aname    = ai_type_names.get(rack.ai_type, rack.ai_type).split("—")[0].strip()
    badge_fs = max(1, int(12*scale))
    name_fs  = max(1, int(10*scale))
    badge_x  = rx + 38*scale
    _draw_text(str(ai_idx+1), badge_x, cy - badge_fs/2, badge_fs, HAL_TEXT)
    bw = _text_width(str(ai_idx+1), badge_fs) + 5*scale
    _draw_text(aname.upper(), badge_x+bw, cy-name_fs/2, name_fs,
               (0.55, 0.22, 0.16, 1.0))

    status = getattr(rack, 'ai_status', 'READY')
    dot_cols = {
        'READY':      (0.1, 0.7, 0.3, 1.0),
        'PROCESSING': (0.9, 0.5, 0.1, 1.0),
        'DONE':       (0.1, 0.9, 0.4, 1.0),
        'ERROR':      (0.9, 0.1, 0.1, 1.0),
    }
    _draw_circle(rx + rw/2, cy, 4*scale, dot_cols.get(status, (0.3, 0.3, 0.3, 1.0)))

    btn_h  = 16*scale
    btn_y  = cy - btn_h / 2
    del_w  = 18*scale
    del_x  = rx + rw - 22*scale
    _draw_rect(del_x, btn_y, del_w, btn_h, (0.18, 0.04, 0.04, 1.0))
    dvs = [(del_x,btn_y),(del_x+del_w,btn_y),(del_x+del_w,btn_y+btn_h),
           (del_x,btn_y+btn_h),(del_x,btn_y)]
    db2 = batch_for_shader(shader, "LINE_STRIP", {"pos": dvs})
    shader.bind(); shader.uniform_float("color", (0.6, 0.1, 0.1, 1.0)); db2.draw(shader)
    fs_x = max(1, int(9*scale))
    tw_x = _text_width("X", fs_x)
    _draw_text("X", del_x+del_w/2-tw_x/2, btn_y+btn_h/2-fs_x/2+1,
               fs_x, (0.8, 0.15, 0.15, 1.0))

    onoff_w = 40*scale
    onoff_x = del_x - onoff_w - 4*scale
    if rack.enabled:
        _draw_rect(onoff_x, btn_y, onoff_w, btn_h, (0.0, 0.12, 0.0, 1.0))
        oc = (0.0, 0.65, 0.3, 1.0); ot = "ON"
    else:
        _draw_rect(onoff_x, btn_y, onoff_w, btn_h, (0.12, 0.0, 0.0, 1.0))
        oc = (0.65, 0.0, 0.0, 1.0); ot = "OFF"
    ovs = [(onoff_x,btn_y),(onoff_x+onoff_w,btn_y),
           (onoff_x+onoff_w,btn_y+btn_h),(onoff_x,btn_y+btn_h),(onoff_x,btn_y)]
    obat = batch_for_shader(shader, "LINE_STRIP", {"pos": ovs})
    shader.bind(); shader.uniform_float("color", oc); obat.draw(shader)
    fs_oo = max(1, int(9*scale))
    tw_oo = _text_width(ot, fs_oo)
    _draw_text(ot, onoff_x+onoff_w/2-tw_oo/2, btn_y+btn_h/2-fs_oo/2+1, fs_oo, oc)

    assigned = get_ai_rack_channels(rack)
    ch_size  = CH_BTN_SIZE * scale
    ch_gap   = 4 * scale
    fs_b     = max(1, int(10*scale))
    ch_right = onoff_x - 6*scale
    for i, ch_idx in enumerate(sorted(assigned)):
        bx   = ch_right - (i+1) * (ch_size+ch_gap)
        ch_y = cy - ch_size/2
        is_on = getattr(rack, f'ch{ch_idx}', False)
        bg = (0.0, 0.18, 0.10, 1.0) if is_on else (0.07, 0.07, 0.07, 1.0)
        bc = (0.0, 0.75, 0.45, 1.0) if is_on else (0.2, 0.2, 0.2, 1.0)
        _draw_rect(bx, ch_y, ch_size, ch_size, bg)
        csv = [(bx,ch_y),(bx+ch_size,ch_y),(bx+ch_size,ch_y+ch_size),
               (bx,ch_y+ch_size),(bx,ch_y)]
        cbat = batch_for_shader(shader, "LINE_STRIP", {"pos": csv})
        shader.bind(); shader.uniform_float("color", bc); cbat.draw(shader)
        tw = _text_width(str(ch_idx+1), fs_b)
        _draw_text(str(ch_idx+1), bx+ch_size/2-tw/2, ch_y+ch_size/2-fs_b/2, fs_b, bc)


def _draw_ai_rack_expanded(rx, ry, rw, rh, rack, ai_idx, scale):
    """Draw a fully expanded AI rack — chassis, rail, body dispatch."""
    try:
        from ui.mixer.draw_utils import (
            draw_rect   as _draw_rect,
            draw_text   as _draw_text,
            text_width  as _text_width,
            draw_circle as _draw_circle,
            draw_line   as _draw_line,
        )
    except ImportError:
        from ui.racks.rack_base import (
            _draw_rect, _draw_text, _text_width, _draw_circle, _draw_line,
        )

    HAL_BG     = (0.05, 0.04, 0.04, 1.0)
    HAL_RAIL   = (0.09, 0.07, 0.07, 1.0)
    HAL_BORDER = (0.20, 0.08, 0.08, 1.0)
    HAL_TEXT   = (0.80, 0.22, 0.14, 1.0)

    shader = _get_shader()
    rail_h = RACK_RAIL_H * scale

    # Full-rack photoreal skin — one PNG spans the whole unit (rail + body).
    # When present it replaces the flat chassis/rail/screws/collapse-arrow
    # chrome below; dynamic content (badge #, preset name, channel states,
    # knobs, script text, etc.) still draws on top at the same coordinates
    # either way. Falls back to the old flat GPU chassis if the PNG isn't
    # found yet — same convention as the DSP racks' "_bg" skins.
    _ai_bg_key = {
        "PIPER_TTS": "rack_piper_bg",
        "RVC":       "rack_knnvc_bg",
        "RESEMBLE":  "rack_voicefixer_bg",
        "DEMUCS":    "rack_demucs_bg",
        "WHISPER":   "rack_whisper_bg",
    }.get(rack.ai_type)
    _ai_bg_tex = None
    if _ai_bg_key:
        try:
            from ui.mixer.texture_cache import get_texture as _gtc_aibg
            _ai_bg_tex = _gtc_aibg(_ai_bg_key)
        except Exception:
            _ai_bg_tex = None

    if _ai_bg_tex is not None:
        from ui.mixer.texture_cache import blit_texture as _blt_aibg
        _blt_aibg(_ai_bg_tex, rx, ry, rw, rh, key=_ai_bg_key)
    else:
        _draw_rect(rx, ry, rw, rh, HAL_BG)
        bv = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": bv})
        shader.bind(); shader.uniform_float("color", HAL_BORDER); batch.draw(shader)

        for sx, sy in [(rx+14*scale, ry+rh-16*scale), (rx+rw-14*scale, ry+rh-16*scale),
                       (rx+14*scale, ry+14*scale),     (rx+rw-14*scale, ry+14*scale)]:
            _draw_circle(sx, sy, 4*scale, (0.07, 0.03, 0.03, 1.0))
            _draw_circle(sx, sy, 4*scale, (0.25, 0.10, 0.08, 1.0), filled=False)
            _draw_line(sx-3*scale, sy, sx+3*scale, sy, (0.25, 0.10, 0.08, 0.8))
            _draw_line(sx, sy-3*scale, sx, sy+3*scale, (0.25, 0.10, 0.08, 0.8))

        _draw_rect(rx, ry+rh-rail_h, rw, rail_h, HAL_RAIL)
        _draw_rect(rx, ry+rh-rail_h-2*scale, rw, 2*scale, (0.06, 0.04, 0.04, 1.0))

    col_x = rx + 4*scale
    col_y = ry + rh - 28*scale
    col_w = 24*scale
    col_h = 20*scale
    if _ai_bg_tex is None:
        _draw_rect(col_x, col_y, col_w, col_h, (0.08, 0.05, 0.05, 1.0))
        cbv = [(col_x,col_y),(col_x+col_w,col_y),(col_x+col_w,col_y+col_h),
               (col_x,col_y+col_h),(col_x,col_y)]
        cb = batch_for_shader(shader, "LINE_STRIP", {"pos": cbv})
        shader.bind(); shader.uniform_float("color", (0.30, 0.12, 0.08, 1.0)); cb.draw(shader)
        ax = col_x + col_w * 0.5
        ay = col_y + col_h * 0.5
        arrow = [(ax-5*scale, ay-3*scale), (ax+5*scale, ay-3*scale), (ax, ay+5*scale)]
        ab = batch_for_shader(shader, "TRIS", {"pos": arrow})
        shader.uniform_float("color", (0.65, 0.25, 0.18, 1.0)); ab.draw(shader)

    ai_type_names = dict(AI_RACK_TYPES)
    atype     = rack.ai_type
    aname     = ai_type_names.get(atype, atype).split("—")[0].strip()
    badge_x   = col_x + col_w + 4*scale
    badge_y   = ry + rh - 28*scale
    badge_fs  = max(1, int(13 * scale))
    badge_lbl = str(ai_idx + 1)
    badge_w   = max(22*scale, _text_width(badge_lbl, badge_fs) + 12*scale)
    badge_h   = 20*scale
    if _ai_bg_tex is None:
        _draw_rect(badge_x, badge_y, badge_w, badge_h, (0.15, 0.05, 0.03, 1.0))
        bverts = [(badge_x,badge_y),(badge_x+badge_w,badge_y),
                  (badge_x+badge_w,badge_y+badge_h),(badge_x,badge_y+badge_h),(badge_x,badge_y)]
        bbat = batch_for_shader(shader, "LINE_STRIP", {"pos": bverts})
        shader.bind(); shader.uniform_float("color", (0.65, 0.22, 0.14, 0.7)); bbat.draw(shader)
    tw_b = _text_width(badge_lbl, badge_fs)
    _draw_text(badge_lbl, badge_x+badge_w/2-tw_b/2,
               badge_y+badge_h/2-badge_fs/2, badge_fs, HAL_TEXT)
    fs_name = max(1, int(11 * scale))
    name_draw_x = badge_x + badge_w + 6*scale
    if _ai_bg_tex is None:
        _draw_text(aname.upper(), name_draw_x,
                   ry+rh-22*scale, fs_name, (0.75, 0.30, 0.20, 1.0))

    # ── PRESET SELECTOR — in rail after name ──────────────────────────────
    # Geometry shared with hit test — single calculation
    _del_x_r  = rx + rw - 26*scale
    _on_x_r   = _del_x_r - 40*scale - 4*scale
    _ch_s_r   = 18*scale
    _ch_g_r   = 3*scale
    _avail_r  = _on_x_r - (name_draw_x + _text_width(aname.upper(), fs_name) + 8*scale) - 6*scale
    _mfit_r   = max(1, int(_avail_r / (_ch_s_r + _ch_g_r)))
    _scene_r  = bpy.context.scene
    _high_r   = 0
    if _scene_r and _scene_r.sequence_editor:
        for _sr in _scene_r.sequence_editor.sequences_all:
            if _sr.type == "SOUND" and _sr.sound:
                _high_r = max(_high_r, _sr.channel - 1)
    _nch_r    = min(max(9, _high_r + 1), _mfit_r)
    _chtot_r  = _nch_r * _ch_s_r + (_nch_r - 1) * _ch_g_r
    _chstart_r = name_draw_x + _text_width(aname.upper(), fs_name) + 8*scale + (_avail_r - _chtot_r) / 2
    _p_right  = _chstart_r - 6*scale
    _p_aw     = 14*scale
    _p_bw     = min(120*scale, _p_right - name_draw_x - _text_width(aname.upper(), fs_name) - 8*scale - _p_aw*2 - 4*scale)

    if _p_bw > 30*scale:
        # For all rack types including Piper, use AI_PRESETS for rail preset selector
        ai_presets_ps = AI_PRESETS.get(atype, [])
        if ai_presets_ps:
            p_idx    = getattr(rack, 'preset_idx', 0) % max(1, len(ai_presets_ps))
            p_name   = ai_presets_ps[p_idx]
            p_box_x  = _p_right - _p_bw - _p_aw - 2*scale
            p_box_y  = ry + rh - 27*scale
            p_box_h  = 16*scale

            lax = p_box_x - _p_aw + 2*scale
            lay = p_box_y + p_box_h / 2
            la  = [(lax, lay), (lax+10*scale, lay+5*scale), (lax+10*scale, lay-5*scale)]
            la_b = batch_for_shader(shader, "TRIS", {"pos": la})
            shader.bind(); shader.uniform_float("color", (0.45, 0.18, 0.10, 1.0)); la_b.draw(shader)

            _draw_rect(p_box_x, p_box_y, _p_bw, p_box_h, (0.09, 0.05, 0.04, 1.0))
            pv = [(p_box_x,p_box_y),(p_box_x+_p_bw,p_box_y),
                  (p_box_x+_p_bw,p_box_y+p_box_h),(p_box_x,p_box_y+p_box_h),(p_box_x,p_box_y)]
            pb = batch_for_shader(shader, "LINE_STRIP", {"pos": pv})
            shader.bind(); shader.uniform_float("color", (0.30, 0.12, 0.08, 0.8)); pb.draw(shader)
            fs_pn = max(1, int(8*scale))
            tw_pn = _text_width(p_name, fs_pn)
            _draw_text(p_name, p_box_x + _p_bw/2 - tw_pn/2,
                       p_box_y + p_box_h/2 - fs_pn/2 + 1, fs_pn, (0.70, 0.28, 0.16, 1.0))

            rax = p_box_x + _p_bw + 2*scale
            ray = p_box_y + p_box_h / 2
            ra  = [(rax+10*scale, ray), (rax, ray+5*scale), (rax, ray-5*scale)]
            ra_b = batch_for_shader(shader, "TRIS", {"pos": ra})
            shader.bind(); shader.uniform_float("color", (0.45, 0.18, 0.10, 1.0)); ra_b.draw(shader)

    # ── ON/OFF + CLOSE BUTTONS (PNG) — same textures/geometry as DSP racks ──
    # RackOff.png (100×38px source): OFF button + close X — always drawn when expanded
    # RackOn.png  (100×38px source): ON button only — drawn on top if enabled
    # Pulled from rack_base so this matches hit_test_ai_racks()'s geometry
    # exactly (it already reads these same constants) — previously this drew
    # flat 40×16/18×16 ImGui-style rects at different coordinates than the
    # hitbox expected.
    try:
        from ui.racks.rack_base import (RACK_BTN_W as _RBW4, RACK_BTN_H as _RBH4,
                                        RACK_BTN_X_OFFSET as _RBXO4, RACK_BTN_Y_OFFSET as _RBYO4)
    except Exception:
        _RBW4, _RBH4, _RBXO4, _RBYO4 = 70.0, 26.6, -5.0, -2.0
    _btn_w = _RBW4 * scale
    _btn_h = _RBH4 * scale
    _btn_x = rx + rw - _btn_w + _RBXO4 * scale
    _btn_y = ry + rh - (RACK_RAIL_H * scale + _btn_h) / 2 + _RBYO4 * scale
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_aibtn
        from ui.mixer.texture_cache import blit_texture as _blt_aibtn
        _tex_aioff = _gtc_aibtn("rack_btn_off")
        if _tex_aioff:
            _blt_aibtn(_tex_aioff, _btn_x, _btn_y, _btn_w, _btn_h, key="rack_btn_off")
        else:
            # GPU fallback — old flat rects, kept so buttons never disappear
            _fdel_x, _fdel_y = rx+rw-26*scale, ry+rh-27*scale
            _draw_rect(_fdel_x, _fdel_y, 18*scale, 16*scale, (0.18, 0.04, 0.04, 1.0))
            _fdvs = [(_fdel_x,_fdel_y),(_fdel_x+18*scale,_fdel_y),(_fdel_x+18*scale,_fdel_y+16*scale),
                     (_fdel_x,_fdel_y+16*scale),(_fdel_x,_fdel_y)]
            _fdbat = batch_for_shader(shader, "LINE_STRIP", {"pos": _fdvs})
            shader.bind(); shader.uniform_float("color", (0.6, 0.1, 0.1, 1.0)); _fdbat.draw(shader)
            fs_del = max(1, int(9*scale)); tw_del = _text_width("X", fs_del)
            _draw_text("X", _fdel_x+9*scale-tw_del/2, _fdel_y+8*scale-fs_del/2+1,
                       fs_del, (0.8, 0.15, 0.15, 1.0))
            _fon_x, _fon_y = _fdel_x-44*scale, _fdel_y
            _fon_col = (0.0, 0.65, 0.3, 1.0) if rack.enabled else (0.65, 0.0, 0.0, 1.0)
            _draw_rect(_fon_x, _fon_y, 40*scale, 16*scale,
                       (0.0,0.12,0.0,1.0) if rack.enabled else (0.12,0.0,0.0,1.0))
            _fovs = [(_fon_x,_fon_y),(_fon_x+40*scale,_fon_y),(_fon_x+40*scale,_fon_y+16*scale),
                     (_fon_x,_fon_y+16*scale),(_fon_x,_fon_y)]
            _fobat = batch_for_shader(shader, "LINE_STRIP", {"pos": _fovs})
            shader.bind(); shader.uniform_float("color", _fon_col); _fobat.draw(shader)
            fs_on = max(1, int(9*scale))
            _fon_txt = "ON" if rack.enabled else "OFF"
            tw_on = _text_width(_fon_txt, fs_on)
            _draw_text(_fon_txt, _fon_x+20*scale-tw_on/2, _fon_y+8*scale-fs_on/2+1, fs_on, _fon_col)
        if rack.enabled:
            _tex_aion = _gtc_aibtn("rack_btn_on")
            if _tex_aion:
                _blt_aibtn(_tex_aion, _btn_x, _btn_y, _btn_w, _btn_h, key="rack_btn_on")
    except Exception:
        pass

    # ── CHANNEL BUTTONS — in the rail, between name and ON/OFF ────────────
    ch_btn_s   = 18*scale
    ch_btn_gap = 3*scale
    ch_btn_h   = 16*scale
    ch_btn_y   = ry + rh - 27*scale

    # Always show exactly 9 channels — the 9 that belong to this rack's group.
    # ch0-ch8 are local indices; labels show the absolute VSE channel number.
    group_off = getattr(rack, 'group_idx', 0) * 9
    num_ch    = 9

    # Available space: from right edge of name to left edge of ON/OFF+close
    name_right  = badge_x + badge_w + 6*scale + _text_width(aname.upper(), fs_name) + 8*scale
    avail_w     = _btn_x - name_right - 6*scale
    max_fit     = max(1, int(avail_w / (ch_btn_s + ch_btn_gap)))
    num_ch      = min(num_ch, max_fit)

    # "CHANNELS" micro-label above the row
    fs_chl  = max(1, int(7 * scale))
    ch_total_w = num_ch * ch_btn_s + (num_ch - 1) * ch_btn_gap
    ch_start_x = name_right + (avail_w - ch_total_w) / 2   # centre in gap

    _draw_text("CHANNELS", ch_start_x,
               ch_btn_y + ch_btn_h + 1*scale, fs_chl, (0.38, 0.12, 0.06, 1.0))

    # Channel-number label nudge — pulls from the module-level constants
    # above (AI_RAIL_CH_LABEL_X_OFFSET / _Y_OFFSET) so they're easy to find
    # and adjust without hunting through this function. Only applied once a
    # full-rack skin is loaded — the flat-rect fallback (no skin yet) still
    # centres the label on the GPU-drawn box exactly as before.
    _ch_lbl_dx = AI_RAIL_CH_LABEL_X_OFFSET*scale if _ai_bg_tex is not None else 0.0
    _ch_lbl_dy = AI_RAIL_CH_LABEL_Y_OFFSET*scale if _ai_bg_tex is not None else 0.0

    for ci in range(num_ch):
        bx = ch_start_x + ci * (ch_btn_s + ch_btn_gap)
        by = ch_btn_y
        assigned_ch = getattr(rack, f'ch{ci}', False)
        bg = (0.0, 0.18, 0.10, 1.0) if assigned_ch else (0.10, 0.05, 0.04, 1.0)
        bc = (0.0, 0.75, 0.45, 1.0) if assigned_ch else AI_RAIL_CH_LABEL_OFF_COLOR
        if _ai_bg_tex is None:
            _draw_rect(bx, by, ch_btn_s, ch_btn_h, bg)
            cverts = [(bx,by),(bx+ch_btn_s,by),(bx+ch_btn_s,by+ch_btn_h),
                      (bx,by+ch_btn_h),(bx,by)]
            cbat = batch_for_shader(shader, "LINE_STRIP", {"pos": cverts})
            shader.bind(); shader.uniform_float("color", bc); cbat.draw(shader)
        fs_ci = max(1, int(8*scale))
        lbl_c = str(group_off + ci + 1)   # absolute VSE channel number
        tw_c  = _text_width(lbl_c, fs_ci)
        _draw_text(lbl_c, bx + ch_btn_s/2 - tw_c/2 + _ch_lbl_dx,
                   by + ch_btn_h/2 - fs_ci/2 + _ch_lbl_dy, fs_ci, bc)

    if atype == "PIPER_TTS":
        try:
            from ui.racks.rack_piper import _draw_piper_body
            _draw_piper_body(rx, ry, rw, rh, rack, ai_idx, scale)
        except Exception as e:
            print(f"[AI RACKS] Piper draw error rack {ai_idx}: {e}")

    elif atype == "RVC":
        try:
            from ui.racks.rack_knnvc import _draw_rvc_body
            _draw_rvc_body(rx, ry, rw, rh, rack, ai_idx, scale)
        except Exception as e:
            print(f"[AI RACKS] RVC draw error rack {ai_idx}: {e}")
            import traceback; traceback.print_exc()

    elif atype == "RESEMBLE":
        try:
            from ui.racks.rack_voicefixer import _draw_voicefixer_body
            _draw_voicefixer_body(rx, ry, rw, rh, rack, ai_idx, scale)
        except Exception as e:
            print(f"[AI RACKS] VoiceFixer draw error rack {ai_idx}: {e}")
            import traceback; traceback.print_exc()

    elif atype == "WHISPER":
        try:
            from ui.racks.rack_whisper import _draw_whisper_body
            _draw_whisper_body(rx, ry, rw, rh, rack, ai_idx, scale)
        except Exception as e:
            print(f"[AI RACKS] Whisper draw error rack {ai_idx}: {e}")
            import traceback; traceback.print_exc()

    elif atype == "DEMUCS":
        try:
            from ui.racks.rack_demucs import _draw_demucs_body
            _draw_demucs_body(rx, ry, rw, rh, rack, ai_idx, scale)
        except Exception as e:
            print(f"[AI RACKS] Demucs draw error rack {ai_idx}: {e}")
            import traceback; traceback.print_exc()


def draw_ai_racks(rx, ry, scale, area_width=None, group_idx=0):
    """Draw the AI processing section — divider + all AI racks + add button.

    rx, ry: position where the AI section starts (top edge of divider).
    group_idx: only draw AI racks belonging to this fader group.
    Returns total height consumed so caller can advance the layout cursor.
    """
    global _ai_popup_open, _ai_popup_x, _ai_popup_y

    try:
        from ui.mixer.draw_utils import (
            draw_rect   as _draw_rect,
            draw_text   as _draw_text,
            text_width  as _text_width,
            draw_circle as _draw_circle,
        )
    except ImportError:
        from ui.racks.rack_base import (
            _draw_rect, _draw_text, _text_width, _draw_circle,
        )

    rw = (area_width if area_width is not None else RACK_WIDTH) * scale

    # PNG skin — one combined brushed-metal art file covers BOTH the divider
    # bar and the add-button bar, stacked top/bottom (divider on top, button
    # on bottom, split down the middle). Sliced via UV sub-rects so each row
    # blits from its own half of the same texture. Falls back to the old
    # flat rect + border + text/LED-dot per row if no art is dropped in.
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_airack
        from ui.mixer.texture_cache import blit_texture as _blt_airack
        _airack_tex = _gtc_airack("add_ai_rack_btn")
    except Exception:
        _airack_tex = None

    div_h = 28 * scale
    shader = _get_shader()

    if _airack_tex:
        # Top half of the art (v: 0.5-1.0) = the "AI PROCESSING" divider bar.
        _blt_airack(_airack_tex, rx, ry - div_h, rw, div_h,
                    key="add_ai_rack_btn", uv=(0, 0.5, 1, 1))
    else:
        _draw_rect(rx, ry - div_h, rw, div_h, (0.06, 0.04, 0.04, 1.0))
        div_verts = [(rx, ry-div_h), (rx+rw, ry-div_h),
                     (rx+rw, ry), (rx, ry), (rx, ry-div_h)]
        div_batch = batch_for_shader(shader, "LINE_STRIP", {"pos": div_verts})
        shader.bind(); shader.uniform_float("color", (0.22, 0.08, 0.06, 1.0))
        div_batch.draw(shader)

        fs_sec = max(1, int(9 * scale))
        lbl    = "AI PROCESSING"
        tw_lbl = _text_width(lbl, fs_sec)
        _draw_text(lbl, rx + 12*scale, ry - div_h + div_h/2 - fs_sec/2,
                   fs_sec, (0.70, 0.18, 0.10, 1.0))

        eye_dot_x = rx + 12*scale + tw_lbl + 14*scale
        eye_dot_y = ry - div_h/2
        _draw_circle(eye_dot_x, eye_dot_y, 4*scale, (0.75, 0.12, 0.07, 1.0))
        _draw_circle(eye_dot_x, eye_dot_y, 2*scale, (1.0,  0.3,  0.15, 1.0))

    y_cursor = ry - div_h
    total_h  = div_h

    scene = bpy.context.scene
    if not scene:
        return total_h

    all_ai_racks = getattr(scene, "pb_ai_racks", [])
    # Only draw racks that belong to this fader group
    group_racks = [(ai_idx, rack) for ai_idx, rack in enumerate(all_ai_racks)
                   if getattr(rack, 'group_idx', 0) == group_idx]

    for ai_idx, rack in group_racks:
        rack_h = _get_ai_rack_height(rack, scale)
        rack_y = y_cursor - rack_h
        try:
            if rack.collapsed:
                _draw_ai_rack_collapsed(rx, rack_y, rw, rack_h, rack, ai_idx, scale)
            else:
                _draw_ai_rack_expanded(rx, rack_y, rw, rack_h, rack, ai_idx, scale)
        except Exception as e:
            print(f"[AI RACKS] draw error rack {ai_idx}: {e}")

        y_cursor -= rack_h + RACK_GAP * scale
        total_h  += rack_h + RACK_GAP * scale

    add_h = 28 * scale
    add_y = y_cursor - add_h

    if _airack_tex:
        # Bottom half of the same art (v: 0.0-0.5) = the "+ ADD AI RACK" button bar.
        _blt_airack(_airack_tex, rx, add_y, rw, add_h,
                    key="add_ai_rack_btn", uv=(0, 0, 1, 0.5))
    else:
        _draw_rect(rx, add_y, rw, add_h, (0.06, 0.04, 0.04, 1.0))
        av = [(rx,add_y),(rx+rw,add_y),(rx+rw,add_y+add_h),(rx,add_y+add_h),(rx,add_y)]
        ab = batch_for_shader(shader, "LINE_STRIP", {"pos": av})
        shader.bind(); shader.uniform_float("color", (0.22, 0.08, 0.06, 1.0)); ab.draw(shader)
        fs_add = max(1, int(9 * scale))
        add_lbl = "+  ADD AI RACK"
        tw_add  = _text_width(add_lbl, fs_add)
        _draw_text(add_lbl, rx + rw/2 - tw_add/2,
                   add_y + add_h/2 - fs_add/2, fs_add, (0.50, 0.14, 0.08, 1.0))
    total_h += add_h

    return total_h


def _draw_ai_add_popup(px, py, scale):
    """Draw the AI type selector popup.

    px, py = left edge, bottom of the add button.
    Popup draws DOWNWARD from py (same convention as DSP popup).
    py is the TOP of the popup (highest y), items stack downward.
    """
    try:
        from ui.mixer.draw_utils import (
            draw_rect  as _draw_rect,
            draw_text  as _draw_text,
            text_width as _text_width,
        )
    except ImportError:
        from ui.racks.rack_base import _draw_rect, _draw_text, _text_width

    popup_w = 260 * scale
    title_h = 24 * scale
    # Total popup height: title + one row per type + small padding
    item_h  = 28 * scale
    popup_h = title_h + len(AI_RACK_TYPES) * item_h + 6 * scale

    # Draw downward: popup top = py, bottom = py - popup_h
    popup_top = py
    popup_bot = py - popup_h

    # Shadow
    _draw_rect(px + 3*scale, popup_bot - 3*scale, popup_w, popup_h,
               (0.0, 0.0, 0.0, 0.5))
    # Background
    _draw_rect(px, popup_bot, popup_w, popup_h, (0.10, 0.06, 0.05, 1.0))
    shader = _get_shader()
    verts = [(px, popup_bot), (px+popup_w, popup_bot),
             (px+popup_w, popup_top), (px, popup_top), (px, popup_bot)]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind(); shader.uniform_float("color", (0.30, 0.12, 0.08, 1.0))
    batch.draw(shader)

    # Title bar at the top of the popup
    _draw_rect(px, popup_top - title_h, popup_w, title_h, (0.14, 0.08, 0.06, 1.0))
    fs_t = max(1, int(9*scale))
    _draw_text("SELECT AI RACK TYPE", px + 8*scale,
               popup_top - title_h + title_h/2 - fs_t/2,
               fs_t, (0.70, 0.25, 0.16, 1.0))

    # Items drawn downward from just below the title bar
    fs_b = max(1, int(10*scale))
    for i, (atype, aname) in enumerate(AI_RACK_TYPES):
        # Item top = popup_top - title_h - i*item_h - padding
        item_top = popup_top - title_h - i * item_h - 3*scale
        item_bot = item_top - item_h + 4*scale
        bh = item_h - 4*scale
        bg = (0.12, 0.07, 0.05, 1.0) if i % 2 == 0 else (0.09, 0.05, 0.04, 1.0)
        _draw_rect(px + 2*scale, item_bot, popup_w - 4*scale, bh, bg)
        _draw_text(aname, px + 12*scale, item_bot + bh/2 - fs_b/2,
                   fs_b, (0.75, 0.30, 0.20, 1.0))


# ---------------------------------------------------------------------------
# AI rack hit test and click handler
# ---------------------------------------------------------------------------
def hit_test_ai_racks(mouse_x, mouse_y, ai_section_top_y, rack_x, scale,
                      area_width=None, group_idx=0):
    """Return a hit dict for AI section clicks, or None if no hit.

    ai_section_top_y: y at the BOTTOM of the DSP add-rack button = top of AI divider.
    rack_x: left edge of rack area.
    group_idx: which fader group to test (only racks in this group are checked).

    Layout from top downward:
      [ai_section_top_y]
      [  AI divider bar  28px ]
      [  AI racks (if any)    ]
      [  ADD AI RACK button 28px ]
    """
    rw    = (area_width if area_width is not None else RACK_WIDTH) * scale
    div_h = 28 * scale
    add_h = 28 * scale

    # y_cur starts just below the divider bar
    y_cur = ai_section_top_y - div_h

    scene = bpy.context.scene
    if not scene:
        return None
    all_ai_racks = getattr(scene, "pb_ai_racks", [])
    # Only consider racks belonging to this group
    ai_racks = [(ai_idx, rack) for ai_idx, rack in enumerate(all_ai_racks)
                if getattr(rack, 'group_idx', 0) == group_idx]

    # Walk down past AI racks to find add_y (matches draw_ai_racks exactly)
    y_walk = y_cur
    for _, rack in ai_racks:
        rh = _get_ai_rack_height(rack, scale)
        y_walk -= rh + RACK_GAP * scale
    add_y = y_walk - add_h   # bottom-left of the ADD AI RACK button

    # Check AI popup first — it draws downward from _ai_popup_y.
    # Must use the STORED _ai_popup_y here, not a freshly recomputed add_y —
    # _draw_ai_add_popup() draws from _ai_popup_y (the raw click Y that opened
    # the popup, set in handle_ai_rack_click), so the hit-test has to read the
    # same value or the two disagree on where the popup actually is (this was
    # the "have to click the bottom edge of each row" bug). Mirrors how the
    # DSP add-rack popup already does it with _popup_x/_popup_y.
    if _ai_popup_open:
        popup_w = 260 * scale
        title_h = 24 * scale
        item_h  = 28 * scale
        popup_h = title_h + len(AI_RACK_TYPES) * item_h + 6 * scale
        popup_top = _ai_popup_y
        popup_bot = _ai_popup_y - popup_h
        if (_ai_popup_x <= mouse_x <= _ai_popup_x + popup_w and
                popup_bot <= mouse_y <= popup_top):
            for i, (atype, _) in enumerate(AI_RACK_TYPES):
                item_top = popup_top - title_h - i * item_h - 3*scale
                item_bot = item_top - item_h + 4*scale
                if item_bot <= mouse_y <= item_top:
                    return {'zone': 'ai_add_type', 'ai_type': atype}
            return {'zone': 'ai_popup_dismiss'}
        return {'zone': 'ai_popup_dismiss'}

    # Check ADD AI RACK button
    if rack_x <= mouse_x <= rack_x + rw and add_y <= mouse_y <= add_y + add_h:
        return {'zone': 'ai_add_click', 'bx': mouse_x, 'by': mouse_y,
                'group_idx': group_idx}

    # Check AI section divider bar
    div_y = ai_section_top_y - div_h
    if rack_x <= mouse_x <= rack_x + rw and div_y <= mouse_y <= div_y + div_h:
        return {'zone': 'ai_divider'}

    # Check individual AI racks
    for ai_idx, rack in ai_racks:
        rack_h   = _get_ai_rack_height(rack, scale)
        rack_y   = y_cur - rack_h
        rack_top = rack_y + rack_h

        if not (rack_x <= mouse_x <= rack_x + rw and rack_y <= mouse_y <= rack_top):
            y_cur -= rack_h + RACK_GAP * scale
            continue

        # Collapse button
        col_x = rack_x + 4*scale
        col_y = rack_top - 28*scale
        if col_x <= mouse_x <= col_x+24*scale and col_y <= mouse_y <= col_y+20*scale:
            return {'zone': 'ai_collapse', 'ai_idx': ai_idx}

        # Delete / ON-OFF hitboxes — derived from PNG blit geometry
        try:
            from ui.racks.rack_base import (RACK_BTN_W as _RBW3, RACK_BTN_H as _RBH3,
                                            RACK_BTN_X_OFFSET as _RBXO3, RACK_BTN_Y_OFFSET as _RBYO3,
                                            RACK_BTN_ONOFF_SPLIT as _RBSP3)
        except Exception:
            _RBW3, _RBH3, _RBXO3, _RBYO3, _RBSP3 = 60.0, 22.8, 0.0, 0.0, 0.60
        _rbw3 = _RBW3 * scale
        _rbh3 = _RBH3 * scale
        _rbx3 = rack_x + rw - _rbw3 + _RBXO3 * scale
        _rby3 = rack_top - (RACK_RAIL_H * scale + _rbh3) / 2 + _RBYO3 * scale
        del_x = _rbx3 + _rbw3 * _RBSP3
        del_y = _rby3
        if del_x <= mouse_x <= _rbx3 + _rbw3 and del_y <= mouse_y <= _rby3 + _rbh3:
            return {'zone': 'ai_delete', 'ai_idx': ai_idx}

        # ON/OFF button
        on_x_ht = _rbx3
        on_y_ht = _rby3
        if on_x_ht <= mouse_x <= _rbx3 + _rbw3 * _RBSP3 and on_y_ht <= mouse_y <= _rby3 + _rbh3:
            return {'zone': 'ai_on_off', 'ai_idx': ai_idx}

        # Piper TTS: CLEAR button, voice cards, and knob hit testing
        if rack.ai_type == "PIPER_TTS" and not rack.collapsed:
            _r_h_p   = RACK_RAIL_H * scale
            _bb_p    = rack_y
            _bt_p    = rack_y + rack_h - _r_h_p
            _bh_p    = _bt_p - _bb_p
            _sb_h_p  = max(16*scale, _bh_p * 0.065)
            _ct_h_p  = _bh_p * 0.22
            _ct_b_p  = _bb_p + _sb_h_p + 2*scale
            _wh_p    = _bh_p * 0.18
            _wy_p    = _ct_b_p + _ct_h_p + 2*scale
            _uh_p    = _bt_p - (_wy_p + _wh_p) - 4*scale
            _uy_p    = _wy_p + _wh_p + 2*scale
            _marg_p  = 8*scale
            _sp_x_p  = rack_x + _marg_p
            _sp_w_p  = (rack_x + rw * 0.52) - rack_x - _marg_p*2

            # SCRIPT TEXT AREA click — geometry + raw mouse pos passed through
            # so handle_ai_rack_click() can map the click to a character
            # index (click-to-position-cursor / drag-select) via the SAME
            # word-wrap layout rack_piper.py's draw code uses.
            _ta_top_p  = _uy_p + _uh_p
            _ta_bot_p  = _uy_p + 28*scale
            if (_sp_x_p <= mouse_x <= _sp_x_p+_sp_w_p and
                    _ta_bot_p <= mouse_y <= _ta_top_p):
                return {'zone': 'ai_piper_text', 'ai_idx': ai_idx,
                        'sp_x': _sp_x_p, 'sp_y': _uy_p,
                        'sp_w': _sp_w_p, 'sp_h': _uh_p, 'scale': scale,
                        'mouse_x': mouse_x, 'mouse_y': mouse_y}

            # Button row geometry — mirrors rack_piper.py exactly, including
            # its independent per-button PIPER_{GEN,PV,CL}_BTN_*_OFFSET/SCALE
            # tuning constants, imported live so a change there stays in sync
            # here. Base positions come from the SAME natural-flow formula
            # rack_piper.py uses (never from another button's tuned position),
            # so each button's own offset/scale is independent there too.
            try:
                from ui.racks.rack_piper import (
                    PIPER_GEN_BTN_X_OFFSET as _PGXO, PIPER_GEN_BTN_Y_OFFSET as _PGYO,
                    PIPER_GEN_BTN_W_SCALE as _PGWS, PIPER_GEN_BTN_H_SCALE as _PGHS,
                    PIPER_PV_BTN_X_OFFSET as _PPXO, PIPER_PV_BTN_Y_OFFSET as _PPYO,
                    PIPER_PV_BTN_W_SCALE as _PPWS,
                    PIPER_CL_BTN_X_OFFSET as _PCXO, PIPER_CL_BTN_Y_OFFSET as _PCYO,
                    PIPER_CL_BTN_W_SCALE as _PCWS,
                    PIPER_VOICE_PANEL_X_OFFSET as _PVPXO,
                    PIPER_VOICE_PANEL_W_SCALE as _PVPWS,
                )
            except Exception:
                _PGXO = _PGYO = _PPXO = _PPYO = _PCXO = _PCYO = _PVPXO = 0.0
                _PGWS = _PGHS = _PPWS = _PCWS = _PVPWS = 1.0

            _gen_w_base = min(80*scale, _sp_w_p*0.48)
            _pv_w_base  = min(60*scale, _sp_w_p*0.36)
            _cl_w_base  = min(38*scale, _sp_w_p*0.22)
            _gen_x_base = _sp_x_p + 4*scale
            _pv_x_base  = _gen_x_base + _gen_w_base + 4*scale
            _cl_x_base  = _pv_x_base + _pv_w_base + 4*scale
            _row_y_base = _uy_p + 4*scale

            _gw_p = _gen_w_base * _PGWS
            _gh_p = max(16*scale, 20*scale) * _PGHS
            _gx_p = _gen_x_base + _PGXO*scale
            _gy_p = _row_y_base + _PGYO*scale

            # GENERATE button
            if _gx_p <= mouse_x <= _gx_p+_gw_p and _gy_p <= mouse_y <= _gy_p+_gh_p:
                return {'zone': 'ai_process', 'ai_idx': ai_idx, 'ai_type': 'PIPER_TTS'}

            # PREVIEW button
            _pvw_p = _pv_w_base * _PPWS
            _pvx_p = _pv_x_base + _PPXO*scale
            _pvy_p = _row_y_base + _PPYO*scale
            if _pvx_p <= mouse_x <= _pvx_p+_pvw_p and _pvy_p <= mouse_y <= _pvy_p+_gh_p:
                return {'zone': 'ai_piper_preview', 'ai_idx': ai_idx}

            # CLEAR button
            _clw_p = _cl_w_base * _PCWS
            _clx_p = _cl_x_base + _PCXO*scale
            _cly_p = _row_y_base + _PCYO*scale
            if _clx_p <= mouse_x <= _clx_p+_clw_p and _cly_p <= mouse_y <= _cly_p+_gh_p:
                return {'zone': 'ai_piper_clear', 'ai_idx': ai_idx}

            # Voice panel geometry
            _spl_x_p  = rack_x + rw * 0.52
            _vp_x_p   = _spl_x_p + _marg_p*0.5 + _PVPXO*scale
            _vp_w_p   = (rack_x + rw - _spl_x_p - _marg_p*1.5) * _PVPWS
            _vp_y_p   = _uy_p
            _vp_h_p   = _uh_p
            _fs_lbl_p = max(1, int(8*scale))
            _arr_h_p  = 14*scale

            # ▲ up arrow
            _arr_top_y_p = _vp_y_p + _vp_h_p - _fs_lbl_p - 8*scale - _arr_h_p
            if (_vp_x_p <= mouse_x <= _vp_x_p+_vp_w_p and
                    _arr_top_y_p <= mouse_y <= _arr_top_y_p+_arr_h_p):
                return {'zone': 'ai_piper_scroll', 'ai_idx': ai_idx, 'dir': -1}

            # ▼ down arrow
            _arr_bot_y_p = _vp_y_p + 2*scale
            if (_vp_x_p <= mouse_x <= _vp_x_p+_vp_w_p and
                    _arr_bot_y_p <= mouse_y <= _arr_bot_y_p+_arr_h_p):
                return {'zone': 'ai_piper_scroll', 'ai_idx': ai_idx, 'dir': 1}

            # Voice cards (with scroll offset from p5)
            _cd_h_p   = max(22*scale, _vp_h_p * 0.20)
            _cg_p     = 2*scale
            _cards_area_p = _vp_h_p - _fs_lbl_p - 8*scale - _arr_h_p*2 - 4*scale
            _max_vis_p    = max(1, int(_cards_area_p / (_cd_h_p + _cg_p)))
            _v_scroll_p   = int(getattr(rack, 'p5', 0.0))
            try:
                from core.ai_piper import get_voices as _gv_p
                _vlist_p = _gv_p()
            except Exception:
                _vlist_p = []
            _v_start_y_p = _arr_top_y_p - _cg_p
            for _slot in range(_max_vis_p):
                _vi = _v_scroll_p + _slot
                if _vi >= len(_vlist_p): break
                _cy_c = _v_start_y_p - _slot*(_cd_h_p+_cg_p) - _cd_h_p
                if _cy_c < _vp_y_p + _arr_h_p + 2*scale: break
                if (_vp_x_p+4*scale <= mouse_x <= _vp_x_p+_vp_w_p-4*scale and
                        _cy_c <= mouse_y <= _cy_c+_cd_h_p):
                    return {'zone': 'ai_piper_voice', 'ai_idx': ai_idx,
                            'voice_idx': _vi}

            # Piper knobs
            import math as _math_p
            _cs_p   = rack_x + rw * 0.45
            _kzw_p  = rack_x + rw - _cs_p - _marg_p
            _kw_p   = _kzw_p / 3
            _kr_p   = min(12*scale, _ct_h_p*0.38) + 8*scale
            _ky_p   = _ct_b_p + _ct_h_p * 0.58
            for _ki in range(3):
                _kx_p = _cs_p + _kw_p*(_ki+0.5)
                if _math_p.sqrt((mouse_x-_kx_p)**2+(mouse_y-_ky_p)**2) < _kr_p:
                    return {'zone': 'ai_piper_knob', 'ai_idx': ai_idx, 'knob_idx': _ki}

        # RVC: setup guide button hit test
        if rack.ai_type == "RVC" and not rack.collapsed:
            # Setup guide button (shown when PyTorch not found)
            try:
                btn_geom = rack.get('rvc_setup_btn')
                if btn_geom:
                    bx,by,bw,bh = btn_geom
                    if bx <= mouse_x <= bx+bw and by <= mouse_y <= by+bh:
                        return {'zone': 'ai_rvc_setup_guide', 'ai_idx': ai_idx}
            except Exception:
                pass
            # Fallback warning button geometry
            _rail_rvc = RACK_RAIL_H * scale
            _bb_rvc   = rack_y
            _bh_rvc   = rack_y + rack_h - _rail_rvc - _bb_rvc
            _mg_rvc   = 8 * scale
            _ww_rvc   = rw - _mg_rvc * 4
            _wh_rvc   = min(_bh_rvc * 0.78, 160 * scale)
            _wx_rvc   = rack_x + (rw - _ww_rvc) / 2
            _wy_rvc   = _bb_rvc + (_bh_rvc - _wh_rvc) / 2
            _bw_rvc   = min(140 * scale, _ww_rvc * 0.38)
            _bh2_rvc  = max(16 * scale, 7 * scale + 8 * scale)
            _bx_rvc   = _wx_rvc + _ww_rvc - _bw_rvc - 12 * scale
            _by_rvc   = _wy_rvc + 8 * scale
            if _bx_rvc <= mouse_x <= _bx_rvc+_bw_rvc and _by_rvc <= mouse_y <= _by_rvc+_bh2_rvc:
                return {'zone': 'ai_rvc_setup_guide', 'ai_idx': ai_idx}

            # ── RVC full hit test (mirrors rack_rvc.py geometry exactly) ──────
            _mg2          = 8 * scale
            _body_bot2    = rack_y
            _body_top2    = rack_y + rack_h - RACK_RAIL_H * scale
            _body_h2      = _body_top2 - _body_bot2
            _sbar_h2      = max(16*scale, _body_h2*0.07)
            _left_w2      = rw * 0.30
            _right_w2     = rw * 0.24
            _cent_w2      = rw - _left_w2 - _right_w2 - _mg2*4
            _cent_x2      = rack_x + _mg2 + _left_w2 + _mg2
            _lx2          = rack_x + _mg2
            _work_bot2    = _body_bot2 + _sbar_h2 + 2*scale
            _work_top2    = _body_top2 - 2*scale
            _work_h2      = _work_top2 - _work_bot2
            _fs_lbl2      = max(1, int(7*scale))

            # State area + CONVERT/PREVIEW buttons
            _state_h2     = _work_h2 * 0.44
            _state_y2     = _work_bot2 + _work_h2 - _fs_lbl2 - 10*scale - _state_h2
            _btn_h2b      = max(22*scale, _work_h2*0.11)
            _btn_y2b      = _state_y2 - 2*scale - _btn_h2b
            _conv_w2      = _cent_w2 * 0.52
            _conv_x2      = _cent_x2 + 4*scale
            _prev_w2      = _cent_w2 * 0.38
            _prev_x2      = _conv_x2 + _conv_w2 + 4*scale
            if (_conv_x2 <= mouse_x <= _conv_x2+_conv_w2 and
                    _btn_y2b <= mouse_y <= _btn_y2b+_btn_h2b):
                return {'zone': 'ai_rvc_convert', 'ai_idx': ai_idx}
            if (_prev_x2 <= mouse_x <= _prev_x2+_prev_w2 and
                    _btn_y2b <= mouse_y <= _btn_y2b+_btn_h2b):
                return {'zone': 'ai_rvc_preview', 'ai_idx': ai_idx}

            # Output channel < > arrows
            # Geometry mirrors draw exactly:
            #   lbl_tw = _text_width("OUT CH ", fs_lbl)
            #   left arrow at cx+5*scale+lbl_tw, value box at cx+5*scale+lbl_tw+arr_w+2*scale
            #   right arrow at oc_x+oc_s+2*scale = cx+5*scale+lbl_tw+arr_w+2*scale+oc_s+2*scale
            _row2_h2      = max(16*scale, _work_h2*0.08)
            _row2_y2      = _btn_y2b - 2*scale - _row2_h2
            _arr_w2       = max(14*scale, _row2_h2)
            _oc_s2        = max(22*scale, _row2_h2)
            _fs_lbl2_oc   = max(1, int(7*scale))
            import blf as _blf2
            _blf2.size(0, _fs_lbl2_oc)
            _lbl_tw2      = _blf2.dimensions(0, "OUT CH ")[0]
            _minus_x2     = _cent_x2 + 5*scale + _lbl_tw2
            _plus_x2      = _minus_x2 + _arr_w2 + 2*scale + _oc_s2 + 2*scale
            if (_minus_x2 <= mouse_x <= _minus_x2+_arr_w2 and
                    _row2_y2 <= mouse_y <= _row2_y2+_row2_h2):
                return {'zone': 'ai_rvc_outch_dec', 'ai_idx': ai_idx}
            if (_plus_x2 <= mouse_x <= _plus_x2+_arr_w2 and
                    _row2_y2 <= mouse_y <= _row2_y2+_row2_h2):
                return {'zone': 'ai_rvc_outch_inc', 'ai_idx': ai_idx}

            # ADD FROM TIMELINE section
            _add_sec_h2   = min(_work_h2*0.38, 90*scale)
            _add_sep_y2   = _work_bot2 + _add_sec_h2
            _add_ch_s2    = min(16*scale, (_left_w2-10*scale)/9)
            _add_ch_y2    = _work_bot2 + _add_sec_h2 - _fs_lbl2 - _add_ch_s2 - 4*scale
            for _ci in range(9):
                _bx_ac = _lx2 + 5*scale + 16*scale + _ci*(_add_ch_s2+1*scale)
                if (_bx_ac <= mouse_x <= _bx_ac+_add_ch_s2 and
                        _add_ch_y2 <= mouse_y <= _add_ch_y2+_add_ch_s2):
                    return {'zone': 'ai_rvc_add_ch', 'ai_idx': ai_idx, 'ch_idx': _ci}
            _nf_h2        = max(14*scale, _fs_lbl2+4*scale)
            _nf_y2        = _add_ch_y2 - _nf_h2 - 3*scale
            _nf_w2        = _left_w2 - 10*scale
            if (_lx2+5*scale <= mouse_x <= _lx2+5*scale+_nf_w2 and
                    _nf_y2 <= mouse_y <= _nf_y2+_nf_h2):
                return {'zone': 'ai_rvc_name_field', 'ai_idx': ai_idx}
            _ab_h2        = max(16*scale, _fs_lbl2+4*scale)
            _ab_y2        = _nf_y2 - _ab_h2 - 3*scale
            _ab_w2        = _left_w2 - 10*scale
            if (_lx2+5*scale <= mouse_x <= _lx2+5*scale+_ab_w2 and
                    _ab_y2 <= mouse_y <= _ab_y2+_ab_h2):
                return {'zone': 'ai_rvc_add_voice', 'ai_idx': ai_idx}

            # TOPK + REF SECS knob hit test
            # Geometry mirrors rack_rvc.py right panel exactly
            _rx2_k   = rack_x + _mg2 + _left_w2 + _mg2 + _cent_w2 + _mg2
            _ry2_k   = _work_bot2
            _rh2_k   = _work_h2
            _rw2_k   = _right_w2
            _kr_k    = min(18*scale, _rw2_k*0.28, _work_h2*0.18)
            _ky0_k   = _ry2_k + _rh2_k * 0.72
            _kx0_k   = _rx2_k + _rw2_k * 0.30
            _kx1_k   = _rx2_k + _rw2_k * 0.72
            if (_kx0_k-_kr_k <= mouse_x <= _kx0_k+_kr_k and
                    _ky0_k-_kr_k <= mouse_y <= _ky0_k+_kr_k):
                return {'zone': 'ai_rvc_knob', 'ai_idx': ai_idx, 'knob_idx': 0}
            if (_kx1_k-_kr_k <= mouse_x <= _kx1_k+_kr_k and
                    _ky0_k-_kr_k <= mouse_y <= _ky0_k+_kr_k):
                return {'zone': 'ai_rvc_knob', 'ai_idx': ai_idx, 'knob_idx': 1}

            # Voice cards + per-card preview button
            # Geometry mirrors rack_rvc.py exactly:
            #   cards_bot = work_top - fs_lbl - fs_ch*2 - 14*scale
            #   hint_y    = cards_bot - fs_lbl - 2*scale
            try:
                from ui.racks.rack_knnvc import _discover_ref_voices as _gv2
                _vlist2 = _gv2(ai_idx)
            except Exception:
                _vlist2 = []
            _work_top2    = _work_bot2 + _work_h2
            _fs_ch2       = _fs_lbl2
            _cards_bot2   = _work_top2 - _fs_lbl2 - _fs_ch2*2 - 14*scale
            _hint_y2      = _cards_bot2 - _fs_lbl2 - 2*scale
            _card_h2      = max(20*scale, _work_h2*0.11)
            _card_gap2    = 2*scale
            _prev_btn_w2  = max(16*scale, _card_h2*0.7)
            _max_vis2     = max(1, int((_hint_y2 - _add_sep_y2 - 6*scale) / (_card_h2+_card_gap2)))
            _scroll_ofs2  = int(rack.get('rvc_scroll', 0))

            # Scroll arrow hit test — use stashed geometry from draw
            _arr_up2 = rack.get('rvc_arr_up')
            _arr_dn2 = rack.get('rvc_arr_dn')
            if _arr_up2:
                _ax2, _ay2, _aw2, _ah2 = _arr_up2
                if _ax2 <= mouse_x <= _ax2+_aw2 and _ay2 <= mouse_y <= _ay2+_ah2:
                    return {'zone': 'ai_rvc_scroll', 'ai_idx': ai_idx, 'dir': -1}
            if _arr_dn2:
                _dx2, _dy2, _dw2, _dh2 = _arr_dn2
                if _dx2 <= mouse_x <= _dx2+_dw2 and _dy2 <= mouse_y <= _dy2+_dh2:
                    return {'zone': 'ai_rvc_scroll', 'ai_idx': ai_idx, 'dir': 1}

            for _slot in range(_max_vis2):
                _vi2 = _slot + _scroll_ofs2
                if _vi2 >= len(_vlist2): break
                _cy2 = _hint_y2 - _fs_lbl2 - 4*scale - _slot*(_card_h2+_card_gap2) - _card_h2
                if _cy2 < _add_sep_y2 + 2*scale: break
                _pb_x2 = _lx2 + _left_w2 - 4*scale - _prev_btn_w2
                _pb_y2 = _cy2 + _card_h2*0.1
                _pb_h2 = _card_h2*0.8
                if (_pb_x2 <= mouse_x <= _pb_x2+_prev_btn_w2 and
                        _pb_y2 <= mouse_y <= _pb_y2+_pb_h2):
                    return {'zone': 'ai_rvc_voice_preview', 'ai_idx': ai_idx, 'voice_idx': _vi2}
                if (_lx2+4*scale <= mouse_x <= _lx2+_left_w2-4*scale and
                        _cy2 <= mouse_y <= _cy2+_card_h2):
                    return {'zone': 'ai_rvc_voice', 'ai_idx': ai_idx, 'voice_idx': _vi2}
        # VoiceFixer hit test (RESEMBLE rack type)
        if rack.ai_type == "RESEMBLE" and not rack.collapsed:
            _mg_vf      = 8 * scale
            _rail_h_vf  = 32 * scale
            _body_bot_vf = rack_y
            _body_top_vf = rack_y + rack_h - _rail_h_vf
            _body_h_vf  = _body_top_vf - _body_bot_vf
            _sbar_h_vf  = max(16*scale, _body_h_vf*0.07)
            _left_w_vf  = rw * 0.30
            _right_w_vf = rw * 0.24
            _cent_w_vf  = rw - _left_w_vf - _right_w_vf - _mg_vf*4
            _cent_x_vf  = rack_x + _mg_vf + _left_w_vf + _mg_vf
            _lx_vf      = rack_x + _mg_vf
            _work_bot_vf = _body_bot_vf + _sbar_h_vf + 2*scale
            _work_top_vf = _body_top_vf - 2*scale
            _work_h_vf  = _work_top_vf - _work_bot_vf
            _fs_lbl_vf  = max(1, int(7*scale))

            # Mode selector buttons (3 stacked in left panel)
            _mode_sec_h = _work_h_vf * 0.62
            _mode_sep_y = _work_bot_vf + _work_h_vf - _fs_lbl_vf*2 - 18*scale
            _btn_h_m    = min(_mode_sec_h/3 - 3*scale, 28*scale)
            for _mi in range(3):
                _btn_y_m = _mode_sep_y - (_mi+1)*(_btn_h_m+3*scale)
                if _btn_y_m < _work_bot_vf + 2*scale: break
                if (_lx_vf+4*scale <= mouse_x <= _lx_vf+_left_w_vf-4*scale and
                        _btn_y_m <= mouse_y <= _btn_y_m+_btn_h_m):
                    return {'zone': 'ai_vf_mode', 'ai_idx': ai_idx, 'mode_idx': _mi}

            # ENHANCE + PREVIEW buttons
            _state_h_vf = _work_h_vf * 0.44
            _state_y_vf = _work_bot_vf + _work_h_vf - _fs_lbl_vf - 10*scale - _state_h_vf
            _btn_h_vf   = max(22*scale, _work_h_vf*0.11)
            _btn_y_vf   = _state_y_vf - 2*scale - _btn_h_vf
            _enh_w_vf   = _cent_w_vf * 0.52
            _enh_x_vf   = _cent_x_vf + 4*scale
            _prv_w_vf   = _cent_w_vf * 0.38
            _prv_x_vf   = _enh_x_vf + _enh_w_vf + 4*scale
            if (_enh_x_vf <= mouse_x <= _enh_x_vf+_enh_w_vf and
                    _btn_y_vf <= mouse_y <= _btn_y_vf+_btn_h_vf):
                return {'zone': 'ai_vf_enhance', 'ai_idx': ai_idx}
            if (_prv_x_vf <= mouse_x <= _prv_x_vf+_prv_w_vf and
                    _btn_y_vf <= mouse_y <= _btn_y_vf+_btn_h_vf):
                return {'zone': 'ai_vf_preview', 'ai_idx': ai_idx}

            # Output channel < > arrows
            _row2_h_vf  = max(16*scale, _work_h_vf*0.08)
            _row2_y_vf  = _btn_y_vf - 2*scale - _row2_h_vf
            _lbl_tw_vf  = max(1,int(7*scale)) * 6 * 0.6
            _arr_w_vf   = max(14*scale, _row2_h_vf)
            _oc_s_vf    = max(22*scale, _row2_h_vf)
            _minus_x_vf = _cent_x_vf + 4*scale + _lbl_tw_vf
            _plus_x_vf  = _minus_x_vf + _arr_w_vf + 2*scale + _oc_s_vf + 2*scale
            if (_minus_x_vf <= mouse_x <= _minus_x_vf+_arr_w_vf and
                    _row2_y_vf <= mouse_y <= _row2_y_vf+_row2_h_vf):
                return {'zone': 'ai_vf_outch_dec', 'ai_idx': ai_idx}
            if (_plus_x_vf <= mouse_x <= _plus_x_vf+_arr_w_vf and
                    _row2_y_vf <= mouse_y <= _row2_y_vf+_row2_h_vf):
                return {'zone': 'ai_vf_outch_inc', 'ai_idx': ai_idx}

            # Setup guide button
            try:
                btn_geom = rack.get('vf_setup_btn')
                if btn_geom:
                    bx,by,bw,bh = btn_geom
                    if bx <= mouse_x <= bx+bw and by <= mouse_y <= by+bh:
                        return {'zone': 'ai_resemble_setup_guide', 'ai_idx': ai_idx}
            except Exception:
                pass

        # Preset arrows — exact same geometry as draw code
        try:
            from ui.mixer.draw_utils import text_width as _tw_ps
        except ImportError:
            def _tw_ps(t, s): return len(t) * s * 0.6
        ai_type_names_ht = dict(AI_RACK_TYPES)
        aname_ht     = ai_type_names_ht.get(rack.ai_type, rack.ai_type).split("—")[0].strip()
        fs_name_ht   = max(1, int(11*scale))
        col_x_ht     = rack_x + 4*scale
        col_w_ht     = 24*scale
        badge_x_ht   = col_x_ht + col_w_ht + 4*scale
        fs_badge_ht  = max(1, int(13*scale))
        badge_w_ht   = max(22*scale, _tw_ps(str(ai_idx+1), fs_badge_ht) + 12*scale)
        name_dx_ht   = badge_x_ht + badge_w_ht + 6*scale
        name_w_ht    = _tw_ps(aname_ht.upper(), fs_name_ht)

        _del_x_ht  = rack_x + rw - 26*scale
        _on_x_ht   = _del_x_ht - 40*scale - 4*scale
        _ch_s_ht   = 18*scale
        _ch_g_ht   = 3*scale
        _avail_ht  = _on_x_ht - (name_dx_ht + name_w_ht + 8*scale) - 6*scale
        _mfit_ht   = max(1, int(_avail_ht / (_ch_s_ht + _ch_g_ht)))
        _scene_ht2 = bpy.context.scene
        _high_ht   = 0
        if _scene_ht2 and _scene_ht2.sequence_editor:
            for _sh in _scene_ht2.sequence_editor.sequences_all:
                if _sh.type == "SOUND" and _sh.sound:
                    _high_ht = max(_high_ht, _sh.channel - 1)
        _nch_ht    = min(max(9, _high_ht + 1), _mfit_ht)
        _chtot_ht  = _nch_ht * _ch_s_ht + (_nch_ht - 1) * _ch_g_ht
        _chst_ht   = name_dx_ht + name_w_ht + 8*scale + (_avail_ht - _chtot_ht) / 2
        _p_right_ht = _chst_ht - 6*scale
        _p_aw_ht    = 14*scale
        _p_bw_ht    = min(120*scale, _p_right_ht - name_dx_ht - name_w_ht - 8*scale - _p_aw_ht*2 - 4*scale)

        ai_presets_ht = AI_PRESETS.get(rack.ai_type, [])
        if ai_presets_ht and _p_bw_ht > 30*scale:
            p_box_x_ht = _p_right_ht - _p_bw_ht - _p_aw_ht - 2*scale
            p_box_y_ht = rack_top - 27*scale
            p_box_h_ht = 16*scale
            lax_ht = p_box_x_ht - _p_aw_ht + 2*scale
            if lax_ht <= mouse_x <= lax_ht+10*scale and p_box_y_ht <= mouse_y <= p_box_y_ht+p_box_h_ht:
                return {'zone': 'ai_preset_prev', 'ai_idx': ai_idx}
            rax_ht = p_box_x_ht + _p_bw_ht + 2*scale
            if rax_ht <= mouse_x <= rax_ht+10*scale and p_box_y_ht <= mouse_y <= p_box_y_ht+p_box_h_ht:
                return {'zone': 'ai_preset_next', 'ai_idx': ai_idx}

        # Channel buttons — in the rail, mirrors _draw_ai_rack_expanded geometry
        del_x_ht    = rack_x + rw - 26*scale
        on_x_ht     = del_x_ht - 40*scale - 4*scale
        ch_btn_s    = 18*scale
        ch_btn_gap  = 3*scale
        ch_btn_h    = 16*scale
        ch_btn_y_ht = rack_top - 27*scale

        # Always exactly 9 buttons — the group's channel range
        num_ch_ht = 9

        # Reproduce ch_start_x from draw code
        col_x_ht    = rack_x + 4*scale
        col_w_ht    = 24*scale
        badge_x_ht  = col_x_ht + col_w_ht + 4*scale
        fs_badge_ht = max(1, int(13*scale))
        fs_name_ht  = max(1, int(11*scale))
        ai_type_names_ht = dict(AI_RACK_TYPES)
        aname_ht    = ai_type_names_ht.get(rack.ai_type, rack.ai_type).split("—")[0].strip()
        try:
            from ui.mixer.draw_utils import text_width as _tw_ht
        except ImportError:
            def _tw_ht(t, s): return len(t) * s * 0.6
        badge_w_ht    = max(22*scale, _tw_ht(str(ai_idx+1), fs_badge_ht) + 12*scale)
        name_right_ht = (badge_x_ht + badge_w_ht + 6*scale
                         + _tw_ht(aname_ht.upper(), fs_name_ht) + 8*scale)
        avail_w_ht    = on_x_ht - name_right_ht - 6*scale
        max_fit_ht    = max(1, int(avail_w_ht / (ch_btn_s + ch_btn_gap)))
        num_ch_ht     = min(num_ch_ht, max_fit_ht)
        ch_total_w_ht = num_ch_ht * ch_btn_s + (num_ch_ht - 1) * ch_btn_gap
        ch_start_x_ht = name_right_ht + (avail_w_ht - ch_total_w_ht) / 2

        for ci in range(num_ch_ht):
            bx = ch_start_x_ht + ci * (ch_btn_s + ch_btn_gap)
            by = ch_btn_y_ht
            if bx <= mouse_x <= bx + ch_btn_s and by <= mouse_y <= by + ch_btn_h:
                return {'zone': 'ai_channel_btn', 'ai_idx': ai_idx, 'ch_idx': ci}

        # ── Whisper hit test ───────────────────────────────────────────────────
        if rack.ai_type == "WHISPER" and not rack.collapsed:
            _rail_wsp   = RACK_RAIL_H * scale
            _body_bot_w = rack_y
            _body_top_w = rack_y + rack_h - _rail_wsp
            _body_h_w   = _body_top_w - _body_bot_w
            _mg_w       = 8 * scale
            _sbar_h_w   = max(22 * scale, _body_h_w * 0.07)
            _work_bot_w = _body_bot_w + _sbar_h_w + 2 * scale
            _work_top_w = _body_top_w - 2 * scale
            _work_h_w   = _work_top_w - _work_bot_w

            _col_w   = (rw - _mg_w * 4) / 3
            _col1_x  = rack_x + _mg_w
            _col2_x  = _col1_x + _col_w + _mg_w
            _col3_x  = _col2_x + _col_w + _mg_w

            _row_h_w = max(18 * scale, _work_h_w * 0.105)
            _arr_w_w = max(14 * scale, _row_h_w * 0.9)
            _fs_l_w  = max(1, int(8 * scale))
            _fs_sm_w = max(1, int(7 * scale))
            _btn_gap = 3 * scale
            _ch_s    = min(20 * scale, (_col_w - _btn_gap * 8) / 9)
            _fitem_h = max(12 * scale, _fs_sm_w + 4 * scale)

            # ── COL 1: Input channel buttons ──────────────────────────────
            _in_lbl_y  = _work_top_w - _fs_l_w - 4 * scale
            _in_ch_y   = _in_lbl_y - _ch_s - 4 * scale
            for ci in range(9):
                _bx = _col1_x + ci * (_ch_s + _btn_gap)
                if _bx <= mouse_x <= _bx + _ch_s and _in_ch_y <= mouse_y <= _in_ch_y + _ch_s:
                    return {'zone': 'ai_wsp_inch_btn', 'ai_idx': ai_idx, 'ch_idx': ci}

            # ── COL 1: Output channel buttons ─────────────────────────────
            _out_lbl_y = _in_ch_y - _fs_l_w - 10 * scale
            _out_ch_y  = _out_lbl_y - _ch_s - 4 * scale
            for ci in range(9):
                _bx = _col1_x + ci * (_ch_s + _btn_gap)
                if _bx <= mouse_x <= _bx + _ch_s and _out_ch_y <= mouse_y <= _out_ch_y + _ch_s:
                    return {'zone': 'ai_wsp_outch_btn', 'ai_idx': ai_idx, 'ch_idx': ci}

            # ── COL 1: Model selector ‹ › ─────────────────────────────────
            _mdl_lbl_y = _out_ch_y - _fs_l_w - 10 * scale - 8 * scale  # divider gap
            _mdl_sel_y = _mdl_lbl_y - _row_h_w - 2 * scale
            _mdl_nbw   = _col_w - _arr_w_w * 2 - 4 * scale
            _mdl_nbx   = _col1_x + _arr_w_w + 2 * scale
            _mdl_ra_x  = _mdl_nbx + _mdl_nbw + 2 * scale
            if _col1_x <= mouse_x <= _col1_x + _arr_w_w and _mdl_sel_y <= mouse_y <= _mdl_sel_y + _row_h_w:
                return {'zone': 'ai_wsp_model_prev', 'ai_idx': ai_idx}
            if _mdl_ra_x <= mouse_x <= _mdl_ra_x + _arr_w_w and _mdl_sel_y <= mouse_y <= _mdl_sel_y + _row_h_w:
                return {'zone': 'ai_wsp_model_next', 'ai_idx': ai_idx}

            # ── COL 1: Max segment length (chunk) ‹ › ─────────────────────
            _chk_lbl_y = _mdl_sel_y - _fs_sm_w - 4 * scale - _fs_l_w - 8 * scale
            _chk_sel_y = _chk_lbl_y - _row_h_w - 2 * scale
            _chk_nbw   = _col_w - _arr_w_w * 2 - 4 * scale
            _chk_nbx   = _col1_x + _arr_w_w + 2 * scale
            _chk_ra_x  = _chk_nbx + _chk_nbw + 2 * scale
            if _col1_x <= mouse_x <= _col1_x + _arr_w_w and _chk_sel_y <= mouse_y <= _chk_sel_y + _row_h_w:
                return {'zone': 'ai_wsp_chunk_dec', 'ai_idx': ai_idx}
            if _chk_ra_x <= mouse_x <= _chk_ra_x + _arr_w_w and _chk_sel_y <= mouse_y <= _chk_sel_y + _row_h_w:
                return {'zone': 'ai_wsp_chunk_inc', 'ai_idx': ai_idx}

            # ── COL 2: Font scroll list + scroll buttons ───────────────────
            _flist_h      = min(_work_h_w * 0.30, _fitem_h * 7)
            _scroll_btn_w = max(14 * scale, _fitem_h * 0.9)
            _flist_w      = _col_w - _scroll_btn_w - 2 * scale
            _flist_y      = _work_top_w - _fs_l_w - 4 * scale - _flist_h - 2 * scale
            _sbtn_x       = _col2_x + _flist_w + 2 * scale
            _sbtn_h       = _flist_h / 2 - 1 * scale
            # Click on list items
            if _col2_x <= mouse_x <= _col2_x + _flist_w and _flist_y <= mouse_y <= _flist_y + _flist_h:
                _slot = int((_flist_y + _flist_h - mouse_y) / _fitem_h)
                return {'zone': 'ai_wsp_font_pick', 'ai_idx': ai_idx, 'row': _slot}
            # ▲ scroll up
            if (_sbtn_x <= mouse_x <= _sbtn_x + _scroll_btn_w and
                    _flist_y + _sbtn_h + 2 * scale <= mouse_y <= _flist_y + _flist_h):
                return {'zone': 'ai_wsp_font_scroll_up', 'ai_idx': ai_idx}
            # ▼ scroll down
            if (_sbtn_x <= mouse_x <= _sbtn_x + _scroll_btn_w and
                    _flist_y <= mouse_y <= _flist_y + _sbtn_h):
                return {'zone': 'ai_wsp_font_scroll_dn', 'ai_idx': ai_idx}

            # ── COL 2: Font size − + ──────────────────────────────────────
            _sz_lbl_y  = _flist_y - _fs_l_w - 8 * scale
            _sz_y      = _sz_lbl_y - _row_h_w - 2 * scale
            _sz_aw     = max(14 * scale, _row_h_w * 0.9)
            _sz_vw     = _col_w - _sz_aw * 2 - 4 * scale
            # − button
            if _col2_x <= mouse_x <= _col2_x + _sz_aw and _sz_y <= mouse_y <= _sz_y + _row_h_w:
                return {'zone': 'ai_wsp_size_dec', 'ai_idx': ai_idx}
            # + button
            _sz_ra_x = _col2_x + _sz_aw + 2 * scale + _sz_vw + 2 * scale
            if _sz_ra_x <= mouse_x <= _sz_ra_x + _sz_aw and _sz_y <= mouse_y <= _sz_y + _row_h_w:
                return {'zone': 'ai_wsp_size_inc', 'ai_idx': ai_idx}

            # ── COL 2: Style buttons B I U SH BOX ─────────────────────────
            _sty_lbl_y = _sz_y - _fs_l_w - 8 * scale
            _sty_y     = _sty_lbl_y - _row_h_w - 2 * scale
            _sty_bw    = (_col_w - 4 * _btn_gap) / 5
            for si in range(5):
                _sbx = _col2_x + si * (_sty_bw + _btn_gap)
                if _sbx <= mouse_x <= _sbx + _sty_bw and _sty_y <= mouse_y <= _sty_y + _row_h_w:
                    return {'zone': 'ai_wsp_style_btn', 'ai_idx': ai_idx, 'style_idx': si}

            # ── COL 2: Position buttons TOP MID BOT ───────────────────────
            _pos_lbl_y = _sty_y - _fs_l_w - 8 * scale
            _pos_y     = _pos_lbl_y - _row_h_w - 2 * scale
            _pos_bw    = (_col_w - 2 * _btn_gap) / 3
            for pi in range(3):
                _pbx = _col2_x + pi * (_pos_bw + _btn_gap)
                if _pbx <= mouse_x <= _pbx + _pos_bw and _pos_y <= mouse_y <= _pos_y + _row_h_w:
                    return {'zone': 'ai_wsp_pos_btn', 'ai_idx': ai_idx, 'pos_idx': pi}

            # ── COL 3: Language selector ‹ › ──────────────────────────────
            _lng_lbl_y = _work_top_w - _fs_l_w - 4 * scale
            _lng_sel_y = _lng_lbl_y - _row_h_w - 2 * scale
            _lng_nbw   = _col_w - _arr_w_w * 2 - 4 * scale
            _lng_nbx   = _col3_x + _arr_w_w + 2 * scale
            _lng_ra_x  = _lng_nbx + _lng_nbw + 2 * scale
            if _col3_x <= mouse_x <= _col3_x + _arr_w_w and _lng_sel_y <= mouse_y <= _lng_sel_y + _row_h_w:
                return {'zone': 'ai_wsp_lang_prev', 'ai_idx': ai_idx}
            if _lng_ra_x <= mouse_x <= _lng_ra_x + _arr_w_w and _lng_sel_y <= mouse_y <= _lng_sel_y + _row_h_w:
                return {'zone': 'ai_wsp_lang_next', 'ai_idx': ai_idx}

            # ── COL 3: Mode buttons TRANSCRIBE / TRANSLATE→EN ─────────────
            _mode_lbl_y = _lng_sel_y - _fs_sm_w - 3 * scale - _fs_l_w - 8 * scale
            _mode_y     = _mode_lbl_y - _row_h_w - 2 * scale
            _mbw        = (_col_w - _btn_gap) / 2
            if _col3_x <= mouse_x <= _col3_x + _mbw and _mode_y <= mouse_y <= _mode_y + _row_h_w:
                return {'zone': 'ai_wsp_mode_btn', 'ai_idx': ai_idx, 'mode_idx': 0}
            if _col3_x + _mbw + _btn_gap <= mouse_x <= _col3_x + _col_w and _mode_y <= mouse_y <= _mode_y + _row_h_w:
                return {'zone': 'ai_wsp_mode_btn', 'ai_idx': ai_idx, 'mode_idx': 1}

            # ── COL 3: VAD toggle pill ────────────────────────────────────
            _pill_w    = 30 * scale
            _pill_h    = 14 * scale
            _pill_x    = _col3_x + _col_w - _pill_w
            _vad_lbl_y = _mode_y - _fs_sm_w - 3 * scale - 8 * scale - 6 * scale  # note + divider
            _vad_y     = _vad_lbl_y - 1 * scale
            if _pill_x <= mouse_x <= _pill_x + _pill_w and _vad_y <= mouse_y <= _vad_y + _pill_h:
                return {'zone': 'ai_wsp_vad_toggle', 'ai_idx': ai_idx}

            # ── COL 3: SRT toggle pill ────────────────────────────────────
            _srt_lbl_y = _vad_y - _pill_h - 2 * scale - _fs_l_w - 10 * scale
            _srt_y     = _srt_lbl_y - 1 * scale
            if _pill_x <= mouse_x <= _pill_x + _pill_w and _srt_y <= mouse_y <= _srt_y + _pill_h:
                return {'zone': 'ai_wsp_srt_toggle', 'ai_idx': ai_idx}

            # ── Status bar TRANSCRIBE button ──────────────────────────────
            _tbtn_w = min(rw * 0.22, 150 * scale)
            _tbtn_x = rack_x + rw - _tbtn_w - _mg_w
            _tbtn_y = _body_bot_w + 2 * scale
            _tbtn_h = _sbar_h_w - 4 * scale
            if _tbtn_x <= mouse_x <= _tbtn_x + _tbtn_w and _tbtn_y <= mouse_y <= _tbtn_y + _tbtn_h:
                return {'zone': 'ai_wsp_transcribe', 'ai_idx': ai_idx}

        # ── Demucs hit test ────────────────────────────────────────────────────
        if rack.ai_type == "DEMUCS" and not rack.collapsed:
            from ui.racks.rack_demucs import MODELS, MODEL_STEMS, STEM_BITS, STEM_CH_PROPS
            _rail_d     = RACK_RAIL_H * scale
            _body_bot_d = rack_y
            _body_top_d = rack_y + rack_h - _rail_d
            _body_h_d   = _body_top_d - _body_bot_d

            content_w_d = rw - 100 * scale
            left_w_d    = content_w_d * 0.24
            right_w_d   = content_w_d * 0.22
            centre_w_d  = content_w_d - left_w_d - right_w_d
            centre_x_d  = rack_x + left_w_d
            right_x_d   = centre_x_d + centre_w_d

            # Model buttons (left column)
            mx_d = rack_x + 5 * scale
            mw_d = left_w_d - 10 * scale
            model_btn_h = max(13 * scale, (_body_h_d * 0.60 / len(MODELS)) - 3 * scale)
            models_top_d = _body_top_d - max(1, int(7 * scale)) - 8 * scale
            for mi in range(len(MODELS)):
                by_m = models_top_d - (mi + 1) * (model_btn_h + 3 * scale)
                if mx_d <= mouse_x <= mx_d + mw_d and by_m <= mouse_y <= by_m + model_btn_h:
                    return {'zone': 'demucs_model', 'ai_idx': ai_idx, 'model_idx': mi}

            # Preview / Full buttons
            mode_h_d = min(18 * scale, _body_h_d * 0.12)
            mode_y_d = _body_bot_d + 24 * scale
            half_w_d = (mw_d - 3 * scale) / 2
            if mx_d <= mouse_x <= mx_d + half_w_d and mode_y_d <= mouse_y <= mode_y_d + mode_h_d:
                return {'zone': 'demucs_preview', 'ai_idx': ai_idx, 'val': True}
            if (mx_d + half_w_d + 3 * scale <= mouse_x <= mx_d + mw_d and
                    mode_y_d <= mouse_y <= mode_y_d + mode_h_d):
                return {'zone': 'demucs_preview', 'ai_idx': ai_idx, 'val': False}

            # Stem rows (centre column)
            model_idx_d = max(0, min(int(getattr(rack, 'p0', 1.0)), len(MODELS) - 1))
            all_stems_d = MODEL_STEMS[MODELS[model_idx_d]]
            cx_d        = centre_x_d + 5 * scale
            cw_d        = centre_w_d - 10 * scale
            n_stems_d   = len(all_stems_d)
            fs_lbl_d    = max(1, int(7 * scale))
            stems_area_h_d = _body_h_d - fs_lbl_d - 12 * scale - 5 * scale
            row_h_d     = min(22 * scale, (stems_area_h_d - (n_stems_d - 1) * 3 * scale) / n_stems_d)
            stems_top_d = _body_top_d - fs_lbl_d - 8 * scale

            for si, stem in enumerate(all_stems_d):
                row_y_d = stems_top_d - (si + 1) * (row_h_d + 3 * scale)
                if not (row_y_d <= mouse_y <= row_y_d + row_h_d):
                    continue
                is_6s_only = stem in ("piano", "guitar")
                available  = not (is_6s_only and MODELS[model_idx_d] != "htdemucs_6s")
                if not available:
                    continue

                # Checkbox (left ~15px)
                chk_s_d = min(row_h_d - 4 * scale, 10 * scale)
                chk_x_d = cx_d + 3 * scale
                if cx_d <= mouse_x <= cx_d + chk_s_d + 6 * scale:
                    return {'zone': 'demucs_stem_toggle', 'ai_idx': ai_idx,
                            'stem': stem, 'bit': STEM_BITS[stem]}

                # Channel stepper (right ~54px)
                st_w_d = 50 * scale
                st_x_d = cx_d + cw_d - st_w_d
                if st_x_d <= mouse_x <= cx_d + cw_d:
                    third_d = st_w_d / 3.0
                    if mouse_x <= st_x_d + third_d:
                        return {'zone': 'demucs_stem_ch_minus', 'ai_idx': ai_idx,
                                'stem': stem}
                    elif mouse_x >= st_x_d + st_w_d - third_d:
                        return {'zone': 'demucs_stem_ch_plus', 'ai_idx': ai_idx,
                                'stem': stem}

            # Mute original toggle (right column)
            rx2_d   = right_x_d + 4 * scale
            rw2_d   = right_w_d - 8 * scale
            opt_y_d = _body_top_d - fs_lbl_d - 10 * scale - 14 * scale
            opt_h_d = 13 * scale
            if rx2_d <= mouse_x <= rx2_d + rw2_d and opt_y_d <= mouse_y <= opt_y_d + opt_h_d:
                return {'zone': 'demucs_mute_toggle', 'ai_idx': ai_idx}

            # Run button (right column, bottom)
            run_h_d = min(22 * scale, _body_h_d * 0.16)
            run_y_d = _body_bot_d + 4 * scale
            if rx2_d <= mouse_x <= rx2_d + rw2_d and run_y_d <= mouse_y <= run_y_d + run_h_d:
                return {'zone': 'demucs_split', 'ai_idx': ai_idx}

        return {'zone': 'ai_rack_body', 'ai_idx': ai_idx}

    return None


def handle_ai_rack_click(hit, context):
    """Handle a click that hit an AI rack zone. Returns True if consumed."""
    global _ai_popup_open, _ai_popup_x, _ai_popup_y

    zone = hit.get('zone')

    if zone == 'ai_popup_dismiss':
        _ai_popup_open = False
        return True

    if zone == 'ai_add_click':
        _ai_popup_open = not _ai_popup_open
        # Clamp x so popup doesn't overflow right edge — popup is 260 unscaled px
        scale = hit.get('scale', 1.0)
        popup_w = 260 * scale
        raw_x   = hit.get('bx', 0.0)
        max_x   = hit.get('region_w', 9999.0) - popup_w
        _ai_popup_x = min(raw_x, max_x)
        _ai_popup_y = hit.get('by', 0.0)
        # Remember which group this button belongs to so ai_add_type can set it
        global _ai_popup_group_idx
        _ai_popup_group_idx = hit.get('group_idx', 0)
        return True

    if zone == 'ai_add_type':
        atype    = hit['ai_type']
        ai_racks = context.scene.pb_ai_racks
        new_rack = ai_racks.add()
        new_rack.ai_type   = atype
        new_rack.group_idx = _ai_popup_group_idx
        _ai_popup_open     = False
        print(f"[AI RACKS] added {atype} rack (group {_ai_popup_group_idx})")
        return True

    if zone == 'ai_delete':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks.remove(i)
            print(f"[AI RACKS] deleted rack {i}")
        return True

    if zone == 'ai_on_off':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            rack = ai_racks[i]
            rack.enabled = not rack.enabled
            print(f"[AI RACKS] rack {i} enabled={rack.enabled}")
            # Toggle A/B: update both VSE strip.mute (visual) AND engine handle
            # volume via pb_sync_tracks.mute (what the engine actually reads).
            try:
                out_ch = rack.get('dnf_output_channel')   # 1-based VSE channel
                src_ch = rack.get('dnf_source_channel')   # 1-based VSE channel
                seq    = context.scene.sequence_editor
                tracks = getattr(context.scene, "pb_sync_tracks", [])

                if out_ch and src_ch and seq:
                    # src_ch and out_ch are 1-based VSE channels
                    src_idx = src_ch - 1   # 0-based engine channel index
                    out_idx = out_ch - 1

                    # Update VSE strip.mute for visual correctness
                    for s in seq.sequences_all:
                        if s.type == "SOUND":
                            if s.channel == src_ch:
                                s.mute = rack.enabled       # mute original when rack ON
                            elif s.channel == out_ch:
                                s.mute = not rack.enabled   # mute processed when rack OFF

                    # Update pb_sync_tracks.mute — this is what _pb_channel_volume reads
                    if src_idx < len(tracks):
                        tracks[src_idx].mute = rack.enabled
                    if out_idx < len(tracks):
                        tracks[out_idx].mute = not rack.enabled

                    # Tell the engine to update handle volumes immediately
                    try:
                        from core.audio import _pb_engine_update_volume
                        _pb_engine_update_volume(src_idx)
                        _pb_engine_update_volume(out_idx)
                    except Exception as _ve:
                        print(f"[AI RACKS] volume update error: {_ve}")

                    state = "processed" if rack.enabled else "original"
                    print(f"[AI RACKS] A/B: now playing {state} "
                          f"(src_ch={src_ch} mute={rack.enabled}, "
                          f"out_ch={out_ch} mute={not rack.enabled})")
                else:
                    # No processed output yet — just toggle the source channel mute
                    # so the button still does something useful
                    src_idx = rack.get('dnf_source_channel', 0)
                    if src_idx and src_idx - 1 < len(tracks):
                        # Don't mute if no output exists yet
                        pass
            except Exception as _e:
                print(f"[AI RACKS] A/B toggle error: {_e}")
        return True

    if zone == 'ai_collapse':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].collapsed = not ai_racks[i].collapsed
            print(f"[AI RACKS] rack {i} collapsed={ai_racks[i].collapsed}")
        return True

    if zone == 'ai_channel_btn':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i      = hit['ai_idx']
        ch_idx = hit['ch_idx']
        if i < len(ai_racks):
            attr = f'ch{ch_idx}'
            rack = ai_racks[i]
            setattr(rack, attr, not getattr(rack, attr, False))
            state = 'assigned' if getattr(rack, attr) else 'removed'
            print(f"[AI RACKS] ch{ch_idx+1} {state} from ai rack {i}")
        return True

    if zone == 'ai_process':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            rack  = ai_racks[i]
            atype = rack.ai_type
            if atype == "PIPER_TTS":
                try:
                    from core.ai_piper import generate_piper
                    generate_piper(i, context)
                except Exception as e:
                    print(f"[AI RACKS] Piper launch failed: {e}")
                    import traceback; traceback.print_exc()
                    rack.ai_status = "ERROR"
            elif atype == "DEMUCS":
                try:
                    from core.ai_demucs import separate_demucs
                    separate_demucs(i, context)
                except Exception as e:
                    print(f"[AI RACKS] Demucs launch failed: {e}")
                    import traceback; traceback.print_exc()
                    rack.ai_status = "ERROR"
            else:
                print(f"[AI RACKS] {atype} processing not yet implemented")
        return True

    # ── Demucs click handlers ─────────────────────────────────────────────────
    if zone == 'demucs_split':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_demucs import separate_demucs
                separate_demucs(i, context)
            except Exception as e:
                print(f"[DEMUCS] launch failed: {e}")
                ai_racks[i].ai_status = "ERROR"
        return True

    if zone == 'demucs_model':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p0 = float(hit['model_idx'])
            ai_racks[i].ai_status = 'READY'
        return True

    if zone == 'demucs_preview':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p1 = 1.0 if hit['val'] else 0.0
        return True

    if zone == 'demucs_mute_toggle':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            rack    = ai_racks[i]
            cur     = float(getattr(rack, 'p2', 1.0))
            new_val = 0.0 if cur > 0.5 else 1.0
            rack.p2 = new_val
            mute_on = new_val > 0.5

            # Apply the mute to the source channel via the full mixer path:
            # pb_sync_tracks.mute + strip.mute + engine hj.set_mute/set_volume
            try:
                from core.audio import sync_vse_mute
                # Source channel: local ch0-ch8 + group offset = absolute VSE channel
                _grp_off  = getattr(rack, 'group_idx', 0) * 9
                active_chs = [_grp_off + ci + 1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
                if active_chs:
                    src_ch_1based = active_chs[0]
                    src_ch_idx    = src_ch_1based - 1   # 0-based engine index
                    tracks = getattr(context.scene, "pb_sync_tracks", [])
                    if src_ch_idx < len(tracks):
                        tracks[src_ch_idx].mute = mute_on
                    sync_vse_mute(src_ch_idx, mute_on)
                    print(f"[DEMUCS] mute_original ch{src_ch_1based} → {mute_on}")
            except Exception as _me:
                print(f"[DEMUCS] mute_toggle error: {_me}")
        return True

    if zone == 'demucs_stem_toggle':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i   = hit['ai_idx']
        bit = hit['bit']
        if i < len(ai_racks):
            cur  = int(getattr(ai_racks[i], 'p3', 15.0))
            mask = 1 << bit
            ai_racks[i].p3 = float(cur ^ mask)
        return True

    if zone == 'demucs_stem_ch_minus':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i    = hit['ai_idx']
        stem = hit['stem']
        if i < len(ai_racks):
            from ui.racks.rack_demucs import STEM_CH_PROPS
            prop = STEM_CH_PROPS.get(stem)
            if prop:
                cur = int(getattr(ai_racks[i], prop, 0.0))
                setattr(ai_racks[i], prop, float(max(0, cur - 1)))
        return True

    if zone == 'demucs_stem_ch_plus':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i    = hit['ai_idx']
        stem = hit['stem']
        if i < len(ai_racks):
            from ui.racks.rack_demucs import STEM_CH_PROPS
            prop = STEM_CH_PROPS.get(stem)
            if prop:
                cur = int(getattr(ai_racks[i], prop, 0.0))
                setattr(ai_racks[i], prop, float(min(32, cur + 1)))
        return True

    # Piper text area click — activate text field, place the cursor at the
    # clicked character (not always the end), and arm drag-select. The
    # click's own geometry/mouse position rode along on `hit` (see
    # hit_test_ai_racks' ai_piper_text branch) so the cursor index comes
    # from the SAME word-wrap layout the text is actually drawn with.
    if zone == 'ai_piper_text':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            rack = ai_racks[i]
            text = getattr(rack, 'ai_text', '') or ''
            try:
                from ui.racks.rack_piper import cursor_index_from_xy
                idx = cursor_index_from_xy(
                    text, hit['sp_x'], hit['sp_y'], hit['sp_w'], hit['sp_h'],
                    hit['scale'], hit['mouse_x'], hit['mouse_y'])
            except Exception as _cie:
                print(f"[PIPER] cursor hit-test error: {_cie}")
                idx = len(text)
            # Signal interaction.py to activate text field for this rack.
            # sel_start/sel_end start collapsed at the click point; a plain
            # click leaves no selection, dragging extends it from here.
            try:
                import ui.mixer.interaction as _inter
                _inter._active_text_field = {
                    'ai_idx': i,
                    'cursor': idx,
                    'sel_start': idx,
                    'sel_end': idx,
                    'drag_anchor': idx,
                    # Geometry snapshot so drag-select (MOUSEMOVE, no fresh
                    # hit-test each frame) can keep re-deriving the cursor
                    # from the same layout without re-walking the rack tree.
                    'drag_sp_x': hit['sp_x'], 'drag_sp_y': hit['sp_y'],
                    'drag_sp_w': hit['sp_w'], 'drag_sp_h': hit['sp_h'],
                    'drag_scale': hit['scale'],
                }
                print(f"[PIPER] text field activated for rack {i}, cursor={idx}")
            except Exception as _te:
                print(f"[PIPER] text field activate error: {_te}")
        return True

    # Piper CLEAR button — wipe the script text
    if zone == 'ai_piper_clear':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].ai_text   = ""
            ai_racks[i].ai_status = "READY"
            print(f"[PIPER] rack {i} script cleared")
        return True

    # Piper voice card click — store in p4 (separate from knob preset_idx)
    if zone == 'ai_piper_voice':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p4 = float(hit['voice_idx'])
            print(f"[PIPER] rack {i} voice → {hit['voice_idx']}")
        return True

    # Piper preview button — play last generated output
    if zone == 'ai_piper_preview':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_piper import preview_piper
                preview_piper(i, context)
            except Exception as e:
                print(f"[PIPER] preview failed: {e}")
        return True

    # Piper voice scroll arrows — dir=-1 scroll up, dir=1 scroll down
    if zone == 'ai_piper_scroll':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            current = int(getattr(ai_racks[i], 'p5', 0.0))
            ai_racks[i].p5 = float(max(0, current + hit['dir']))
            print(f"[PIPER] rack {i} scroll → {int(ai_racks[i].p5)}")
        return True

    # Piper knob drag handled in interaction.py — consume here
    if zone == 'ai_piper_knob':
        return True

    if zone == 'ai_preset_prev':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            rack    = ai_racks[i]
            presets = AI_PRESET_DATA.get(rack.ai_type, [])
            if presets:
                new_idx = (getattr(rack, 'preset_idx', 0) - 1) % len(presets)
                _load_ai_preset(rack, new_idx)
                print(f"[AI RACKS] preset → {presets[new_idx][0]}")
        return True

    if zone == 'ai_preset_next':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            rack    = ai_racks[i]
            presets = AI_PRESET_DATA.get(rack.ai_type, [])
            if presets:
                new_idx = (getattr(rack, 'preset_idx', 0) + 1) % len(presets)
                _load_ai_preset(rack, new_idx)
                print(f"[AI RACKS] preset → {presets[new_idx][0]}")
        return True

    # kNN-VC CONVERT button
    if zone == 'ai_rvc_convert':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_knnvc import (convert_knnvc, has_rack_output,
                                            _rack_output_path)
                import os, time as _t
                scene = context.scene
                # If a preview has already been processed, just place it on
                # the timeline without running conversion again.
                wav_path = _rack_output_path.get(i)
                if not wav_path or not os.path.exists(wav_path):
                    import glob, tempfile
                    matches = sorted(glob.glob(
                        os.path.join(tempfile.gettempdir(),
                                     f"pb_knnvc_{i}_*.wav")))
                    if matches:
                        wav_path = matches[-1]
                        _rack_output_path[i] = wav_path

                if wav_path and os.path.exists(wav_path):
                    # Copy to project folder so the file survives temp-dir cleanup
                    try:
                        from core.ai_knnvc import _persist_output, _rack_output_path
                        wav_path = _persist_output(wav_path, i)
                        _rack_output_path[i] = wav_path
                    except Exception as _pe:
                        print(f"[KNNVC] WARNING: persist failed: {_pe}")
                    # Place on timeline
                    print(f"[KNNVC] generate: placing output on timeline")
                    rack = ai_racks[i]
                    seq  = scene.sequence_editor
                    if not seq:
                        scene.sequence_editor_create()
                        seq = scene.sequence_editor
                    _grp_off_kn = getattr(rack, 'group_idx', 0) * 9
                    active_chs = [_grp_off_kn + ci + 1 for ci in range(9)
                                  if getattr(rack, f"ch{ci}", False)]
                    target_ch  = int(getattr(rack, "p3", 0.0)) or                                  (active_chs[0]+1 if active_chs else 2)
                    target_ch  = max(1, min(9, target_ch))
                    place_frame = scene.frame_start
                    strip_name  = f"kNNVC_{i}_{int(_t.time()) % 100000}"
                    seq.sequences.new_sound(
                        name=strip_name, filepath=wav_path,
                        channel=target_ch, frame_start=place_frame)
                    rack.ai_status = "DONE"
                    print(f"[KNNVC] placed '{strip_name}' on ch{target_ch}"
                          f" at frame {place_frame}")
                    for window in context.window_manager.windows:
                        for area in window.screen.areas:
                            if area.type in ('SEQUENCE_EDITOR','NODE_EDITOR'):
                                area.tag_redraw()
                else:
                    # No preview yet - run full conversion and place on done
                    convert_knnvc(i, context, preview_only=False)
            except Exception as e:
                print(f"[KNNVC] convert failed: {e}")
                import traceback; traceback.print_exc()
                ai_racks[i].ai_status = "ERROR"
        return True

    # kNN-VC output channel dec/inc
    if zone == 'ai_rvc_outch_dec':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p3', 1.0))
            ai_racks[i].p3 = float(max(1, cur - 1))
        return True

    if zone == 'ai_rvc_outch_inc':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p3', 1.0))
            ai_racks[i].p3 = float(min(9, cur + 1))
        return True

    # kNN-VC PREVIEW button — plays last converted output
    if zone == 'ai_rvc_preview':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_knnvc import preview_knnvc
                preview_knnvc(i, context)
            except Exception as e:
                print(f"[KNNVC] preview failed: {e}")
        return True

    # kNN-VC voice card ▶ preview button
    if zone == 'ai_rvc_voice_preview':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        vi = hit.get('voice_idx', 0)
        print(f"[KNNVC] voice_preview handler: rack={i} voice={vi}")
        if i < len(ai_racks):
            try:
                from core.ai_knnvc import preview_voice_card
                preview_voice_card(i, vi, context)
            except Exception as e:
                import traceback
                print(f"[KNNVC] voice preview failed: {e}")
                traceback.print_exc()
        return True

    # kNN-VC voice card click (select reference)
    if zone == 'ai_rvc_voice':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p4 = float(hit.get('voice_idx', 0))
        return True

    # kNN-VC add-from-timeline channel selector
    if zone == 'ai_rvc_add_ch':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ci = hit.get('ch_idx', 0)
            ai_racks[i].p5 = float(ci)
            # Auto-update default name when channel changes
            try:
                cur_name = ai_racks[i].get('add_voice_name', '')
                import re
                if not cur_name or re.match(r'^CH\d+Sample$', cur_name):
                    ai_racks[i]['add_voice_name'] = f"CH{ci+1}Sample"
            except Exception:
                pass
        return True

    # kNN-VC name field click — activate text input
    if zone == 'ai_rvc_name_field':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            # Use whatever is stored — don't force a default if user cleared it
            cur = ai_racks[i].get('add_voice_name', '')
            if cur is None:
                cur = ''
            cur = str(cur)
            try:
                from ui.mixer import interaction as _inter
            except ImportError:
                try:
                    import sys as _sys
                    _inter = _sys.modules.get('ui.mixer.interaction') or _sys.modules.get('interaction')
                except Exception:
                    _inter = None
            if _inter:
                _inter._active_text_field = {
                    'ai_idx': i, 'field': 'add_voice_name',
                    'cursor': len(cur),
                }
                print(f"[KNNVC] name field active: '{cur}'")
        return True

    # kNN-VC ADD TO VOICES button
    if zone == 'ai_rvc_add_voice':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            if not ai_racks[i].get('add_voice_busy', False):
                try:
                    from core.ai_knnvc import add_voice_from_timeline
                    add_voice_from_timeline(i, context)
                except Exception as e:
                    print(f"[KNNVC] add_voice failed: {e}")
                    import traceback; traceback.print_exc()
        return True

    # RVC scroll arrows
    if zone == 'ai_rvc_scroll':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            rack_s = ai_racks[i]
            direction = hit.get('dir', 1)
            cur_scroll = int(rack_s.get('rvc_scroll', 0))
            max_vis_s  = int(rack_s.get('rvc_max_vis', 1))
            try:
                from ui.racks.rack_knnvc import _discover_ref_voices as _gv3
                n_voices = len(_gv3(i))
            except Exception:
                n_voices = 0
            new_scroll = cur_scroll + direction
            new_scroll = max(0, min(new_scroll, max(0, n_voices - max_vis_s)))
            rack_s['rvc_scroll'] = new_scroll
        return True

    # RVC setup guide button
    if zone == 'ai_rvc_setup_guide':
        import webbrowser
        webbrowser.open("https://github.com/bshall/knn-vc")
        return True

    # Resemble setup guide button
    # VoiceFixer setup guide
    if zone == 'ai_resemble_setup_guide':
        import webbrowser
        webbrowser.open("https://github.com/haoheliu/voicefixer")
        return True

    # VoiceFixer ENHANCE button
    if zone == 'ai_vf_enhance':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_voicefixer import enhance_voicefixer
                enhance_voicefixer(i, context)
            except Exception as e:
                import traceback
                print(f"[VOICEFIXER] enhance failed: {e}")
                traceback.print_exc()
        return True

    # VoiceFixer PREVIEW button
    if zone == 'ai_vf_preview':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_voicefixer import preview_voicefixer
                preview_voicefixer(i, context)
            except Exception as e:
                print(f"[VOICEFIXER] preview failed: {e}")
        return True

    # VoiceFixer mode selector
    if zone == 'ai_vf_mode':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p0 = float(hit.get('mode_idx', 0))
        return True

    # VoiceFixer output channel dec/inc
    if zone == 'ai_vf_outch_dec':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p3', 1.0))
            ai_racks[i].p3 = float(max(1, cur-1))
        return True

    if zone == 'ai_vf_outch_inc':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p3', 1.0))
            ai_racks[i].p3 = float(min(9, cur+1))
        return True

    # ── Whisper handlers ──────────────────────────────────────────────────────
    if zone == 'ai_wsp_inch_btn':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']; ci = hit['ch_idx']
        if i < len(ai_racks):
            rack = ai_racks[i]
            already = all(getattr(rack, f'ch{j}', False) == (j == ci) for j in range(9))
            for j in range(9):
                setattr(rack, f'ch{j}', False)
            if not already:
                setattr(rack, f'ch{ci}', True)
        return True

    if zone == 'ai_wsp_outch_btn':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p3 = float(hit['ch_idx'] + 1)
        return True

    if zone == 'ai_wsp_model_prev':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p0', 1.0))
            ai_racks[i].p0 = float(max(0, cur - 1))
        return True

    if zone == 'ai_wsp_model_next':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_whisper import MODEL_SIZES
                max_idx = len(MODEL_SIZES) - 1
            except ImportError:
                max_idx = 4
            cur = int(getattr(ai_racks[i], 'p0', 1.0))
            ai_racks[i].p0 = float(min(max_idx, cur + 1))
        return True

    if zone == 'ai_wsp_lang_prev':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p1', 0.0))
            ai_racks[i].p1 = float(max(0, cur - 1))
        return True

    if zone == 'ai_wsp_lang_next':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_whisper import LANGUAGES
                max_idx = len(LANGUAGES) - 1
            except ImportError:
                max_idx = 15
            cur = int(getattr(ai_racks[i], 'p1', 0.0))
            ai_racks[i].p1 = float(min(max_idx, cur + 1))
        return True

    if zone == 'ai_wsp_mode_btn':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p2 = float(hit.get('mode_idx', 0))
        return True

    if zone == 'ai_wsp_font_pick':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from ui.racks.rack_whisper import get_system_fonts, _font_scroll
                fonts = get_system_fonts()
                row   = hit.get('row', 0)
                scroll = _font_scroll.get(i, 0)
                idx = scroll + row
                if 0 <= idx < len(fonts):
                    ai_racks[i].wsp_font_path = fonts[idx][1]
                    ai_racks[i].ai_text       = fonts[idx][0]
            except Exception as e:
                print(f"[WHISPER] font pick error: {e}")
        return True

    if zone == 'ai_wsp_font_scroll_up':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from ui.racks.rack_whisper import get_system_fonts, _font_scroll
                fonts = get_system_fonts()
                cur   = _font_scroll.get(i, 0)
                _font_scroll[i] = max(0, cur - 3)
            except Exception as e:
                print(f"[WHISPER] font scroll error: {e}")
        return True

    if zone == 'ai_wsp_font_scroll_dn':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from ui.racks.rack_whisper import get_system_fonts, _font_scroll
                fonts = get_system_fonts()
                cur   = _font_scroll.get(i, 0)
                _font_scroll[i] = min(max(0, len(fonts) - 1), cur + 3)
            except Exception as e:
                print(f"[WHISPER] font scroll error: {e}")
        return True

    if zone == 'ai_wsp_size_dec':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p4', 40.0))
            ai_racks[i].p4 = float(max(12, cur - 2))
        return True

    if zone == 'ai_wsp_size_inc':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = int(getattr(ai_racks[i], 'p4', 40.0))
            ai_racks[i].p4 = float(min(200, cur + 2))
        return True

    if zone == 'ai_wsp_chunk_dec':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = getattr(ai_racks[i], 'wsp_chunk_length', 10)
            ai_racks[i].wsp_chunk_length = max(5, cur - 1)
        return True

    if zone == 'ai_wsp_chunk_inc':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = getattr(ai_racks[i], 'wsp_chunk_length', 10)
            ai_racks[i].wsp_chunk_length = min(30, cur + 1)
        return True

    if zone == 'ai_wsp_style_btn':
        flags = [1, 2, 4, 8, 16]
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']; si = hit.get('style_idx', 0)
        if i < len(ai_racks) and si < len(flags):
            cur = int(getattr(ai_racks[i], 'p5', 0.0))
            ai_racks[i].p5 = float(cur ^ flags[si])
        return True

    if zone == 'ai_wsp_pos_btn':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            ai_racks[i].p6 = float(hit.get('pos_idx', 2))
        return True

    if zone == 'ai_wsp_vad_toggle':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = float(getattr(ai_racks[i], 'p7', 1.0))
            ai_racks[i].p7 = 0.0 if cur > 0.5 else 1.0
        return True

    if zone == 'ai_wsp_srt_toggle':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            cur = ai_racks[i].get('wsp_srt_enabled', True)
            ai_racks[i]['wsp_srt_enabled'] = not cur
        return True

    if zone == 'ai_wsp_transcribe':
        ai_racks = getattr(context.scene, "pb_ai_racks", [])
        i = hit['ai_idx']
        if i < len(ai_racks):
            try:
                from core.ai_whisper import transcribe_whisper
                transcribe_whisper(i, context)
            except Exception as e:
                print(f"[WHISPER] transcribe launch failed: {e}")
                import traceback; traceback.print_exc()
                ai_racks[i].ai_status = "ERROR"
        return True

    if zone == 'ai_wsp_setup_guide':
        import webbrowser
        webbrowser.open("https://github.com/SYSTRAN/faster-whisper")
        return True

    return False


# ---------------------------------------------------------------------------
# AI rack registration
# ---------------------------------------------------------------------------
def register_ai_racks():
    bpy.utils.register_class(PB_AIRackSettings)
    bpy.types.Scene.pb_ai_racks = bpy.props.CollectionProperty(
        type=PB_AIRackSettings)
    print("[AI RACKS] registered")


def unregister_ai_racks():
    try:
        bpy.utils.unregister_class(PB_AIRackSettings)
        del bpy.types.Scene.pb_ai_racks
    except Exception:
        pass
    print("[AI RACKS] unregistered")