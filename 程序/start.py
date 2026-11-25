#!/usr/bin/env python3
"""启动本地界面。macOS 和 Windows 都运行这个文件。"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui_server


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_port(preferred: int) -> int:
    if preferred < 1 or preferred > 65535:
        raise SystemExit("端口必须在 1 到 65535")
    for port in range(preferred, min(preferred + 20, 65536)):
        if port_is_free(port):
            return port
    raise SystemExit(f"从 {preferred} 起没有可用端口")


def main() -> None:
    parser = argparse.ArgumentParser(description="启动异物生成界面")
    parser.add_argument("--port", type=int, default=8765, help="首选端口，占用时向后尝试")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    port = choose_port(args.port)
    ui_server.PORT = port
    url = f"http://127.0.0.1:{port}"
    if port != args.port:
        print(f"{args.port} 已被占用，改用 {port}")
    print(f"界面：{url}", flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.6, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    ui_server.main()


if __name__ == "__main__":
    main()
