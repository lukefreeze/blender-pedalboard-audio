# =============================================================================
# core/constants.py
# All layout constants for the Pedalboard HUD in one place.
# Dimensions are UNSCALED pixels — multiply by UI_SCALE at draw time.
# To resize any UI element, change its constant here and nowhere else.
# =============================================================================

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ADDON_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ADDON_DIR, "ui", "assets", "skins")

# ---------------------------------------------------------------------------
# Engine / channel limits
# ---------------------------------------------------------------------------
MAX_CHANNELS        = 32
DEFAULT_CHANNELS    = 9      # minimum fader count shown (matches Blender VSE default)

# ---------------------------------------------------------------------------
# Fader strip layout
# ---------------------------------------------------------------------------
STRIP_W             = 120    # width of one channel strip
STRIP_STRIDE        = 135    # left-edge to left-edge spacing between strips
STRIP_LEFT_MARGIN   = 30     # x offset of first strip from canvas left

FADER_MIN           = 0.001
FADER_MAX           = 1.25
FADER_HEIGHT        = 180    # draggable travel distance
FADER_HANDLE_H      = 20
FADER_HANDLE_W      = 40
FADER_HANDLE_X_OFF  = 45     # from strip left edge

FADER_TRACK_BOTTOM  = 580    # distance from base_y down to fader bottom

METER_W             = 10     # VU bar width
METER_X_OFF         = 30     # from strip left edge

NUMBOX_H            = 18
NUMBOX_Y_OFFSET     = 560    # distance below base_y (just below fader)

# ---------------------------------------------------------------------------
# Knob layout
# ---------------------------------------------------------------------------
GAIN_MIN            = 0.25   # ~-12 dB
GAIN_MAX            = 4.0    # ~+12 dB
GAIN_DEFAULT        = 1.0

KNOB_GAIN_R         = 20     # gain knob radius
KNOB_EQ_R           = 16     # EQ knob radius
KNOB_PAN_R          = 14     # pan knob radius
KNOB_GAIN_Y_OFF     = 100    # from base_y down
EQ_KNOB_START_Y     = 175    # from base_y down (before send height added)
EQ_KNOB_SPACING     = 50     # between H/M/L knobs

# ---------------------------------------------------------------------------
# Mute / Solo buttons
# ---------------------------------------------------------------------------
M_BTN_X_OFF         = 10
M_BTN_Y_OFF         = 50     # from base_y down
M_BTN_W             = 45
M_BTN_H             = 30
S_BTN_X_OFF         = 65

# ---------------------------------------------------------------------------
# Send buttons (rack assignment column in each strip)
# ---------------------------------------------------------------------------
SEND_BTN_H          = 20
SEND_BTN_GAP        = 3
SEND_MIN_SLOTS      = 3
SEND_START_Y        = 165    # distance below base_y where section starts

EFFECT_ABBREV = {
    "COMP_SINGLE": "CMP",
    "COMP_MULTI":  "MBC",
    "EQ":          "EQ",
    "REVERB":      "R",
    "NOISE_GATE":  "NG",
    "DELAY":       "D",
}

# ---------------------------------------------------------------------------
# Scrollbar
# ---------------------------------------------------------------------------
SB_TRACK_PX         = 8      # track thickness (unscaled, always thin)
SB_THUMB_PX         = 6      # thumb thickness
SB_INSET            = 1      # gap between thumb and track edge
SB_MARGIN           = 2      # gap between scrollbar and canvas edge
SB_RADIUS           = 3      # corner radius of pill thumb
SB_H_RANGE          = 5000.0 # max horizontal scroll content range
SB_V_RANGE          = 2000.0 # max vertical scroll content range

# ---------------------------------------------------------------------------
# Auto-fit content dimensions (at scale 1.0)
# ---------------------------------------------------------------------------
# width  = STRIP_LEFT_MARGIN + DEFAULT_CHANNELS*STRIP_STRIDE + STRIP_W - 15
AUTOFIT_CONTENT_W   = 1260.0
# height = top gap (150) + strip body (650)
AUTOFIT_CONTENT_H   = 800.0
AUTOFIT_PADDING     = 0.95   # scale factor — 5% padding around content

# ---------------------------------------------------------------------------
# Meter
# ---------------------------------------------------------------------------
METER_POLL_INTERVAL = 0.05
METER_DECAY         = 0.10
PEAK_HOLD_TIME      = 1.5

# ---------------------------------------------------------------------------
# Double-click
# ---------------------------------------------------------------------------
DOUBLE_CLICK_TIME   = 0.4    # seconds
