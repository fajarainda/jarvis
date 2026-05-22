# cache_jasatirta.py - Background fetcher, jalan terpisah
import json, time, os, logging
import importlib.util

CACHE_FILE_ARR  = "cache_arr.json"
CACHE_FILE_AWLR = "cache_awlr.json"
INTERVAL = 120  # fetch tiap 2 menit

def _load_session():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "jasatirta_session",
        os.path.join(here, "jasatirta_session.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

sess = _load_session()
sess.init_session()

while True:
    data = sess.fetch_listpos(timeout=60)
    if data:
        with open(CACHE_FILE_ARR,  "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)
        with open(CACHE_FILE_AWLR, "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)
        print(f"[cache] updated {time.strftime('%H:%M:%S')}")
    else:
        print("[cache] fetch gagal, skip")
    time.sleep(INTERVAL)