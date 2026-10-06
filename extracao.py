"""Converte arquivos de qualquer formato em páginas de texto.

Texto digital (PDF com texto, Word, ODT, RTF, HTML, TXT...) é lido localmente.
Páginas escaneadas, fotos e imagens são transcritas por um modelo de visão, e cada
página transcrita fica em cache para nunca ser lida duas vezes.
"""

import hashlib
import io
import re
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Callable

import pypdfium2
from PIL import Image, ImageOps, ImageSequence
from pypdf import PdfReader

try:  # fotos de iPhone (.heic)
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

import ia
import ocr_windows
from config import (DPI_PAGINAS_ESCANEADAS, LEITURAS_DE_IMAGEM_PARALELAS,
                    PASTA_CACHE_TRANSCRICAO, TRANSCREVER_TODOS_OS_PDFS,
                    USAR_OCR_DO_WINDOWS_COMO_RESERVA)

Image.MAX_IMAGE_PIXELS = None  # digitalizações em alta resolução são legítimas

EXT_TEXTO = {".txt", ".md", ".csv", ".tsv", ".json", ".xml"}
EXT_HTML = {".html", ".htm"}
LADO_MAXIMO_IMAGEM = 2400
TAMANHO_MAXIMO_IMAGEM = 3_000_000
CARACTERES_POR_TRECHO = 4000
_trava_pdfium = threading.Lock()  # a biblioteca de renderização não é segura entre threads

INSTRUCOES_TRANSCRICAO = """Esta é uma página de uma Convenção ou Acordo Coletivo de Trabalho.
Transcreva todo o texto da página, fielmente e na ordem de leitura.
- Reproduza exatamente números, valores em R$, percentuais, datas, nomes e números de cláusulas.
- Tabelas devem virar tabelas em markdown, preservando todas as linhas e colunas.
- Não resuma, não corrija, não comente e não acrescente nada.
- Trecho ilegível: escreva [ilegível].
- Página sem texto: responda apenas [página sem texto]."""


@dataclass
class Pagina:
    rotulo: str                                   # ex.: "pág. 3" ou "foto2.jpg"
    texto: str = ""
    gerar_conteudo: Callable[[], bytes] | None = None  # imagem JPEG p/ a IA, se precisar transcrever
    chave_cache: str = ""
    provisoria: bool = False  # lida pelo OCR do Windows; será relida pela IA depois


def ler_arquivo(caminho: Path, prefixo: str, avisos: list[str]) -> list[Pagina]:
    """Lê um arquivo e devolve suas páginas. `prefixo` identifica o arquivo dentro de uma CCT
    composta por vários arquivos (vazio quando a CCT é um único arquivo)."""
    ext = caminho.suffix.lower()
    try:
        conteudo = caminho.read_bytes()
        sha = hashlib.sha256(conteudo).hexdigest()
        if ext == ".pdf":
            return _ler_pdf(conteudo, sha, prefixo, avisos, caminho.name)
        if ext == ".docx":
            return _fatiar(_ler_docx(conteudo), prefixo)
        if ext == ".odt":
            return _fatiar(_ler_odt(conteudo), prefixo)
        if ext == ".rtf":
            from striprtf.striprtf import rtf_to_text
            return _fatiar(rtf_to_text(_decodificar(conteudo)), prefixo)
        if ext in EXT_HTML:
            return _fatiar(_html_para_texto(_decodificar(conteudo)), prefixo)
        if ext in EXT_TEXTO:
            return _fatiar(_decodificar(conteudo), prefixo)
        if ext == ".doc":
            texto = _ler_doc_antigo(caminho)
            if texto is None:
                avisos.append(f"{caminho.name}: formato .doc antigo não pôde ser lido neste "
                              "computador. Abra no Word e salve como .docx ou .pdf "
                              "(na versão online ele é lido normalmente).")
                return []
            return _fatiar(texto, prefixo)
        # Qualquer outro formato: tenta como imagem (jpg, png, tiff, bmp, webp, heic...).
        try:
            return _ler_imagem(conteudo, sha, prefixo)
        except Exception:
            pass
        texto = _decodificar(conteudo, estrito=True)
        if texto:
            return _fatiar(texto, prefixo)
        avisos.append(f"{caminho.name}: formato não reconhecido; arquivo ignorado.")
    except Exception as e:  # um arquivo ruim não pode derrubar a indexação inteira
        avisos.append(f"{caminho.name}: erro ao ler o arquivo ({e}); arquivo ignorado.")
    return []


