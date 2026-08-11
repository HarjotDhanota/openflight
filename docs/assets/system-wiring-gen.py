W, H = 1300, 1520
X0, COL = 315, 30
Y_ODD, Y_EVEN = 560, 600
HDR_X0, HDR_X1 = 300, 900

C = dict(
    v5="#D0342C",
    v33="#E8811F",
    gnd="#1B1B1B",
    tx="#8A4FBF",
    rx="#2B6FD0",
    gate="#E8C21F",
    sda="#1E8A4F",
    scl="#17A2B8",
    usb="#6E7378",
    fut="#9AA0A6",
    ink="#3d3d3a",
)


def px(n):
    return X0 + ((n - 1) // 2) * COL


def py(n):
    return Y_ODD if n % 2 else Y_EVEN


FUNC = {
    1: "3V3",
    2: "5V",
    3: "SDA",
    4: "5V",
    5: "SCL",
    6: "GND",
    7: "G4",
    8: "TX0",
    9: "GND",
    10: "RX0",
    11: "G17",
    12: "G18",
    13: "G27",
    14: "GND",
    15: "G22",
    16: "G23",
    17: "3V3",
    18: "G24",
    19: "MOSI",
    20: "GND",
    21: "MISO",
    22: "G25",
    23: "SCLK",
    24: "CE0",
    25: "GND",
    26: "CE1",
    27: "ID",
    28: "ID",
    29: "G5",
    30: "GND",
    31: "G6",
    32: "G12",
    33: "G13",
    34: "GND",
    35: "G19",
    36: "G16",
    37: "G26",
    38: "G20",
    39: "GND",
    40: "G21",
}
USED = {
    1: C["v33"],
    2: C["v5"],
    3: C["sda"],
    4: C["v5"],
    5: C["scl"],
    6: C["gnd"],
    8: C["tx"],
    9: C["gnd"],
    10: C["rx"],
    11: C["gate"],
    14: C["gnd"],
    17: C["v33"],
    25: C["gnd"],
}
RSV, NOGO = {7, 21, 24, 29}, {27, 28}

o = []
A = o.append
A('<?xml version="1.0" encoding="UTF-8"?>')
A(
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img">'
)
A("<title>OpenFlight full system wiring</title>")
A(
    "<desc>Raspberry Pi 5 40-pin header. Above it, the SEN-14262 sound detector and the BME280 plus LIS3DH I2C sensor chain. Below it, the OPS243-A radar and the 7-inch display. Connections that do not use the header are listed in a separate panel.</desc>"
)
A("<style>")
A(f' .ts{{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:12px;fill:{C["ink"]}}}')
A(f' .tn{{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:10px;fill:{C["ink"]}}}')
A(
    f' .th{{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:14px;font-weight:500;fill:{C["ink"]}}}'
)
A(
    f' .tt{{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:19px;font-weight:600;fill:{C["ink"]}}}'
)
A(
    ' .wt{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:14px;font-weight:500;fill:#FFFFFF}'
)
A(' .wn{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:11px;fill:#FFFFFF}')
A("</style>")
A(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')
A('<text class="tt" x="40" y="42">OpenFlight — full system wiring</text>')
A(
    '<text class="ts" x="40" y="64">Raspberry Pi 5 · Layout A (OPS243 on GPIO UART, IWR6843 on USB) · all pin numbers are PHYSICAL</text>'
)


def box(x, y, w, h, fill, stroke, label, subs=()):
    A(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
    )
    A(f'<text class="wt" x="{x + 14}" y="{y + 25}">{label}</text>')
    for i, s in enumerate(subs):
        A(f'<text class="wn" x="{x + 14}" y="{y + 45 + i * 15}">{s}</text>')


def wire(pts, col, dash=None):
    d = "M " + " L ".join(f"{a} {b}" for a, b in pts)
    da = f' stroke-dasharray="{dash}"' if dash else ""
    A(
        f'<path d="{d}" fill="none" stroke="{col}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"{da}/>'
    )


def tap(n):
    A(f'<circle cx="{px(n)}" cy="{py(n)}" r="7" fill="none" stroke="#FFFFFF" stroke-width="1.6"/>')


def splice(x, y):
    A(f'<circle cx="{x}" cy="{y}" r="5.5" fill="#B8901A"/>')


# ---- header ----
A('<rect x="282" y="486" width="392" height="20" fill="#FFFFFF"/>')
A('<text class="th" x="286" y="500">Raspberry Pi 5 — 40-pin GPIO header (pin 1 top-left)</text>')
A(
    '<rect x="286" y="520" width="628" height="120" rx="6" fill="#D9B24C" stroke="#A8842E" stroke-width="1.5"/>'
)
for n in range(1, 41):
    x, y = px(n), py(n)
    col = USED.get(n)
    if col:
        f_, s_ = col, "#141414"
    elif n in NOGO:
        f_, s_ = "#FFFFFF", "#B00020"
    elif n in RSV:
        f_, s_ = "#FFFFFF", C["fut"]
    else:
        f_, s_ = "#2A2A2A", "#141414"
    A(
        f'<rect x="{x - 8}" y="{y - 8}" width="16" height="16" rx="2" fill="{f_}" stroke="{s_}" stroke-width="1.5"/>'
    )
    A(f'<text class="tn" x="{x}" y="{y - 15 if n % 2 else y + 23}" text-anchor="middle">{n}</text>')
    A(
        f'<text class="tn" x="{x}" y="{y - 27 if n % 2 else y + 35}" text-anchor="middle" fill="#7A6520">{FUNC[n]}</text>'
    )

# ---- SEN-14262 (above left) ----
box(
    40,
    150,
    210,
    150,
    "#E23A34",
    "#141414",
    "SEN-14262",
    ("Sound detector", "impact trigger", "R17 = 47 kOhm"),
)
A('<circle cx="76" cy="255" r="20" fill="#2A2A2A" stroke="#141414" stroke-width="2"/>')
for lbl, yy in (("GATE", 200), ("VCC", 228), ("GND", 256)):
    A(
        f'<rect x="226" y="{yy - 7}" width="24" height="14" rx="2" fill="#EDEDED" stroke="#141414" stroke-width="1.5"/>'
    )
    A(
        f'<text class="wn" x="220" y="{yy}" text-anchor="end" dominant-baseline="central">{lbl}</text>'
    )
wire([(250, 228), (252, 228), (252, 440), (px(1), 440), (px(1), py(1))], C["v33"])
tap(1)
wire([(250, 256), (262, 256), (262, 452), (px(9), 452), (px(9), py(9))], C["gnd"])
tap(9)
wire([(250, 200), (272, 200), (272, 464), (px(11), 464), (px(11), py(11))], C["gate"])
tap(11)
splice(272, 464)
wire([(272, 464), (272, 752), (310, 752), (310, 768)], C["gate"])

# ---- I2C chain (above right) ----
box(650, 150, 240, 120, "#2B5FA8", "#123C6E", "BME280", ("Air density", "Adafruit 2652 · 0x77"))
box(
    950,
    150,
    240,
    120,
    "#5B3E9B",
    "#33215C",
    "LIS3DH",
    ("Mount tilt · 0x18", "Adafruit 2809", "goes on the radar mount plate"),
)
A(f'<path d="M890 210 L950 210" stroke="{C["usb"]}" stroke-width="7" stroke-linecap="round"/>')
A('<text class="tn" x="920" y="200" text-anchor="middle">QT-QT</text>')
A(
    f'<rect x="590" y="296" width="300" height="28" rx="6" fill="#F2F2EE" stroke="{C["usb"]}" stroke-width="1.5"/>'
)
A(
    '<text class="tn" x="740" y="314" text-anchor="middle">STEMMA QT to FEMALE-socket cable (Adafruit 4397)</text>'
)
A(f'<path d="M690 270 L690 296" stroke="{C["usb"]}" stroke-width="7" stroke-linecap="round"/>')
for n, c, ax, lane, lbl in (
    (3, C["sda"], 620, 380, "SDA"),
    (5, C["scl"], 672, 392, "SCL"),
    (17, C["v33"], 780, 404, "3V3"),
    (25, C["gnd"], 860, 416, "GND"),
):
    wire([(ax, 324), (ax, lane), (px(n), lane), (px(n), py(n))], c)
    tap(n)
    A(f'<text class="tn" x="{ax}" y="{342}" text-anchor="middle">{lbl}</text>')

# ---- OPS243 (below left) ----
A(
    '<rect x="40" y="768" width="400" height="30" rx="4" fill="#0E5730" stroke="#0A3D22" stroke-width="1.5"/>'
)
A(
    '<text class="ts" x="452" y="783" dominant-baseline="central">J3 header — pin 1 at the RIGHT end</text>'
)
box(
    40,
    835,
    400,
    185,
    "#1E8A4F",
    "#0E5730",
    "OPS243-A",
    (
        "24 GHz Doppler — ball/club speed, spin, trigger",
        "10=GND  9=5V  7=TxD  6=RxD  3=HOST_INT",
        "J3 pin 1 is a GPIO, NOT ground",
    ),
)

J3 = {10: 100, 9: 130, 8: 160, 7: 190, 6: 220, 5: 250, 4: 280, 3: 310, 2: 340, 1: 370}
for p, xx in J3.items():
    hot = p in (10, 9, 7, 6, 3)
    A(
        f'<rect x="{xx - 8}" y="{775}" width="16" height="16" rx="2" fill="{"#F2F2EE" if hot else "#7FB79A"}" stroke="#141414" stroke-width="1.5"/>'
    )
    A(f'<text class="tn" x="{xx}" y="{818}" text-anchor="middle">{p}</text>')

A('<rect x="60" y="930" width="170" height="75" rx="3" fill="#E0B63C"/>')
A('<rect x="250" y="930" width="170" height="75" rx="3" fill="#E0B63C"/>')
wire([(100, 768), (100, 748), (px(6), 748), (px(6), py(6))], C["gnd"])
tap(6)
wire([(130, 768), (130, 732), (px(4), 732), (px(4), py(4))], C["v5"])
tap(4)
wire([(190, 768), (190, 716), (px(10), 716), (px(10), py(10))], C["rx"])
tap(10)
wire([(220, 768), (220, 700), (px(8), 700), (px(8), py(8))], C["tx"])
tap(8)

# ---- display (below right) ----
box(
    760,
    820,
    440,
    170,
    "#546E7A",
    "#2F4048",
    'ROADOM 7" 1024x600',
    (
        "Display + capacitive touch",
        "Use ONE 5V wire of the 3-pin GPIO cable.",
        "Leave the upper (Power) micro-USB unplugged —",
        "it would take 550 mA out of the Pi USB budget.",
        "FPC ribbon unused.",
    ),
)
wire([(760, 860), (700, 860), (700, 672), (px(2), 672), (px(2), py(2))], C["v5"])
tap(2)
wire([(760, 890), (716, 890), (716, 686), (px(14), 686), (px(14), py(14))], C["gnd"])
tap(14)

# ---- non-header panel ----
P = 1060
A(
    f'<rect x="40" y="{P}" width="1220" height="200" rx="10" fill="#F7F7F5" stroke="#D8D8D4" stroke-width="1.5"/>'
)
A(f'<text class="th" x="60" y="{P + 28}">Connections that do NOT use the 40-pin header</text>')
for i, (a, b, c) in enumerate(
    [
        (
            "IWR6843LEVM",
            "USB-A · 1 port, 2 tty devices",
            "Use the Enhanced/UARTA interface, normally /dev/ttyUSB0. Current draw is UNKNOWN — measure it.",
        ),
        ("Display video", "Pi micro-HDMI 0 to display HDMI", "Included HDMI cable."),
        ("Display touch", "USB-A · 1 port", "The LOWER micro-USB on the panel, labelled Touch."),
        (
            "IMX296 camera",
            "CSI-2 / MIPI CAM connector",
            "FUTURE — does not touch the header. Pi 5 has two 4-lane connectors.",
        ),
        (
            "Power in",
            "USB-C · official 27 W, 5V/5A",
            "NOT optional. Below 5A the total USB budget drops from 1.6 A to 600 mA.",
        ),
    ]
):
    y = P + 58 + i * 29
    A(f'<text class="ts" x="60" y="{y}" font-weight="500">{a}</text>')
    A(f'<text class="ts" x="230" y="{y}">{b}</text>')
    A(f'<text class="ts" x="580" y="{y}" fill="#6E6E68">{c}</text>')

# ---- legend ----
L = 1300
for i, (lbl, col) in enumerate(
    [
        ("5V", C["v5"]),
        ("3.3V", C["v33"]),
        ("GND", C["gnd"]),
        ("Pi TX to OPS RxD", C["tx"]),
        ("OPS TxD to Pi RX", C["rx"]),
        ("GATE (3-way splice)", C["gate"]),
        ("I2C SDA", C["sda"]),
        ("I2C SCL", C["scl"]),
    ]
):
    cx, cy = 60 + (i % 4) * 310, L + (i // 4) * 26
    A(
        f'<path d="M{cx} {cy} L{cx + 26} {cy}" stroke="{col}" stroke-width="3" stroke-linecap="round"/>'
    )
    A(f'<text class="ts" x="{cx + 34}" y="{cy}" dominant-baseline="central">{lbl}</text>')
for i, t in enumerate(
    [
        "All GND pins are one electrical rail. Pins 6, 9, 14 and 25 are picked to keep wires short and untangled, not because they behave differently.",
        "Pins 27 and 28 (red outline) are the HAT EEPROM I2C bus — never use them. White-outlined pins 7, 21, 24 and 29 are reserved for the future camera-trigger MCU.",
        "OPS243 J3 pin 1 is at the RIGHT end of that header and is a GPIO, NOT ground. Ground is pin 10 — corrected upstream in PR #166.",
        "The BME280 and LIS3DH have no address collision, so one bus serves both. I2C must be enabled first: dtparam=i2c_arm=on in /boot/firmware/config.txt.",
    ]
):
    A(f'<text class="ts" x="60" y="{L + 72 + i * 20}" fill="#6E6E68">— {t}</text>')
A("</svg>")
open("system-wiring.svg", "w").write("\n".join(o))
