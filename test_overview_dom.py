import urllib.request
import json
import asyncio
import websockets
import subprocess
import time

proc = subprocess.Popen([
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=1440,900", "--remote-debugging-port=9235",
    "http://127.0.0.1:8000/"
])

async def test():
    await asyncio.sleep(2.5)
    resp = urllib.request.urlopen("http://127.0.0.1:9235/json/list")
    targets = json.loads(resp.read().decode("utf-8"))
    ws_url = [t for t in targets if t.get("type") == "page"][0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "document.getElementById('overview-table-body').innerHTML"}}))
        res = json.loads(await ws.recv())
        val = res.get("result", {}).get("result", {}).get("value")
        print("overview-table-body innerHTML length:", len(val) if val else 0)
        print("overview-table-body sample:\n", val[:500] if val else "EMPTY")

        await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {"expression": "document.getElementById('table-exec-count').innerText"}}))
        res2 = json.loads(await ws.recv())
        print("table-exec-count text:", res2.get("result", {}).get("result", {}).get("value"))

        await ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate", "params": {"expression": "(() => { const r = document.getElementById('overview-executions-table').getBoundingClientRect(); return JSON.stringify(r); })()"}}))
        res3 = json.loads(await ws.recv())
        print("table bounding client rect:", res3.get("result", {}).get("result", {}).get("value"))

if __name__ == "__main__":
    try:
        asyncio.run(test())
    finally:
        proc.terminate()
        proc.wait()
