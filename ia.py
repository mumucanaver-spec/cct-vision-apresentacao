"""Acesso aos modelos de IA (API gratuita da NVIDIA, formato compatível com OpenAI)."""

import base64
import json
import os
import re
import threading
import time

import openai
from openai import OpenAI

from config import MODELOS_TEXTO, MODELOS_VISAO, PASTA_BASE, URL_API

TENTATIVAS_POR_MODELO = 4
ESPERA_INICIAL = 5           # segundos entre rodadas; dobra a cada rodada
TEMPO_LIMITE_IMAGEM = 120    # segundos por página antes de tentar outro modelo
TEMPO_LIMITE_TEXTO = 600     # respostas sobre CCTs inteiras podem demorar


class ErroIA(Exception):
    """Falha que deve ser mostrada ao usuário."""


def _carregar_env():
    """Lê variáveis do arquivo .env da pasta do projeto (sem sobrescrever as do sistema)."""
    arquivo = PASTA_BASE / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env()
_cliente = None
_trava = threading.Lock()


def chave_configurada() -> bool:
    chave = os.getenv("NVIDIA_API_KEY", "")
    return chave.startswith("nvapi-")


def cliente() -> OpenAI:
    global _cliente
    with _trava:
        if _cliente is None:
            _cliente = OpenAI(base_url=URL_API, api_key=os.getenv("NVIDIA_API_KEY"),
                              max_retries=0, timeout=900)
        return _cliente


def conversar(system: str, mensagem: str, max_tokens: int = 16000) -> tuple[str, bool]:
    """Envia uma pergunta ao melhor modelo de texto disponível.
    Devolve (resposta, foi_cortada_por_tamanho)."""
    mensagens = [{"role": "system", "content": system}, {"role": "user", "content": mensagem}]
    return _com_reserva(MODELOS_TEXTO, mensagens, max_tokens)


def conversar_json(system: str, mensagem: str, max_tokens: int = 16000) -> dict:
    """Como `conversar`, mas exige e devolve um objeto JSON."""
    ultimo_erro = None
    for _ in range(2):
        texto, _cortada = conversar(system, mensagem, max_tokens)
        try:
            return _extrair_json(texto)
        except ValueError as e:
            ultimo_erro = e
    raise ErroIA(f"O modelo não devolveu um JSON válido ({ultimo_erro}).")


def transcrever_imagem(jpeg: bytes, instrucoes: str) -> tuple[str, bool]:
    """Lê uma imagem (página escaneada, foto) com o melhor modelo de visão disponível."""
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    mensagens = [{"role": "user", "content": [
        {"type": "text", "text": instrucoes},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    # Transcrever é copiar, não raciocinar: desligar o "pensamento" deixa 10x mais rápido.
    # Uma página leva ~10 s; se ficar presa na fila do serviço gratuito, tenta outro modelo.
    return _com_reserva(MODELOS_VISAO, mensagens, 16000, sem_raciocinio=True,
                        tempo_limite=TEMPO_LIMITE_IMAGEM, validar=_transcricao_valida)


# Às vezes o modelo devolve o próprio rascunho ("We need to transcribe...") em vez da página.
_RASCUNHO = re.compile(r"(?i)\b(we need to|let'?s (read|transcribe|start)|the user (wants|asks)|"
                       r"i need to|i will|the (page|image) (appears|contains|shows|is))\b")


def _transcricao_valida(texto: str) -> bool:
    return not _RASCUNHO.search(texto[:1500])


def _com_reserva(modelos: list[str], mensagens: list[dict], max_tokens: int,
                 sem_raciocinio: bool = False, tempo_limite: float = TEMPO_LIMITE_TEXTO,
                 validar=None) -> tuple[str, bool]:
    """Tenta os modelos em rodízio. Se um estiver sobrecarregado, passa na hora para o
    próximo; só espera (cada vez mais) depois que todos recusaram na mesma rodada."""
    ativos = list(modelos)
    erros = []
    espera = ESPERA_INICIAL
    for rodada in range(TENTATIVAS_POR_MODELO):
        for modelo in list(ativos):
            extras = None
            if sem_raciocinio and "nemotron" in modelo:
                extras = {"chat_template_kwargs": {"enable_thinking": False}}
            try:
                resposta = cliente().with_options(timeout=tempo_limite).chat.completions.create(
                    model=modelo, messages=mensagens, max_tokens=max_tokens, temperature=0.2,
                    extra_body=extras)
                escolha = resposta.choices[0]
                texto = _limpar(escolha.message.content or "")
                if not texto:
                    raise _Temporario("resposta vazia")
                if validar and not validar(texto):
                    raise _Temporario("resposta fora do formato pedido")
                return texto, escolha.finish_reason == "length"
            except (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError,
                    openai.APITimeoutError, _Temporario) as e:
                erros.append(f"{modelo}: {_resumo(e)}")
            except openai.AuthenticationError as e:
                raise ErroIA("Chave da NVIDIA inválida ou expirada. Confira o arquivo .env.") from e
            except openai.PermissionDeniedError as e:
                raise ErroIA(f"A chave da NVIDIA não tem acesso a {modelo}: {_resumo(e)}") from e
            except (openai.NotFoundError, openai.BadRequestError) as e:
                # Modelo retirado do catálogo ou texto grande demais para ele: sai do rodízio.
                erros.append(f"{modelo}: {_resumo(e)}")
                ativos.remove(modelo)
        if not ativos:
            break
        if rodada < TENTATIVAS_POR_MODELO - 1:
            time.sleep(espera)
            espera *= 2
    raise ErroIA("Os modelos da NVIDIA estão indisponíveis no momento (serviço gratuito "
                 "sobrecarregado). Tente de novo em alguns minutos.\n\nDetalhes: " + erros[-1])


class _Temporario(Exception):
    pass


def _limpar(texto: str) -> str:
    """Remove o raciocínio interno que alguns modelos incluem na resposta."""
    texto = re.sub(r"(?s)<think>.*?</think>", "", texto)
    if "</think>" in texto:
        texto = texto.split("</think>", 1)[1]
    return texto.strip()


def _extrair_json(texto: str) -> dict:
    texto = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.M).strip()
    inicio = texto.find("{")
    if inicio < 0:
        raise ValueError("nenhum objeto JSON na resposta")
    objeto, _ = json.JSONDecoder().raw_decode(texto[inicio:])
    if not isinstance(objeto, dict):
        raise ValueError("a resposta não é um objeto JSON")
    return objeto


def _resumo(erro: Exception) -> str:
    return str(erro)[:300]
