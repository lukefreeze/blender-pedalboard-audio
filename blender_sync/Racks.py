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
# Shared state — set by Loader.py before calling draw_racks()
# ---------------------------------------------------------------------------
_UI_SCALE  = 1.0
_SCROLL_X  = 0.0
_SCROLL_Y  = 0.0
_IS_PLAYING = False

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

# Reorder dropdown state
_reorder_open     = False   # whether the reorder dropdown is open
_reorder_rack_idx = -1      # which rack's badge was clicked
_reorder_x        = 0.0
_reorder_y        = 0.0

# ---------------------------------------------------------------------------
# Rack dimensions (in unscaled pixels, multiplied by UI_SCALE at draw time)
# ---------------------------------------------------------------------------
# Rack width matches the fader section exactly:
# left margin=30px, each fader strip=120px wide, stride=135px
# right edge of 9th fader = 30 + 8*135 + 120 = 1230px
# rack starts at 30px → rack width = 1230 - 30 = 1200px
RACK_WIDTH          = 1200         # matches 9-fader section width exactly
RACK_EXPANDED_H     = 260
RACK_EXPANDED_H_MB  = 400   # taller for multiband 3x2 knob grid
RACK_EXPANDED_H_EQ  = 580   # tall studio rack — display + spacious 7-band knob row
RACK_EXPANDED_H_RV  = 340   # reverb — display + 5-knob row
RACK_COLLAPSED_H    = 36
RACK_MARGIN_TOP     = 40           # gap between fader section and racks
RACK_GAP            = 4            # gap between rack units
RACK_RAIL_H         = 32           # top rail height

# Knob layout (left section of expanded rack)
KNOB_SECTION_W      = 360
KNOB_START_X        = 55
KNOB_Y_OFFSET       = 130          # from rack top
KNOB_SPACING        = 68

# Spectrum display
SPEC_X              = 430
SPEC_W              = 480
SPEC_H              = 200
SPEC_Y_OFFSET       = 50           # from rack top
SPEC_BANDS          = 48           # number of frequency bars

# GR meter section
GR_X                = 862
GR_BAR_W            = 12
GR_BAR_SPACING      = 16

