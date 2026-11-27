"""Busca a versão atualizada de uma CCT vencida do acervo.

Quando o programa avisa que uma CCT importada está vencida, o usuário pode pedir que a IA
procure a versão nova na internet. O caminho é sempre o mesmo e cada passo é mostrado:
1. Buscas: a IA monta as buscas a partir da ficha da CCT vencida (sindicatos, categoria,
   cidades e anos). Nada vem de suposição: a categoria e o local são os da própria CCT.
2. Busca na internet em segundo plano (Tavily; Brave como reserva, se configurado).
3. Triagem: a IA escolhe os resultados com mais chance de serem o texto integral da
   versão nova (sites de sindicatos, federações e órgãos públicos).
4. Leitura: cada documento é baixado e lido como no acervo (inclusive PDF escaneado).
5. Conferência: a IA confere se o documento é a mesma convenção (mesmas partes, categoria e
   território); a vigência é conferida pelo próprio programa, contra a data de hoje e contra
   a vigência da CCT antiga. Só uma versão mais nova e vigente é aceita.
6. Acervo: a versão nova é guardada na mesma pasta da antiga, e a antiga passa para
   "Deixadas de lado". Se nada servir, o programa diz isso claramente e não inventa nada.

O Mediador do MTE bloqueia acesso automatizado (reCAPTCHA) e não é consultado diretamente;
o número de registro no MTE da CCT encontrada permite conferi-la lá.
"""

import hashlib
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

import ia
from config import (BUSCAS_POR_ATUALIZACAO, CHAMADAS_PARALELAS, DOCUMENTOS_ABERTOS_POR_BUSCA,
                    PAGINAS_MAXIMAS_POR_DOCUMENTO, PASTA_CACHE_PESQUISA, PASTA_CCTS, PASTA_DADOS,
                    PASTA_INDICE, TAMANHO_MAXIMO_DOWNLOAD, URL_BUSCA, URL_BUSCA_RESERVA, VERSAO_INDICE)
from extracao import _html_para_texto, ler_arquivo, transcrever_pendentes
from indice import (MARCADOR_PASTA, SEM_PASTA, _extrair_metadados, _hash, _id, _trava_indexacao,
                    carregar_indice, data_iso, impressoes_digitais, pasta_da_cct, titulo)

ARQUIVO_ATUALIZACOES = PASTA_DADOS / "atualizacoes.json"
VALIDADE_CACHE = timedelta(days=7)
CABECALHOS = {"User-Agent": "Mozilla/5.0 (compatible; CCTVision/1.1; leitura de convencoes coletivas)",
              "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
              "Accept-Language": "pt-BR,pt;q=0.9"}
# Sites que não entregam o documento a programas (bloqueio anti-robô) ou que só têm conversa.
DOMINIOS_EXCLUIDOS = ["mediador.trabalho.gov.br", "facebook.com", "instagram.com", "youtube.com",
                      "tiktok.com", "linkedin.com", "x.com", "twitter.com"]
EXT_DOCUMENTO = (".pdf", ".doc", ".docx", ".odt", ".rtf")

SISTEMA_BUSCAS = """Você ajuda um escritório de contabilidade a encontrar na internet a VERSÃO MAIS NOVA de
uma Convenção (ou Acordo) Coletiva de Trabalho que está vencida no acervo dele.

Você recebe a ficha da versão vencida (partes, categoria, abrangência e vigência). Escreva de 2 a 3
buscas curtas, em português, para achar o TEXTO INTEGRAL da versão seguinte desse MESMO
instrumento, de preferência o PDF publicado pelo sindicato ou pela federação:
- use o tipo do instrumento ("convenção coletiva" ou "acordo coletivo"), a categoria, a cidade ou
  região e a sigla ou o nome do sindicato;
- use os anos da versão nova (ANOS_NOVOS), não os da versão vencida;
- uma das buscas pode terminar com "pdf".
Responda SOMENTE com JSON: {"buscas": ["...", "..."]}"""

SISTEMA_TRIAGEM = """Você recebe resultados de uma busca na internet (código, título, endereço e trecho) e
escolhe quais abrir para encontrar o texto integral da VERSÃO MAIS NOVA da convenção coletiva
descrita na ficha (as mesmas partes, a mesma categoria e o mesmo território).
- Prefira documentos (PDF, Word) com o texto da convenção, publicados por sindicatos,
  federações ou órgãos públicos.
- Prefira os mais recentes (veja os anos no título, no endereço e no trecho) e descarte os da
  vigência antiga ou anterior.
- Páginas de sindicatos que listam as convenções também servem: delas o programa tira os links.
- Evite notícias, resumos, blogs, modelos, cursos e convenções de outra categoria ou de outro local.
- Ordene do mais promissor para o menos promissor, no máximo QUANTIDADE códigos.
- Lista vazia se nenhum resultado servir.
Responda SOMENTE com JSON: {"codigos": ["R3", "R1"]}"""

