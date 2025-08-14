
import os
import io
import re
import sys
import zipfile
import requests

# === CONFIGURA QUI L'ID GIUSTO DEL TUO FILE SU GOOGLE DRIVE ===
GOOGLE_DRIVE_FILE_ID = os.environ.get("EPHE_FILE_ID", "INSERISCI_ID_FILE_DRIVE")
EPHE_DIR = "ephe"
ZIP_PATH = "ephe.zip"

# URL base per download da Drive (conferma gestita via token)
BASE_URL = "https://docs.google.com/uc?export=download"
CHUNK_SIZE = 32768


def _log(msg):
    print(msg, flush=True)


def get_confirm_token_from_response(response_text: str):
    """
    Alcuni file grandi richiedono un token di conferma. Lo estraiamo dall'HTML.
    """
    # Esempi di pattern che Drive usa (possono cambiare, teniamo più regex)
    patterns = [
        r'confirm=([0-9A-Za-z_]+)&amp;id=',
        r'confirm=([0-9A-Za-z_]+)&id=',
        r'name="confirm" value="([0-9A-Za-z_]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, response_text)
        if m:
            return m.group(1)
    return None


def download_file_from_google_drive(file_id: str, destination: str):
    """
    Scarica un file da Google Drive gestendo conferma e salva su 'destination'.
    Ritorna True se il download sembra riuscito, False altrimenti.
    """
    if not file_id or file_id == "INSERISCI_ID_FILE_DRIVE":
        _log("[ERROR] EPHE_FILE_ID non impostato o ID placeholder.")
        return False

    session = requests.Session()
    params = {"id": file_id}
    _log("[INFO] Avvio download da Google Drive...")
    r = session.get(BASE_URL, params=params, stream=True)

    if r.status_code != 200:
        _log(f"[ERROR] HTTP {r.status_code} iniziale da Drive.")
        return False

    # Se Drive ha restituito HTML (pagina con conferma/quota), cerchiamo il token
    ctype = r.headers.get("Content-Type", "")
    if "text/html" in ctype.lower():
        text = r.text
        # Quota exceeded
        if "download quota" in text.lower():
            _log("[ERROR] Drive quota exceeded per questo file.")
            return False

        token = get_confirm_token_from_response(text)
        if token:
            _log("[INFO] Trovato token di conferma. Procedo...")
            params = {"id": file_id, "confirm": token}
            r = session.get(BASE_URL, params=params, stream=True)
        else:
            _log("[WARN] Nessun token trovato. Provo comunque a salvare il contenuto (potrebbe essere una pagina HTML di errore).")

    # Salvataggio streaming
    try:
        with open(destination, "wb") as f:
            for chunk in r.iter_content(CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
    except Exception as e:
        _log(f"[ERROR] Errore nello scrivere su file: {e}")
        return False

    return True


def ensure_ephe():
    if os.path.isdir(EPHE_DIR):
        _log(f"[INFO] Cartella '{EPHE_DIR}' già presente. Salto il download.")
        return

    _log(f"[INFO] Cartella '{EPHE_DIR}' non trovata. Scarico ephe.zip da Google Drive...")

    ok = download_file_from_google_drive(GOOGLE_DRIVE_FILE_ID, ZIP_PATH)
    if not ok:
        _log("[ERROR] Download da Google Drive fallito.")
        sys.exit(1)

    # Verifica: è davvero uno ZIP?
    if not zipfile.is_zipfile(ZIP_PATH):
        _log("[ERROR] Il file scaricato non è uno ZIP valido.")
        # Stampa anteprima per debug
        try:
            with open(ZIP_PATH, "rb") as f:
                head = f.read(500)
                # prova a decodificare per vedere se è HTML
                try:
                    snippet = head.decode("utf-8", errors="replace")
                except Exception:
                    snippet = str(head)
                _log("[DEBUG] Prime 500 bytes del file scaricato:")
                _log(snippet)
        except Exception as e:
            _log(f"[WARN] Impossibile leggere anteprima del file: {e}")
        sys.exit(1)

    # Estrazione
    try:
        with zipfile.ZipFile(ZIP_PATH, "r") as zip_ref:
            zip_ref.extractall(EPHE_DIR)
        _log("[INFO] Estrazione completata.")
    except zipfile.BadZipFile:
        _log("[ERROR] BadZipFile in estrazione nonostante is_zipfile=True (ZIP corrotto?).")
        sys.exit(1)
    except Exception as e:
        _log(f"[ERROR] Errore in estrazione: {e}")
        sys.exit(1)
    finally:
        # opzionale: rimuovi lo zip per risparmiare spazio
        try:
            os.remove(ZIP_PATH)
        except Exception:
            pass


if __name__ == "__main__":
    ensure_ephe()
