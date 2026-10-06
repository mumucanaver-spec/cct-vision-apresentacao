"""Compara velocidade e precisão das formas de ler uma página escaneada."""
import asyncio, base64, difflib, io, os, random, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ia  # carrega o .env
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

# ---------------------------------------------------------------- página de teste
random.seed(7)
f = ImageFont.truetype("C:/Windows/Fonts/times.ttf", 30)
fb = ImageFont.truetype("C:/Windows/Fonts/timesbd.ttf", 30)
linhas = [("CONVENÇÃO COLETIVA DE TRABALHO 2026/2027", fb),
          ("SINDICATO DOS EMPREGADOS NO COMÉRCIO DE RIBEIRÃO PRETO", fb), ("", f),
          ("CLÁUSULA TERCEIRA - PISOS SALARIAIS", fb),
          ("A partir de 1º de setembro de 2026, ficam assegurados os seguintes pisos:", f)]
funcoes = ["Empacotador", "Repositor", "Caixa", "Vendedor", "Fiscal de loja", "Estoquista",
           "Auxiliar administrativo", "Operador de empilhadeira", "Encarregado", "Gerente de setor"]
valores = []
for fn in funcoes:
    v = f"R$ {random.randint(1900, 4900):,}".replace(",", ".") + f",{random.randint(10, 99)}"
    valores.append(v); linhas.append((f"{fn:<32}{v}", f))
linhas += [("", f), ("CLÁUSULA QUARTA - REAJUSTE SALARIAL", fb)]
pcts = []
for i in range(6):
    p = f"{random.randint(3, 9)},{random.randint(10, 99)}%"; pcts.append(p)
    linhas.append((f"Parágrafo {i+1}º - Faixa salarial {i+1}: reajuste de {p} sobre o salário de agosto.", f))
linhas += [("", f), ("CLÁUSULA DÉCIMA - HORAS EXTRAS", fb)]
for i in range(8):
    p = f"{random.randint(50, 100)}%"; pcts.append(p)
    linhas.append((f"Item {i+1}: as horas extras do tipo {chr(65+i)} terão adicional de {p} sobre a hora normal.", f))
gabarito = "\n".join(l for l, _ in linhas)

img = Image.new("RGB", (1654, 2339), (250, 248, 242)); d = ImageDraw.Draw(img)
for i, (l, fonte) in enumerate(linhas):
    d.text((120, 120 + i * 52), l, fill=(20, 20, 20), font=fonte)
img = Image.blend(img, Image.effect_noise(img.size, 35).convert("RGB"), 0.12).rotate(0.7, fillcolor="white")


def jpeg(im, lado):
    im = im.copy(); im.thumbnail((lado, lado)); b = io.BytesIO(); im.save(b, "JPEG", quality=85); return b.getvalue()


def nota(texto):
    alvo = valores + pcts
    achados = sum(1 for v in alvo if v.replace(" ", "") in texto.replace(" ", ""))
    sim = difflib.SequenceMatcher(None, re.sub(r"\s+", " ", gabarito), re.sub(r"[|*#:\-]+|\s+", " ", texto)).ratio()
    return f"valores corretos {achados}/{len(alvo)} | similaridade {sim:.0%}"


# ---------------------------------------------------------------- OCR do Windows
async def ocr_windows(dados: bytes) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    stream = InMemoryRandomAccessStream()
    w = DataWriter(stream); w.write_bytes(dados); await w.store_async(); await w.flush_async(); stream.seek(0)
    bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    res = await OcrEngine.try_create_from_language(Language("pt-BR")).recognize_async(bmp)
    return "\n".join(l.text for l in res.lines)

for lado in (1654, 2339 * 2):
    im = img if lado == 1654 else img.resize((img.width * 2, img.height * 2))
    b = io.BytesIO(); im.save(b, "PNG")
    t = time.time(); txt = asyncio.run(ocr_windows(b.getvalue()))
    print(f"OCR Windows ({im.width}px): {time.time()-t:5.1f}s | {nota(txt)}")

# ---------------------------------------------------------------- modelos NVIDIA
c = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=os.environ["NVIDIA_API_KEY"], max_retries=3, timeout=600)
instr = "Transcreva fielmente todo o texto desta página, na ordem de leitura. Tabelas em markdown. Não comente."
casos = [
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", 2400, {}),
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", 2400, {"chat_template_kwargs": {"enable_thinking": False}}),
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", 1600, {"chat_template_kwargs": {"enable_thinking": False}}),
    ("meta/llama-3.2-11b-vision-instruct", 2400, {}),
    ("meta/llama-3.2-90b-vision-instruct", 2400, {}),
]
for modelo, lado, extra in casos:
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg(img, lado)).decode()
    t = time.time()
    try:
        r = c.chat.completions.create(model=modelo, max_tokens=8000, temperature=0.1, extra_body=extra or None,
                                      messages=[{"role": "user", "content": [{"type": "text", "text": instr},
                                                {"type": "image_url", "image_url": {"url": url}}]}])
        txt = ia._limpar(r.choices[0].message.content or "")
        print(f"{modelo.split('/')[1][:28]:28} {lado}px {'sem raciocínio' if extra else '':14}: "
              f"{time.time()-t:5.1f}s | {nota(txt)} | tokens saída {r.usage.completion_tokens}")
    except Exception as e:
        print(f"{modelo} {lado} ERRO: {str(e)[:200]}")