SISTEMA_CONFERENCIA = """Você confere se documentos encontrados na internet são uma nova versão de uma
convenção coletiva que está vencida no acervo do escritório.

Você recebe a ficha da versão vencida e, para cada documento (código D1, D2...), a ficha e o
início do texto dele. Com base SOMENTE nisso, para cada documento:
- tipo: "Convenção Coletiva", "Acordo Coletivo", "Termo Aditivo" ou "Outro" (notícia, resumo,
  circular, tabela avulsa, modelo, documento incompleto...);
- mesma_convencao: true só se for o mesmo instrumento coletivo em outra edição: as mesmas partes
  (o sindicato dos trabalhadores e o sindicato patronal ou a empresa, que podem aparecer com
  nome por extenso, abreviado ou sigla), a mesma categoria e o mesmo território. Outra cidade,
  outra categoria ou outro sindicato: false. Na dúvida, false;
- motivo: uma frase curta explicando a decisão.
A vigência é conferida depois pelo programa; não decida por ela.
Responda SOMENTE com JSON:
{"documentos": [{"codigo": "D1", "tipo": "", "mesma_convencao": true, "motivo": ""}]}"""


@dataclass
class Atualizacao:
    texto: str
    etapas: list[str] = field(default_factory=list)
    encontrada: dict | None = None   # ficha pública da versão nova (título, vigência, link...)
    nova_id: str | None = None        # id no acervo da versão nova


class _Falha(Exception):
    """Documento que não pôde ser usado (o motivo vai para o relatório)."""


def _tem_tavily() -> bool:
    return os.getenv("TAVILY_API_KEY", "").strip().startswith("tvly-")


def _tem_brave() -> bool:
    return len(os.getenv("BRAVE_API_KEY", "").strip()) > 10


def chave_configurada() -> bool:
    """Pelo menos um fornecedor de busca na internet configurado."""
    return _tem_tavily() or _tem_brave()


