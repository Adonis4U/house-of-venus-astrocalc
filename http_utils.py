# http_utils.py
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SESSION = requests.Session()
_retries = Retry(
    total=3, backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
SESSION.mount("http://", HTTPAdapter(max_retries=_retries))
SESSION.mount("https://", HTTPAdapter(max_retries=_retries))

DEFAULT_TIMEOUT = (5, 20)  # connect, read seconds

def http_get(url, **kw):
    kw.setdefault("timeout", DEFAULT_TIMEOUT)
    return SESSION.get(url, **kw)

def http_post(url, **kw):
    kw.setdefault("timeout", DEFAULT_TIMEOUT)
    return SESSION.post(url, **kw)

# Only for DEBUG (Da qui Sotto in Poi)
if __name__ == "__main__":
    print("Testing http_utils...")

    try:
        r = http_get("https://httpbin.org/get", params={"ping": "pong"})
        print("GET status:", r.status_code)
        print("GET body:", r.json())
    except Exception as e:
        print("GET failed:", e)

    try:
        r = http_post("https://httpbin.org/post", json={"hello": "venus"})
        print("POST status:", r.status_code)
        print("POST body:", r.json())
    except Exception as e:
        print("POST failed:", e)