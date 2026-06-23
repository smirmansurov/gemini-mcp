#!/usr/bin/env python3
"""
Remote MCP-сервер генерации изображений через Gemini (gemini-2.5-flash-image / «Nano Banana»).
Транспорт: Streamable HTTP. Картинки сохраняются на сервере и отдаются по ссылке /img/<имя>.

Эндпоинт MCP:  https://<домен>/mcp
Картинки:      https://<домен>/img/<имя>.png
"""
import os
import re
import datetime
import pathlib

from mcp.server.fastmcp import FastMCP
from google import genai
from starlette.staticfiles import StaticFiles
from starlette.routing import Mount

MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEN_DIR = pathlib.Path(os.environ.get("GEN_DIR", "/tmp/generated"))
GEN_DIR.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")  # напр. https://gemini-mcp-...up.railway.app

# Отключаем DNS-rebinding-защиту (иначе 421 на домене Railway)
try:
    from mcp.server.transport_security import TransportSecuritySettings
    _sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp = FastMCP("gemini-image", stateless_http=True, transport_security=_sec)
except Exception:
    mcp = FastMCP("gemini-image", stateless_http=True)

_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _name(filename: str) -> str:
    if not filename:
        filename = "img_" + datetime.datetime.now().strftime("%H%M%S")
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
    if not filename.lower().endswith(".png"):
        filename += ".png"
    return filename


def _url(name: str) -> str:
    return f"{BASE}/img/{name}" if BASE else f"/img/{name}"


@mcp.tool()
def ping() -> str:
    """Проверка живости сервера."""
    return "gemini-image MCP alive; model=" + MODEL


@mcp.tool()
def generate_image(prompt: str, filename: str = "", aspect_ratio: str = "1:1") -> str:
    """Сгенерировать изображение через Gemini, сохранить на сервере и вернуть ССЫЛКУ (не сами данные).

    prompt: подробное описание (англ. промты точнее).
    filename: желаемое имя файла, напр. 'plane.png'.
    aspect_ratio: '1:1' | '16:9' | '9:16' | '4:3' | '3:4'.
    Возвращает короткую строку с URL картинки.
    """
    full = f"{prompt}\n\nAspect ratio {aspect_ratio}. High quality, centered."
    resp = _client.models.generate_content(model=MODEL, contents=[full])
    for part in resp.candidates[0].content.parts:
        inl = getattr(part, "inline_data", None)
        if inl and getattr(inl, "data", None):
            data = inl.data
            if isinstance(data, str):
                import base64
                data = base64.b64decode(data)
            name = _name(filename)
            (GEN_DIR / name).write_bytes(bytes(data))
            return f"OK image_url={_url(name)} (bytes={len(data)})"
    try:
        return "ERROR: " + (resp.candidates[0].content.parts[0].text or "no image")
    except Exception:
        return "ERROR: no image returned"


@mcp.tool()
def list_images() -> str:
    """Список ссылок на уже сгенерированные картинки."""
    names = sorted(p.name for p in GEN_DIR.glob("*.png"))
    if not names:
        return "Пока ничего не сгенерировано."
    return "\n".join(_url(n) for n in names)


# ASGI: MCP на /mcp + статика картинок на /img
app = mcp.streamable_http_app()
app.router.routes.append(Mount("/img", app=StaticFiles(directory=str(GEN_DIR))))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