def buscar_versao_atual(cct: dict, progresso=None) -> Atualizacao:
    """Procura na internet a versão nova da CCT vencida `cct` e, se achar uma mais nova e
    vigente, guarda no acervo no lugar dela."""
    hoje = date.today()
    nome = titulo(cct)
    m = cct.get("metadados") or {}
    fim_antigo = data_iso(m.get("vigencia_fim"))
    etapas = [f"A CCT **{nome}** do seu acervo "
              + (f"venceu em {fim_antigo:%d/%m/%Y}." if fim_antigo else "está com a vigência vencida.")
              + " Procurei a versão nova a partir da ficha dela: mesmos sindicatos, mesma categoria "
              "e mesmo local."]
    if not chave_configurada():
        return Atualizacao("A busca na internet não está ativada neste computador: falta a chave da "
                           "API de busca (`TAVILY_API_KEY` no arquivo `.env` ou em *Settings → "
                           "Secrets*). Enquanto isso, peça a CCT nova ao sindicato e adicione o "
                           "arquivo em **Adicionar CCTs**.", etapas)

    ficha = _ficha_texto(cct)
    _avisar(progresso, "Montando as buscas a partir da ficha da CCT vencida...")
    anos = _anos_novos(fim_antigo or hoje, hoje)
    plano = ia.conversar_json(SISTEMA_BUSCAS.replace("ANOS_NOVOS", anos),
                              f"Data de hoje: {hoje:%Y-%m-%d}\n\n<ficha_da_versao_vencida>\n{ficha}\n"
                              f"</ficha_da_versao_vencida>")
    buscas = [str(b).strip() for b in plano.get("buscas") or [] if str(b).strip()][:BUSCAS_POR_ATUALIZACAO]
    if not buscas:
        categoria = "; ".join(m.get("categorias_abrangidas") or [])[:80]
        local = "; ".join(m.get("abrangencia_territorial") or [])[:60]
        buscas = [f"convenção coletiva {categoria} {local} {hoje.year}"]

    aprovadas, relatorio, erros, tentados = [], [], [], set()
    fornecedores = _fornecedores()
    for nome_busca, buscar in fornecedores:
        _avisar(progresso, f"Pesquisando a versão nova na internet ({nome_busca})...")
        try:
            resultados = [r for r in buscar(buscas) if _chave_url(r.get("url", "")) not in tentados]
        except ia.ErroIA as e:
            erros.append(f"{nome_busca}: {e}")
            etapas.append(f"Tentei pesquisar pelo {nome_busca}, mas ele não respondeu ({e}).")
            continue
        etapas.append(f"Pesquisei na internet pelo {nome_busca}: " + "; ".join(f"“{b}”" for b in buscas)
                      + f" ({len(resultados)} resultado(s)).")
        if not resultados:
            relatorio.append(f"{nome_busca}: a busca não trouxe resultados novos")
            continue
        novas, rel = _rodada(cct, ficha, resultados, tentados, progresso, etapas)
        aprovadas += novas
        relatorio += rel
        if any(_serve(d, cct, hoje) for d in aprovadas):
            break
        if nome_busca != fornecedores[-1][0]:
            etapas.append("Ainda sem uma versão nova e vigente: tentei o próximo fornecedor de busca.")
    if erros and len(erros) == len(fornecedores):
        raise ia.ErroIA("Nenhum fornecedor de busca respondeu agora. " + " | ".join(erros))

    servem = sorted((d for d in aprovadas if _serve(d, cct, hoje)),
                    key=lambda d: (_vigencia(d)[0] or date.min, sum(len(p["texto"]) for p in d["paginas"])),
                    reverse=True)
    if not servem:
        return Atualizacao(_nao_encontrada(cct, aprovadas, relatorio, hoje), etapas + [
            "Nenhum documento encontrado é uma versão mais nova e vigente desta CCT: não troquei nada "
            "no acervo, para não usar uma convenção errada."])

    escolhida = servem[0]
    publica = _ficha_publica(escolhida, hoje)
    etapas.append(f"Escolhi **{publica['titulo']}**, vigência {publica['vigencia']} (vigente), "
                  f"fonte: {publica['site']}"
                  + (f", registro no MTE {publica['registro_mte']}" if publica["registro_mte"] else "") + ".")
    _avisar(progresso, "Guardando a versão nova no acervo...")
    nova = _guardar_no_acervo(escolhida, cct)
    pasta = pasta_da_cct(nova)
    etapas.append(f"Guardei a versão nova na pasta “{pasta}” do acervo e deixei a versão vencida "
                  "de lado: as próximas respostas usam a versão nova.")
    texto = (f"Encontrei a versão atualizada: **{publica['titulo']}**.\n\n"
             f"- **Vigência:** {publica['vigencia']} (vigente)\n"
             + (f"- **Registro no MTE:** {publica['registro_mte']} (pode ser conferido no Mediador)\n"
                if publica["registro_mte"] else "")
             + f"- **Fonte:** [{publica['site']}]({publica['url']})\n"
             + (f"- **Conferência:** {publica['motivo']}\n" if publica["motivo"] else "")
             + f"\nEla já está no acervo, na pasta “{pasta}”, e a versão vencida (**{nome}**) foi "
             "deixada de lado.")
    return Atualizacao(texto, etapas, publica, nova["id"])


def _fornecedores() -> list[tuple[str, object]]:
    return [(n, f) for n, f, ok in (("Tavily", _buscar, _tem_tavily()),
                                     ("Brave", _buscar_brave, _tem_brave())) if ok]


def _anos_novos(fim_antigo: date, hoje: date) -> str:
    """Anos da versão seguinte, ex.: "2026/2027, 2026"."""
    anos = sorted({fim_antigo.year, hoje.year})
    return ", ".join([f"{a}/{a + 1}" for a in anos] + [str(hoje.year)])


def _ficha_texto(cct: dict) -> str:
    m = cct.get("metadados") or {}
    ficha = {"arquivo": cct["nome"]}
    for campo in ("titulo", "tipo_instrumento", "sindicatos_trabalhadores",
                  "sindicatos_empregadores_ou_empresas", "categorias_abrangidas",
                  "abrangencia_territorial", "vigencia_inicio", "vigencia_fim", "data_base",
                  "numero_registro_mte"):
        if m.get(campo):
            ficha[campo] = m[campo]
    return json.dumps(ficha, ensure_ascii=False)