# Channel buttons (right section)
CH_BTN_X            = 870
CH_BTN_SIZE         = 26
CH_BTN_SPACING      = 28
CH_BTN_ROWS         = 3

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
    "EQ": [
        ("LOW",       "Low",        -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("LOW_MID",   "Low Mid",    -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("MID",       "Mid",        -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("HIGH_MID",  "High Mid",   -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("HIGH",      "High",       -24.0, 24.0,   0.0, "{:+.0f}dB"),
    ],
    "REVERB": [
        ("ROOM",      "Room",         0.0,  1.0,   0.5, "{:.0%}"),
        ("DAMP",      "Damp",         0.0,  1.0,   0.5, "{:.0%}"),
        ("WIDTH",     "Width",        0.0,  1.0,   1.0, "{:.0%}"),
        ("WET",       "Wet",          0.0,  1.0,   0.3, "{:.0%}"),
        ("DRY",       "Dry",          0.0,  1.0,   1.0, "{:.0%}"),
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
}

# PRESET_DATA: dict of effect_type -> list of (name, [normalised_params])
# COMP_SINGLE: [thr, ratio, atk, rel, makeup, knee]  (p0-p5)
# COMP_MULTI:  [thr*4, ratio*4, atk*4, rel*4, gain*4, knee*4] (p0-p23)
PRESET_DATA = {'COMP_SINGLE': [('Vocal Compress', [0.55, 0.10526315789473684, 0.0990990990990991, 0.0707070707070707, 0.16666666666666666, 0.14893617021276595]), ('Gentle Glue', [0.4, 0.02631578947368421, 0.29929929929929927, 0.1919191919191919, 0.08333333333333333, 0.3191489361702128]), ('Drum Bus', [0.7, 0.2631578947368421, 0.009009009009009009, 0.09090909090909091, 0.125, 0.06382978723404255]), ('Heavy Squash', [0.25, 0.47368421052631576, 0.04904904904904905, 0.04040404040404041, 0.3333333333333333, 0.02127659574468085]), ('Transparent', [0.5, 0.05263157894736842, 0.49949949949949946, 0.494949494949495, 0.041666666666666664, 0.40425531914893614]), ('Broadcast Voice', [0.65, 0.15789473684210525, 0.04904904904904905, 0.050505050505050504, 0.25, 0.06382978723404255]), ('Intimate Whisper', [0.45, 0.07894736842105263, 0.39939939939939934, 0.29292929292929293, 0.125, 0.3191489361702128]), ('Telephone', [0.8, 1.0, 0.0, 0.010101010101010102, 0.4166666666666667, 0.0]), ('Radio Ready', [0.75, 0.3684210526315789, 0.019019019019019017, 0.030303030303030304, 0.3333333333333333, 0.0425531914893617]), ('Vintage Tape', [0.55, 0.10526315789473684, 0.19919919919919918, 0.1919191919191919, 0.125, 0.23404255319148937]), ('Dark Presence', [0.75, 0.5789473684210527, 0.009009009009009009, 0.020202020202020204, 0.4166666666666667, 0.02127659574468085]), ('Robot Voice', [0.85, 1.0, 0.0, 0.0, 0.5, 0.0]), ('Underwater', [0.5, 0.15789473684210525, 0.7997997997997998, 0.898989898989899, 0.16666666666666666, 0.48936170212765956]), ('Announcer', [0.6, 0.13157894736842105, 0.07907907907907907, 0.09090909090909091, 0.20833333333333334, 0.10638297872340426]), ('Whisper to Shout', [0.25, 0.7368421052631579, 0.04904904904904905, 0.1414141414141414, 0.3333333333333333, 0.14893617021276595])], 'COMP_MULTI': [('Voice Over Clean', [0.45, 0.5, 0.55, 0.6, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.19919919919919918, 0.14914914914914915, 0.0990990990990991, 0.07907907907907907, 0.1919191919191919, 0.1414141414141414, 0.09090909090909091, 0.0707070707070707, 0.5, 0.5, 0.5, 0.5, 0.3191489361702128, 0.23404255319148937, 0.19148936170212766, 0.14893617021276595]), ('Voice Over Warm', [0.5, 0.55, 0.6, 0.65, 0.07894736842105263, 0.10526315789473684, 0.10526315789473684, 0.05263157894736842, 0.14914914914914915, 0.0990990990990991, 0.07907907907907907, 0.04904904904904905, 0.1414141414141414, 0.09090909090909091, 0.0707070707070707, 0.050505050505050504, 0.5833333333333334, 0.4583333333333333, 0.4166666666666667, 0.5, 0.23404255319148937, 0.19148936170212766, 0.14893617021276595, 0.10638297872340426]), ('Voice Over Bright', [0.45, 0.5, 0.55, 0.65, 0.05263157894736842, 0.07894736842105263, 0.10526315789473684, 0.10526315789473684, 0.19919919919919918, 0.11911911911911911, 0.07907907907907907, 0.04904904904904905, 0.1717171717171717, 0.1111111111111111, 0.0707070707070707, 0.050505050505050504, 0.4583333333333333, 0.5, 0.5416666666666666, 0.5833333333333334, 0.2765957446808511, 0.19148936170212766, 0.14893617021276595, 0.10638297872340426]), ('Male Voice', [0.55, 0.65, 0.5, 0.45, 0.10526315789473684, 0.15789473684210525, 0.07894736842105263, 0.05263157894736842, 0.0990990990990991, 0.07907907907907907, 0.14914914914914915, 0.19919919919919918, 0.09090909090909091, 0.0707070707070707, 0.1414141414141414, 0.1919191919191919, 0.5416666666666666, 0.5, 0.4583333333333333, 0.4166666666666667, 0.14893617021276595, 0.10638297872340426, 0.23404255319148937, 0.3191489361702128]), ('Female Voice', [0.45, 0.55, 0.65, 0.6, 0.05263157894736842, 0.10526315789473684, 0.15789473684210525, 0.10526315789473684, 0.19919919919919918, 0.11911911911911911, 0.05905905905905906, 0.07907907907907907, 0.1717171717171717, 0.09090909090909091, 0.050505050505050504, 0.0707070707070707, 0.4583333333333333, 0.5, 0.5416666666666666, 0.5, 0.3191489361702128, 0.19148936170212766, 0.10638297872340426, 0.14893617021276595]), ('Narration', [0.4, 0.45, 0.5, 0.55, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.29929929929929927, 0.2492492492492492, 0.19919919919919918, 0.14914914914914915, 0.29292929292929293, 0.24242424242424243, 0.1919191919191919, 0.1414141414141414, 0.5, 0.5, 0.5, 0.5, 0.40425531914893614, 0.3191489361702128, 0.23404255319148937, 0.19148936170212766]), ('Podcast Ready', [0.55, 0.6, 0.65, 0.65, 0.10526315789473684, 0.10526315789473684, 0.15789473684210525, 0.10526315789473684, 0.0990990990990991, 0.07907907907907907, 0.05905905905905906, 0.04904904904904905, 0.09090909090909091, 0.0707070707070707, 0.050505050505050504, 0.050505050505050504, 0.5416666666666666, 0.5416666666666666, 0.5416666666666666, 0.5, 0.19148936170212766, 0.14893617021276595, 0.10638297872340426, 0.10638297872340426]), ('Broadcast', [0.7, 0.75, 0.7, 0.65, 0.2631578947368421, 0.3684210526315789, 0.2631578947368421, 0.21052631578947367, 0.039039039039039033, 0.029029029029029027, 0.039039039039039033, 0.04904904904904905, 0.04040404040404041, 0.030303030303030304, 0.04040404040404041, 0.050505050505050504, 0.625, 0.5833333333333334, 0.5833333333333334, 0.5416666666666666, 0.06382978723404255, 0.06382978723404255, 0.06382978723404255, 0.10638297872340426]), ('Dark Presence', [0.8, 0.6, 0.45, 0.4, 0.47368421052631576, 0.15789473684210525, 0.05263157894736842, 0.05263157894736842, 0.019019019019019017, 0.0990990990990991, 0.19919919919919918, 0.29929929929929927, 0.030303030303030304, 0.09090909090909091, 0.1919191919191919, 0.29292929292929293, 0.6666666666666666, 0.5416666666666666, 0.4583333333333333, 0.4166666666666667, 0.02127659574468085, 0.10638297872340426, 0.23404255319148937, 0.3191489361702128]), ('Whisper Voice', [0.3, 0.35, 0.4, 0.45, 0.02631578947368421, 0.02631578947368421, 0.05263157894736842, 0.05263157894736842, 0.39939939939939934, 0.3493493493493493, 0.2492492492492492, 0.19919919919919918, 0.3434343434343434, 0.29292929292929293, 0.24242424242424243, 0.1919191919191919, 0.5, 0.5, 0.5, 0.5, 0.48936170212765956, 0.40425531914893614, 0.3191489361702128, 0.23404255319148937]), ('Telephone MB', [0.85, 0.9, 0.9, 0.75, 1.0, 0.7368421052631579, 0.7368421052631579, 0.47368421052631576, 0.0, 0.0, 0.0, 0.009009009009009009, 0.0, 0.0, 0.0, 0.04040404040404041, 0.0, 0.6666666666666666, 0.6666666666666666, 0.0, 0.0, 0.0, 0.0, 0.0]), ('Loud & Proud', [0.8, 0.75, 0.75, 0.7, 0.47368421052631576, 0.3684210526315789, 0.3684210526315789, 0.2631578947368421, 0.009009009009009009, 0.019019019019019017, 0.019019019019019017, 0.029029029029029027, 0.020202020202020204, 0.030303030303030304, 0.030303030303030304, 0.04040404040404041, 0.7083333333333334, 0.625, 0.625, 0.5833333333333334, 0.02127659574468085, 0.02127659574468085, 0.06382978723404255, 0.06382978723404255]), ('Gentle Master', [0.3, 0.35, 0.4, 0.45, 0.02631578947368421, 0.02631578947368421, 0.02631578947368421, 0.02631578947368421, 0.49949949949949946, 0.39939939939939934, 0.3493493493493493, 0.29929929929929927, 0.3939393939393939, 0.3434343434343434, 0.29292929292929293, 0.24242424242424243, 0.5, 0.5, 0.5, 0.5, 0.48936170212765956, 0.40425531914893614, 0.3191489361702128, 0.23404255319148937]), ('Drum Glue', [0.65, 0.6, 0.5, 0.4, 0.21052631578947367, 0.15789473684210525, 0.07894736842105263, 0.05263157894736842, 0.019019019019019017, 0.04904904904904905, 0.14914914914914915, 0.2492492492492492, 0.050505050505050504, 0.0707070707070707, 0.1414141414141414, 0.1919191919191919, 0.5833333333333334, 0.5416666666666666, 0.5, 0.4583333333333333, 0.06382978723404255, 0.10638297872340426, 0.19148936170212766, 0.2765957446808511]), ('Master Bus', [0.45, 0.5, 0.55, 0.6, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.05263157894736842, 0.2492492492492492, 0.19919919919919918, 0.14914914914914915, 0.0990990990990991, 0.24242424242424243, 0.1919191919191919, 0.1414141414141414, 0.09090909090909091, 0.5, 0.5, 0.5, 0.5, 0.3191489361702128, 0.23404255319148937, 0.19148936170212766, 0.14893617021276595]), ('EXTREME Crush', [0.85, 0.85, 0.85, 0.85, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8333333333333334, 0.8333333333333334, 0.8333333333333334, 0.8333333333333334, 0.0, 0.0, 0.0, 0.0]), ('Robot Voice MB', [0.8, 0.85, 0.85, 0.7, 0.8947368421052632, 1.0, 1.0, 0.3684210526315789, 0.0, 0.0, 0.0, 0.0, 0.005050505050505051, 0.0, 0.0, 0.020202020202020204, 0.25, 0.75, 0.8333333333333334, 0.16666666666666666, 0.0, 0.0, 0.0, 0.0]), ('Underwater MB', [0.5, 0.4, 0.75, 0.85, 0.10526315789473684, 0.05263157894736842, 0.3684210526315789, 0.7368421052631579, 0.49949949949949946, 0.39939939939939934, 0.04904904904904905, 0.0, 0.494949494949495, 0.3939393939393939, 0.04040404040404041, 0.0, 0.9166666666666666, 0.5833333333333334, 0.3333333333333333, 0.08333333333333333, 0.48936170212765956, 0.3191489361702128, 0.06382978723404255, 0.0])]}
# EQ presets — 21 params: p0-p6=gain(norm 0-1, 0.5=0dB ±24dB),
#                          p7-p13=freq(log-norm 0-1, 20Hz-20kHz),
#                          p14-p20=Q(log-norm 0-1, 0.1-10.0)
PRESET_DATA['EQ'] = [
    ('Presence Boost', [0.5, 0.5, 0.5, 0.5625, 0.604167, 0.5625, 0.5, 0.200687, 0.365637, 0.514689, 0.725364, 0.825707, 0.92605, 0.967697, 0.422549, 0.5, 0.5, 0.539591, 0.5, 0.5, 0.422549]),
    ('Low Cut',        [0.125, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.23299, 0.365637, 0.514689, 0.666667, 0.799313, 0.899657, 0.967697, 0.422549, 0.5, 0.5, 0.5, 0.5, 0.5, 0.422549]),
    ('Air',            [0.5, 0.5, 0.458333, 0.5, 0.5, 0.583333, 0.625, 0.200687, 0.39203, 0.514689, 0.666667, 0.799313, 0.92605, 0.967697, 0.422549, 0.539591, 0.5, 0.5, 0.5, 0.5, 0.422549]),
    ('Mud Remove',     [0.5, 0.416667, 0.4375, 0.541667, 0.541667, 0.5, 0.5, 0.200687, 0.333333, 0.433677, 0.725364, 0.799313, 0.899657, 0.967697, 0.422549, 0.588046, 0.588046, 0.5, 0.5, 0.5, 0.422549]),
    ('Telephone',      [0.125, 0.5625, 0.583333, 0.5625, 0.375, 0.25, 0.125, 0.23299, 0.39203, 0.53402, 0.69897, 0.799313, 0.899657, 0.967697, 0.422549, 0.650515, 0.588046, 0.588046, 0.588046, 0.588046, 0.422549]),
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

# Legacy name lists for rack preset display
PRESETS = {
    "COMP_SINGLE": [p[0] for p in PRESET_DATA["COMP_SINGLE"]],
    "COMP_MULTI":  [p[0] for p in PRESET_DATA["COMP_MULTI"]],
    "EQ":          [p[0] for p in PRESET_DATA["EQ"]],
    "REVERB":      [p[0] for p in PRESET_DATA["REVERB"]],
    "NOISE_GATE":  ["Tight Gate", "Soft Gate", "Drum Gate", "Vocal Gate", "Natural"],
    "DELAY":       ["Slapback", "Quarter Note", "Dotted 8th", "Ping Pong", "Tape Echo"],
}

# ---------------------------------------------------------------------------
# Property group — one rack instance
# ---------------------------------------------------------------------------
class PB_RackSettings(bpy.types.PropertyGroup):
    effect_type:      bpy.props.StringProperty(default="COMP_SINGLE")
    enabled:          bpy.props.BoolProperty(default=True)
    collapsed:        bpy.props.BoolProperty(default=False)
    preset_idx:       bpy.props.IntProperty(default=0)
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
    ch9:  bpy.props.BoolProperty(default=False)
    ch10: bpy.props.BoolProperty(default=False)
    ch11: bpy.props.BoolProperty(default=False)
    ch12: bpy.props.BoolProperty(default=False)
    ch13: bpy.props.BoolProperty(default=False)
    ch14: bpy.props.BoolProperty(default=False)
    ch15: bpy.props.BoolProperty(default=False)
    ch16: bpy.props.BoolProperty(default=False)
    ch17: bpy.props.BoolProperty(default=False)
    ch18: bpy.props.BoolProperty(default=False)
    ch19: bpy.props.BoolProperty(default=False)
    ch20: bpy.props.BoolProperty(default=False)
    ch21: bpy.props.BoolProperty(default=False)
    ch22: bpy.props.BoolProperty(default=False)
    ch23: bpy.props.BoolProperty(default=False)
    ch24: bpy.props.BoolProperty(default=False)
    ch25: bpy.props.BoolProperty(default=False)
    ch26: bpy.props.BoolProperty(default=False)
    ch27: bpy.props.BoolProperty(default=False)
    ch28: bpy.props.BoolProperty(default=False)
    ch29: bpy.props.BoolProperty(default=False)
    ch30: bpy.props.BoolProperty(default=False)
    ch31: bpy.props.BoolProperty(default=False)
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
    """Return list of assigned channel indices (0-based) for a rack."""
    return [i for i in range(32) if getattr(rack, f'ch{i}', False)]


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
    # Reverb: default to Medium Room (index 2) — more useful starting point
    if rack.effect_type == "REVERB":
        rack.preset_idx = 2
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
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch  = batch_for_shader(shader, "TRI_STRIP",
                              {"pos": [(x,y),(x+w,y),(x,y+h),(x+w,y+h)]})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line(x1, y1, x2, y2, color, width=1.0):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch  = batch_for_shader(shader, "LINES",
                              {"pos": [(x1,y1),(x2,y2)]})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def _draw_circle(cx, cy, r, color, filled=True):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    """Draw channel assignment buttons — one per active VSE channel.
    Channels are laid out in rows of 3, only showing channels that
    actually have strips in the VSE sequence editor.
    """
    btn_s = CH_BTN_SIZE * scale
    gap   = 4 * scale
    fs    = max(1, int(10*scale))

    # Show DEFAULT_CHANNELS minimum buttons, expand if higher channels exist
    try:
        from Loader import DEFAULT_CHANNELS
    except Exception:
        DEFAULT_CHANNELS = 9

    scene   = bpy.context.scene
    highest = 0
    if scene and scene.sequence_editor:
        for s in scene.sequence_editor.sequences_all:
            if s.type == "SOUND" and s.sound:
                highest = max(highest, s.channel - 1)

    num_buttons = max(DEFAULT_CHANNELS, highest + 1)
    active      = list(range(num_buttons))

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    for col_idx, ch_idx in enumerate(active):
        row = col_idx // 3
        col = col_idx % 3
        bx  = rx + col * (btn_s + gap)
        by  = ry - row * (btn_s + gap) - btn_s

        attr     = f'ch{ch_idx}' if ch_idx < 9 else None
        assigned = getattr(rack, attr, False) if attr else False

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

        label = str(ch_idx + 1)
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

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

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

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

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

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

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



def _draw_rack_expanded(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a fully expanded rack unit."""
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = (RACK_EXPANDED_H_MB  if rack.effect_type == "COMP_MULTI"
           else RACK_EXPANDED_H_EQ if rack.effect_type == "EQ"
           else RACK_EXPANDED_H_RV if rack.effect_type == "REVERB"
           else RACK_EXPANDED_H) * scale

    # --- CHASSIS ---
    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.28,0.28,0.28,1.0))
    batch.draw(shader)

    # --- TOP RAIL ---
    rail_h = RACK_RAIL_H * scale
    _draw_rect(rx, ry+rh-rail_h, rw, rail_h, (0.16, 0.16, 0.16, 1.0))
    _draw_rect(rx, ry+rh-rail_h-2*scale, rw, 2*scale, (0.1,0.1,0.1,1.0))

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
    etype  = rack.effect_type
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
    p_box_x   = rx + 280*scale
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
    shader2 = gpu.shader.from_builtin("UNIFORM_COLOR")
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

    # --- BODY CONTENT — dispatch by effect type ---
    body_h = rh - RACK_RAIL_H * scale
    spec_h = min(SPEC_H * scale, body_h - 50*scale)

    if etype == "COMP_MULTI":
        _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "EQ":
        _draw_eq_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "REVERB":
        _draw_reverb_body(rx, ry, rw, rh, rack, rack_idx, scale)
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



def _draw_rack_collapsed(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a collapsed rack unit — single row."""
    rw = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh = RACK_COLLAPSED_H * scale

    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    shader_d = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    global _UI_SCALE, _SCROLL_X, _SCROLL_Y
    _UI_SCALE = ui_scale
    _SCROLL_X = scroll_x
    _SCROLL_Y = scroll_y

    scene = bpy.context.scene
    if not scene: return

    racks = getattr(scene, "pb_racks", [])

    # Calculate Y position: below the fader+numbox section
    # base_y in Loader = height - 150*scale - scroll_y
    # fader bottom = base_y - FADER_TRACK_BOTTOM*scale
    # numbox bottom = fader_bottom - numbox_h - margin
    from Loader import FADER_TRACK_BOTTOM, NUMBOX_H, NUMBOX_Y_OFFSET
    base_y       = region_height - 150*ui_scale - scroll_y
    # fader bottom moves down by send section height
    try:
        from Loader import _send_section_height, SEND_MIN_SLOTS
        n_racks_s = len(getattr(scene, "pb_racks", []))
        send_h    = _send_section_height(n_racks_s, ui_scale)
    except Exception:
        send_h = 0.0
    fader_bot_y  = base_y - FADER_TRACK_BOTTOM*ui_scale - send_h
    rack_top_y   = fader_bot_y - (NUMBOX_H + RACK_MARGIN_TOP)*ui_scale

    # Draw racks from top downward
    cur_y = rack_top_y
    rack_x = 30*ui_scale + scroll_x

    # Dynamic rack width — matches however many faders are currently shown
    # Each fader is 120px wide with 135px stride, starting at 30px
    scene2       = bpy.context.scene
    num_tracks   = len(getattr(scene2, "pb_sync_tracks", [])) if scene2 else 9
    num_tracks   = max(9, num_tracks)
    dynamic_rack_w = num_tracks * 135 - 15  # = right edge of last fader - left margin

    for i, rack in enumerate(racks):
        if rack.collapsed:
            rh = RACK_COLLAPSED_H * ui_scale
        elif rack.effect_type == "COMP_MULTI":
            rh = RACK_EXPANDED_H_MB * ui_scale
        elif rack.effect_type == "EQ":
            rh = RACK_EXPANDED_H_EQ * ui_scale
        elif rack.effect_type == "REVERB":
            rh = RACK_EXPANDED_H_RV * ui_scale
        else:
            rh = RACK_EXPANDED_H * ui_scale

        # Cull if completely off screen vertically
        if cur_y - rh > region_height or cur_y < -rh:
            cur_y -= rh + RACK_GAP*ui_scale
            continue

        try:
            if rack.collapsed:
                _draw_rack_collapsed(rack_x, cur_y - rh, rack, i,
                                     ui_scale, dynamic_rack_w)
            else:
                _draw_rack_expanded(rack_x, cur_y - rh, rack, i,
                                    ui_scale, dynamic_rack_w)
        except Exception as e:
            print(f"[RACKS] draw error rack {i}: {e}")

        cur_y -= rh + RACK_GAP*ui_scale

    # Add rack button
    _draw_add_rack_button(rack_x, cur_y - 28*ui_scale, ui_scale,
                          dynamic_rack_w)

    # Popup (drawn on top of everything)
    if _popup_open:
        _draw_add_popup(_popup_x, _popup_y, ui_scale)

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

            shader2 = gpu.shader.from_builtin("UNIFORM_COLOR")

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
    """
    import math
    try:
        from Loader import FADER_TRACK_BOTTOM, NUMBOX_H, _send_section_height
        scene = bpy.context.scene
        if not scene: return None
        n_racks_s = len(getattr(scene, "pb_racks", []))
        send_h    = _send_section_height(n_racks_s, ui_scale)
    except Exception:
        return None

    scene = bpy.context.scene
    if not scene: return None
    racks = getattr(scene, "pb_racks", [])

    base_y      = region_height - 150*ui_scale - scroll_y
    fader_bot_y = base_y - FADER_TRACK_BOTTOM*ui_scale - send_h
    rack_top_y  = fader_bot_y - (NUMBOX_H + RACK_MARGIN_TOP)*ui_scale

    cur_y  = rack_top_y
    rack_x = 30*ui_scale + scroll_x

    # Dynamic rack width
    nt  = max(9, len(getattr(scene, "pb_sync_tracks", [])))
    rw  = (nt * 135 - 15) * ui_scale

    knob_r = 20 * ui_scale

    for i, rack in enumerate(racks):
        if rack.collapsed:
            rh = RACK_COLLAPSED_H * ui_scale
            cur_y -= rh + RACK_GAP * ui_scale
            continue



        rh = (RACK_EXPANDED_H_MB  if rack.effect_type == "COMP_MULTI"
              else RACK_EXPANDED_H_EQ if rack.effect_type == "EQ"
              else RACK_EXPANDED_H_RV if rack.effect_type == "REVERB"
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
            knob_r       = max(min(knob_area_w*0.15, 16*ui_scale), 10*ui_scale)
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
                div_x       = bx + fader_strip_w
                knob_base_x = div_x + 4*ui_scale
                knob_col_gap3 = knob_area_w / 3
                kx0 = knob_base_x + knob_col_gap3 * 0.5
                kx1 = knob_base_x + knob_col_gap3 * 1.5
                kx2 = knob_base_x + knob_col_gap3 * 2.5
                ky1 = ctrl_y + 4*ui_scale + knob_label_h + knob_r   # bottom row
                ky0 = ky1 + knob_r + knob_gap + knob_label_h + knob_r  # top row

                kr  = knob_r + 4*ui_scale  # hit radius with tolerance
                if math.dist((rx,ry),(kx0,ky0)) < kr:
                    return (i, band)        # threshold
                if math.dist((rx,ry),(kx1,ky0)) < kr:
                    return (i, band+4)      # ratio
                if math.dist((rx,ry),(kx2,ky0)) < kr:
                    return (i, band+20)     # knee
                if math.dist((rx,ry),(kx0,ky1)) < kr:
                    return (i, band+8)      # attack
                if math.dist((rx,ry),(kx1,ky1)) < kr:
                    return (i, band+12)     # release
                if math.dist((rx,ry),(kx2,ky1)) < kr:
                    return (i, band+16)     # gain (also reachable via fader)
        else:
            # EQ: 5 columns of (Gain, Freq, Q) knobs
            if rack.effect_type == "EQ":
                # 7-band layout: p0-p6=gain, p7-p13=freq, p14-p20=Q
                # Geometry mirrors _draw_eq_body exactly
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
                    if math.dist((rx, ry), (col_cx_eq, row_gain_eq)) < kr_gain_eq  + tol_eq:
                        return (i, bi)           # gain p0-p6
                    if math.dist((rx, ry), (col_cx_eq, row_freq_eq)) < kr_small_eq + tol_eq:
                        return (i, bi + 7)       # freq p7-p13
                    _, bftype_ht, _, _ = EQ7_BANDS_HT[bi]
                    if bftype_ht == "peak":
                        if math.dist((rx, ry), (col_cx_eq, row_q_eq)) < kr_small_eq + tol_eq:
                            return (i, bi + 14)  # Q p14-p20
            elif rack.effect_type == "REVERB":
                # 5 knobs: p0=Room, p1=Damp, p2=Wet, p3=Pre-dly, p4=Width
                # Geometry mirrors _draw_reverb_body knob strip exactly
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
                tol_rv      = 8 * ui_scale
                for ki in range(N_KNOBS_RV):
                    cx_rv = disp_x_rv + (ki + 0.5) * col_w_rv
                    if math.dist((rx, ry), (cx_rv, row_knob_rv)) < kr_rv + tol_rv:
                        return (i, ki)           # p0-p4
            else:
                # Single band 2x3 knob grid — must mirror draw geometry exactly
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
        from Loader import FADER_TRACK_BOTTOM, NUMBOX_H
    except Exception:
        return None

    scene = bpy.context.scene
    if not scene: return None
    racks = getattr(scene, "pb_racks", [])

    # Reorder dropdown check — must happen before everything else
    # since the dropdown floats on top
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

    base_y       = region_height - 150*ui_scale - scroll_y
    try:
        from Loader import _send_section_height
        n_racks_s2 = len(getattr(scene, "pb_racks", [])) if scene else 0
        send_h2    = _send_section_height(n_racks_s2, ui_scale)
    except Exception:
        send_h2 = 0.0
    fader_bot_y  = base_y - FADER_TRACK_BOTTOM*ui_scale - send_h2
    rack_top_y   = fader_bot_y - (NUMBOX_H + RACK_MARGIN_TOP)*ui_scale

    cur_y  = rack_top_y
    rack_x = 30*ui_scale + scroll_x
    # Dynamic width — same calculation as draw_racks
    scene3     = bpy.context.scene
    nt         = len(getattr(scene3, "pb_sync_tracks", [])) if scene3 else 9
    nt         = max(9, nt)
    rw         = (nt * 135 - 15) * ui_scale

    # Check popup first (drawn on top)
    if _popup_open:
        pw  = 200*ui_scale
        ph  = (len(EFFECT_TYPES)*28 + 10)*ui_scale + 24*ui_scale
        if _popup_x <= rx <= _popup_x+pw and _popup_y <= ry <= _popup_y+ph:
            # Which effect was clicked? Mirror draw calculation
            title_h = 24*ui_scale
            for i, (etype, _) in enumerate(EFFECT_TYPES):
                by = _popup_y + ph - title_h - (i+1)*28*ui_scale - 4*ui_scale
                bh = 24*ui_scale
                if by <= ry <= by+bh:
                    return {'zone': 'popup_effect', 'effect_type': etype}
            return {'zone': 'popup_dismiss'}
        else:
            return {'zone': 'popup_dismiss'}

    for i, rack in enumerate(racks):
        if rack.collapsed:
            rh = RACK_COLLAPSED_H * ui_scale
        elif rack.effect_type == "COMP_MULTI":
            rh = RACK_EXPANDED_H_MB * ui_scale
        elif rack.effect_type == "EQ":
            rh = RACK_EXPANDED_H_EQ * ui_scale
        elif rack.effect_type == "REVERB":
            rh = RACK_EXPANDED_H_RV * ui_scale
        else:
            rh = RACK_EXPANDED_H * ui_scale

        rack_y = cur_y - rh

        if rack_x <= rx <= rack_x+rw and rack_y <= ry <= rack_y+rh:
            # Hit in this rack — determine zone
            rail_top = rack_y + rh - RACK_RAIL_H*ui_scale

            # Collapse button — dedicated 24px button at far left of rail
            col_btn_x2 = rack_x + 4*ui_scale
            col_btn_y2 = rack_y + rh - 28*ui_scale
            col_btn_w2 = 24*ui_scale
            col_btn_h2 = 20*ui_scale
            if (col_btn_x2 <= rx <= col_btn_x2 + col_btn_w2 and
                    col_btn_y2 <= ry <= col_btn_y2 + col_btn_h2):
                return {'zone': 'collapse', 'rack_idx': i}

            # Delete button
            del_x = rack_x + rw - 26*ui_scale
            del_y = rack_y + rh - 27*ui_scale
            if del_x <= rx <= del_x+18*ui_scale and del_y <= ry <= del_y+16*ui_scale:
                return {'zone': 'delete_rack', 'rack_idx': i}

            # Rack number badge — starts after collapse button
            badge_x2 = rack_x + 4*ui_scale + 24*ui_scale + 4*ui_scale
            badge_y2 = rack_y + rh - 28*ui_scale
            badge_w2 = 38*ui_scale
            badge_h2 = 20*ui_scale
            if (badge_x2 <= rx <= badge_x2 + badge_w2 and
                    badge_y2 <= ry <= badge_y2 + badge_h2):
                return {'zone': 'rack_badge', 'rack_idx': i,
                        'bx': badge_x2, 'by': badge_y2+badge_h2}

            # ON/OFF button (expanded: shifted left of delete)
            on_x = rack_x + rw - 68*ui_scale
            on_y = rack_y + rh - 27*ui_scale
            if on_x <= rx <= on_x+40*ui_scale and on_y <= ry <= on_y+16*ui_scale:
                return {'zone': 'on_off', 'rack_idx': i}

            # Delete button (collapsed)
            if rack.collapsed:
                cdel_x = rack_x + rw - 28*ui_scale
                cdel_y = rack_y + rh/2 - 7*ui_scale
                if cdel_x <= rx <= cdel_x+18*ui_scale and cdel_y <= ry <= cdel_y+14*ui_scale:
                    return {'zone': 'delete_rack', 'rack_idx': i}

            # Preset arrows
            p_box_x = rack_x + 280*ui_scale
            p_box_w = 160*ui_scale
            if rack_y+rh-30*ui_scale <= ry <= rack_y+rh-12*ui_scale:
                if p_box_x-18*ui_scale <= rx <= p_box_x:
                    return {'zone': 'preset_left', 'rack_idx': i}
                if p_box_x+p_box_w <= rx <= p_box_x+p_box_w+18*ui_scale:
                    return {'zone': 'preset_right', 'rack_idx': i}

            # Channel buttons (expanded only)
            if not rack.collapsed:
                ch_right_x = rack_x + rw - 100*ui_scale
                ch_top_y   = rack_y + rh - RACK_RAIL_H*ui_scale - 20*ui_scale
                btn_s      = CH_BTN_SIZE * ui_scale
                btn_gap    = 4*ui_scale
                # Use active channels only — same layout as _draw_channel_buttons
                try:
                    from Loader import DEFAULT_CHANNELS as _DC
                except Exception:
                    _DC = 9
                scene2  = bpy.context.scene
                highest2 = 0
                if scene2 and scene2.sequence_editor:
                    for _s in scene2.sequence_editor.sequences_all:
                        if _s.type == "SOUND" and _s.sound:
                            highest2 = max(highest2, _s.channel - 1)
                active_chs = list(range(max(_DC, highest2 + 1)))
                for col_idx, ch_idx in enumerate(active_chs):
                    row = col_idx // 3
                    col = col_idx % 3
                    bx  = ch_right_x + col*(btn_s+btn_gap)
                    by  = ch_top_y - row*(btn_s+btn_gap) - btn_s
                    if bx <= rx <= bx+btn_s and by <= ry <= by+btn_s:
                        return {'zone': 'channel_btn',
                                'rack_idx': i, 'ch_idx': ch_idx}

            # Collapsed channel buttons
            if rack.collapsed:
                assigned = get_rack_channels(rack)
                btn_x    = rack_x + 250*ui_scale
                btn_y    = rack_y + rh/2 - 8*ui_scale
                btn_w    = 20*ui_scale
                btn_h    = 16*ui_scale
                btn_gap  = 4*ui_scale
                for j, ch_idx in enumerate(assigned):
                    bx = btn_x + j*(btn_w+btn_gap)
                    if bx <= rx <= bx+btn_w and btn_y <= ry <= btn_y+btn_h:
                        return {'zone': 'channel_btn',
                                'rack_idx': i, 'ch_idx': ch_idx}

            return {'zone': 'rack_body', 'rack_idx': i}

        cur_y -= rh + RACK_GAP*ui_scale

    # Add rack button
    add_y = cur_y - 28*ui_scale
    if rack_x <= rx <= rack_x+rw and add_y <= ry <= add_y+28*ui_scale:
        return {'zone': 'add_rack', 'click_x': rx, 'click_y': ry}

    return None


# ---------------------------------------------------------------------------
# Handle a click result from hit_test
# ---------------------------------------------------------------------------
def _trigger_reprocess(rack_idx, rack, context):
    """Reprocess all channels affected by a rack change.
    Handles: preset change, ON/OFF toggle, channel assign/deassign.
    When a channel is deassigned or rack is bypassed, that channel
    reverts to unprocessed audio.
    """
    try:
        from Loader import (_pb_reprocess_channel, _pb_wire_rack_to_engine,
                            _pb_channels)
        # Reprocess currently assigned channels (new settings)
        assigned = get_rack_channels(rack)
        for ch in assigned:
            _pb_wire_rack_to_engine(ch)
            _pb_reprocess_channel(ch)

        # Also reprocess any channels that are playing but not assigned
        # (covers deselect case — they need to revert to unprocessed audio)
        for ch in list(_pb_channels.keys()):
            if ch not in assigned:
                # This channel might have been deselected — rewire clears
                # its effect slot, reprocess plays unprocessed audio
                _pb_wire_rack_to_engine(ch)
                _pb_reprocess_channel(ch)
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
        rack  = context.scene.pb_racks.add()
        rack.effect_type = etype
        rack.collapsed   = False
        init_rack_defaults(rack)
        print(f"[RACKS] added {etype} rack")
        return True

    if zone == 'add_rack':
        # Open popup — store click position and draw BELOW it
        _popup_open = True
        click_y = hit.get('click_y', 200.0)
        click_x = hit.get('click_x', 200.0)
        popup_h = (len(EFFECT_TYPES) * 28 + 10) * _UI_SCALE + 24 * _UI_SCALE
        # Draw below the click point, centred horizontally on click
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
        i      = hit['rack_idx']
        ch_idx = hit['ch_idx']
        racks  = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            attr = f'ch{ch_idx}'
            rack = racks[i]
            setattr(rack, attr, not getattr(rack, attr, False))
            state = 'assigned' if getattr(rack, attr) else 'removed'
            print(f"[RACKS] ch{ch_idx+1} {state} from rack {i} — reprocessing")
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
    print("[RACKS] registered")


def unregister_racks():
    try:
        bpy.utils.unregister_class(PB_RackSettings)
        del bpy.types.Scene.pb_racks
    except Exception:
        pass
    print("[RACKS] unregistered")
