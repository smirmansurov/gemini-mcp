#!/usr/bin/env python3
"""
Remote MCP-сервер генерации изображений через Gemini (gemini-2.5-flash-image / «Nano Banana»).
Транспорт: Streamable HTTP (совместим с кастомными коннекторами Claude).
Эндпоинт MCP:  https://<домен>/mcp
"""
import os
import base64

from mcp.server.fastmcp import FastMCP
from google import genai

MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

# ВАЖНО: отключаем DNS-rebinding-защиту, иначе MCP отбивает запросы
# с внешнего домена Railway («Invalid Host header» → 421 Misdirected Request).
try:
    from mcp.server.transport_security import TransportSecuritySettings
    _sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp = FastMCP("gemini-image", stateless_http=True, transport_security=_sec)
except Exception:
    # запасной вариант для старых версий SDK
    mcp = FastMCP("gemini-image", stateless_http=True)

_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


@mcp.tool()
def ping() -> str:
    """Проверка живости сервера."""
    return "gemini-image MCP alive; model=" + MODEL


@mcp.tool()
def generate_image(prompt: str, aspect_ratio: str = "1:1") -> str:
    """Сгенерировать изображение по промту через Gemini.

    prompt: подробное описание (англ. промты точнее).
    aspect_ratio: '1:1' | '16:9' | '9:16' | '4:3' | '3:4'.
    Возвращает PNG в виде base64-строки (без префикса data:).
    """
    full = f"{prompt}\n\nAspect ratio {aspect_ratio}. High quality, centered."
    resp = _client.models.generate_content(model=MODEL, contents=[full])
    for part in resp.candidates[0].content.parts:
        inl = getattr(part, "inline_data", None)
        if inl and getattr(inl, "data", None):
            data = inl.data
            if isinstance(data, (bytes, bytearray)):
                return base64.b64encode(bytes(data)).decode()
            return data
    try:
        return "ERROR: " + (resp.candidates[0].content.parts[0].text or "no image")
    except Exception:
        return "ERROR: no image returned"


# ASGI-приложение для uvicorn
app = mcp.streamable_http_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
