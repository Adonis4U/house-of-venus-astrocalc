import os
from http_utils import http_get, http_post
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from threading import BoundedSemaphore
# from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import pytz
import swisseph as swe
import sys, time, random, json, requests
from typing import Optional, Dict, Any, Tuple
# --- TimezoneFinder cache ---
from cachetools import TTLCache
# from requests.adapters import HTTPAdapter # <- non usato qui
# from urllib3.util.retry import Retry # <- non usato qui
# --- Timezone utilities (in alto, vicino ad altri global) ---
from pytz import AmbiguousTimeError, NonExistentTimeError
# --- Per test, contatori e contatore di utilizzo api (Incluso Google)
from collections import Counter
from datetime import date

start_ts = time.time()

# Concorrenza massima dichiarata (deve matchare il tuo BoundedSemaphore)
MAX_CONC = 6   # o il valore che hai usato in NATAL_SEM = BoundedSemaphore(6)

STATS = {
    "total":      {"natal_calls": 0, "google_calls": 0, "cache_hits": 0},
    "daily":      {"natal_calls": 0, "google_calls": 0, "cache_hits": 0},
    "last_reset": str(date.today()),  # YYYY-MM-DD del giorno corrente
}

def _ensure_stats_day():
    """Reset giornaliero se è cambiata la data."""
    today = str(date.today())
    if STATS["last_reset"] != today:
        STATS["daily"] = {"natal_calls": 0, "google_calls": 0, "cache_hits": 0}
        STATS["last_reset"] = today

def _bump(field: str, n: int = 1):
    """Incrementa un campo su total e daily, con reset day-safe."""
    _ensure_stats_day()
    STATS["total"][field] += n
    STATS["daily"][field] += n

# Telemetria geocoding (per /google-usage)
GEOCODE_PROVIDER_COUNTS = Counter()
LAST_GEOCODE_HIT = {"source": None, "name": None, "lat": None, "lon": None}

TF = TimezoneFinder()
TZ_CACHE = TTLCache(maxsize=5000, ttl=30*24*3600)  # cache 30 giorni

def resolve_timezone(lat: float, lon: float) -> str:
    key = (round(lat, 4), round(lon, 4))
    hit = TZ_CACHE.get(key)
    if hit: return hit
    tzname = TF.timezone_at(lng=lon, lat=lat) or TF.closest_timezone_at(lng=lon, lat=lat)
    if tzname:
        TZ_CACHE[key] = tzname
    return tzname

# --- CONFIG GEOCODING ---
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip() # opzionale
GEOCODER_UA = os.getenv("GEOCODER_UA", "house-of-venus-astrocalc/1.0")
GEO_TTL_SECONDS = int(os.getenv("GEO_TTL_SECONDS", "86400"))   # 24h
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))
RETRY_BASE_SLEEP = float(os.getenv("RETRY_BASE_SLEEP", "0.6"))
RETRY_MAX_SLEEP = float(os.getenv("RETRY_MAX_SLEEP", "4.0"))

# Cache geocoding: 2000 voci per 24h
GEOCODE_CACHE = TTLCache(maxsize=2000, ttl=24*3600)

# Intestazione richiesta richiesta da Nominatim (metti un contatto reale)
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "adonis.gagliardi@gmail.com")
UA_SITE = os.getenv("UA_SITE", "https://www.houseofvenus.pl")
NOMINATIM_HEADERS = {
    "User-Agent": f"HouseOfVenus-AstroCalc/1.0 ({UA_SITE}; {CONTACT_EMAIL})",
    "Accept": "application/json",
}

def _norm_place(p: str) -> str:
    return " ".join(p.strip().split()).lower()

# --- Provider: Google ---
def geocode_google(place: str) -> Optional[Dict[str, Any]]:
    if not GOOGLE_MAPS_API_KEY:
        return None
    r = http_get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": place, "key": GOOGLE_MAPS_API_KEY}
    )
    if r.status_code != 200:
        return None
    data = r.json()
    if data.get("status") == "OK" and data.get("results"):
        it = data["results"][0]
        loc = it["geometry"]["location"]
        return {
            "lat": float(loc["lat"]), "lon": float(loc["lng"]),
            "name": it.get("formatted_address", place), "source": "google"
        }
    return None

