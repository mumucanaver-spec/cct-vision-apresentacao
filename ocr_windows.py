"""OCR embutido no Windows 10/11: gratuito, local e instantâneo (menos preciso que a IA).

Usado como reserva quando a API de IA está indisponível.
"""

import asyncio

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    _IMPORTADO = True
except ImportError:  # outro sistema operacional ou pacotes não instalados
    _IMPORTADO = False

IDIOMA = "pt-BR"


def disponivel() -> bool:
    return _IMPORTADO and OcrEngine.is_language_supported(Language(IDIOMA))


def ler(imagem: bytes) -> str:
    """Devolve o texto de uma imagem (JPEG/PNG), linha a linha."""
    return asyncio.run(_ler(imagem))


async def _ler(imagem: bytes) -> str:
    fluxo = InMemoryRandomAccessStream()
    escritor = DataWriter(fluxo)
    escritor.write_bytes(imagem)
    await escritor.store_async()
    await escritor.flush_async()
    fluxo.seek(0)
    decodificador = await BitmapDecoder.create_async(fluxo)
    bitmap = await decodificador.get_software_bitmap_async()
    motor = OcrEngine.try_create_from_language(Language(IDIOMA))
    resultado = await motor.recognize_async(bitmap)
    return "\n".join(linha.text for linha in resultado.lines)
