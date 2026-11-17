import urllib.request
import json
import asyncio
import websockets
import subprocess
import time

proc = subprocess.Popen([
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "--headless=new", "--disable-gpu", "--no-sandbox", "--remote-debugging-port=9226",
    "http://127.0.0.1:8000/"
])

async def test():
    await asyncio.sleep(2.5)
    resp = urllib.request.urlopen("http://127.0.0.1:9226/json/list")
    targets = json.loads(resp.read().decode("utf-8"))
    ws_url = [t for t in targets if t.get("type") == "page"][0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url) as ws:
        # Check title
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "document.title"}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                print("Title:", m)
                break
        
        # Check active view before
        await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {"expression": "document.querySelector('.workbench-view.active')?.id"}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 2:
                print("Active before:", m)
                break

        # Call switchTab('scenarios')
        await ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate", "params": {"expression": "switchTab('scenarios'); document.querySelector('.workbench-view.active')?.id"}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 3:
                print("Active after switchTab('scenarios'):", m)
                break

if __name__ == "__main__":
    try:
        asyncio.run(test())
    finally:
        proc.terminate()
        proc.wait()