# --- Provider: Nominatim (OpenStreetMap) ---
def geocode_nominatim(place: str) -> Optional[Dict[str, Any]]:
    r = http_get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1, "addressdetails": 1},
        headers=NOMINATIM_HEADERS,
    )
    if r.status_code != 200:
        return None
    js = r.json()
    if isinstance(js, list) and js:
        it = js[0]
        return {
            "lat": float(it["lat"]), "lon": float(it["lon"]),
            "name": it.get("display_name", place), "source": "nominatim"
        }
    return None

# --- Provider: Open-Meteo Geocoding ---
def geocode_openmeteo(place: str) -> Optional[Dict[str, Any]]:
    r = http_get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": place, "count": 1, "language": "en", "format": "json"}
    )
    if r.status_code != 200:
        return None
    data = r.json()
    res = data.get("results") or []
    if res:
        it = res[0]
        label = it.get("name", "")
        cc = it.get("country_code", "")
        name = f"{label}, {cc}".strip(", ")
        return {
            "lat": float(it["latitude"]), "lon": float(it["longitude"]),
            "name": name or place, "source": "open-meteo"
        }
    return None

# --- Provider: Maps.co (free wrapper OSM) ---
def geocode_mapsco(place: str) -> Optional[Dict[str, Any]]:
    r = http_get("https://geocode.maps.co/search", params={"q": place})
    if r.status_code != 200:
        return None
    data = r.json()
    if isinstance(data, list) and data:
        it = data[0]
        return {
            "lat": float(it["lat"]), "lon": float(it["lon"]),
            "name": it.get("display_name", place), "source": "maps.co"
        }
    return None

# --- Geocoder order via ENV, con validazione e fallback ---
def get_geocoder_order():
    """
    Costruisce la lista di provider nell'ordine richiesto.
    Usa GEOCODER_ORDER, es. "nominatim,openmeteo,mapsco,google".
    Valori non riconosciuti vengono ignorati con warning.
    Google viene escluso se manca la GOOGLE_MAPS_API_KEY.
    """
    raw = os.getenv("GEOCODER_ORDER", "google,nominatim,openmeteo,mapsco")
    requested = [s.strip().lower() for s in raw.split(",") if s.strip()]

    allowed = {
        "google": geocode_google if GOOGLE_MAPS_API_KEY else None,
        "nominatim": geocode_nominatim,
        "openmeteo": geocode_openmeteo,
        "mapsco": geocode_mapsco,
    }

    providers = []
    seen = set()
    for name in requested:
        if name not in allowed:
            app.logger.warning(f"[geocode] provider sconosciuto in GEOCODER_ORDER: '{name}' (ignorato)")
            continue
        fn = allowed[name]
        if fn and name not in seen:
            providers.append(fn)
            seen.add(name)

    # se dopo il filtraggio non resta nulla, usa l'ordine di default valido
    if not providers:
        default_order = ["google", "nominatim", "openmeteo", "mapsco"]
        providers = [allowed[n] for n in default_order if allowed[n]]

    try:
        app.logger.info(f"[geocode] ordine effettivo: {[fn.__name__ for fn in providers]}")
    except Exception:
        pass

    return providers

# --- Master geocode con cache+fallback ---
def geocode_place(place: str) -> Optional[Dict[str, Any]]:
    if not place or not place.strip():
        return None
    key = _norm_place(place)

    # Cache hit
    cached = GEOCODE_CACHE.get(key)
    if cached:
        return cached
    
    # invece della lista fissa
    # OLD Version: providers = [geocode_google, geocode_nominatim, geocode_openmeteo, geocode_mapsco]
    providers = get_geocoder_order()
    for prov in providers:
        try:
            res = prov(place)
            if res and "lat" in res and "lon" in res:
                GEOCODE_CACHE[key] = res
                # 👇👇 AGGIUNGI QUI
                GEOCODE_PROVIDER_COUNTS[res.get("source","unknown")] += 1
                LAST_GEOCODE_HIT.update({
                    "source": res.get("source"),
                    "name": res.get("name"),
                    "lat": float(res.get("lat")) if res.get("lat") is not None else None,
                    "lon": float(res.get("lon")) if res.get("lon") is not None else None,
                })
                # 👆👆
                # 👇 aggiungi qui
                if res.get("source") == "google":
                    _bump("google_calls")
                # 👆
                return res
        except Exception as e:
            app.logger.warning(f"geocode provider {prov.__name__} error: {e}")
    return None

