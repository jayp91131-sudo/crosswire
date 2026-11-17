import subprocess
import time
import json
import urllib.request
import asyncio
import websockets
import base64
import os

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SCREENSHOT_DIR = r"C:\Users\jayp9\.gemini\antigravity\scratch\crosswire\screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

views = [
    ("screenshot_01_overview.png", "overview"),
    ("screenshot_02_scenarios.png", "scenarios"),
    ("screenshot_03_execution_workspace.png", "workspace"),
    ("screenshot_04_findings.png", "findings"),
    ("screenshot_05_evidence.png", "evidence"),
]

resolutions = [
    (1440, 900, "1440"),
    (1366, 768, "1366"),
]

async def capture_for_resolution(width, height, prefix):
    port = 9230 if prefix == "1440" else 9231
    proc = subprocess.Popen([
        CHROME_PATH,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        f"--window-size={width},{height}",
        f"--remote-debugging-port={port}",
        "http://127.0.0.1:8000/"
    ])

    try:
        await asyncio.sleep(2.5)
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list")
        targets = json.loads(resp.read().decode("utf-8"))
        page_target = [t for t in targets if t.get("type") == "page"][0]
        ws_url = page_target["webSocketDebuggerUrl"]
        print(f"[{prefix}] Attached to target: {ws_url}")

        async with websockets.connect(ws_url, max_size=30*1024*1024) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.recv()
            await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
            await ws.recv()

            await asyncio.sleep(1.5)

            msg_id = 10
            for filename, view_id in views:
                msg_id += 1
                expr = f"switchView('{view_id}');"
                await ws.send(json.dumps({
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expr}
                }))

                while True:
                    res = json.loads(await ws.recv())
                    if res.get("id") == msg_id:
                        break

                await asyncio.sleep(1.5)

                msg_id += 1
                await ws.send(json.dumps({
                    "id": msg_id,
                    "method": "Page.captureScreenshot",
                    "params": {"format": "png"}
                }))

                while True:
                    res = json.loads(await ws.recv())
                    if res.get("id") == msg_id:
                        img_data = base64.b64decode(res["result"]["data"])
                        # Save resolution-specific name
                        res_name = f"m2_7_{prefix}_{filename.replace('screenshot_', '').replace('.png', '')}.png"
                        dest_res = os.path.join(SCREENSHOT_DIR, res_name)
                        with open(dest_res, "wb") as f:
                            f.write(img_data)
                        print(f"Saved {res_name} ({len(img_data)} bytes)")

                        # If 1440, also save primary name
                        if prefix == "1440":
                            dest_std = os.path.join(SCREENSHOT_DIR, filename)
                            with open(dest_std, "wb") as f:
                                f.write(img_data)
                            print(f"Saved {filename} ({len(img_data)} bytes)")
                        break

    finally:
        proc.terminate()
        proc.wait()

async def main():
    for w, h, p in resolutions:
        print(f"\n--- Capturing screenshots at {w}x{h} ({p}) ---")
        await capture_for_resolution(w, h, p)

if __name__ == "__main__":
    asyncio.run(main())