def _rodada(antiga: dict, ficha: str, resultados: list[dict], tentados: set, progresso,
            etapas: list[str]) -> tuple[list[dict], list[str]]:
    """Triagem, leitura e conferência dos resultados de um fornecedor de busca."""
    _avisar(progresso, f"Escolhendo entre {len(resultados)} resultados...")
    escolhidos = _triagem(ficha, [(r["url"], r.get("title", ""), r.get("content", "")) for r in resultados])
    tentados.update(_chave_url(u) for u in escolhidos)
    if escolhidos:
        etapas.append("Abri os resultados mais promissores: " + ", ".join(_site(u) for u in escolhidos) + ".")
    else:
        etapas.append("Nenhum resultado parecia ser o texto de uma versão nova desta convenção.")
    documentos, relatorio, links = _abrir_varios(escolhidos, progresso)
    if len(documentos) < 2 and links:  # páginas de sindicato com a lista de convenções
        links = [l for l in links if _chave_url(l[0]) not in tentados]
        novos = _triagem(ficha, links)
        tentados.update(_chave_url(u) for u in novos)
        if novos:
            etapas.append("Numa página de sindicato com a lista de convenções, abri: "
                          + ", ".join(_nome_do_arquivo(u) or _site(u) for u in novos) + ".")
        mais, rel2, _ = _abrir_varios(novos, progresso)
        documentos += mais
        relatorio += rel2
    for d in documentos:
        etapas.append(f"Li o documento {d['nome']} ({_site(d['url'])}): {len(d['paginas'])} página(s)/trecho(s).")
    for r in relatorio:
        etapas.append(f"Não consegui usar: {r}.")
    if not documentos:
        return [], relatorio
    _avisar(progresso, "Conferindo se é a mesma convenção e se está vigente...")
    aprovadas, relatorio_conf = _conferir(documentos, ficha)
    hoje = date.today()
    for d in aprovadas:
        etapas.append(f"Conferi {titulo(d)}: {d['conferencia']['motivo']} "
                      f"Vigência {_texto_vigencia(d)}: {_situacao(d, hoje)}"
                      + ("" if _mais_nova(d, antiga) else ", não é mais nova que a do acervo") + ".")
    for r in relatorio_conf:
        etapas.append(f"Conferi e descartei {r}.")
    return aprovadas, relatorio + relatorio_conf


# ---------------------------------------------------------------- busca e triagem

def _buscar(consultas: list[str]) -> list[dict]:
    chave = os.getenv("TAVILY_API_KEY", "").strip()

    def uma(consulta: str) -> list[dict]:
        corpo = {"query": consulta, "max_results": 8, "search_depth": "basic", "topic": "general",
                 "country": "brazil", "exclude_domains": DOMINIOS_EXCLUIDOS}
        for tentativa in range(3):
            try:
                r = requests.post(URL_BUSCA, json=corpo, timeout=40,
                                  headers={"Authorization": f"Bearer {chave}"})
            except requests.RequestException as e:
                if tentativa == 2:
                    raise ia.ErroIA(f"a busca na internet não respondeu ({type(e).__name__})") from e
                time.sleep(2)
                continue
            if r.status_code == 401:
                raise ia.ErroIA("chave da API de busca (TAVILY_API_KEY) inválida")
            if r.status_code in (432, 433):
                raise ia.ErroIA("o limite mensal de buscas da Tavily acabou")
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (tentativa + 1))
                continue
            if r.status_code != 200:
                raise ia.ErroIA(f"a busca na internet recusou o pedido (código {r.status_code})")
            return r.json().get("results") or []
        raise ia.ErroIA("a busca na internet está sobrecarregada")

    return _juntar(uma, consultas)


def _buscar_brave(consultas: list[str]) -> list[dict]:
    """Fornecedor de reserva: Brave Search (índice próprio, diferente do da Tavily)."""
    chave = os.getenv("BRAVE_API_KEY", "").strip()

    def uma(consulta: str) -> list[dict]:
        for tentativa in range(3):
            try:
                r = requests.get(URL_BUSCA_RESERVA, timeout=40,
                                 params={"q": consulta, "country": "BR", "search_lang": "pt-br", "count": 15},
                                 headers={"X-Subscription-Token": chave, "Accept": "application/json"})
            except requests.RequestException as e:
                if tentativa == 2:
                    raise ia.ErroIA(f"a busca de reserva não respondeu ({type(e).__name__})") from e
                time.sleep(2)
                continue
            if r.status_code in (401, 403, 422):
                raise ia.ErroIA(f"chave da busca de reserva (BRAVE_API_KEY) recusada ({r.status_code})")
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (tentativa + 1))
                continue
            if r.status_code != 200:
                raise ia.ErroIA(f"a busca de reserva recusou o pedido (código {r.status_code})")
            itens = (r.json().get("web") or {}).get("results") or []
            return [{"url": i.get("url", ""), "title": i.get("title", ""),
                     "content": re.sub(r"<[^>]+>", "", i.get("description", ""))} for i in itens
                    if not any(d in i.get("url", "") for d in DOMINIOS_EXCLUIDOS)]
        raise ia.ErroIA("a busca de reserva está sobrecarregada")

    return _juntar(uma, consultas)


