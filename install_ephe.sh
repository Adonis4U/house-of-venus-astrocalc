#!/usr/bin/env bash
set -euo pipefail

: "${EPHE_FILE_ID:?[ERROR] EPHE_FILE_ID non impostata. Inserisci ID del file ephe.zip su Google Drive.}"
EPHE_DIR="ephe"
ZIP_PATH="ephe.zip"

log() { echo "[INFO] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

if [[ -d "$EPHE_DIR" ]]; then
  log "Cartella '$EPHE_DIR' già presente: salto download."
  exit 0
fi

log "Scarico ephe.zip con gdown (ID: $EPHE_FILE_ID)…"

# usa python + gdown (già installato in buildCommand via requirements.txt)
python3 - <<PY
import os, sys, zipfile
import gdown

file_id = os.environ["EPHE_FILE_ID"]
url = f"https://drive.google.com/uc?id={file_id}"
out = "ephe.zip"

# download con gdown (gestisce token/conferme)
gdown.download(url, out=out, quiet=False)

# verifica ZIP
if not zipfile.is_zipfile(out):
    print("[ERROR] Il file scaricato non è uno ZIP valido.", file=sys.stderr)
    with open(out, "rb") as f:
        head = f.read(500)
    try:
        snippet = head.decode("utf-8", errors="replace")
    except:
        snippet = str(head)
    print("[DEBUG] Prime 500 bytes:", file=sys.stderr)
    print(snippet, file=sys.stderr)
    sys.exit(1)

# estrazione
os.makedirs("ephe", exist_ok=True)
with zipfile.ZipFile(out, "r") as zf:
    zf.extractall("ephe")
print("[INFO] Estrazione completata.")
PY

# pulizia
rm -f "$ZIP_PATH" || true
log "Pronto."