# def geocode_place(place: str):
#     if not place or not place.strip():
#         return None
#     place = place.strip()
#
#     # Cache hit
#     cached = GEOCODE_CACHE.get(place)
#     if cached:
#         return cached
#
#     # --- Provider 1: Nominatim (OpenStreetMap) ---
#     try:
#         r = http_get(
#             "https://nominatim.openstreetmap.org/search",
#             params={"q": place, "format": "json", "limit": 1, "addressdetails": 0},
#             headers=NOMINATIM_HEADERS,
#         )
#         if r.status_code == 200:
#             js = r.json()
#             if isinstance(js, list) and js:
#                 it = js[0]
#                 name = it.get("display_name") or place
#                 res = _normalize_geo(it["lat"], it["lon"], name, "nominatim")
#                 GEOCODE_CACHE[place] = res
#                 return res
#     except Exception as e:
#         app.logger.warning(f"geocode nominatim error: {e}")
#
#     # --- Provider 2: LocationIQ (opzionale) ---
#     liq_key = os.getenv("LOCATIONIQ_KEY")
#     if liq_key:
#         try:
#             r = http_get(
#                 "https://us1.locationiq.com/v1/search",
#                 params={"key": liq_key, "q": place, "format": "json", "limit": 1},
#             )
#             if r.status_code == 200:
#                 js = r.json()
#                 if isinstance(js, list) and js:
#                     it = js[0]
#                     name = it.get("display_name") or place
#                     res = _normalize_geo(it["lat"], it["lon"], name, "locationiq")
#                     GEOCODE_CACHE[place] = res
#                     return res
#         except Exception as e:
#             app.logger.warning(f"geocode locationiq error: {e}")
#
#     # --- Provider 3: Google Geocoding (opzionale) ---
#     gkey = os.getenv("GOOGLE_MAPS_KEY")
#     if gkey:
#         try:
#             r = http_get(
#                 "https://maps.googleapis.com/maps/api/geocode/json",
#                 params={"address": place, "key": gkey}
#             )
#             if r.status_code == 200:
#                 js = r.json()
#                 if js.get("status") == "OK" and js.get("results"):
#                     it = js["results"][0]
#                     loc = it["geometry"]["location"]
#                     name = it.get("formatted_address") or place
#                     res = _normalize_geo(loc["lat"], loc["lng"], name, "google")
#                     GEOCODE_CACHE[place] = res
#                     return res
#         except Exception as e:
#             app.logger.warning(f"geocode google error: {e}")
#
#     return None

# --- TTL cache semplice in memoria ---

