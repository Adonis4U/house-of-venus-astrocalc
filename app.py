import os
from datetime import datetime
from flask import Flask, request, jsonify
# from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import pytz
import swisseph as swe
import time, random, json, requests
from typing import Optional, Dict, Any, Tuple

# --- CONFIG GEOCODING ---
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
GEOCODER_UA = os.getenv("GEOCODER_UA", "house-of-venus-astrocalc/1.0")
GEO_TTL_SECONDS = int(os.getenv("GEO_TTL_SECONDS", "86400"))   # 24h
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))
RETRY_BASE_SLEEP = float(os.getenv("RETRY_BASE_SLEEP", "0.6"))
RETRY_MAX_SLEEP = float(os.getenv("RETRY_MAX_SLEEP", "4.0"))

# --- TTL cache semplice in memoria ---
class SimpleTTLCache:
    def __init__(self, maxsize=8000, ttl=86400):
        self.store: Dict[str, Tuple[float, Any]] = {}
        self.maxsize, self.ttl = maxsize, ttl
    def get(self, key: str):
        rec = self.store.get(key)
        if not rec: return None
        ts, val = rec
        if time.time() - ts > self.ttl:
            self.store.pop(key, None)
            return None
        return val
    def set(self, key: str, val: Any):
        if len(self.store) > self.maxsize:
            # prune semplice: rimuovi scaduti o una manciata dei più vecchi
            for k in list(self.store.keys())[:1000]:
                ts, _ = self.store[k]
                if time.time() - ts > self.ttl:
                    self.store.pop(k, None)
        self.store[key] = (time.time(), val)

GEO_CACHE = SimpleTTLCache(ttl=GEO_TTL_SECONDS)

def _norm_place(s: str) -> str:
    return " ".join((s or "").strip().split()).lower()

def _req_with_retry(url, params=None, headers=None, timeout=12) -> Optional[requests.Response]:
    params = params or {}; headers = headers or {}
    last_exc = None
    for attempt in range(1, RETRY_MAX + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200 and r.text:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            sleep_s = min(RETRY_MAX_SLEEP, RETRY_BASE_SLEEP * (2 ** (attempt - 1))) + random.uniform(0, 0.25)
            app.logger.warning(f"[geocode] retry {attempt}/{RETRY_MAX} after {e}; sleep {sleep_s:.2f}s")
            time.sleep(sleep_s)
    app.logger.error(f"[geocode] all retries failed: {last_exc}")
    return None

# --- Provider: Google (se key presente)
def geocode_google(place: str) -> Optional[Dict[str, Any]]:
    if not GOOGLE_MAPS_API_KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": place, "key": GOOGLE_MAPS_API_KEY}
    r = _req_with_retry(url, params=params, headers={"User-Agent": GEOCODER_UA})
    if not r: return None
    try:
        data = r.json()
        if data.get("status") == "OK" and data.get("results"):
            it = data["results"][0]
            loc = it["geometry"]["location"]
            return {"lat": float(loc["lat"]), "lon": float(loc["lng"]),
                    "name": it.get("formatted_address", place), "source": "google"}
    except Exception:
        app.logger.exception("[geocode] google parse error")
    return None

# --- Provider: Nominatim
def geocode_nominatim(place: str) -> Optional[Dict[str, Any]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": place, "format": "json", "limit": 1, "addressdetails": 1}
    r = _req_with_retry(url, params=params, headers={"User-Agent": GEOCODER_UA, "Accept":"application/json"})
    if not r: return None
    try:
        data = r.json()
        if isinstance(data, list) and data:
            it = data[0]
            return {"lat": float(it["lat"]), "lon": float(it["lon"]),
                    "name": it.get("display_name", place), "source": "nominatim"}
    except Exception:
        app.logger.exception("[geocode] nominatim parse error")
    return None

# --- Provider: Open-Meteo
def geocode_openmeteo(place: str) -> Optional[Dict[str, Any]]:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": place, "count": 1, "language": "en", "format": "json"}
    r = _req_with_retry(url, params=params, headers={"User-Agent": GEOCODER_UA})
    if not r: return None
    try:
        data = r.json(); res = data.get("results") or []
        if res:
            it = res[0]
            return {"lat": float(it["latitude"]), "lon": float(it["longitude"]),
                    "name": f'{it.get("name","")}, {it.get("country_code","")}'.strip(", "),
                    "source": "open-meteo"}
    except Exception:
        app.logger.exception("[geocode] open-meteo parse error")
    return None

# --- Provider: Maps.co (ulteriore fallback)
def geocode_mapsco(place: str) -> Optional[Dict[str, Any]]:
    url = "https://geocode.maps.co/search"
    params = {"q": place}
    r = _req_with_retry(url, params=params, headers={"User-Agent": GEOCODER_UA})
    if not r: return None
    try:
        data = r.json()
        if isinstance(data, list) and data:
            it = data[0]
            return {"lat": float(it["lat"]), "lon": float(it["lon"]),
                    "name": it.get("display_name", place), "source": "maps.co"}
    except Exception:
        app.logger.exception("[geocode] maps.co parse error")
    return None

def geocode_place(place: str) -> Optional[Dict[str, Any]]:
    key = f"geo:{_norm_place(place)}"
    cached = GEO_CACHE.get(key)
    if cached: return cached

    providers = [geocode_google, geocode_nominatim, geocode_openmeteo, geocode_mapsco]
    for fn in providers:
        try:
            res = fn(place)
            if res and "lat" in res and "lon" in res:
                GEO_CACHE.set(key, res)
                return res
        except Exception:
            app.logger.exception(f"[geocode] provider crash: {fn.__name__}")
            continue
    return None

app = Flask(__name__)
DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"

# ---- API key protection ----
API_KEY = os.getenv("API_KEY", "a96be9cd-d006-439c-962b-3f8314d2e080")

@app.before_request
def check_api_key():
    # Endpoint pubblici e preflight CORS
    public_paths = ("/", "/health", "/healthz")
    if request.method == "OPTIONS" or request.path in public_paths:
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

# ---- Public endpoints ----
@app.get("/")
def index():
    # 200 OK per health-check; HEAD è gestito automaticamente da Flask
    return jsonify({
        "status": "ok",
        "service": "houseofvenus-astrocalc",
        "time_utc": datetime.utcnow().isoformat() + "Z"
    }), 200

@app.get("/health")
def health():
    return {"status": "ok"}, 200

@app.get("/healthz")
def healthz():
    return {"status": "ok"}, 200

# ---- Main API ----
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

    geo = geocode_place(place)
    if not geo:
        return jsonify({"error": f"Geocoding failed for '{place}' (providers exhausted). "
                                 f"Try 'City, Country'"}), 502
    lat, lon = float(geo["lat"]), float(geo["lon"])
    resolved_place = geo.get("name", place)
    geocoder_source = geo.get("source", "unknown")
    app.logger.info(f"[natal] geocoder={geocoder_source} place='{place}' -> '{resolved_place}' ({lat},{lon})")


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
            "resolved_place": resolved_place,
            "geocoder": geocoder_source,
            "lat": lat,
            "lon": lon,
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

