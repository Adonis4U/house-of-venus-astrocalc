
import os, urllib.request, zipfile, io, sys

BASE = os.path.dirname(__file__)
EPHE_DIR = os.path.join(BASE, "ephe")
os.makedirs(EPHE_DIR, exist_ok=True)

def is_empty(path: str) -> bool:
    try:
        return len([p for p in os.listdir(path) if not p.startswith('.')]) == 0
    except FileNotFoundError:
        return True

# Permette di forzare un URL personalizzato (es. tuo mirror)
OVERRIDE = os.getenv("EPHE_URL_OVERRIDE")

CANDIDATE_URLS = []
if OVERRIDE:
    CANDIDATE_URLS.append(OVERRIDE)

# 1) GitHub ufficiale (raw) - pacchetto completo ephe.zip
CANDIDATE_URLS.append("https://github.com/aloistr/swisseph/raw/master/ephe.zip")

# 2) Dropbox ufficiale (cartella 'ephe' compressa come ephe.zip)
#    Nota: il link pubblico della cartella non punta direttamente allo zip;
#    se hai uno zip tuo con link diretto, usalo via EPHE_URL_OVERRIDE.
#    Lascio come fallback il raw GitHub che è quello più stabile.

def fetch_and_extract(url: str) -> bool:
    try:
        print(f"[ephe] Downloading: {url}", file=sys.stderr, flush=True)
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(EPHE_DIR)
        print(f"[ephe] Extracted into: {EPHE_DIR}", file=sys.stderr, flush=True)
        return True
    except Exception as e:
        print(f"[ephe] WARN: failed {url} → {e}", file=sys.stderr, flush=True)
        return False

if is_empty(EPHE_DIR):
    ok = False
    for u in CANDIDATE_URLS:
        if fetch_and_extract(u):
            ok = True
            break
    if not ok:
        print("[ephe] ERROR: ephe directory is still empty. Swiss Ephemeris may fail.", file=sys.stderr, flush=True)
else:
    print("[ephe] Ephemeris already present.", file=sys.stderr, flush=True)