def _juntar(uma, consultas: list[str]) -> list[dict]:
    vistos, saida = set(), []
    with ThreadPoolExecutor(max(1, len(consultas))) as pool:
        for lista in pool.map(uma, consultas):
            for r in lista:
                chave_url = _chave_url(r.get("url", ""))
                if chave_url and chave_url not in vistos:
                    vistos.add(chave_url)
                    saida.append(r)
    return saida


def _triagem(ficha: str, itens: list[tuple[str, str, str]]) -> list[str]:
    """itens: (url, título, trecho). Devolve as URLs escolhidas, da mais promissora à menos."""
    if not itens:
        return []
    por_codigo = {f"R{i}": url for i, (url, _, _) in enumerate(itens, 1)}
    linhas = [f"R{i} | {t.strip()[:150]} | {u} | {' '.join(c.split())[:350]}"
              for i, (u, t, c) in enumerate(itens, 1)]
    mensagem = (f"Data de hoje: {date.today():%Y-%m-%d}\n\n<ficha_da_versao_vencida>\n{ficha}\n"
                f"</ficha_da_versao_vencida>\n\n<resultados>\n" + "\n".join(linhas) + "\n</resultados>")
    sistema = SISTEMA_TRIAGEM.replace("QUANTIDADE", str(DOCUMENTOS_ABERTOS_POR_BUSCA))
    codigos = ia.conversar_json(sistema, mensagem).get("codigos") or []
    urls = []
    for c in codigos:
        c = str(c).strip().upper()
        c = c if c.startswith("R") else f"R{c}"
        if c in por_codigo and por_codigo[c] not in urls:
            urls.append(por_codigo[c])
    return urls[:DOCUMENTOS_ABERTOS_POR_BUSCA]


# ---------------------------------------------------------------- abrir e ler documentos

def _abrir_varios(urls: list[str], progresso) -> tuple[list[dict], list[str], list[tuple]]:
    """Baixa e lê os documentos em paralelo. Devolve (documentos lidos, relatório dos
    descartados, links de documentos achados em páginas que listam convenções)."""
    documentos, relatorio, links = [], [], []
    if not urls:
        return documentos, relatorio, links
    _avisar(progresso, f"Lendo {len(urls)} documento(s) encontrado(s)...")
    with ThreadPoolExecutor(min(len(urls), CHAMADAS_PARALELAS)) as pool:
        for url, saida in zip(urls, pool.map(_abrir_seguro, urls)):
            if isinstance(saida, dict):
                documentos.append(saida)
            elif isinstance(saida, list):
                links += [l for l in saida if l[0] not in {x[0] for x in links}]
            else:
                relatorio.append(f"{_site(url)}: {saida}")
    return documentos, relatorio, links


def _abrir_seguro(url: str):
    try:
        return _abrir(url)
    except _Falha as e:
        return str(e)
    except ia.ErroIA as e:
        return f"a IA não conseguiu ler o documento ({e})"
    except Exception as e:  # um site com problema não pode derrubar a busca
        return f"erro ao abrir ({type(e).__name__})"