# class SimpleTTLCache:
#     def __init__(self, maxsize=8000, ttl=86400):
#         self.store: Dict[str, Tuple[float, Any]] = {}
#         self.maxsize, self.ttl = maxsize, ttl
#     def get(self, key: str):
#         rec = self.store.get(key)
#         if not rec: return None
#         ts, val = rec
#         if time.time() - ts > self.ttl:
#             self.store.pop(key, None)
#             return None
#         return val
#     def set(self, key: str, val: Any):
#         if len(self.store) > self.maxsize:
#             # prune semplice: rimuovi scaduti o una manciata dei più vecchi
#             for k in list(self.store.keys())[:1000]:
#                 ts, _ = self.store[k]
#                 if time.time() - ts > self.ttl:
#                     self.store.pop(k, None)
#         self.store[key] = (time.time(), val)
#
# GEO_CACHE = SimpleTTLCache(ttl=GEO_TTL_SECONDS)
# ROUTE_TTL_SECONDS = int(os.getenv("ROUTE_TTL_SECONDS", "300"))  # default 5 min
# ROUTE_CACHE = SimpleTTLCache(maxsize=2000, ttl=ROUTE_TTL_SECONDS)
#
#
# # --- Provider: Google (se key presente)
# def geocode_google(place: str) -> Optional[Dict[str, Any]]:
#     if not GOOGLE_MAPS_API_KEY:
#         return None
#     url = "https://maps.googleapis.com/maps/api/geocode/json"
#     params = {"address": place, "key": GOOGLE_MAPS_API_KEY}
#     r = _req_with_retry(url, params=params, headers={"User-Agent": GEOCODER_UA})
#     if not r: return None
#     try:
#         data = r.json()
#         if data.get("status") == "OK" and data.get("results"):
#             it = data["results"][0]
#             loc = it["geometry"]["location"]
#             return {"lat": float(loc["lat"]), "lon": float(loc["lng"]),
#                     "name": it.get("formatted_address", place), "source": "google"}
#     except Exception:
#         app.logger.exception("[geocode] google parse error")
#     return None
#
# # --- Provider: Nominatim
# def geocode_nominatim(place: str) -> Optional[Dict[str, Any]]:
#     # Esempio dentro geocode_nominatim:
#     r = http_get(
#         "https://nominatim.openstreetmap.org/search",
#         params={"q": place, "format": "json", "limit": 1, "addressdetails": 1},
#         headers={"User-Agent": GEOCODER_UA, "Accept": "application/json"},
#     )
#     if not r: return None
#     try:
#         data = r.json()
#         if isinstance(data, list) and data:
#             it = data[0]
#             return {"lat": float(it["lat"]), "lon": float(it["lon"]),
#                     "name": it.get("display_name", place), "source": "nominatim"}
#     except Exception:
#         app.logger.exception("[geocode] nominatim parse error")
#     return None
#
# # --- Provider: Open-Meteo
# def geocode_openmeteo(place: str) -> Optional[Dict[str, Any]]:
#     url = "https://geocoding-api.open-meteo.com/v1/search"
#     params = {"name": place, "count": 1, "language": "en", "format": "json"}
#     r = _req_with_retry(url, params=params, headers={"User-Agent": GEOCODER_UA})
#     if not r: return None
#     try:
#         data = r.json(); res = data.get("results") or []
#         if res:
#             it = res[0]
#             return {"lat": float(it["latitude"]), "lon": float(it["longitude"]),
#                     "name": f'{it.get("name","")}, {it.get("country_code","")}'.strip(", "),
#                     "source": "open-meteo"}
#     except Exception:
#         app.logger.exception("[geocode] open-meteo parse error")
#     return None
#
# # --- Provider: Maps.co (ulteriore fallback)
# def geocode_mapsco(place: str) -> Optional[Dict[str, Any]]:
#     url = "https://geocode.maps.co/search"
#     params = {"q": place}
#     r = _req_with_retry(url, params=params, headers={"User-Agent": GEOCODER_UA})
#     if not r: return None
#     try:
#         data = r.json()
#         if isinstance(data, list) and data:
#             it = data[0]
#             return {"lat": float(it["lat"]), "lon": float(it["lon"]),
#                     "name": it.get("display_name", place), "source": "maps.co"}
#     except Exception:
#         app.logger.exception("[geocode] maps.co parse error")
#     return None
#
# def geocode_place(place: str) -> Optional[Dict[str, Any]]:
#     key = f"geo:{_norm_place(place)}"
#     cached = GEO_CACHE.get(key)
#     if cached: return cached
#
#     providers = [geocode_google, geocode_nominatim, geocode_openmeteo, geocode_mapsco]
#     for fn in providers:
#         try:
#             res = fn(place)
#             if res and "lat" in res and "lon" in res:
#                 GEO_CACHE.set(key, res)
#                 return res
#         except Exception:
#             app.logger.exception(f"[geocode] provider crash: {fn.__name__}")
#             continue
#     return None
#
# app = Flask(__name__)
# DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"
#
# # ---- API key protection ----
# API_KEY = os.getenv("API_KEY", "a96be9cd-d006-439c-962b-3f8314d2e080")

app = Flask(__name__)

# Debug OFF di default in produzione (abilitalo solo mettendo FLASK_DEBUG=1)
DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

# API key: se non la imposti in ENV, l’API resta aperta (comodo per test)
API_KEY = os.getenv("API_KEY", "")

