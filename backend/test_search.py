import urllib.request
import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def search(query, mrl_dim):
    data = json.dumps({"query": query, "mrl_dim": mrl_dim, "limit": 3}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/search/text",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    res = urllib.request.urlopen(req)
    res_data = json.loads(res.read().decode())
    print(f"\n=== Query: '{query}' | MRL: {mrl_dim}d | Latency: {res_data['latency_ms']} ms ===")
    for r in res_data["results"]:
        print(f"  -> {r['filename']:<24} | Match: {r['accuracy_percent']}% [{r['accuracy_tier'].upper()}] | Sim: {r['similarity']}")

def test_similar(photo_id):
    data = json.dumps({"photo_id": photo_id, "mrl_dim": 256, "limit": 3}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/photo/similar",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    res = urllib.request.urlopen(req)
    res_data = json.loads(res.read().decode())
    print(f"\n=== YouTube-Style Related Photos for ID: {photo_id[:16]}... | Latency: {res_data['latency_ms']} ms ===")
    for r in res_data["results"]:
        print(f"  -> {r['filename']:<24} | Match: {r['accuracy_percent']}% | Sim: {r['similarity']}")

if __name__ == "__main__":
    print("Testing MRL Dimension Switching on NVIDIA RTX 3050...")
    search("golden sunset on the beach", 128)
    search("golden sunset on the beach", 256)
    search("golden sunset on the beach", 512)
    search("golden sunset on the beach", 768)
    search("tax invoice receipt payment document", 256)
    search("fast red sports racing car", 256)

    # Fetch a photo ID from /api/photos and test related recommendations
    res = urllib.request.urlopen("http://127.0.0.1:8000/api/photos")
    photos = json.loads(res.read().decode())["photos"]
    if photos:
        test_similar(photos[0]["id"])
