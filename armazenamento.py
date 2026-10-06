"""Guarda o acervo (CCTs, índice e transcrições) num repositório privado do GitHub.

Hospedagens gratuitas apagam os arquivos do servidor quando ele reinicia. Por isso, ao
iniciar, o programa baixa o acervo do GitHub e, depois de cada alteração (envio, exclusão
ou atualização do índice), envia de volta só o que mudou.

Configuração (variáveis de ambiente ou "Secrets" do Streamlit):
    GITHUB_TOKEN        token de acesso do GitHub com permissão de escrita no repositório
    GITHUB_REPOSITORIO  "usuario/nome-do-repositorio" (privado) onde o acervo fica guardado
Sem essas duas variáveis, o programa funciona só com a pasta local, como antes.
"""

import base64
import hashlib
import os
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import requests

from config import PASTA_BASE, PASTA_CCTS, PASTA_DADOS

API = "https://api.github.com"
PASTAS_SINCRONIZADAS = (PASTA_CCTS, PASTA_DADOS)
ENTRADAS_POR_ENVIO = 300
TAMANHO_MAXIMO_ARQUIVO = 95_000_000  # o GitHub recusa arquivos acima de 100 MB

_trava = threading.Lock()
_salvos: dict[str, str] = {}  # caminho relativo -> sha256 do conteúdo já guardado no GitHub
_ramo: str | None = None


class ErroArmazenamento(Exception):
    """Falha ao ler ou gravar o acervo no GitHub."""


def configurado() -> bool:
    return bool(os.getenv("GITHUB_TOKEN") and os.getenv("GITHUB_REPOSITORIO"))


def baixar() -> int:
    """Traz o acervo do GitHub para as pastas locais. Devolve quantos arquivos vieram."""
    with _trava:
        # "Já salvo no GitHub" = só o que veio do GitHub. Arquivos que já estavam na pasta local
        # e não vieram de lá precisam ser enviados no próximo sincronizar().
        _salvos.clear()
        if not _tem_commits():
            return 0
        recebidos = 0
        with tempfile.TemporaryFile() as temporario:
            resposta = _pedir("GET", f"/tarball/{_ramo_padrao()}", stream=True)
            for pedaco in resposta.iter_content(1 << 20):
                temporario.write(pedaco)
            temporario.seek(0)
            with tarfile.open(fileobj=temporario, mode="r:gz") as pacote:
                for membro in pacote.getmembers():
                    # Os caminhos vêm como "<usuario>-<repo>-<commit>/ccts/arquivo.pdf".
                    partes = membro.name.split("/", 1)
                    if not membro.isfile() or len(partes) < 2:
                        continue
                    relativo = partes[1]
                    destino = (PASTA_BASE / relativo).resolve()
                    if not _dentro_das_pastas(destino):
                        continue
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    with pacote.extractfile(membro) as origem:
                        dados = origem.read()
                    destino.write_bytes(dados)
                    _salvos[destino.relative_to(PASTA_BASE.resolve()).as_posix()] = \
                        hashlib.sha256(dados).hexdigest()
                    recebidos += 1
        return recebidos


def sincronizar(mensagem: str) -> tuple[int, list[str]]:
    """Envia ao GitHub o que mudou desde o último envio.
    Devolve (arquivos enviados ou removidos, avisos)."""
    with _trava:
        atuais = _arquivos_locais()
        alterados = sorted(r for r, sha in atuais.items() if _salvos.get(r) != sha)
        removidos = sorted(r for r in _salvos if r not in atuais)
        if not alterados and not removidos:
            return 0, []

        _garantir_primeiro_commit()
        avisos, entradas = [], []
        for relativo in alterados:
            dados = (PASTA_BASE / relativo).read_bytes()
            if len(dados) > TAMANHO_MAXIMO_ARQUIVO:
                avisos.append(f"{relativo}: arquivo maior que 95 MB não pode ser guardado no GitHub; "
                              "ele será perdido quando o servidor reiniciar.")
                atuais.pop(relativo)
                continue
            entradas.append({"path": relativo, "mode": "100644", "type": "blob",
                             "sha": _criar_blob(dados)})
        for relativo in removidos:
            entradas.append({"path": relativo, "mode": "100644", "type": "blob", "sha": None})

        for inicio in range(0, len(entradas), ENTRADAS_POR_ENVIO):
            _gravar_commit(entradas[inicio:inicio + ENTRADAS_POR_ENVIO], mensagem)
        _salvos.clear()
        _salvos.update(atuais)
        return len(entradas), avisos


# ---------------------------------------------------------------- GitHub

