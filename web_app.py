from __future__ import annotations

import asyncio
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes import router
from api.websocket_manager import web_socket_manager
from main import main as trading_main


app = FastAPI(title="Trading Web UI")
app.include_router(router)
app.mount("/static", StaticFiles(directory="static"), name="static")

WEB_HOST = "0.0.0.0"
WEB_PORT = 8000


class ThreadedUvicornServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _run_web_server() -> None:
    config = uvicorn.Config(app, host=WEB_HOST, port=WEB_PORT, reload=False, log_level="info")
    server = ThreadedUvicornServer(config)
    server.run()


@app.on_event("startup")
async def on_startup() -> None:
    web_socket_manager.bind_loop(asyncio.get_running_loop())
    asyncio.create_task(web_socket_manager.timer_loop())


if __name__ == "__main__":
    lan_ip = _local_ip()
    print(f"Web UI Local: http://127.0.0.1:{WEB_PORT}")
    print(f"Web UI Mobile: http://{lan_ip}:{WEB_PORT}")
    server_thread = threading.Thread(target=_run_web_server, daemon=True, name="web-ui-server")
    server_thread.start()
    time.sleep(1)
    trading_main()
