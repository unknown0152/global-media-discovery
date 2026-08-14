from __future__ import annotations

import json
import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/api/v1/health"
try:
    with urllib.request.urlopen(url, timeout=4) as response:
        data = json.load(response)
    raise SystemExit(0 if data.get("status") == "ok" else 1)
except Exception:
    raise SystemExit(1)