def _pedir(metodo: str, caminho: str, **kwargs) -> requests.Response:
    repositorio = os.environ["GITHUB_REPOSITORIO"].strip().strip("/")
    cabecalhos = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN'].strip()}",
                  "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}
    for tentativa in range(4):
        try:
            resposta = requests.request(metodo, f"{API}/repos/{repositorio}{caminho}",
                                        headers=cabecalhos, timeout=300, **kwargs)
        except requests.RequestException as e:
            if tentativa == 3:
                raise ErroArmazenamento(f"Sem conexão com o GitHub: {e}") from e
            time.sleep(3 * 2 ** tentativa)
            continue
        if resposta.status_code >= 500 and tentativa < 3:
            time.sleep(3 * 2 ** tentativa)
            continue
        if resposta.status_code == 401:
            raise ErroArmazenamento("Token do GitHub inválido ou expirado (GITHUB_TOKEN).")
        if resposta.status_code == 404 and caminho == "":
            raise ErroArmazenamento(f"Repositório '{repositorio}' não encontrado, ou o token "
                                    "não tem acesso a ele (GITHUB_REPOSITORIO / GITHUB_TOKEN).")
        return resposta
    return resposta


def _verificar(resposta: requests.Response, acao: str) -> dict:
    if resposta.status_code >= 400:
        raise ErroArmazenamento(f"GitHub recusou {acao} ({resposta.status_code}): {resposta.text[:300]}")
    return resposta.json() if resposta.content else {}


def _ramo_padrao() -> str:
    global _ramo
    if _ramo is None:
        _ramo = _verificar(_pedir("GET", ""), "a leitura do repositório")["default_branch"]
    return _ramo


def _tem_commits() -> bool:
    resposta = _pedir("GET", f"/git/ref/heads/{_ramo_padrao()}")
    if resposta.status_code in (404, 409):  # repositório recém-criado, ainda vazio
        return False
    _verificar(resposta, "a leitura do ramo")
    return True


def _garantir_primeiro_commit():
    """A API de commits não funciona em repositório vazio; cria um arquivo inicial."""
    if _tem_commits():
        return
    conteudo = base64.b64encode("Acervo do Assistente de CCTs. Não edite manualmente.\n"
                                .encode("utf-8")).decode()
    _verificar(_pedir("PUT", "/contents/LEIAME.md",
                      json={"message": "Inicia o acervo", "content": conteudo}),
               "a criação do arquivo inicial")


def _criar_blob(dados: bytes) -> str:
    resposta = _pedir("POST", "/git/blobs", json={"content": base64.b64encode(dados).decode(),
                                                  "encoding": "base64"})
    return _verificar(resposta, "o envio de um arquivo")["sha"]


def _gravar_commit(entradas: list[dict], mensagem: str):
    ramo = _ramo_padrao()
    for _ in range(4):  # outro usuário pode ter salvo ao mesmo tempo: refaz sobre a versão nova
        topo = _verificar(_pedir("GET", f"/git/ref/heads/{ramo}"), "a leitura do ramo")["object"]["sha"]
        arvore_base = _verificar(_pedir("GET", f"/git/commits/{topo}"), "a leitura do commit")["tree"]["sha"]
        arvore = _verificar(_pedir("POST", "/git/trees", json={"base_tree": arvore_base, "tree": entradas}),
                            "a montagem da árvore de arquivos")["sha"]
        commit = _verificar(_pedir("POST", "/git/commits", json={"message": mensagem, "tree": arvore,
                                                                 "parents": [topo]}),
                            "a criação do commit")["sha"]
        resposta = _pedir("PATCH", f"/git/refs/heads/{ramo}", json={"sha": commit})
        if resposta.status_code == 422:  # o ramo andou nesse meio-tempo
            continue
        _verificar(resposta, "a atualização do ramo")
        return
    raise ErroArmazenamento("Não foi possível salvar no GitHub: o acervo mudou várias vezes seguidas.")


# ---------------------------------------------------------------- arquivos locais

_cache_sha: dict[tuple, str] = {}


def _arquivos_locais() -> dict[str, str]:
    arquivos = {}
    for pasta in PASTAS_SINCRONIZADAS:
        if not pasta.exists():
            continue
        for arquivo in pasta.rglob("*"):
            if arquivo.is_file():
                relativo = arquivo.relative_to(PASTA_BASE).as_posix()
                info = arquivo.stat()
                chave = (str(arquivo), info.st_mtime_ns, info.st_size)
                if chave not in _cache_sha:  # só relê arquivos que mudaram
                    _cache_sha[chave] = hashlib.sha256(arquivo.read_bytes()).hexdigest()
                arquivos[relativo] = _cache_sha[chave]
    return arquivos


def _dentro_das_pastas(caminho: Path) -> bool:
    return any(caminho.is_relative_to(p.resolve()) for p in PASTAS_SINCRONIZADAS)
