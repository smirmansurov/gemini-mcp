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
import io
try:
    from PIL import Image
except Exception:
    Image = None

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


def _strip_magenta(data: bytes) -> bytes:
    """Если фон картинки — хромакей-маджента, делаем его прозрачным (серверно, без CORS)."""
    if Image is None:
        return data
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        w, h = im.size
        px = im.load()
        def ismag(c):
            r, g, b = c[0], c[1], c[2]
            return r > 150 and b > 120 and g < 110
        corners = [px[0, 0], px[w-1, 0], px[0, h-1], px[w-1, h-1]]
        if not any(ismag(c) for c in corners):
            return data  # не маджента (например, сцены на мятном фоне) — не трогаем
        out = []
        for (r, g, b, a) in im.getdata():
            out.append((r, g, b, 0) if (r > 150 and b > 120 and g < 110) else (r, g, b, a))
        im.putdata(out)
        buf = io.BytesIO(); im.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return data


def _save(resp, filename: str) -> str:
    for part in resp.candidates[0].content.parts:
        inl = getattr(part, "inline_data", None)
        if inl and getattr(inl, "data", None):
            data = inl.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            data = _strip_magenta(data)   # авто-вырезка хромакей-фона у спрайтов
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
def animate_frame(prompt: str, ref_filename: str, filename: str = "", aspect_ratio: str = "4:3") -> str:
    """Сгенерировать СЛЕДУЮЩИЙ КАДР анимации на основе уже существующей картинки.

    ref_filename — имя файла из /img (например 'scene_idea.png'). Возвращает ССЫЛКУ.
    Меняем ТОЛЬКО то, что описано в prompt (например «лампочка загорелась», «рука поднята вверх»),
    сохраняя того же персонажа, позу, цвета, фон и кадрирование — чтобы два изображения
    проигрывались как соседние кадры анимации без рывка.
    """
    ref = GEN_DIR / _name(ref_filename)
    if not ref.exists():
        return f"ERROR: ref '{ref_filename}' не найден в /img"
    ref_bytes = ref.read_bytes()
    full = (prompt +
            "\n\nUse the attached image as the EXACT base frame. "
            "Keep the SAME character(s), identical proportions and pose, identical colors, "
            "identical background, composition and framing, identical art style and lighting. "
            "Change ONLY what is described above, so the two images can be played as two "
            "consecutive animation frames without any visible jump or morphing. "
            f"Aspect ratio {aspect_ratio}.")
    resp = _client.models.generate_content(
        model=MODEL,
        contents=[full, types.Part.from_bytes(data=ref_bytes, mime_type="image/png")],
    )
    return _save(resp, filename)


@mcp.tool()
def list_images() -> str:
    """Список ссылок на уже сгенерированные картинки."""
    names = sorted(p.name for p in GEN_DIR.glob("*.png"))
    return "\n".join(_url(n) for n in names) if names else "Пока ничего не сгенерировано."


app = mcp.streamable_http_app()
app.router.routes.append(Mount("/img", app=StaticFiles(directory=str(GEN_DIR))))

# CORS — чтобы игра в браузере могла читать пиксели картинок (обрезка фона у спрайтов)
try:
    from starlette.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
except Exception:
    pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
