#!/bin/bash
set -e

echo "[INFO] Installazione pacchetti necessari..."
apt-get update && apt-get install -y curl unzip

echo "[INFO] Scarico ed estraggo ephe.zip da Google Drive..."

FILE_ID="1luz3NgX1ECrXHh_xw07AHucIyONJ8vGN"
FILE_NAME="ephe.zip"

# Scarica con gestione token di conferma
CONFIRM=$(curl -sc /tmp/cookie "https://drive.google.com/uc?export=download&id=${FILE_ID}" | \
          grep -o 'confirm=[^&]*' | sed 's/confirm=//')
curl -Lb /tmp/cookie "https://drive.google.com/uc?export=download&confirm=${CONFIRM}&id=${FILE_ID}" -o "${FILE_NAME}"

# Crea cartella e estrai
mkdir -p ephe
unzip -o "${FILE_NAME}" -d ephe

# Rimuovi lo zip
rm "${FILE_NAME}"

echo "[OK] ephe.zip scaricato e pronto."
