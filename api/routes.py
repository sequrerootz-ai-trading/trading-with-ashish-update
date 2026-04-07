from __future__ import annotations

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from api.websocket_manager import web_socket_manager


templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "symbols": ["NIFTY", "SENSEX", "CRUDEOIL"],
            "default_symbol": "NIFTY",
        },
    )


@router.get("/api/state")
async def get_state():
    return web_socket_manager.get_snapshot()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await web_socket_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        web_socket_manager.disconnect(websocket)
    except Exception:
        web_socket_manager.disconnect(websocket)
