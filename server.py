#!/usr/bin/env python3
"""
Remote MCP-сервер генерации изображений через Gemini (gemini-2.5-flash-image / «Nano Banana»).
Транспорт: Streamable HTTP. Картинки сохраняются на сервере и отдаются по ссылке /img/<имя>.

Эндпоинт MCP:  https://<домен>/mcp
Картинки:      https://<домен>/img/<имя>.png
"""
import os
import re
import base64
import datetime
import pathlib

from mcp.server.fastmcp import FastMCP
from google import genai
from google.genai import types
from starlette.staticfiles import StaticFiles
from starlette.routing import Mount

MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEN_DIR = pathlib.Path(os.environ.get("GEN_DIR", "/tmp/generated"))
GEN_DIR.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# Фирменный логотип (лежит рядом с server.py) — для нанесения на ассеты
_LOGO_PATH = pathlib.Path(__file__).parent / "logo.png"
_LOGO_BYTES = _LOGO_PATH.read_bytes() if _LOGO_PATH.exists() else None

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


def _save(resp, filename: str) -> str:
    for part in resp.candidates[0].content.parts:
        inl = getattr(part, "inline_data", None)
        if inl and getattr(inl, "data", None):
            data = inl.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            name = _name(filename)
            (GEN_DIR / name).write_bytes(bytes(data))
            return f"OK image_url={_url(name)} (bytes={len(data)})"
    try:
        return "ERROR: " + (resp.candidates[0].content.parts[0].text or "no image")
    except Exception:
        return "ERROR: no image returned"


@mcp.tool()
def ping() -> str:
    """Проверка живости сервера."""
    return f"gemini-image MCP alive; model={MODEL}; logo={'yes' if _LOGO_BYTES else 'no'}"


@mcp.tool()
def generate_image(prompt: str, filename: str = "", aspect_ratio: str = "1:1") -> str:
    """Сгенерировать изображение по промту через Gemini. Возвращает ССЫЛКУ на картинку."""
    full = f"{prompt}\n\nAspect ratio {aspect_ratio}. High quality, centered."
    resp = _client.models.generate_content(model=MODEL, contents=[full])
    return _save(resp, filename)


@mcp.tool()
def generate_with_logo(prompt: str, filename: str = "", aspect_ratio: str = "1:1") -> str:
    """Сгенерировать изображение, ПОКАЗАВ Gemini фирменный логотип Hayot Ventures как образец.

    Используй, когда на ассете нужен ТОЧНЫЙ логотип (на футболке, баннере, табличке и т.п.).
    Возвращает ССЫЛКУ на картинку.
    """
    if not _LOGO_BYTES:
        return "ERROR: logo.png не найден рядом с server.py"
    full = (prompt +
            "\n\nUse the attached image as the EXACT 'Hayot Ventures' logo "
            "(a pinwheel emblem of four diamond blades — three teal, one orange — with the wordmark). "
            "Reproduce this logo faithfully where the prompt asks. "
            f"Aspect ratio {aspect_ratio}. High quality, centered.")
    resp = _client.models.generate_content(
        model=MODEL,
        contents=[full, types.Part.from_bytes(data=_LOGO_BYTES, mime_type="image/png")],
    )
    return _save(resp, filename)


@mcp.tool()
def list_images() -> str:
    """Список ссылок на уже сгенерированные картинки."""
    names = sorted(p.name for p in GEN_DIR.glob("*.png"))
    return "\n".join(_url(n) for n in names) if names else "Пока ничего не сгенерировано."


app = mcp.streamable_http_app()
app.router.routes.append(Mount("/img", app=StaticFiles(directory=str(GEN_DIR))))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
