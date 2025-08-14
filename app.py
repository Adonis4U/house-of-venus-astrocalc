import os
from datetime import datetime
from flask import Flask, request, jsonify
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import pytz
import swisseph as swe

app = Flask(__name__)
DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"

# ---- API key protection ----
API_KEY = os.getenv("API_KEY", "a96be9cd-d006-439c-962b-3f8314d2e080")

@app.before_request
def check_api_key():
    # health endpoint is public
    if request.path.startswith("/health"):
        return
    key = request.headers.get("X-API-Key")
    if key != API_KEY:
        return jsonify({"error": "Invalid or missing API key"}), 403

# ---- CORS (open while testing; restrict in production) ----
@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return resp

# ---- Swiss Ephemeris path ----
EPHE_PATH = os.getenv("EPHE_PATH", os.path.join(os.path.dirname(__file__), "ephe"))
swe.set_ephe_path(EPHE_PATH)

# ---- Helpers ----
ZODIAC_SIGNS = [
    "Aries","Taurus","Gemini","Cancer","Leo","Virgo",
    "Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"
]

def lon_to_sign_deg(lon: float):
    lon = float(lon) % 360.0
    idx = int(lon // 30)
    d_in_sign = lon - idx * 30.0
    return ZODIAC_SIGNS[idx], d_in_sign, lon

def format_deg(deg: float) -> str:
    d = int(deg)
    m = int(round((deg - d) * 60))
    if m == 60:
        d += 1; m = 0
    return f"{d}°{m:02d}'"

def ensure_house_system(hsys: str) -> str:
    if not hsys: return "P"
    h = hsys.upper().strip()
    return h if h in list("PKORCBV") else "P"

def _calc_ut_tuple(jd_ut, pid, flags):
    """Normalize swe.calc_ut return to a flat 6-floats tuple."""
    ret = swe.calc_ut(jd_ut, pid, flags)
    if isinstance(ret, (tuple, list)) and len(ret) == 2 and isinstance(ret[0], (tuple, list)):
        xx, _ = ret
        return tuple(float(x) for x in xx[:6])
    if isinstance(ret, (tuple, list)) and len(ret) >= 6:
        return tuple(float(x) for x in ret[:6])
    raise ValueError(f"Unexpected swe.calc_ut return: {ret!r}")

PLANETS = [
    (swe.SUN, "Sun"), (swe.MOON, "Moon"), (swe.MERCURY, "Mercury"),
    (swe.VENUS, "Venus"), (swe.MARS, "Mars"), (swe.JUPITER, "Jupiter"),
    (swe.SATURN, "Saturn"), (swe.URANUS, "Uranus"), (swe.NEPTUNE, "Neptune"),
    (swe.PLUTO, "Pluto")
]

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/natal")
def natal():
    # --- robust input parsing: JSON or form, with raw fallback ---
    data = request.get_json(silent=True)
    if not data:
        if request.form:
            data = request.form.to_dict(flat=True)
        else:
            raw = request.get_data(cache=False, as_text=True)
            try:
                import json as _json
                data = _json.loads(raw) if raw else {}
            except Exception:
                data = {}

    name = (data.get("name") or "Unknown").strip() if isinstance(data.get("name"), str) else "Unknown"
    date_str = data.get("date")
    time_str = data.get("time", "12:00")
    place = data.get("place")
    hsys = ensure_house_system(data.get("house_system", "P"))

    if not date_str or not place:
        return jsonify({"error": "Missing required fields: 'date' and 'place'"}), 400

    # Geocode
    geolocator = Nominatim(user_agent=os.getenv("GEOCODER_UA", "house-of-venus-astrocalc/1.0"), timeout=15)
    location = geolocator.geocode(place, language="en")
    if not location:
        return jsonify({"error": f"Place not found: {place}"}), 404
    lat, lon = float(location.latitude), float(location.longitude)

    # Time zone
    tf = TimezoneFinder()
    tzname = tf.timezone_at(lng=lon, lat=lat)
    if not tzname:
        return jsonify({"error": "Timezone not found for coordinates"}), 500
    tz = pytz.timezone(tzname)

    # Localize -> UTC
    try:
        year, month, day = map(int, date_str.split("-"))
        hh, mm = map(int, time_str.split(":"))
        local_dt = tz.localize(datetime(year, month, day, hh, mm, 0))
    except Exception as e:
        return jsonify({"error": f"Invalid date/time: {e}"}), 400
    utc_dt = local_dt.astimezone(pytz.utc)

    # Julian Day (UT)
    ut_hour = utc_dt.hour + utc_dt.minute/60 + utc_dt.second/3600
    jd_ut = swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, ut_hour, swe.GREG_CAL)

    # Planets
    flags = swe.FLG_SWIEPH | swe.FLG_SPEED
    positions = {}
    for pid, pname in PLANETS:
        try:
            lon_deg, lat_deg, dist, slon, slat, sdist = _calc_ut_tuple(jd_ut, pid, flags)
        except Exception as e:
            return jsonify({"error": f"Swiss Ephemeris error for {pname}: {e}"}), 500
        sign, deg_in_sign, absdeg = lon_to_sign_deg(lon_deg)
        positions[pname] = {
            "longitude": round(absdeg, 6),
            "sign": sign,
            "deg_in_sign": round(deg_in_sign, 6),
            "deg_str": format_deg(deg_in_sign),
            "speed_lon": round(slon, 6),
        }

    # Houses/Angles (compat for hsys str/bytes)
    try:
        try:
            cusps, ascmc = swe.houses_ex(jd_ut, lat, lon, hsys)
        except TypeError:
            cusps, ascmc = swe.houses_ex(jd_ut, lat, lon, hsys.encode("ascii"))
    except Exception as e:
        return jsonify({"error": f"Houses calculation failed: {e}"}), 500

    houses = {str(i+1): round(float(cusps[i]), 6) for i in range(12)}
    asc = float(ascmc[0]); mc = float(ascmc[1])
    asc_sign, asc_deg_in_sign, asc_abs = lon_to_sign_deg(asc)
    mc_sign, mc_deg_in_sign, mc_abs = lon_to_sign_deg(mc)

    return jsonify({
        "input": {
            "name": name,
            "place_query": place,
            "resolved_place": location.address,
            "lat": lat, "lon": lon,
            "timezone": tzname,
            "local_datetime": local_dt.isoformat(),
            "utc_datetime": utc_dt.isoformat(),
            "house_system": hsys
        },
        "positions": positions,
        "angles": {
            "ASC": {
                "longitude": round(asc_abs, 6),
                "sign": asc_sign,
                "deg_in_sign": round(asc_deg_in_sign, 6),
                "deg_str": format_deg(asc_deg_in_sign)
            },
            "MC": {
                "longitude": round(mc_abs, 6),
                "sign": mc_sign,
                "deg_in_sign": round(mc_deg_in_sign, 6),
                "deg_str": format_deg(mc_deg_in_sign)
            }
        },
        "houses": houses
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=DEBUG)
