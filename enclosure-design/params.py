"""
OpenFlight launch monitor enclosure — master parameter file v2.
Single source of truth. Units: mm, degrees. Tags: READ / INFERRED / UNKNOWN (measure!).

DECIDED (2026-07-29):
  Radar: OPS243-A + IWR6843LEVM; custom 24GHz PCB later (cassette swap).
  K-LD7s: OUT of enclosure entirely.
  Sensor zone: swappable cassette tray (radars + camera + windows on one keyed tray).
  Windows: three cut PC panes + printed dark bezel overlay (one-inset look).
  Power: Geekworm X1206 V2.0 UPS (4x21700) under Pi 5; 12V DC jack input.
  Display: ROADOM 7" (CALIPERS REQUIRED), fed from X1206 USB-A, not Pi.
  Deployment: outdoor-capable, IP54 target. Material: PETG or ASA (not PLA).
  Peripherals v1: SEN-14262, LIS3DH (Adafruit 2809), BME280 STEMMA (Adafruit 2652).
  No 2nd-camera provision, no Jetson bay.
"""

# ============================================================
# 1. ENVELOPE (locked, direction B two-zone swept)
# ============================================================
ENV_H = 285.0
ENV_W = 183.0
ENV_D = 129.0
CORNER_R = 18.0
SCREEN_TILT_DEG = 25.0          # OPTIMIZED (was 41): viewer at address 3.1m away,
                                # eye 1.52-1.75m, screen center ~0.21m ->
                                # atan(1.39/3.1) ~ 24deg. Less sky glare than 41.
RADAR_TILT_DEG = 3.0            # lower radar face near-plumb

# Carry handle (R50-style integrated slot, upper rear shoulder)
HANDLE_SLOT_W = 110.0           # gloved-hand clearance
HANDLE_SLOT_H = 32.0
HANDLE_EDGE_R = 12.0            # comfortable grip radius
HANDLE_WALL_T = 4.0             # thickened walls around slot; load into body shell
UNIT_MASS_EST_KG = 3.0          # prints + cells + screen + boards

# ============================================================
# 2. WALLS & MATERIAL
# ============================================================
WALL_T = 3.0                    # PETG/ASA; PLA excluded (outdoor heat/UV)
BEZEL_T = 2.0                   # printed dark bezel overlay on cassette face

# ============================================================
# 3. WINDOWS — three cut solid-PC panes, glued (silicone = adhesive + seal)
# Half-wave: Tm = c/(2*f*sqrt(er)); er PC = 2.9. NEVER printed, NEVER metallic tint.
# ============================================================
WIN_24G_T = 3.6                 # OPS243 pane, smoked ok, +/-0.1 [RFbeam AN-03]
WIN_24G_STANDOFF = 6.2          # min, lambda0/2 @24.125GHz; 12.4 also fine
WIN_60G_T = 1.4                 # IWR6843 pane, +/-0.1 [TI SWRA705: 1.42 @62GHz]
WIN_60G_STANDOFF = 2.42         # lambda0/2 @62GHz; use n*2.42 (4.84, 7.26...)
WIN_CAM_T = 2.0                 # optically clear, NOT smoked, AR if possible
WIN_RECESS_CLEAR = 0.3          # pane-to-printed-recess clearance per side
WIN_GLUE_LEDGE = 3.0            # perimeter ledge behind each pane
# Bezel cutouts must clear beams: OPS243 20x24deg; IWR6843 120deg az / 80deg el
# -> IWR6843 cutout edges chamfered 45deg+, no bezel material in beam cone.

# ============================================================
# 4. SENSOR MOUNTS (fixed, minimal-but-solid — cassette dropped 2026-07-29)
# ============================================================
# Each sensor: simple keyed mount into the lower body. On hardware changes,
# freed volume is reused and only affected mounts reprint.
# Camera bracket + IWR mount-plate datum reference the SAME printed feature
# (camera<->radar extrinsics from printed geometry).
MOUNT_KEY_D = 4.0               # dowel keys where orientation matters
MOUNT_SCREWS = "M3 into ruthex inserts"

# ============================================================
# 5. FASTENING — heat-set inserts (ruthex RX-M3x5.7) [READ]
# ============================================================
INSERT_HOLE_D = 4.0
INSERT_L = 5.7
INSERT_POCKET_DEPTH = 6.7
BOSS_WALL_MIN = 1.6
BOSS_OD = 8.0
SCREW_CLEAR_D = 3.4

