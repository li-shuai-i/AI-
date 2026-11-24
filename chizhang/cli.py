"""Command line entry point."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from . import foods as food_db
from .server import make_server
from .store import Store, default_path

DEFAULT_PORT = 8770


def _lan_ip() -> str | None:
    """Best-effort local address so the phone can reach the same server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("223.5.5.5", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chizhang",
        description="吃账 — 本地优先的饮食记录助手，专注减脂。",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1，只允许本机访问）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--data", default=None,
                        help=f"数据文件路径（默认 {default_path()}）")
    parser.add_argument("--lan", action="store_true",
                        help="允许同一 WiFi 下的手机访问（等价于 --host 0.0.0.0）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--version", action="version", version=f"chizhang {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = "0.0.0.0" if args.lan else args.host

    store = Store(args.data)
    try:
        server = make_server(host, args.port, store, quiet=True)
    except OSError as err:
        print(f"启动失败：{err}", file=sys.stderr)
        print(f"端口 {args.port} 可能被占用了，换一个：chizhang --port {args.port + 1}", file=sys.stderr)
        return 1

    port = server.server_address[1]
    local_url = f"http://127.0.0.1:{port}/"
    stats = food_db.stats()

    print()
    print("  吃账 chizhang " + __version__ + " — 饮食记录 · 减脂助手")
    print("  " + "-" * 46)
    print(f"  食物库    {stats['total']} 种常见中餐食物")
    print(f"  数据文件  {Path(store.path)}")
    print(f"  打开      {local_url}")
    if args.lan:
        ip = _lan_ip()
        if ip:
            print(f"  手机访问  http://{ip}:{port}/  （同一 WiFi 下）")
        else:
            print("  手机访问  未获取到局域网地址，请检查网络")
    print("  " + "-" * 46)
    print("  Ctrl+C 停止")
    print()

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(local_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。数据保存在 ", store.path)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
