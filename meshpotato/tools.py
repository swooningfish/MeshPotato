"""Offline commands: frequency lists, dice, coin, eight ball, unit conversion, Ohm's law, resistors."""

import random
import re
import math
from typing import Optional

from . import config as cfg
from . import state

# ---------- !freq: frequency lists ----------
FREQ_ALIASES = {"pmr446": "pmr", "amateur": "ham", "sea": "marine", "boat": "marine",
                "airband": "air", "aircraft": "air", "meshcore": "mesh", "lora": "mesh"}


def mesh_freq(self_info: Optional[dict] = None) -> str:
    """The bot radio's own settings, so others can match them: 'MeshCore here: 869.618 MHz, BW 62.5kHz, SF8, CR8'."""
    if self_info is None:
        self_info = getattr(state.radio, "self_info", None) or {}
    freq = self_info.get("radio_freq")
    if not freq:
        return "MeshCore radio settings not known"
    parts = [f"{freq:g} MHz"]
    if self_info.get("radio_bw"):
        parts.append(f"BW {self_info['radio_bw']:g}kHz")
    if self_info.get("radio_sf"):
        parts.append(f"SF{self_info['radio_sf']}")
    if self_info.get("radio_cr"):
        parts.append(f"CR{self_info['radio_cr']}")
    return "MeshCore here: " + ", ".join(parts)


def format_freq(topic: str, self_info: Optional[dict] = None) -> str:
    icon = "📻 " if cfg.USE_EMOJI else ""
    t = topic.strip().lower().lstrip("!")
    t = FREQ_ALIASES.get(t, t)
    if t == "mesh":
        return icon + mesh_freq(self_info)
    if t in cfg.FREQ_LISTS:
        return icon + cfg.FREQ_LISTS[t]
    return icon + "Frequencies: !freq " + ", ".join(list(cfg.FREQ_LISTS) + ["mesh"])


# ---------- Fun commands ----------
_rng = random.SystemRandom()
ROLL_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$")