# ============================================================
# 5b. LEVELING FEET (ground placement — no tripod)
# ============================================================
FOOT_N = 4                      # 4x threaded leveling feet, adjust-to-touch
FOOT_THREAD = "M8"              # stud into heat-set insert in base boss
FOOT_INSERT = "ruthex M8 (hole ~10.2, L 12.7 — VERIFY on ruthex datasheet)"
FOOT_ADJUST_RANGE = 15.0        # usable height adjustment per foot
FOOT_PAD_D = 40.0               # wide rubber pad — don't sink into grass
FOOT_BOSS_OD = 18.0             # ribbed into base; bosses clear of gasket path
FOOT_LOCKNUT = True             # knurled locknut so feet hold setting
# Workflow: set on ground, watch LIS3DH bubble level on screen, twist feet
# until level. Feet do coarse level + anti-rock; sensor handles residual tilt.

# ============================================================
# 6. MAIN SEAM GASKET (IP54, Full Swing pattern)
# ============================================================
GASKET_CORD_D = 3.0             # silicone cord, Shore A 40-50
GASKET_GROOVE_W = 4.0
GASKET_GROOVE_DEPTH = 2.3       # 23% compression
GASKET_SCREW_PITCH_MAX = 60.0

# ============================================================
# 7. COMPONENTS
# ============================================================
# Raspberry Pi 5 [READ plan / UNKNOWN Z -> official Jun-2026 STEP]
PI5_L, PI5_W = 85.0, 56.0
PI5_HOLE_D = 2.7
PI5_HOLE_PATTERN = (58.0, 49.0)
PI5_HOLE_INSET = 3.5

# Geekworm X1206 V2.0 UPS [READ wiki / UNKNOWN stack height & hole coords]
X1206_L, X1206_W = 108.0, 85.0  # mounts UNDER Pi 5 via GPIO pogo pins
X1206_CELLS = "4x21700 1S4P"    # NOT 18650 (that's X1202, only 25.5W < 26W peak)
X1206_OUT_MAX_A = 6.0           # 5.1V, 30.6W total across pogo+XH2.54+2xUSB-A
X1206_IN = "9-18V DC5521 jack (>=3A) for charge-through at full load; USB-C 5V/5A alt"
X1206_I2C_ADDR = 0x36           # MAX17040 fuel gauge
X1206_CASE_REF = (112.0, 91.0, 64.0)  # X1206-C1 case bounds Pi+UPS stack [READ]
# CONFIRMED vs full X12xx lineup (X1200-X1209): only board with 9-18V input +
# 6A + 4x21700 + ALON always-on. VERIFY silkscreen = V2.0 (V1.1 dies on 12V,
# discontinued 2026-04). Pogo feed is the sole Pi power path, rated for the
# 6A output; FAQ Q1 explicitly supports 5A mode. Watch contact quality
# (loose standoffs -> undervoltage / 0x36 vanishes). Unprotected cells only.
# EEPROM: PSU_MAX_CURRENT=5000, usb_max_current_enable=1, POWER_OFF_ON_HALT=1.
# GPIO6 (pin31) = AC-loss detect, GPIO16 (pin36) = charge enable. SoC only, no current telemetry.

# OPS243-A [READ envelope / UNKNOWN holes -> email OmniPreSense for CAD]
OPS243_L, OPS243_W, OPS243_T = 90.0, 75.0, 12.0
OPS243_BEAM = (20.0, 24.0)

# TI IWR6843LEVM [READ SWRU585 / UNKNOWN outline+holes -> TI Altium pkg (login)]
IWR6843_BAND = (60.0, 64.0)     # GHz; FCC 15.255 for eventual product cert
IWR6843_ARRAY = "3TX/4RX, 12-el virtual, antennas TOP face (etched FR4)"
IWR6843_FOV = (120.0, 80.0)     # az x el
IWR6843_PWR_W = 3.0             # planning number, INFERRED; micro-USB 5V
# TI warns single USB can't sustain 3-TX operation -> power from X1206 5.1V rail.

# InnoMaker CAM-IMX296 [READ features / UNKNOWN holes — CALIPERS]
CAM_BOARD = 38.0
CAM_FLANGE_C, CAM_FLANGE_CS = 17.526, 12.526
# J3 trigger + J2 opto strobe. XTR = 1.8V logic — level-shift from Pico.

