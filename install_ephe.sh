#!/usr/bin/env bash
set -euo pipefail

# ================== CONFIG ==================
# Imposta l'ID del file di Google Drive via env var EPHE_FILE_ID su Render
: "${EPHE_FILE_ID:?[ERROR] EPHE_FILE_ID non impostata. Inserisci ID del file ephe.zip su Google Drive.}"
EPHE_DIR="ephe"
ZIP_PATH="ephe.zip"
BASE_URL="https://docs.google.com/uc?export=download"
COOKIES_FILE=".gdcookies.txt"
UA="curl/8.0 (+render-deploy)"
# ============================================

log() { echo "[INFO] $*" >&2; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

cleanup() {
  rm -f "$COOKIES_FILE" 2>/dev/null || true
}
trap cleanup EXIT

if [[ -d "$EPHE_DIR" ]]; then
  log "Cartella '$EPHE_DIR' già presente: salto download."
  exit 0
fi

log "Cartella '$EPHE_DIR' non trovata. Scarico ephe.zip da Google Drive..."

rm -f "$ZIP_PATH" "$COOKIES_FILE" || true

# 1) Richiesta iniziale (potrebbe restituire HTML con token di conferma)
log "Richiesta iniziale a Google Drive..."
initial_html="$(curl -sS -L -c "$COOKIES_FILE" -A "$UA" \
  --get "$BASE_URL" --data-urlencode "id=${EPHE_FILE_ID}")" || {
  err "Richiesta iniziale fallita."
  exit 1
}

# 2) Cerco token di conferma nell'HTML
confirm_token="$(printf '%s' "$initial_html" | \
  grep -Eo 'confirm=([0-9A-Za-z_]+)&id=' | sed -E 's/confirm=([0-9A-Za-z_]+)&id=/\1/' || true)"

if [[ -z "${confirm_token:-}" ]]; then
  # Provo altro pattern (name="confirm" value="TOKEN")
  confirm_token="$(printf '%s' "$initial_html" | \
    grep -Eo 'name="confirm" value="([0-9A-Za-z_]+)"' | sed -E 's/.*value="([0-9A-Za-z_]+)".*/\1/' || true)"
fi

# 3) Se ho un token → scarico file con token e cookie; altrimenti può darsi che l'HTML fosse già il file (raro)
if [[ -n "${confirm_token:-}" ]]; then
  log "Trovato token di conferma. Procedo al download finale..."
  curl -sS -L -b "$COOKIES_FILE" -A "$UA" \
    --get "$BASE_URL" \
    --data-urlencode "id=${EPHE_FILE_ID}" \
    --data-urlencode "confirm=${confirm_token}" \
    -o "$ZIP_PATH" || {
      err "Download con token fallito."
      exit 1
    }
else
  log "Nessun token trovato: provo a capire se la risposta iniziale è un file o HTML di errore…"
  # In genere è HTML. Salvo comunque su ZIP_PATH per diagnosi.
  printf '%s' "$initial_html" > "$ZIP_PATH"
fi

# 4) Verifica rapida: se l’inizio del file sembra HTML, mostro preview e fallisco
if head -c 15 "$ZIP_PATH" | tr '[:upper:]' '[:lower:]' | grep -qE '<!doctype|<html'; then
  err "Il contenuto scaricato sembra HTML (non ZIP)."
  warn "Prime 25 righe per debug:"
  head -n 25 "$ZIP_PATH" >&2
  warn "Cause tipiche: ID errato, file non pubblico, quota superata, file non è uno ZIP reale."
  exit 1
fi

# 5) Verifica che sia davvero uno zip (preferisco unzip -tq se presente; fallback con file magic)
if command -v unzip >/dev/null 2>&1; then
  if ! unzip -tq "$ZIP_PATH" >/dev/null 2>&1; then
    err "Lo ZIP è corrotto o non valido."
    warn "Prime 25 righe per debug:"
    head -n 25 "$ZIP_PATH" | sed $'s/\x00/\\x00/g' >&2
    exit 1
  fi
else
  # Fallback: controllo magic number PK\x03\x04
  if ! head -c 4 "$ZIP_PATH" | grep -q $'^PK\x03\x04'; then
    err "Il file scaricato non ha la signature ZIP (PK\\x03\\x04)."
    warn "Prime 25 righe per debug:"
    head -n 25 "$ZIP_PATH" | sed $'s/\x00/\\x00/g' >&2
    exit 1
  fi
fi

# 6) Estrazione
log "Estrazione in '$EPHE_DIR'…"
mkdir -p "$EPHE_DIR"
if command -v unzip >/dev/null 2>&1; then
  unzip -oq "$ZIP_PATH" -d "$EPHE_DIR"
else
  # Fallback via Python (se unzip non esiste nell’immagine)
  python3 - <<'PY'
import zipfile, sys, os
zip_path = "ephe.zip"
out_dir = "ephe"
os.makedirs(out_dir, exist_ok=True)
with zipfile.ZipFile(zip_path, "r") as zf:
    zf.extractall(out_dir)
PY
fi
log "Estrazione completata."

# 7) Pulizia
rm -f "$ZIP_PATH" "$COOKIES_FILE" || true
log "Pulizia fatta. Pronto!"
