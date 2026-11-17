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

proc = subprocess.Popen([
    CHROME_PATH,
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--window-size=1440,900",
    "--remote-debugging-port=9225",
    "http://127.0.0.1:8000/"
])

async def capture_all():
    await asyncio.sleep(2.5)
    resp = urllib.request.urlopen("http://127.0.0.1:9225/json/list")
    targets = json.loads(resp.read().decode("utf-8"))
    page_target = [t for t in targets if t.get("type") == "page"][0]
    ws_url = page_target["webSocketDebuggerUrl"]
    print("Attached to page target:", ws_url)

    tabs = [
        ("screenshot_01_overview.png", "overview"),
        ("screenshot_02_scenarios.png", "scenarios"),
        ("screenshot_03_execution_console.png", "workspace"),
        ("screenshot_04_findings.png", "findings"),
        ("screenshot_05_evidence.png", "evidence"),
    ]

    async with websockets.connect(ws_url, max_size=25*1024*1024) as ws:
        # Enable Page and Runtime
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        await ws.recv()
        await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
        await ws.recv()

        # Let the initial dashboard load
        await asyncio.sleep(1.5)

        msg_id = 10
        for filename, tab_name in tabs:
            msg_id += 1
            print(f"Switching to tab: {tab_name}...")
            # Call switchTab in page context
            expr = f"switchTab('{tab_name}');"
            await ws.send(json.dumps({
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {"expression": expr}
            }))
            
            # Wait for evaluate response
            while True:
                res = json.loads(await ws.recv())
                if res.get("id") == msg_id:
                    break

            # Allow DOM updates / fetch to resolve
            await asyncio.sleep(1.2)

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
                    dest = os.path.join(SCREENSHOT_DIR, filename)
                    with open(dest, "wb") as f:
                        f.write(img_data)
                    print(f"Captured {filename} ({len(img_data)} bytes)")
                    break

if __name__ == "__main__":
    try:
        asyncio.run(capture_all())
    finally:
        proc.terminate()
        proc.wait()
