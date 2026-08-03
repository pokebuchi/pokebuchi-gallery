# -*- coding: utf-8 -*-
"""
写真を入れる画面をひらく。

  ・まだ動いていなければ、裏で立ち上げてから開く
  ・すでに動いていれば、そのまま開くだけ

デスクトップのショートカットから呼ばれる。黒い画面は出ない。
"""

import os
import sys
import time
import socket
import pathlib
import subprocess
import webbrowser
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
PORT = 8080
HOSTNAME = socket.gethostname().split(".")[0].lower()
URL = f"http://{HOSTNAME}.local:{PORT}"


def 動いているか():
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", PORT)) != 0:
            return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/where",
                                     headers={"X-Pokebuchi": "1"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def 裏で立ち上げる():
    """黒い画面を出さずに起動する"""
    pythonw = pathlib.Path(sys.executable).with_name("pythonw.exe")
    実行 = str(pythonw) if pythonw.exists() else sys.executable
    旗 = 0
    if os.name == "nt":
        旗 = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen([実行, str(HERE / "server.py")],
                     cwd=str(HERE.parent), creationflags=旗,
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def main():
    if not 動いているか():
        裏で立ち上げる()
        for _ in range(30):            # 立ち上がるまで少し待つ
            time.sleep(0.4)
            if 動いているか():
                break
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