def transcrever_pendentes(paginas: list[Pagina], avisos: list[str],
                          progresso: Callable[[str, float], None] | None = None) -> int:
    """Transcreve com a IA as páginas que precisam. Devolve quantas falharam."""
    pendentes = [p for p in paginas if p.gerar_conteudo]
    if not pendentes:
        return 0
    falhas = 0
    with ThreadPoolExecutor(LEITURAS_DE_IMAGEM_PARALELAS) as pool:
        futuros = {pool.submit(_transcrever, p): p for p in pendentes}
        for i, futuro in enumerate(as_completed(futuros), 1):
            pagina = futuros[futuro]
            try:
                futuro.result()
            except Exception as e:
                falhas += 1
                avisos.append(f"Falha ao transcrever {pagina.rotulo}: {e}")
            if progresso:
                progresso(f"Lendo páginas digitalizadas/imagens ({i}/{len(pendentes)})",
                          i / len(pendentes))
    return falhas


def _transcrever(pagina: Pagina):
    imagem = pagina.gerar_conteudo()
    try:
        texto, cortada = ia.transcrever_imagem(imagem, INSTRUCOES_TRANSCRICAO)
    except ia.ErroIA:
        if not (USAR_OCR_DO_WINDOWS_COMO_RESERVA and ocr_windows.disponivel()):
            raise
        # API fora do ar: lê localmente agora, sem guardar no cache, para a IA reler depois.
        pagina.texto = ocr_windows.ler(imagem)
        pagina.provisoria = True
        pagina.gerar_conteudo = None
        return
    texto = texto.strip()
    if cortada:
        texto += "\n[transcrição incompleta]"
    if texto == "[página sem texto]":
        texto = ""
    PASTA_CACHE_TRANSCRICAO.mkdir(parents=True, exist_ok=True)
    (PASTA_CACHE_TRANSCRICAO / f"{pagina.chave_cache}.txt").write_text(texto, encoding="utf-8")
    pagina.texto = texto
    pagina.gerar_conteudo = None


def _pagina_para_ia(rotulo: str, chave: str, gerar: Callable[[], bytes]) -> Pagina:
    cache = PASTA_CACHE_TRANSCRICAO / f"{chave}.txt"
    if cache.exists():
        return Pagina(rotulo, texto=cache.read_text(encoding="utf-8"))
    return Pagina(rotulo, gerar_conteudo=gerar, chave_cache=chave)


def _rotulo(prefixo: str, numero: int, total: int) -> str:
    if not prefixo:
        return f"pág. {numero}"
    return f"{prefixo}, pág. {numero}" if total > 1 else prefixo


# ---------------------------------------------------------------- PDF

def _ler_pdf(conteudo: bytes, sha: str, prefixo: str, avisos: list[str], nome: str):
    leitor = PdfReader(io.BytesIO(conteudo))
    if leitor.is_encrypted:
        leitor.decrypt("")
    total = len(leitor.pages)
    paginas = []
    for i, pagina in enumerate(leitor.pages, 1):
        rotulo = _rotulo(prefixo, i, total)
        try:
            texto = pagina.extract_text() or ""
        except Exception:
            texto = ""
        if (not TRANSCREVER_TODOS_OS_PDFS and _texto_aproveitavel(texto)
                and not _tem_imagem_grande(pagina)):
            paginas.append(Pagina(rotulo, texto=texto))
        else:
            paginas.append(_pagina_para_ia(rotulo, f"{sha}-p{i}",
                                           lambda i=i: _renderizar_pagina_pdf(conteudo, i - 1)))
    if not paginas:
        avisos.append(f"{nome}: PDF sem páginas.")
    return paginas


def _texto_aproveitavel(texto: str) -> bool:
    texto = texto.strip()
    if len(texto) < 80:
        return False
    letras = sum(c.isalpha() for c in texto)
    return letras / len(texto) > 0.4


def _tem_imagem_grande(pagina) -> bool:
    """Página com texto mas também com figura grande (ex.: tabela salarial escaneada)."""
    try:
        for imagem in pagina.images:
            largura, altura = imagem.image.size
            if largura * altura > 250_000:
                return True
    except Exception:
        pass
    return False


