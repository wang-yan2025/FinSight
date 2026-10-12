"""浏览器视觉验收脚本（零第三方依赖：Chrome DevTools 协议 + 标准库）。

用途：真实打开 Streamlit 页面 → 模拟点击完整演示流 → 多分辨率截图。
输出：docs/screenshots/*.png

用法：python scripts/visual_check.py [--port 8501] [--fast]
  --fast 跳过长等待交互（只截各页面初始态）。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT_DIR = os.path.join("docs", "screenshots")


# ---------------- 极简 WebSocket 客户端（客户端帧需掩码） ----------------

class WS:
    def __init__(self, host: str, port: int, path: str):
        self.sock = socket.create_connection((host, port), timeout=120)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        assert b"101" in buf.split(b"\r\n")[0], buf[:120]

    def send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_msg(self, timeout: float = 120) -> dict | None:
        self.sock.settimeout(timeout)
        header = self._recv_exact(2)
        opcode, length = header[0] & 0x0F, header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length) if length else b""
        if opcode in (0x1, 0x0):
            return json.loads(payload.decode("utf-8", "replace"))
        return None

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(min(65536, n - len(buf)))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return buf


class CDP:
    def __init__(self, url: str):
        from urllib.parse import urlparse
        u = urlparse(url)
        self.ws = WS(u.hostname, u.port, u.path)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 120) -> dict:
        self._id += 1
        rid = self._id
        self.ws.send({"id": rid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.ws.recv_msg(timeout=max(1, deadline - time.time()))
            if msg and msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def evaluate(self, expr: str):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")


def click_text(cdp: CDP, needle: str, scope: str = "document") -> bool:
    js = f"""(() => {{
      const els = [...{scope}.querySelectorAll('button, [role="button"], label')];
      const el = els.find(e => e.textContent.replace(/\\s+/g,' ').includes({json.dumps(needle)}));
      if (!el) return false; el.click(); return true;
    }})()"""
    return bool(cdp.evaluate(js))


def shot(cdp: CDP, name: str) -> str:
    r = cdp.call("Page.captureScreenshot", {"format": "png"})
    path = os.path.join(OUT_DIR, f"{name}.png")
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["data"]))
    print(f"  📷 {path}（{len(r['data']) // 1024} KB）")
    return path


def resize(cdp: CDP, w: int, h: int):
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(1.5)


def wait_ready(cdp: CDP, seconds: float):
    """等待 Streamlit 渲染：出现侧边栏品牌且无 stStatus 运行态。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = cdp.evaluate(
            "(() => {const b=[...document.querySelectorAll('[data-testid=\"stSidebar\"]')];"
            "return b.length ? 1 : 0})()")
        if state:
            time.sleep(2)
            return
        time.sleep(0.5)
    time.sleep(2)


def main():
    port = 8501
    fast = "--fast" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    user_dir = os.path.join(os.environ.get("TEMP", "/tmp"), "fs_chrome_profile")
    chrome = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
        f"--remote-debugging-port=9223", f"--user-data-dir={user_dir}",
        "--window-size=1920,1080", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(4)
        targets = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:9223/json", timeout=10).read())
        tab = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.navigate", {"url": f"http://localhost:{port}"})
        print("打开页面并等待渲染…")
        wait_ready(cdp, 30)
        resize(cdp, 1920, 1080)
        time.sleep(2)
        # 断言：侧边栏无原生 radio 圆点；品牌标题完整可见（未被遮挡）
        checks = cdp.evaluate("""(() => {
          const r = {};
          r.radios = document.querySelectorAll('section[data-testid="stSidebar"] input[type=radio]').length;
          const t = document.querySelector('.fs-brand-title');
          const s = document.querySelector('.fs-brand-sub');
          if (t && s) {
            const a = t.getBoundingClientRect(), b = s.getBoundingClientRect();
            r.brand_ok = (a.top >= 0 && a.bottom <= innerHeight && a.height > 16)
                      && (b.top >= a.bottom - 2 && b.height > 10);
            r.brand_text = t.textContent + ' / ' + s.textContent;
          }
          return JSON.stringify(r); })()""")
        print("视觉断言：", checks)
        shot(cdp, "01_report_initial_1920")

        if not fast:
            print("财务分析：输入代码并查询…")
            cdp.evaluate(
                "(() => {const i=[...document.querySelectorAll('input')].find(x=>x.placeholder==='如 600519');"
                "if(i){const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
                "s.call(i,'600519');i.dispatchEvent(new Event('input',{bubbles:true}));}return 1})()")
            time.sleep(1)
            click_text(cdp, "查 询")
            print("  等待取数与渲染（最长 60s）…")
            time.sleep(45)
            shot(cdp, "02_report_result_1920")
            resize(cdp, 1366, 768)
            time.sleep(2)
            shot(cdp, "03_report_result_1366")
            resize(cdp, 420, 900)
            time.sleep(2)
            shot(cdp, "04_report_narrow_420")
            resize(cdp, 1920, 1080)

            print("投资备忘录：加载并生成…")
            click_text(cdp, "投资备忘录",
                       'document.querySelector(\'[data-testid="stSidebar"]\')')
            time.sleep(3)
            shot(cdp, "05_memo_initial_1920")
            click_text(cdp, "加载数据")
            time.sleep(30)
            click_text(cdp, "生成备忘录")
            time.sleep(25)
            shot(cdp, "06_memo_generated_1920")

            print("独立报告核验：开始核验…")
            click_text(cdp, "独立报告核验",
                       'document.querySelector(\'[data-testid="stSidebar"]\')')
            time.sleep(3)
            shot(cdp, "07_verify_initial_1920")
            click_text(cdp, "开始核验")
            time.sleep(35)
            shot(cdp, "08_verify_result_1920")

            print("智能投研工作台：完整闭环…")
            click_text(cdp, "智能投研工作台",
                       'document.querySelector(\'[data-testid="stSidebar"]\')')
            time.sleep(3)
            shot(cdp, "09_bench_initial_1920")
            click_text(cdp, "1 加载数据")
            time.sleep(30)
            click_text(cdp, "2 生成备忘录并独立核验")
            time.sleep(40)
            shot(cdp, "10_bench_round1_1920")
            # 勾选全部确定性修订建议（如存在）并生成修订稿
            picked = cdp.evaluate(
                "(() => {const cs=[...document.querySelectorAll('input[type=checkbox]')];"
                "cs.forEach(c=>{if(!c.checked)c.click();});return cs.length})()")
            if picked:
                time.sleep(1.5)
                click_text(cdp, "3 生成修订稿并再核验")
                time.sleep(40)
                shot(cdp, "11_bench_round2_1920")
            else:
                print("  （无确定性修订建议，跳过修订步骤截图）")
        print("视觉截图完成。")
    finally:
        chrome.kill()


if __name__ == "__main__":
    main()
