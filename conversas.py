"""Histórico de conversas de cada usuário, como no ChatGPT.

Cada conversa é um arquivo `dados/conversas/<usuario>/<id>.json`. Por estar dentro de
`dados/`, na versão online ela é guardada no GitHub junto com o acervo.
"""

import json
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import PASTA_DADOS

PASTA_CONVERSAS = PASTA_DADOS / "conversas"
TAMANHO_TITULO = 60
FUSO = ZoneInfo("America/Sao_Paulo")  # o servidor online roda em UTC


def identificador_usuario(nome: str) -> str:
    """'João Silva' e 'joao silva' viram o mesmo usuário: 'joao-silva'."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-") or "usuario"


def nova() -> dict:
    agora = _agora()
    return {"id": uuid.uuid4().hex[:12], "titulo": "", "criada_em": agora,
            "atualizada_em": agora, "mensagens": []}


def listar(usuario: str) -> list[dict]:
    """Conversas do usuário, da mais recente para a mais antiga (sem as mensagens)."""
    pasta = PASTA_CONVERSAS / identificador_usuario(usuario)
    if not pasta.exists():
        return []
    resumo = []
    for arquivo in pasta.glob("*.json"):
        try:
            conversa = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        resumo.append({k: conversa.get(k) for k in ("id", "titulo", "atualizada_em")})
    return sorted(resumo, key=lambda c: c["atualizada_em"] or "", reverse=True)


def carregar(usuario: str, id_conversa: str) -> dict | None:
    arquivo = _arquivo(usuario, id_conversa)
    if not arquivo.exists():
        return None
    return json.loads(arquivo.read_text(encoding="utf-8"))


def adicionar_mensagem(usuario: str, conversa: dict, mensagem: dict):
    """Acrescenta uma troca (pergunta + resposta) e grava a conversa."""
    conversa["mensagens"].append(mensagem)
    if not conversa["titulo"]:
        pergunta = " ".join(mensagem["pergunta"].split())
        conversa["titulo"] = (pergunta[:TAMANHO_TITULO].rstrip() + "…"
                              if len(pergunta) > TAMANHO_TITULO else pergunta)
    conversa["atualizada_em"] = _agora()
    arquivo = _arquivo(usuario, conversa["id"])
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(conversa, ensure_ascii=False, indent=1), encoding="utf-8")


def excluir(usuario: str, id_conversa: str):
    _arquivo(usuario, id_conversa).unlink(missing_ok=True)


def agrupar_por_data(lista: list[dict]) -> list[tuple[str, list[dict]]]:
    """Separa em Hoje / Ontem / Últimos 7 dias / Últimos 30 dias / Mais antigas."""
    hoje = datetime.now(FUSO).date()
    faixas = [("Hoje", hoje), ("Ontem", hoje - timedelta(days=1)),
              ("Últimos 7 dias", hoje - timedelta(days=7)),
              ("Últimos 30 dias", hoje - timedelta(days=30)), ("Mais antigas", date.min)]
    grupos: dict[str, list[dict]] = {nome: [] for nome, _ in faixas}
    for conversa in lista:
        try:
            dia = datetime.fromisoformat(conversa["atualizada_em"]).date()
        except (TypeError, ValueError):
            dia = date.min
        for nome, inicio in faixas:
            if dia >= inicio:
                grupos[nome].append(conversa)
                break
    return [(nome, itens) for nome, itens in grupos.items() if itens]


def _arquivo(usuario: str, id_conversa: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", id_conversa or ""):
        raise ValueError("identificador de conversa inválido")
    return PASTA_CONVERSAS / identificador_usuario(usuario) / f"{id_conversa}.json"


def _agora() -> str:
    return datetime.now(FUSO).replace(tzinfo=None).isoformat(timespec="seconds")