def _renderizar_pagina_pdf(conteudo: bytes, indice: int) -> bytes:
    """Transforma uma página de PDF em imagem JPEG para o modelo de visão."""
    with _trava_pdfium:
        documento = pypdfium2.PdfDocument(conteudo)
        try:
            imagem = documento[indice].render(scale=DPI_PAGINAS_ESCANEADAS / 72).to_pil()
        finally:
            documento.close()
    return _jpeg(imagem)


# ---------------------------------------------------------------- imagens

def _ler_imagem(conteudo: bytes, sha: str, prefixo: str) -> list[Pagina]:
    with Image.open(io.BytesIO(conteudo)) as imagem:
        total = getattr(imagem, "n_frames", 1)  # TIFF/GIF podem ter várias páginas
    return [
        _pagina_para_ia(_rotulo(prefixo, i, total), f"{sha}-p{i}",
                        lambda i=i: _imagem_do_arquivo(conteudo, i - 1))
        for i in range(1, total + 1)
    ]


def _imagem_do_arquivo(conteudo: bytes, quadro: int) -> bytes:
    with Image.open(io.BytesIO(conteudo)) as imagem:
        imagem = ImageSequence.Iterator(imagem)[quadro]
        imagem = ImageOps.exif_transpose(imagem)
    return _jpeg(imagem)


def _jpeg(imagem: Image.Image) -> bytes:
    imagem = imagem.convert("RGB")
    imagem.thumbnail((LADO_MAXIMO_IMAGEM, LADO_MAXIMO_IMAGEM))
    for qualidade in (90, 80, 70, 60, 50):
        buffer = io.BytesIO()
        imagem.save(buffer, "JPEG", quality=qualidade)
        if buffer.tell() <= TAMANHO_MAXIMO_IMAGEM:
            break
    return buffer.getvalue()


# ---------------------------------------------------------------- documentos de texto

def _ler_docx(conteudo: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    documento = Document(io.BytesIO(conteudo))
    linhas = []
    for elemento in documento.element.body.iterchildren():
        tag = elemento.tag.rsplit("}", 1)[-1]
        if tag == "p":
            linhas.append(Paragraph(elemento, documento).text)
        elif tag == "tbl":
            for linha in Table(elemento, documento).rows:
                linhas.append(" | ".join(celula.text.strip() for celula in linha.cells))
            linhas.append("")
    return "\n".join(linhas)


def _ler_doc_antigo(caminho: Path) -> str | None:
    """Word 97-2003 (.doc): usa o antiword (instalado no servidor via packages.txt)."""
    import shutil
    import subprocess
    comandos = {"antiword": ["antiword", "-m", "UTF-8.txt", str(caminho)],
                "catdoc": ["catdoc", "-d", "utf-8", str(caminho)]}
    for programa, comando in comandos.items():
        if shutil.which(programa):
            resultado = subprocess.run(comando, capture_output=True, timeout=120)
            if resultado.returncode == 0 and resultado.stdout.strip():
                return _decodificar(resultado.stdout)
    return None


def _ler_odt(conteudo: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        xml = z.read("content.xml").decode("utf-8")
    xml = re.sub(r"</text:(p|h)>|<text:line-break/>|</table:table-row>", "\n", xml)
    xml = re.sub(r"</table:table-cell>", " | ", xml)
    return unescape(re.sub(r"<[^>]+>", "", xml))


def _html_para_texto(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|h\d|li)>", "\n", html)
    html = re.sub(r"(?i)</t[dh]>", " | ", html)
    return unescape(re.sub(r"<[^>]+>", "", html))


def _decodificar(conteudo: bytes, estrito: bool = False) -> str:
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            texto = conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
        if estrito and sum(not (c.isprintable() or c.isspace()) for c in texto) > len(texto) * 0.01:
            return ""
        return texto
    return "" if estrito else conteudo.decode("latin-1")


def _fatiar(texto: str, prefixo: str) -> list[Pagina]:
    """Divide documentos sem páginas (Word, TXT...) em trechos para citação."""
    trechos, atual = [], ""
    for paragrafo in re.split(r"\n\s*\n", texto):
        if atual and len(atual) + len(paragrafo) > CARACTERES_POR_TRECHO:
            trechos.append(atual)
            atual = ""
        atual += paragrafo.strip() + "\n\n"
    if atual.strip():
        trechos.append(atual)
    total = len(trechos)
    if total == 1:
        return [Pagina(prefixo or "documento", texto=trechos[0])]
    base = f"{prefixo}, " if prefixo else ""
    return [Pagina(f"{base}trecho {i}", texto=t) for i, t in enumerate(trechos, 1)]
