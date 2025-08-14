
import os
import requests
import zipfile
import io

# === CONFIGURA QUI IL LINK GOOGLE DRIVE ===
# Sostituisci con l'ID del tuo file ephe.zip caricato su Google Drive
GOOGLE_DRIVE_FILE_ID = "1luz3NgX1ECrXHh_xw07AHucIyONJ8vGN"
GOOGLE_DRIVE_URL = f"https://drive.google.com/uc?export=download&id={GOOGLE_DRIVE_FILE_ID}"

EPHE_DIR = "ephe"

def download_from_google_drive(file_id, destination):
    """Scarica un file da Google Drive gestendo il token di conferma."""
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()

    response = session.get(URL, params={'id': file_id}, stream=True)
    token = get_confirm_token(response)

    if token:
        params = {'id': file_id, 'confirm': token}
        response = session.get(URL, params=params, stream=True)

    save_response_content(response, destination)

def get_confirm_token(response):
    """Estrae il token di conferma se presente."""
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            return value
    return None

def save_response_content(response, destination):
    CHUNK_SIZE = 32768
    with open(destination, "wb") as f:
        for chunk in response.iter_content(CHUNK_SIZE):
            if chunk:
                f.write(chunk)

def ensure_ephe():
    """Scarica ed estrae ephe.zip se non presente."""
    if not os.path.exists(EPHE_DIR):
        print(f"[INFO] Cartella '{EPHE_DIR}' non trovata. Scarico ephe.zip da Google Drive...")
        zip_path = "ephe.zip"
        download_from_google_drive(GOOGLE_DRIVE_FILE_ID, zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(EPHE_DIR)

        os.remove(zip_path)
        print("[OK] ephe.zip scaricato ed estratto con successo.")
    else:
        print(f"[OK] Cartella '{EPHE_DIR}' già presente. Nessun download necessario.")

if __name__ == "__main__":
    ensure_ephe()


