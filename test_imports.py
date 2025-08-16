# test_imports.py
"""
Script per verificare che tutte le librerie del progetto siano installate correttamente.
Esegui con: python test_imports.py
"""

modules = [
    "flask",
    "gunicorn",
    "timezonefinder",
    "pytz",
    "swisseph",
    "requests",
    "urllib3",
    "gdown",
    "cachetools",
]

errors = []

for m in modules:
    try:
        __import__(m)
        print(f"[OK] {m}")
    except Exception as e:
        errors.append((m, str(e)))
        print(f"[FAIL] {m} -> {e}")

print("\n=== RISULTATO ===")
if not errors:
    print("✅ Tutti i pacchetti richiesti sono installati!")
else:
    print("❌ Mancano o danno errore i seguenti pacchetti:")
    for m, e in errors:
        print(f" - {m}: {e}")