def roll_dice(arg: str) -> str:
    """!roll -> 1d6. Accepts 'd20', '2d6', '3d8+2', '20' (one die with 20 sides)."""
    spec = (arg.split() or ["d6"])[0].lower()
    if spec.isdigit():
        spec = f"d{spec}"
    m = ROLL_RE.match(spec)
    if not m:
        return "Use !roll, !roll d20, !roll 2d6 or !roll 3d8+2"
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3) or 0)
    if not 1 <= count <= cfg.ROLL_MAX_DICE or not 2 <= sides <= cfg.ROLL_MAX_SIDES:
        return f"Up to {cfg.ROLL_MAX_DICE} dice with 2 to {cfg.ROLL_MAX_SIDES} sides"
    rolls = [_rng.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + mod
    label = f"{count}d{sides}" + (f"{mod:+d}" if mod else "")
    icon = "\U0001F3B2 " if cfg.USE_EMOJI else ""
    if count == 1 and not mod:
        return f"{icon}{label}: {total}"
    detail = " + ".join(str(r) for r in rolls) + (f" {'+' if mod > 0 else '-'} {abs(mod)}" if mod else "")
    return f"{icon}{label}: {detail} = {total}"


def flip_coin() -> str:
    side = _rng.choice(["Heads", "Tails"])
    return f"\U0001FA99 {side}" if cfg.USE_EMOJI else side


def eightball(question: str) -> str:
    if not question.strip():
        return "Ask a question: !eightball Will it rain?"
    icon = "\U0001F3B1 " if cfg.USE_EMOJI else ""
    return icon + _rng.choice(cfg.EIGHTBALL_ANSWERS)


# ---------- Unit conversion ----------
def _linear(dim: str, factor: float, label: str) -> tuple:
    """A unit worth `factor` base units (m, kg, l, m/s, hPa, W, Hz)."""
    return dim, (lambda v: v * factor), (lambda b: b / factor), label


def _dbm_from_w(w: float) -> float:
    if w <= 0:
        raise ValueError("power must be above 0 W")
    return 10 * math.log10(w) + 30


# name -> (dimension, to base unit, from base unit, label)
UNITS: dict[str, tuple] = {}
for _names, _unit in [
    (("mm",), _linear("length", 0.001, "mm")),
    (("cm",), _linear("length", 0.01, "cm")),
    (("m", "metre", "metres", "meter", "meters"), _linear("length", 1, "m")),
    (("km",), _linear("length", 1000, "km")),
    (("in", "inch", "inches"), _linear("length", 0.0254, "in")),
    (("ft", "foot", "feet"), _linear("length", 0.3048, "ft")),
    (("yd", "yard", "yards"), _linear("length", 0.9144, "yd")),
    (("mi", "mile", "miles"), _linear("length", 1609.344, "mi")),
    (("nmi",), _linear("length", 1852, "nmi")),
    (("g", "gram", "grams"), _linear("mass", 0.001, "g")),
    (("kg", "kilo", "kilos"), _linear("mass", 1, "kg")),
    (("oz", "ounce", "ounces"), _linear("mass", 0.028349523125, "oz")),
    (("lb", "lbs", "pound", "pounds"), _linear("mass", 0.45359237, "lb")),
    (("st", "stone"), _linear("mass", 6.35029318, "st")),
    (("ml",), _linear("volume", 0.001, "ml")),
    (("l", "litre", "litres", "liter", "liters"), _linear("volume", 1, "l")),
    (("floz",), _linear("volume", 0.0284130625, "fl oz")),             # UK
    (("pt", "pint", "pints"), _linear("volume", 0.56826125, "pt")),     # UK
    (("gal", "gallon", "gallons"), _linear("volume", 4.54609, "gal")),  # UK
    (("usgal",), _linear("volume", 3.785411784, "US gal")),
    (("ms", "mps"), _linear("speed", 1, "m/s")),
    (("kmh", "kph"), _linear("speed", 1 / 3.6, "km/h")),
    (("mph",), _linear("speed", 0.44704, "mph")),
    (("kn", "kt", "kts", "knot", "knots"), _linear("speed", 1852 / 3600, "kn")),
    (("hpa", "mb", "mbar"), _linear("pressure", 1, "hPa")),
    (("inhg",), _linear("pressure", 33.8638866667, "inHg")),
    (("mmhg",), _linear("pressure", 1.33322387415, "mmHg")),
    (("psi",), _linear("pressure", 68.9475729318, "psi")),
    (("bar",), _linear("pressure", 1000, "bar")),
    (("mw",), _linear("power", 0.001, "mW")),
    (("w", "watt", "watts"), _linear("power", 1, "W")),
    (("kw",), _linear("power", 1000, "kW")),
    (("dbm",), ("power", lambda v: 10 ** ((v - 30) / 10), _dbm_from_w, "dBm")),
    (("hz",), _linear("freq", 1, "Hz")),
    (("khz",), _linear("freq", 1e3, "kHz")),
    (("mhz",), _linear("freq", 1e6, "MHz")),
    (("ghz",), _linear("freq", 1e9, "GHz")),
    (("c", "degc", "celsius"), ("temp", lambda v: v, lambda b: b, "°C")),
    (("f", "degf", "fahrenheit"), ("temp", lambda v: (v - 32) * 5 / 9, lambda b: b * 9 / 5 + 32, "°F")),
    (("k", "kelvin"), ("temp", lambda v: v - 273.15, lambda b: b + 273.15, "K")),
]:
    for _name in _names:
        UNITS[_name] = _unit

# What to convert to when only one unit is given
CONV_DEFAULT_TO = {
    "mm": "in", "cm": "in", "m": "ft", "km": "mi", "in": "cm", "ft": "m", "yd": "m", "mi": "km",
    "nmi": "km", "g": "oz", "kg": "lb", "oz": "g", "lb": "kg", "st": "kg", "ml": "floz", "l": "pt",
    "floz": "ml", "pt": "l", "gal": "l", "usgal": "l", "ms": "mph", "kmh": "mph", "mph": "kmh",
    "kn": "mph", "hpa": "inhg", "inhg": "hpa", "mmhg": "hpa", "psi": "bar", "bar": "psi",
    "mw": "dbm", "w": "dbm", "kw": "dbm", "dbm": "w", "c": "f", "f": "c", "k": "c",
    "hz": "m", "khz": "m", "mhz": "m", "ghz": "m",
}
CONV_WORDS = {"to", "in", "into", "as", "->", ">", "="}
CONV_RE = re.compile(r"^(-?(?:\d+\.?\d*|\.\d+))\s*(.*)$")
CONV_USAGE = "Use !conv <n> <unit> [unit], e.g. !conv 10 mi km, !conv 20 c, !conv 5 w dbm"
SPEED_OF_LIGHT = 299_792_458


def _unit_key(token: str) -> str:
    """'°C' -> 'c', 'km/h' -> 'kmh'."""
    return token.lower().replace("°", "").replace("/", "")


def _num(v: float) -> str:
    """About 4 significant figures, no exponent: 16.09, 0.3454, 1609."""
    if v == 0:
        return "0"
    digits = min(10, max(0, 3 - math.floor(math.log10(abs(v)))))
    text = f"{v:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _with_unit(v: float, label: str) -> str:
    return _num(v) + ("" if label.startswith("°") else " ") + label


def convert_units(arg: str) -> str:
    """!conv 10 mi km, !conv 10mi to km, !conv 20 c (to °F), !conv 868 mhz (wavelength)."""
    m = CONV_RE.match(arg.strip().replace(",", ""))
    if not m:
        return CONV_USAGE
    value = float(m.group(1))
    tokens = " ".join(m.group(2).lower().replace("fl oz", "floz").split()).split()
    tokens = [_unit_key(t) for t in tokens]
    if len(tokens) == 3 and tokens[1] in CONV_WORDS:
        tokens.pop(1)
    if not 1 <= len(tokens) <= 2:
        return CONV_USAGE
    src = tokens[0]
    dst = tokens[1] if len(tokens) == 2 else CONV_DEFAULT_TO.get(src, "")
    for name in (src, dst):
        if name not in UNITS:
            return f"Unknown unit '{name[:20]}'. Try !conv 10 mi km or see !helpconv"
    s_dim, s_to, _, s_label = UNITS[src]
    d_dim, _, d_from, d_label = UNITS[dst]
    try:
        base = s_to(value)
        if s_dim == "temp" and base < -273.15:
            return "That's below absolute zero"
        if s_dim == d_dim:
            result = d_from(base)
        elif {s_dim, d_dim} == {"freq", "length"}:     # frequency <-> wavelength
            if base <= 0:
                return "Frequency and wavelength must be above 0"
            result = d_from(SPEED_OF_LIGHT / base)
            if len(tokens) == 1 and result < 1:       # 868 MHz reads better as 34.54 cm
                result, d_label = result * 100, "cm"
        else:
            return f"Can't convert {s_label} to {d_label}"
    except (ValueError, OverflowError) as e:
        return f"Can't convert: {e}"
    icon = "📐 " if cfg.USE_EMOJI else ""
    return f"{icon}{_with_unit(value, s_label)} = {_with_unit(result, d_label)}"


# ---------- Ohm's law (V = I x R) and power (P = V x I) ----------
OHM_PREFIXES = {"u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6}
OHM_UNITS = {"v": "V", "volt": "V", "volts": "V", "a": "I", "amp": "I", "amps": "I",
             "ohm": "R", "ohms": "R", "ω": "R", "r": "R", "": "R",
             "w": "P", "watt": "P", "watts": "P"}
OHM_LABELS = {"V": "V", "I": "A", "R": "Ω", "P": "W"}
OHM_RE = re.compile(r"(\d+\.?\d*|\.\d+)\s*([uµmkKM]?)([a-zA-ZΩω]*)")
OHM_USAGE = "Use !ohm with two of V, A, Ω, W: !ohm 12v 2a, !ohm 5v 220r, !ohm 10w 50ohm, !ohm 4.7k 20ma"


def _ohm_term(number: str, prefix: str, unit: str) -> Optional[tuple[str, float]]:
    """('12', '', 'v') -> ('V', 12.0). 'M' is mega and 'm' milli: '1M' is 1 MΩ, '20mA' is 20 mA.
    A bare prefix is ohms ('4.7k'), a bare number is not allowed."""
    unit = unit.lower()
    if unit not in OHM_UNITS or not (unit or prefix):
        return None
    return OHM_UNITS[unit], float(number) * OHM_PREFIXES.get(prefix, 1)


def _si(value: float, label: str) -> str:
    """0.25 A -> '250 mA', 4700 Ω -> '4.7 kΩ'."""
    for prefix, scale in (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1), ("m", 1e-3), ("µ", 1e-6)):
        if abs(value) >= scale or prefix == "µ":
            return f"{_num(value / scale)} {prefix}{label}"
    return f"{_num(value)} {label}"


def ohms_law(arg: str) -> str:
    """Any two of voltage, current, resistance and power give the other two."""
    known: dict[str, float] = {}
    text = arg.strip().replace(",", "")
    terms = OHM_RE.findall(text)
    if len(terms) != 2 or OHM_RE.sub("", text).strip():
        return OHM_USAGE
    for term in terms:
        parsed = _ohm_term(*term)
        if parsed is None:
            unit = (term[1] + term[2])[:10]
            return f"Unknown unit '{unit}'. See !helpconv" if unit else "Give each value a unit: V, A, Ω or W"
        known[parsed[0]] = parsed[1]
    if len(known) != 2:
        return "Give two different values, such as volts and amps"
    if min(known.values()) <= 0:
        return "Values must be above 0"
    v, i, r, p = (known.get(k) for k in "VIRP")
    if v is not None and i is not None:
        r, p = v / i, v * i
    elif v is not None and r is not None:
        i, p = v / r, v * v / r
    elif v is not None and p is not None:
        i, r = p / v, v * v / p
    elif i is not None and r is not None:
        v, p = i * r, i * i * r
    elif i is not None and p is not None:
        v, r = p / i, p / (i * i)
    else:
        v, i = math.sqrt(p * r), math.sqrt(p / r)
    values = {"V": v, "I": i, "R": r, "P": p}
    given = ", ".join(_si(values[k], OHM_LABELS[k]) for k in "VIRP" if k in known)
    found = ", ".join(_si(values[k], OHM_LABELS[k]) for k in "VIRP" if k not in known)
    icon, arrow = ("⚡ ", "→") if cfg.USE_EMOJI else ("", "->")
    return f"{icon}{given} {arrow} {found}"


# ---------- Resistor colour code (IEC 60062) ----------
RES_DIGITS = ["black", "brown", "red", "orange", "yellow", "green", "blue", "violet", "grey", "white"]
RES_MULT = {**{c: i for i, c in enumerate(RES_DIGITS)}, "gold": -1, "silver": -2}    # power of 10
RES_TOL = {"brown": 1, "red": 2, "orange": 0.05, "yellow": 0.02, "green": 0.5, "blue": 0.25,
           "violet": 0.1, "grey": 0.01, "gold": 5, "silver": 10}                     # ± %
RES_TEMPCO = {"black": 250, "brown": 100, "red": 50, "orange": 15, "yellow": 25, "green": 20,
              "blue": 10, "violet": 5, "grey": 1}                                    # ppm/K
RES_ALIASES = {"purple": "violet", "gray": "grey", "bk": "black", "blk": "black", "bn": "brown",
               "brn": "brown", "rd": "red", "og": "orange", "org": "orange", "ye": "yellow",
               "yel": "yellow", "gn": "green", "grn": "green", "bu": "blue", "blu": "blue",
               "vi": "violet", "vio": "violet", "gy": "grey", "gry": "grey", "wh": "white",
               "wht": "white", "gd": "gold", "gld": "gold", "sv": "silver", "sr": "silver",
               "slv": "silver"}
# Grey, gold and silver have no coloured square, so they get the nearest emoji
RES_EMOJI = {"black": "⬛", "brown": "🟫", "red": "🟥", "orange": "🟧", "yellow": "🟨", "green": "🟩",
             "blue": "🟦", "violet": "🟪", "grey": "🩶", "white": "⬜", "gold": "🥇", "silver": "🥈"}
RES_RKM_RE = re.compile(r"^(\d+)([rkmg])(\d*)$")                 # 4k7, 4r7, 470r, 1m
RES_VALUE_RE = re.compile(r"^(\d+\.?\d*|\.\d+)([kmg]?)(?:ohms?|ω|r)?$")
RES_TOL_RE = re.compile(r"^±?(\d+\.?\d*|\.\d+)%$")
RES_SCALE = {"r": 1, "": 1, "k": 1e3, "m": 1e6, "g": 1e9}
RES_USAGE = "Use !res <colours> or !res <value>, e.g. !res yellow violet red gold, !res 4k7, !res 10k 1%"


def _res_value(ohms: float) -> str:
    """4700 -> '4.7 kΩ', 0.47 -> '0.47 Ω'."""
    return f"{_num(ohms)} Ω" if ohms < 1 else _si(ohms, "Ω")


def _res_colour(token: str) -> str:
    token = token.lower()
    return RES_ALIASES.get(token, token)


def _band_names(bands: list[str]) -> str:
    return " ".join(b.title() for b in bands)


def _bands_text(bands: list[str], names: bool = True) -> str:
    """'🟨🟪🟥🥇 Yellow Violet Red Gold', or just the squares when names=False.
    Plain names when USE_EMOJI is off."""
    if not cfg.USE_EMOJI:
        return _band_names(bands)
    strip = "".join(RES_EMOJI[b] for b in bands)
    return f"{strip} {_band_names(bands)}" if names else strip


def resistor_from_colours(bands: list[str]) -> str:
    """3 bands: 2 digits and multiplier (±20%). 4: plus tolerance. 5: 3 digits. 6: plus tempco.
    Raises ValueError with the reply for a bad code."""
    if bands == ["black"]:
        return f"{_bands_text(bands)} = 0 Ω zero-ohm link"
    if bands[0] in ("gold", "silver") and bands[-1] not in ("gold", "silver"):
        bands = bands[::-1]                                     # read from the wrong end
    if not 3 <= len(bands) <= 6:
        raise ValueError("Give 3 to 6 bands, e.g. !res brown black red gold")
    digit_count = 3 if len(bands) >= 5 else 2
    digits, mult, rest = bands[:digit_count], bands[digit_count], bands[digit_count + 1:]
    if any(b not in RES_DIGITS for b in digits):
        raise ValueError(f"{_band_names(digits)}: digit bands can't be gold or silver")
    if mult not in RES_MULT:
        raise ValueError(f"{mult.title()} isn't a multiplier band")
    tol = RES_TOL.get(rest[0]) if rest else 20
    if tol is None:
        raise ValueError(f"{rest[0].title()} isn't a tolerance band")
    ohms = int("".join(str(RES_DIGITS.index(b)) for b in digits)) * 10.0 ** RES_MULT[mult]
    text = f"{_bands_text(bands)} = {_res_value(ohms)} ±{_num(tol)}%"
    if len(rest) == 2:
        if rest[1] not in RES_TEMPCO:
            raise ValueError(f"{rest[1].title()} isn't a tempco band")
        text += f" {RES_TEMPCO[rest[1]]}ppm/K"
    return text


def _colour_bands(ohms: float, digit_count: int) -> Optional[list[str]]:
    """The digit and multiplier bands for `ohms`, or None if it needs more digits than that."""
    exp = math.floor(math.log10(ohms)) - (digit_count - 1)
    digits = round(ohms / 10.0 ** exp)
    if digits >= 10 ** digit_count:                             # rounding went up a decade
        exp, digits = exp + 1, round(ohms / 10.0 ** (exp + 1))
    if not -2 <= exp <= 9 or not math.isclose(digits * 10.0 ** exp, ohms, rel_tol=1e-9):
        return None
    mult = RES_DIGITS[exp] if exp >= 0 else ("gold" if exp == -1 else "silver")
    return [RES_DIGITS[int(d)] for d in str(digits)] + [mult]


def resistor_to_colours(value: str, tol: Optional[float] = None, budget: Optional[int] = None) -> str:
    """4-band (default ±5% gold) and 5-band (default ±1% brown) codes for a value.
    The colour names are dropped from beside the emoji when both codes don't fit in `budget`.
    Raises ValueError with the reply for a bad value."""
    m = RES_RKM_RE.match(value)
    if m:
        ohms = float(f"{m.group(1)}.{m.group(3) or 0}") * RES_SCALE[m.group(2)]
    else:
        m = RES_VALUE_RE.match(value)
        if not m:
            raise ValueError(RES_USAGE)
        ohms = float(m.group(1)) * RES_SCALE[m.group(2)]
    if ohms == 0:
        return f"0 Ω: {_bands_text(['black'])} (zero-ohm link)"
    tol_colour = None
    if tol is not None:
        tol_colour = next((c for c, t in RES_TOL.items() if t == tol), None)
        if tol_colour is None:
            raise ValueError(f"No band for ±{_num(tol)}%. Try 1%, 2%, 5% or 10%")
    codes = []
    for count, default in ((2, "gold"), (3, "brown")):
        bands = _colour_bands(ohms, count)
        if bands:
            codes.append(bands + [tol_colour or default])
    if not codes:
        raise ValueError(f"{_res_value(ohms)} has no colour code (0.1 Ω to 999 GΩ, 3 figures)")

    def text(names: bool) -> str:
        label = (lambda b: "") if cfg.USE_EMOJI else (lambda b: f"{len(b)}-band ")   # the squares show the count
        parts = [f"{label(b)}{_bands_text(b, names)} ±{_num(RES_TOL[b[-1]])}%" for b in codes]
        return f"{_res_value(ohms)}: " + " | ".join(parts)

    full = text(names=True)
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    return full if len(full.encode("utf-8")) <= budget else text(names=False)


def resistor(arg: str, budget: Optional[int] = None) -> str:
    """!res yellow violet red gold -> value, !res 4k7 [5%] -> colours."""
    tokens = re.split(r"[\s,/-]+", arg.strip().lower())
    tokens = [t for t in tokens if t]
    if not tokens:
        return RES_USAGE
    colours = [_res_colour(t) for t in tokens]
    try:
        if colours[0] in RES_MULT or colours[0] in RES_TOL:
            unknown = next((t for t, c in zip(tokens, colours) if c not in RES_MULT and c not in RES_TOL), "")
            if unknown:
                return f"Unknown colour '{unknown[:12]}'. See !helpconv"
            text = resistor_from_colours(colours)
        else:
            tol = None
            if len(tokens) > 1 and RES_TOL_RE.match(tokens[-1]):
                tol = float(RES_TOL_RE.match(tokens.pop())[1])
            text = resistor_to_colours("".join(tokens), tol, budget)
    except ValueError as e:
        return str(e)
    return text