# ROADOM 7" LE070-01 [UNKNOWN — 8 caliper measurements gate CAD]
SCREEN_NOM = (165.1, 121.9, 12.7)
# Power: SETTLED in hardware-integration doc — HDMI + lower/Touch micro-USB +
# 3-pin GPIO cable: ONE 5V wire -> pin 2, GND -> pin 14 (~550mA).
# Pin 4 stays free for OPS243 J3 pin 9. Do NOT use upper/Power micro-USB.
# With X1206 the header 5V pins are fed via pogo rail — verify total pogo-path
# current (Pi + screen + OPS243 ~ up to 5A+) is within X1206 pogo rating.

# Pico [READ outline / verify holes vs STEP]
PICO_L, PICO_W, PICO_HOLE_D = 51.0, 21.0, 2.1

# SEN-14262 [UNKNOWN — calipers incl. mic capsule center for acoustic port]
# R17 = 47kOhm (30dB). Runs at 3.3V (under 3.5V min spec) — enclosure should
# leave room for future 5V feed + 10k/20k divider on GATE near the board.
# Wiring to route: 3-way GATE splice -> OPS HOST_INT (J3 p3) + Pi BCM17 (p11).
SEN14262_NOM = (44.0, 23.0)

# LIS3DH — Adafruit 2809. DELIBERATE choice (leveling session: vs ADXL355/$47,
# SCL3300/$77 — print tolerance +-0.5deg dominates, so cheap sensor + zeroing wins).
# MOUNTS ON THE RADAR MOUNT PLATE, NOT THE CASE (adjustable mount would break
# a case-mounted sensor's reference). TWO locators, not one screw it can rotate on.
LIS3DH_NOM = (25.4, 17.8)

# ============================================================
# 7b. ADJUSTABLE RADAR MOUNT + LEVELING REFERENCES (leveling design doc §7)
# ============================================================
IWR_MOUNT_TILT_NOM = 10.0       # deg up; measured range values 5.5-10.4
IWR_MOUNT_TILT_RANGE = (0.0, 15.0)  # adjustment range of the mount plate
REF_PAD_MIN = 40.0              # flat reference pad, PARALLEL to antenna face,
                                # >=40mm so a magnetic angle-gauge base seats
# Pad + radar mounting datum printed off the SAME feature, same print orientation
# (layer/warp errors partially cancel: ~+-0.5 -> ~+-0.2 residual).
# NO steel (gauge inserts, screws) near radar apertures.
# Pad must be gauge-accessible at assembly (cassette open or external access).
# OPEN QUESTION: antenna tilts with mount; window is near-plumb -> up to ~15deg
# incidence through half-wave pane. Options: tilt pane to IWR_MOUNT_TILT_NOM
# (incidence <=5deg over range) vs accept incidence loss. UNRESOLVED.

# BME280 STEMMA — Adafruit 2652 [READ outline / verify holes]
BME280_L, BME280_W = 25.2, 18.0
# P + T + RH all needed for air density (why BME280 beats BMP390 here).
# Self-heats: flush behind shaded intake grille, away from Pi/screen.

# ============================================================
# 8. PORT ISLAND (rear, panel-mount)
# ============================================================
USBC_PANEL_HOLE_D = 22.0        # Adafruit 4218; panel <=16 thick
HDMI_PANEL_CUTOUT = (25.8, 12.0)  # Adafruit 4054 snap-in; LOCAL WALL <=5.0
DC_JACK = "DC5521 panel mount"  # NEW: X1206 12V charge-through input
DC_JACK_HOLE_D = 12.5           # typical DC5521 panel bushing [INFERRED, verify part]

# ============================================================
# 9. PRINT (Ender 3 Pro, PETG/ASA)
# ============================================================
BED_X, BED_Y, BED_Z = 220.0, 220.0, 250.0
SPLIT_UPPER_H = 120.0           # 285 -> 120 + 165, seam = main gasket seam
SPLIT_LOWER_H = 165.0
SPLIT_KEY_D = 4.0
FIT_CLEARANCE = 0.20
LID_CLEARANCE = 0.30

# ============================================================
# 10. POWER / THERMAL BUDGET
# ============================================================
POWER_TYP_W = 14.0              # hardware-integration doc budget (supersedes 16.5)
POWER_PEAK_W = 19.4             # peak; 3.8A @5.1V vs X1206 6A — ample headroom
POWER_UNKNOWN = "IWR6843 actual draw unmeasured — inline USB meter, gates battery calc"
RUNTIME_H_EST = 4.7             # 4x 21700 5000mAh (~72Wh, 92% conv) @ 14W
VENT_RIB_GAP = 2.5
THERMAL_WARN_C = 53.0
THERMAL_SHUTDOWN_C = 55.0
