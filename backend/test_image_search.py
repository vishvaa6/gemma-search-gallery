import urllib.request
import json
import mimetypes
import uuid
from pathlib import Path

def test_image_search(image_path, mrl_dim=256):
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    p = Path(image_path)
    file_bytes = p.read_bytes()
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{p.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + file_bytes + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="mrl_dim"\r\n\r\n'
        f"{mrl_dim}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="limit"\r\n\r\n'
        f"3\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/search/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )

    res = urllib.request.urlopen(req)
    data = json.loads(res.read().decode())
    print(f"\n=== Visual Search for Image: '{p.name}' | MRL: {mrl_dim}d | Latency: {data['latency_ms']} ms ===")
    for r in data["results"]:
        print(f"  -> {r['filename']:<24} | Match: {r['accuracy_percent']}% [{r['accuracy_tier'].upper()}] | Sim: {r['similarity']}")

if __name__ == "__main__":
    print("Testing Reverse Visual Search on GPU...")
    test_image_search("media/sample_photos/red_sports_car.jpg", 256)
    test_image_search("media/sample_photos/sunset_beach.jpg", 128)