def _abrir(url: str):
    em_cache = _ler_cache(url)
    if em_cache:
        return em_cache
    conteudo, tipo = _baixar(url)
    nome = _nome_do_arquivo(url)
    if "html" in tipo or conteudo[:400].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        html = conteudo.decode("utf-8", errors="replace")
        texto = _html_para_texto(html)
        if not _parece_cct(texto):
            links = _links_de_documentos(html, url)
            if not links:
                raise _Falha("a página não tem o texto da convenção nem links para ela")
            return links
        conteudo, nome = texto.encode("utf-8"), (nome or "pagina") + ".txt"

    avisos: list[str] = []
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / (nome or "documento")
        if not caminho.suffix and "pdf" in tipo:
            caminho = caminho.with_suffix(".pdf")
        caminho.write_bytes(conteudo)
        paginas = ler_arquivo(caminho, "", avisos)
    if not paginas:
        raise _Falha(avisos[0] if avisos else "nenhum texto encontrado")
    if len(paginas) > PAGINAS_MAXIMAS_POR_DOCUMENTO:
        raise _Falha(f"documento com {len(paginas)} páginas, grande demais para ser uma CCT")
    transcrever_pendentes(paginas, avisos)  # páginas escaneadas: lidas pela IA de visão
    paginas = [{"rotulo": p.rotulo, "texto": p.texto.strip()} for p in paginas if p.texto.strip()]
    if sum(len(p["texto"]) for p in paginas) < 1500:
        raise _Falha("texto curto demais para ser uma convenção")
    documento = {"nome": nome or url, "url": url, "avisos": avisos, "paginas": paginas,
                 "metadados": _extrair_metadados(nome or url, paginas)}
    # O arquivo original fica guardado para entrar no acervo se for a versão escolhida.
    extensao = caminho.suffix.lower() or (".pdf" if conteudo[:4] == b"%PDF" else ".txt")
    try:
        PASTA_CACHE_PESQUISA.mkdir(parents=True, exist_ok=True)
        original = _arquivo_cache(url).with_suffix(extensao)
        original.write_bytes(conteudo)
        documento["original"] = original.name
    except OSError:
        pass
    _gravar_cache(url, documento)
    return documento


def _baixar(url: str) -> tuple[bytes, str]:
    try:
        with requests.get(url, headers=CABECALHOS, timeout=(10, 60), stream=True) as r:
            if r.status_code != 200:
                raise _Falha(f"o site não entregou o documento (código {r.status_code})")
            if int(r.headers.get("content-length") or 0) > TAMANHO_MAXIMO_DOWNLOAD:
                raise _Falha("arquivo grande demais")
            dados = bytearray()
            for pedaco in r.iter_content(65536):
                dados += pedaco
                if len(dados) > TAMANHO_MAXIMO_DOWNLOAD:
                    raise _Falha("arquivo grande demais")
            return bytes(dados), (r.headers.get("content-type") or "").lower()
    except requests.RequestException as e:
        raise _Falha(f"o site não respondeu ({type(e).__name__})") from e


def _parece_cct(texto: str) -> bool:
    return len(texto) > 5000 and len(re.findall(r"(?i)cl[áa]usula", texto)) >= 3