# ---- Protezione API key (solo se impostata) ----
@app.before_request
def check_api_key():
    # endpoint pubblici e preflight
    public_paths = ("/", "/health", "/healthz")
    if request.method == "OPTIONS" or request.path in public_paths:
        return
    # se API_KEY è settata, richiedi header X-API-Key
    if API_KEY:
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
@app.get("/astro-stats")
def astro_stats():
    # reset giornaliero se è cambiata la data
    _ensure_stats_day()

    # attenzione: _value è attributo interno, ma utile per un’indicazione live
    current_available = getattr(NATAL_SEM, "_value", None)

    return {
        "service": "houseofvenus-astrocalc",
        "uptime_sec": round(time.time() - start_ts, 1),
        "python": sys.version.split()[0],
        # 👇 NUOVO: mostra l’ordine effettivo dei geocoder
        "geocoder_order": [fn.__name__ for fn in get_geocoder_order()],
        # === NUOVO: contatori compatibili con il tuo script ===
        "last_reset": STATS["last_reset"],
        "daily": {
            "natal_calls": STATS["daily"]["natal_calls"],
            "google_calls": STATS["daily"]["google_calls"],
            "cache_hits":  STATS["daily"]["cache_hits"],
        },
        "total": {
            "natal_calls": STATS["total"]["natal_calls"],
            "google_calls": STATS["total"]["google_calls"],
            "cache_hits":  STATS["total"]["cache_hits"],
        },
        # === FINE NUOVO ===

        "cache": {
            "natal_entries":   len(NATAL_CACHE),
            "geocode_entries": len(GEOCODE_CACHE),
            "tz_entries":      len(TZ_CACHE),
        },
        "concurrency": {
            "max_parallel": MAX_CONC,
            "available_token_estimate": current_available,
        },
        "health": "ok",
    }, 200

@app.get("/google-usage")
def google_usage():
    # Mantieni il reset giornaliero
    _ensure_stats_day()
    # se non imposti la key, segnala "disabled"
    google_key_set = bool(os.getenv("GOOGLE_MAPS_API_KEY"))
    return {
        # ⬇️ campi che avevi già
        "google_maps_api_key": "enabled" if google_key_set else "disabled",
        # 👇 NUOVO: mostra l’ordine effettivo dei geocoder
        "geocoder_order": [fn.__name__ for fn in get_geocoder_order()],
        "geocode_provider_counts": dict(GEOCODE_PROVIDER_COUNTS),
        "last_geocode_hit": LAST_GEOCODE_HIT,

        # ⬇️ campi aggiunti per compatibilità con lo script PS
        "calls_today": STATS["daily"]["google_calls"],
        "last_reset": STATS["last_reset"],
    }, 200

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

# consenti max 6 /natal in parallelo; le altre attendono
NATAL_SEM = BoundedSemaphore(6)

# cache risultati per 1h (regolabile); fino a 2000 chiavi
NATAL_CACHE = TTLCache(maxsize=2000, ttl=3600)

def _key_from_payload(p: dict) -> str:
    # costruiamo una chiave deterministica (adatta se NON includi dati sensibili)
    # usa i campi che determinano il risultato astrologico
    return "|".join([
        str(p.get("date", "")),
        str(p.get("time", "")),
        str(p.get("place", "")),
        str(p.get("lat", "")),
        str(p.get("lon", "")),
        str(p.get("tz", "")),
    ])

