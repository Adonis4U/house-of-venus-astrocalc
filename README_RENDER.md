
# House of Venus — AstroCalc (Render)

This repo runs a Flask API on Render (free plan) with Swiss Ephemeris.
On first start it downloads the ephemeris files into `ephe/`.

## Endpoints
- GET `/health` (public)
- POST `/natal` (requires `X-API-Key`)

## Local quick start
```
python -m venv .venv && ./.venv/Scripts/activate  # on Windows
pip install -r requirements.txt
python download_ephe.py
set API_KEY=your-key-here
python app.py
```

## Render
- Add env var `API_KEY` to protect the API
- Optional: `GEOCODER_UA` to set a nicer user-agent