_LINK = re.compile(r"""<a\s[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.I | re.S)


def _links_de_documentos(html: str, base: str) -> list[tuple[str, str, str]]:
    """Links da página que levam a documentos de convenção: (url, texto do link, "")."""
    links, vistos = [], set()
    for href, rotulo in _LINK.findall(html):
        url = urljoin(base, unescape(href.strip()))
        if not url.startswith("http") or url in vistos or not urlparse(url).path.lower().endswith(EXT_DOCUMENTO):
            continue
        vistos.add(url)
        texto = " ".join(unescape(re.sub(r"<[^>]+>", " ", rotulo)).split())
        links.append((url, texto or _nome_do_arquivo(url), ""))
    fala_de_cct = re.compile(r"(?i)conven|cct|acordo|aditivo")
    links.sort(key=lambda l: not fala_de_cct.search(l[1] + " " + unquote(l[0])))
    return links[:40]


# ---------------------------------------------------------------- conferência

def _conferir(documentos: list[dict], ficha_antiga: str) -> tuple[list[dict], list[str]]:
    blocos = []
    for i, d in enumerate(documentos, 1):
        ficha = {k: v for k, v in (d.get("metadados") or {}).items()
                 if v and k not in ("principais_clausulas", "resumo")}
        inicio = "\n".join(p["texto"] for p in d["paginas"][:2])[:2500]
        blocos.append(f'<documento codigo="D{i}" site="{_site(d["url"])}">\n<ficha>'
                      f'{json.dumps(ficha, ensure_ascii=False)}</ficha>\n<inicio>\n{inicio}\n</inicio>\n</documento>')
    mensagem = (f"<ficha_da_versao_vencida>\n{ficha_antiga}\n</ficha_da_versao_vencida>\n\n"
                + "\n\n".join(blocos))
    respostas = {str(r.get("codigo", "")).strip().upper(): r
                 for r in ia.conversar_json(SISTEMA_CONFERENCIA, mensagem).get("documentos") or []
                 if isinstance(r, dict)}
    aprovadas, relatorio = [], []
    for i, d in enumerate(documentos, 1):
        r = respostas.get(f"D{i}") or {}
        tipo = str(r.get("tipo") or "")
        d["conferencia"] = {"tipo": tipo, "motivo": str(r.get("motivo") or "").strip()}
        if tipo and tipo != "Outro" and r.get("mesma_convencao") is True:
            aprovadas.append(d)
        else:
            relatorio.append(f"{titulo(d)} ({_site(d['url'])}): "
                             f"{d['conferencia']['motivo'] or 'não foi possível confirmar que é a mesma convenção'}")
    return aprovadas, relatorio


def _vigencia(cct: dict) -> tuple[date | None, date | None]:
    m = cct.get("metadados") or {}
    return data_iso(m.get("vigencia_inicio")), data_iso(m.get("vigencia_fim"))


def _texto_vigencia(cct: dict) -> str:
    inicio, fim = _vigencia(cct)
    if inicio and fim:
        return f"{inicio:%d/%m/%Y} a {fim:%d/%m/%Y}"
    m = cct.get("metadados") or {}
    return f"{m.get('vigencia_inicio') or '?'} a {m.get('vigencia_fim') or '?'}"


def _situacao(cct: dict, hoje: date) -> str:
    inicio, fim = _vigencia(cct)
    if not fim:
        return "vigência não informada"
    if fim < hoje:
        return "vencida"
    if inicio and inicio > hoje:
        return "ainda não começou"
    return "vigente"


def _mais_nova(nova: dict, antiga: dict) -> bool:
    """A vigência da encontrada começa ou termina depois da vigência da CCT do acervo."""
    inicio_n, fim_n = _vigencia(nova)
    inicio_a, fim_a = _vigencia(antiga)
    if inicio_n and inicio_a and inicio_n > inicio_a:
        return True
    return bool(fim_n and fim_a and fim_n > fim_a)


def _serve(doc: dict, antiga: dict, hoje: date) -> bool:
    """Só vale uma versão mais nova que a do acervo e vigente hoje (conferido pelo programa)."""
    return _situacao(doc, hoje) == "vigente" and _mais_nova(doc, antiga)


def _ficha_publica(cct: dict, hoje: date) -> dict:
    m = cct.get("metadados") or {}
    return {"titulo": titulo(cct), "vigencia": _texto_vigencia(cct), "situacao": _situacao(cct, hoje),
            "registro_mte": m.get("numero_registro_mte", ""), "url": cct["url"], "site": _site(cct["url"]),
            "motivo": (cct.get("conferencia") or {}).get("motivo", "")}


def _nao_encontrada(antiga: dict, aprovadas: list[dict], relatorio: list[str], hoje: date) -> str:
    texto = (f"Não encontrei na internet uma versão mais nova e vigente de **{titulo(antiga)}** "
             "que eu possa confirmar. A versão nova pode ainda não ter sido assinada ou publicada.")
    mesmas = [d for d in aprovadas if not _serve(d, antiga, hoje)]
    if mesmas:
        texto += "\n\nEncontrei esta convenção, mas não numa versão mais nova e vigente:\n" + "\n".join(
            f"- {titulo(d)}: vigência {_texto_vigencia(d)} ({_situacao(d, hoje)}), {_site(d['url'])}"
            for d in mesmas[:5])
    if relatorio:
        texto += "\n\nO que foi conferido e descartado:\n" + "\n".join(f"- {r}" for r in relatorio[:8])
    return texto + ("\n\nNada foi trocado no acervo. Vale confirmar com o sindicato; quando tiver o "
                    "arquivo da CCT nova, adicione em **Adicionar CCTs**.")


# ---------------------------------------------------------------- guardar no acervo

def _guardar_no_acervo(doc: dict, antiga: dict) -> dict:
    """Grava a versão nova na mesma pasta da antiga, já com a ficha pronta (a leitura feita na
    busca é aproveitada), e marca a antiga como substituída por ela. Devolve a ficha nova."""
    if not _trava_indexacao.acquire(timeout=120):
        raise ia.ErroIA("Há uma atualização do acervo em andamento. Tente de novo em instantes.")
    try:
        dados, extensao = _conteudo_original(doc)
        impressao = hashlib.sha256(dados).hexdigest()
        ja_existe = impressoes_digitais().get(impressao)
        if ja_existe:  # o mesmo arquivo já está no acervo: só liga a antiga a ele
            nova = next((c for c in carregar_indice() if ja_existe in c.get("arquivos", [])), None)
        else:
            nova = None
        if nova is None:
            pasta_org = pasta_da_cct(antiga)
            base = PASTA_CCTS if pasta_org == SEM_PASTA else PASTA_CCTS / pasta_org
            base.mkdir(parents=True, exist_ok=True)
            if base != PASTA_CCTS:
                (base / MARCADOR_PASTA).touch()
            nome_arquivo = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", titulo(doc)).strip(" .")[:120] or "CCT"
            destino, numero = base / f"{nome_arquivo}{extensao}", 2
            while destino.exists():
                destino, numero = base / f"{nome_arquivo} ({numero}){extensao}", numero + 1
            destino.write_bytes(dados)
            nome = destino.relative_to(PASTA_CCTS).as_posix()
            nova = {"id": _id(nome), "nome": nome, "arquivos": [nome], "hash": _hash([destino]),
                    "versao": VERSAO_INDICE, "indexado_em": datetime.now().isoformat(timespec="seconds"),
                    "avisos": [f"Versão atualizada encontrada na internet pela IA em "
                               f"{date.today():%d/%m/%Y}: {doc['url']}"],
                    "origem": doc["url"], "metadados": doc.get("metadados") or {},
                    "paginas": doc["paginas"], "substituida_por": None, "motivo_substituicao": ""}
            _gravar_ficha(nova)
        motivo = (f"versão anterior: a IA encontrou na internet a versão {_texto_vigencia(nova)} "
                  f"em {date.today():%d/%m/%Y}")
        registrar_atualizacao(antiga["id"], nova["id"], motivo)
        antiga = dict(antiga, substituida_por=nova["id"], motivo_substituicao=motivo)
        _gravar_ficha(antiga)
        return nova
    finally:
        _trava_indexacao.release()


def registrar_atualizacao(id_antiga: str, id_nova: str, motivo: str):
    """Guarda a ligação antiga -> nova, respeitada sempre que o acervo é reorganizado."""
    ligacoes = carregar_atualizacoes()
    ligacoes[id_antiga] = {"nova": id_nova, "motivo": motivo}
    ARQUIVO_ATUALIZACOES.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_ATUALIZACOES.write_text(json.dumps(ligacoes, ensure_ascii=False, indent=1), encoding="utf-8")


def carregar_atualizacoes() -> dict:
    try:
        return json.loads(ARQUIVO_ATUALIZACOES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar_ficha(ficha: dict):
    PASTA_INDICE.mkdir(parents=True, exist_ok=True)
    (PASTA_INDICE / f"{ficha['id']}.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=1),
                                                      encoding="utf-8")


def _conteudo_original(doc: dict) -> tuple[bytes, str]:
    """Bytes do arquivo como veio da internet (do cache, ou baixado de novo) e a extensão."""
    if doc.get("original"):
        arquivo = PASTA_CACHE_PESQUISA / doc["original"]
        if arquivo.exists():
            return arquivo.read_bytes(), arquivo.suffix
    conteudo, tipo = _baixar(doc["url"])
    if "html" in tipo or conteudo[:400].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return "\n\n".join(p["texto"] for p in doc["paginas"]).encode("utf-8"), ".txt"
    sufixo = Path(urlparse(doc["url"]).path).suffix.lower()
    return conteudo, sufixo if sufixo in EXT_DOCUMENTO else (".pdf" if conteudo[:4] == b"%PDF" else ".bin")


# ---------------------------------------------------------------- cache e utilidades

def _arquivo_cache(url: str) -> Path:
    return PASTA_CACHE_PESQUISA / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"


def _ler_cache(url: str) -> dict | None:
    try:
        dados = json.loads(_arquivo_cache(url).read_text(encoding="utf-8"))
        if datetime.now() - datetime.fromisoformat(dados["lido_em"]) < VALIDADE_CACHE:
            return dados["documento"]
    except (OSError, ValueError, KeyError):
        pass
    return None


def _gravar_cache(url: str, documento: dict):
    try:
        PASTA_CACHE_PESQUISA.mkdir(parents=True, exist_ok=True)
        _arquivo_cache(url).write_text(json.dumps(
            {"lido_em": datetime.now().isoformat(timespec="seconds"), "documento": documento},
            ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # sem cache a busca continua funcionando, só fica mais lenta


def _chave_url(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def _nome_do_arquivo(url: str) -> str:
    nome = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome)[:150]


def _site(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _avisar(progresso, mensagem: str):
    if progresso:
        progresso(mensagem)