def do_natal(data: dict) -> dict:
    name = (data.get("name") or "Unknown").strip() if isinstance(data.get("name"), str) else "Unknown"
    date_str = data.get("date")
    time_str = data.get("time", "12:00")
    place = data.get("place")
    hsys = ensure_house_system(data.get("house_system", "P"))

    if not date_str or not place:
        raise ValueError("Missing required fields: 'date' and 'place'")

    # Geocoding
    geo = geocode_place(place)
    if not geo:
        raise RuntimeError(f"Geocoding failed for '{place}' (providers exhausted). Try 'City, Country'")
    lat, lon = float(geo["lat"]), float(geo["lon"])
    resolved_place = geo.get("name", place)
    geocoder_source = geo.get("source", "unknown")
    app.logger.info(f"[natal] geocoder={geocoder_source} place='{place}' -> '{resolved_place}' ({lat},{lon})")

    # Time zone (cache globale + TimezoneFinder globale)
    tzname = resolve_timezone(lat, lon)
    if not tzname:
        raise RuntimeError("Timezone not found for coordinates")
    tz = pytz.timezone(tzname)

    # Localize -> UTC con gestione DST
    year, month, day = map(int, date_str.split("-"))
    hh, mm = map(int, time_str.split(":"))
    naive = datetime(year, month, day, hh, mm, 0)
    try:
        local_dt = tz.localize(naive, is_dst=None)
    except AmbiguousTimeError:
        local_dt = tz.localize(naive, is_dst=True)
    except NonExistentTimeError:
        local_dt = tz.localize(naive) + timedelta(hours=1)

    utc_dt = local_dt.astimezone(pytz.utc)

    # Julian Day (UT)
    ut_hour = utc_dt.hour + utc_dt.minute/60 + utc_dt.second/3600
    jd_ut = swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, ut_hour, swe.GREG_CAL)

    # Planets
    flags = swe.FLG_SWIEPH | swe.FLG_SPEED
    positions = {}
    for pid, pname in PLANETS:
        lon_deg, lat_deg, dist, slon, slat, sdist = _calc_ut_tuple(jd_ut, pid, flags)
        sign, deg_in_sign, absdeg = lon_to_sign_deg(lon_deg)
        positions[pname] = {
            "longitude": round(absdeg, 6),
            "sign": sign,
            "deg_in_sign": round(deg_in_sign, 6),
            "deg_str": format_deg(deg_in_sign),
            "speed_lon": round(slon, 6),
        }

    # Houses/Angles
    try:
        try:
            cusps, ascmc = swe.houses_ex(jd_ut, lat, lon, hsys)
        except TypeError:
            cusps, ascmc = swe.houses_ex(jd_ut, lat, lon, hsys.encode("ascii"))
    except Exception as e:
        raise RuntimeError(f"Houses calculation failed: {e}")

    houses = {str(i + 1): round(float(cusps[i]), 6) for i in range(12)}
    asc = float(ascmc[0]); mc = float(ascmc[1])
    asc_sign, asc_deg_in_sign, asc_abs = lon_to_sign_deg(asc)
    mc_sign, mc_deg_in_sign, mc_abs = lon_to_sign_deg(mc)

    return {
        "input": {
            "name": name,
            "place_query": place,
            "resolved_place": resolved_place,
            "geocoder": geocoder_source,
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
        "houses": houses,
        "cached": False
    }

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

    # --- validazione minima: fields essenziali (adatta ai tuoi) ---
    required = ("date", "time", "place")  # aggiungi/varia se usi lat/lon/tz obbligatori
    missing = [f for f in required if not data.get(f)]
    if missing:
        return {"error": f"missing fields: {', '.join(missing)}"}, 400
    _bump("natal_calls")
    # 👇 AGGIUNGI QUESTA RIGA
    cache_key = _key_from_payload(data)
    # --- chiave cache ---
    cached = NATAL_CACHE.get(cache_key)
    if cached is not None:
        _bump("cache_hits")
        return cached, 200

    # --- controllo concorrenza per non saturare i thread ---
    acquired = NATAL_SEM.acquire(timeout=2)  # non bloccare all'infinito
    if not acquired:
        return {"error": "busy, try again"}, 429

    t0 = time.perf_counter()
    try:
        # >>> QUI richiami la tua funzione reale di calcolo <<<
        # IMPORTANTE: dentro do_natal usa timeout+retry per I/O esterno (geocoding, timezone, ecc.)
        result = do_natal(data)   # <-- la tua funzione esistente

        # salva in cache e rispondi
        NATAL_CACHE[cache_key] = result
        return result, 200

    except Exception as e:
        # log utile per capire i colli di bottiglia
        app.logger.exception("natal failed: %s", e)
        return {"error": "internal error"}, 500

    finally:
        dur_ms = (time.perf_counter() - t0) * 1000
        app.logger.info("natal_ms=%.1f", dur_ms)
        NATAL_SEM.release()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=DEBUG)