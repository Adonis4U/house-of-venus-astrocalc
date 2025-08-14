
import os, urllib.request, zipfile, io, sys

EPHE_DIR = os.path.join(os.path.dirname(__file__), "ephe")
os.makedirs(EPHE_DIR, exist_ok=True)

def is_empty(path):
    try:
        return len([p for p in os.listdir(path) if not p.startswith('.')]) == 0
    except FileNotFoundError:
        return True

URLS = [
    # planetary ephemeris 1800-2399 (good default range)
    "https://www.astro.com/ftp/swisseph/ephe/sepl_18.zip",
    # moon high precision for same period
    "https://www.astro.com/ftp/swisseph/ephe/semo_18.zip",
]

def fetch_and_extract(url):
    print(f"Downloading {url} ...", file=sys.stderr, flush=True)
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(EPHE_DIR)
    print(f"Extracted into {EPHE_DIR}", file=sys.stderr, flush=True)

if is_empty(EPHE_DIR):
    for u in URLS:
        try:
            fetch_and_extract(u)
        except Exception as e:
            print(f"WARNING: failed to fetch {u}: {e}", file=sys.stderr, flush=True)
    if is_empty(EPHE_DIR):
        print("ERROR: ephe directory is still empty. Swiss Ephemeris may fail.", file=sys.stderr, flush=True)
else:
    print("Ephemeris already present.", file=sys.stderr, flush=True)
