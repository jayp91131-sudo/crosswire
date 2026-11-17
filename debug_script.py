import urllib.request
import json
import asyncio
import websockets
import subprocess
import time

proc = subprocess.Popen([
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "--headless=new", "--disable-gpu", "--no-sandbox", "--remote-debugging-port=9227",
    "http://127.0.0.1:8000/"
])

async def test():
    await asyncio.sleep(2)
    resp = urllib.request.urlopen("http://127.0.0.1:9227/json/list")
    targets = json.loads(resp.read().decode("utf-8"))
    ws_url = [t for t in targets if t.get("type") == "page"][0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        await ws.recv()
        await ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        await ws.recv()

        # Reload to catch script compile error
        await ws.send(json.dumps({"id": 3, "method": "Page.reload"}))
        
        # Listen for messages
        for _ in range(20):
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                if "exceptionThrown" in msg.get("method", "") or "consoleAPICalled" in msg.get("method", ""):
                    print("Log/Exception:", json.dumps(msg, indent=2))
            except asyncio.TimeoutError:
                break

if __name__ == "__main__":
    try:
        asyncio.run(test())
    finally:
        proc.terminate()
        proc.wait()
