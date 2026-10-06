"""Responde perguntas sobre as CCTs indexadas.

1. Seleção: a IA lê o catálogo (fichas) e escolhe as CCTs que importam para a pergunta.
2. Análise: as CCTs escolhidas são enviadas na íntegra, com cada trecho marcado por um
   código (ex.: [D1-12]); a IA cita os códigos e o programa os converte em fontes
   verificáveis (CCT, página e texto exato). Códigos inexistentes são descartados.
3. Se o material não couber numa única chamada, é analisado em lotes e as respostas
   parciais são combinadas, sem limite de quantidade de CCTs.
"""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import ia
from config import CHAMADAS_PARALELAS, LIMITE_TOKENS_POR_CHAMADA
from indice import carregar_indice, titulo

Progresso = Callable[[str], None] | None
CARACTERES_POR_TOKEN = 3.0      # estimativa conservadora para português
CARACTERES_POR_BLOCO = 700      # granularidade das citações
CARACTERES_CATALOGO_POR_CHAMADA = 400_000
SEM_INFORMACAO = "SEM_INFORMACAO"

SISTEMA_SELECAO = """Você faz a triagem de um acervo de Convenções e Acordos Coletivos de Trabalho.
Dado o catálogo (uma ficha por instrumento, cada uma com um código como C3) e a pergunta do
usuário, indique todos os instrumentos cujo texto pode ser necessário para responder com precisão.
- Considere categoria profissional, sindicatos, empresas, cidades/estados e vigência.
- Na dúvida, inclua: deixar de fora a CCT certa é pior do que incluir uma a mais.
- Perguntas comparativas ou gerais ("qual CCT tem o maior piso", "quais preveem auxílio-creche")
  exigem todas as CCTs pertinentes.
- Se a pergunta não indicar categoria ou local e várias CCTs se aplicarem, inclua todas.
- Se a pergunta depender do histórico da conversa, use-o para entender o contexto.
- Lista vazia somente se nenhuma CCT tiver relação com a pergunta.

Responda SOMENTE com JSON: {"codigos": ["C1", "C4"]}"""

SISTEMA_RESPOSTA = """Você é um especialista em Convenções e Acordos Coletivos de Trabalho brasileiros
e responde perguntas usando os documentos fornecidos.

Os documentos estão divididos em trechos, cada um precedido por um código como [D1-12]
(documento 1, trecho 12) e pela página de origem.

Regras:
- Fundamente cada afirmação no texto dos documentos. Reproduza valores, percentuais, prazos e
  datas exatamente como estão escritos.
- Logo após cada frase ou item com informação tirada dos documentos, cite o(s) código(s) do(s)
  trecho(s) que a contêm, entre colchetes: [D1-12] ou [D1-12][D2-3]. Use somente códigos que
  existem nos documentos e que realmente contêm aquela informação.
- Deixe claro de qual CCT vem cada informação (categoria, sindicatos ou cidade, além da vigência).
  Quando mais de uma CCT se aplicar e elas divergirem, apresente cada uma separadamente;
  para comparações, use tabela.
- Se a pergunta for ambígua (por exemplo, sem categoria ou cidade), responda para as CCTs
  plausíveis e diga qual informação permitiria uma resposta única.
- Compare a vigência com a data de hoje e avise se a CCT estiver vencida.
- Se os documentos não tratarem do assunto, diga isso claramente. Se for útil mencionar a
  regra geral da CLT ou outra norma, deixe explícito que ela não vem da CCT.
- Responda em português do Brasil, de forma direta, em markdown."""

INSTRUCAO_LOTE = (f"Você está vendo apenas parte do acervo; outras partes estão sendo analisadas "
                  f"separadamente. Responda somente com o que ESTES documentos dizem sobre a pergunta. "
                  f"Se eles não tiverem nada relevante, responda apenas {SEM_INFORMACAO}.")

SISTEMA_SINTESE = """Você recebe análises parciais de diferentes Convenções Coletivas de Trabalho,
feitas para a mesma pergunta, e escreve a resposta final ao usuário.
- Use somente o que está nas análises; não acrescente fatos novos.
- Mantenha exatamente os marcadores de citação numéricos, como [3], junto das informações que
  eles sustentam. Não invente marcadores.
- Organize por CCT quando houver diferenças; use tabela para comparações.
- Responda em português do Brasil, de forma direta, em markdown."""

_CITACAO = re.compile(r"[\[(]((?:\s*D\d+\s*-\s*\d+\s*[,;]?)+)\s*[\])]")
_CODIGO = re.compile(r"D(\d+)\s*-\s*(\d+)")


@dataclass
class Resultado:
    texto: str
    fontes: list[dict] = field(default_factory=list)
    ccts_consultadas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


@dataclass
class _Parte:
    cct: dict
    blocos: list[tuple[str, str]]   # (texto do bloco, rótulo da página)
    sufixo: str = ""                # "parte 2/3" quando a CCT é dividida

    @property
    def tokens(self) -> int:
        caracteres = sum(len(t) + len(r) + 16 for t, r in self.blocos)
        return int(caracteres / CARACTERES_POR_TOKEN) + 300


class _Fontes:
    """Numera as citações de forma única entre todas as chamadas."""

    def __init__(self):
        self.itens: list[dict] = []
        self._indices: dict[tuple, int] = {}
        self._trava = threading.Lock()

    def numero(self, cct: dict, pagina: str, trecho: str) -> int:
        chave = (cct["id"], pagina, trecho.strip())
        with self._trava:
            if chave not in self._indices:
                self.itens.append({"cct": titulo(cct), "arquivo": cct["nome"],
                                   "pagina": pagina, "trecho": trecho.strip()})
                self._indices[chave] = len(self.itens)
            return self._indices[chave]


def responder(pergunta: str, historico: list[dict] | None = None,
              progresso: Progresso = None) -> Resultado:
    """`historico`: lista de {"pergunta", "resposta"} das últimas trocas, para perguntas de seguimento."""
    # CCTs substituídas por uma versão mais atual não entram nas respostas.
    ccts = [c for c in carregar_indice() if c.get("paginas") and not c.get("substituida_por")]
    if not ccts:
        return Resultado("Nenhuma CCT indexada ainda. Coloque os arquivos na pasta `ccts` "
                         "e clique em **Atualizar índice**.")
    contexto = _contexto_conversa(historico)

    _avisar(progresso, f"Identificando quais das {len(ccts)} CCTs tratam do assunto...")
    selecionadas = _selecionar(pergunta, contexto, ccts)
    if not selecionadas:
        return Resultado("Não encontrei nenhuma CCT no acervo relacionada a essa pergunta. "
                         "Tente indicar a categoria profissional, o sindicato ou a cidade.")
    nomes = [titulo(c) for c in selecionadas]

    lotes = _empacotar([p for c in selecionadas for p in _partes(c)])
    fontes = _Fontes()
    avisos = []
    if len(lotes) == 1:
        _avisar(progresso, f"Analisando {len(selecionadas)} CCT(s) na íntegra...")
        texto, cortada = _analisar(lotes[0], pergunta, contexto, fontes, parcial=False)
        if cortada:
            avisos.append("A resposta atingiu o tamanho máximo e pode estar incompleta.")
    else:
        texto = _analisar_em_lotes(lotes, pergunta, contexto, fontes, avisos, progresso)

    texto, usadas = _renumerar(texto, fontes.itens)
    return Resultado(texto, usadas, nomes, avisos)


# ---------------------------------------------------------------- seleção

def _selecionar(pergunta: str, contexto: str, ccts: list[dict]) -> list[dict]:
    por_codigo = {f"C{i}": c for i, c in enumerate(ccts, 1)}
    fichas = [json.dumps({"codigo": cod, **_ficha_resumida(c)}, ensure_ascii=False)
              for cod, c in por_codigo.items()]
    grupos, atual, tamanho = [], [], 0
    for ficha in fichas:
        if atual and tamanho + len(ficha) > CARACTERES_CATALOGO_POR_CHAMADA:
            grupos.append(atual)
            atual, tamanho = [], 0
        atual.append(ficha)
        tamanho += len(ficha)
    grupos.append(atual)

    def selecionar_grupo(grupo: list[str]) -> list[str]:
        catalogo = "\n".join(grupo)
        mensagem = (f"Data de hoje: {date.today():%Y-%m-%d}\n\n<catalogo>\n{catalogo}\n</catalogo>\n\n"
                    f"{contexto}<pergunta>\n{pergunta}\n</pergunta>")
        resultado = ia.conversar_json(SISTEMA_SELECAO, mensagem)
        codigos = resultado.get("codigos") or []
        return [str(c).strip().upper() for c in codigos if isinstance(c, (str, int))]

    escolhidos = set()
    with ThreadPoolExecutor(CHAMADAS_PARALELAS) as pool:
        for encontrados in pool.map(selecionar_grupo, grupos):
            escolhidos.update(c if c.startswith("C") else f"C{c}" for c in encontrados)
    return [c for cod, c in por_codigo.items() if cod in escolhidos]


def _ficha_resumida(cct: dict) -> dict:
    m = cct.get("metadados") or {}
    ficha = {"arquivo": cct["nome"]}
    ficha.update({k: v for k, v in m.items() if v})
    if not m:  # ficha não extraída: mostra o começo do texto
        ficha["inicio_do_texto"] = cct["paginas"][0]["texto"][:1500]
    return ficha


# ---------------------------------------------------------------- análise

def _partes(cct: dict) -> list[_Parte]:
    """Quebra a CCT em blocos citáveis e, se ela for grande demais, em partes."""
    blocos = [(b, pg["rotulo"]) for pg in cct["paginas"] for b in _blocos(pg["texto"])]
    limite = int(LIMITE_TOKENS_POR_CHAMADA * CARACTERES_POR_TOKEN * 0.9)
    pedacos, atual, tamanho = [], [], 0
    for bloco in blocos:
        if atual and tamanho + len(bloco[0]) > limite:
            pedacos.append(atual)
            atual, tamanho = [], 0
        atual.append(bloco)
        tamanho += len(bloco[0])
    pedacos.append(atual)
    if len(pedacos) == 1:
        return [_Parte(cct, pedacos[0])]
    return [_Parte(cct, p, f"parte {i}/{len(pedacos)}") for i, p in enumerate(pedacos, 1)]


def _blocos(texto: str) -> list[str]:
    """Divide a página em blocos de ~700 caracteres, cortando apenas em fim de linha."""
    blocos, atual = [], ""
    for linha in texto.splitlines(keepends=True):
        atual += linha
        if len(atual) >= CARACTERES_POR_BLOCO:
            blocos.append(atual)
            atual = ""
    if atual.strip():
        blocos.append(atual)
    return [b for b in blocos if b.strip()]


def _empacotar(partes: list[_Parte]) -> list[list[_Parte]]:
    lotes, atual, tokens = [], [], 0
    for parte in partes:
        if atual and tokens + parte.tokens > LIMITE_TOKENS_POR_CHAMADA:
            lotes.append(atual)
            atual, tokens = [], 0
        atual.append(parte)
        tokens += parte.tokens
    lotes.append(atual)
    return lotes


def _documentos(lote: list[_Parte]) -> str:
    saida = []
    for d, parte in enumerate(lote, 1):
        nome = titulo(parte.cct) + (f" ({parte.sufixo})" if parte.sufixo else "")
        saida.append(f'<documento codigo="D{d}">\n<titulo>{nome}</titulo>\n'
                     f"<informacoes>{_contexto_cct(parte.cct)}</informacoes>")
        for b, (texto, pagina) in enumerate(parte.blocos, 1):
            saida.append(f"[D{d}-{b}] ({pagina})\n{texto.strip()}")
        saida.append("</documento>")
    return "\n\n".join(saida)


def _analisar(lote: list[_Parte], pergunta: str, contexto: str, fontes: _Fontes,
              parcial: bool) -> tuple[str, bool]:
    pedido = (f"{_documentos(lote)}\n\nData de hoje: {date.today():%Y-%m-%d}\n\n"
              f"{contexto}Pergunta: {pergunta}")
    if parcial:
        pedido += f"\n\n{INSTRUCAO_LOTE}"
    texto, cortada = ia.conversar(SISTEMA_RESPOSTA, pedido, max_tokens=16000)
    return _converter_citacoes(texto, lote, fontes), cortada


def _analisar_em_lotes(lotes, pergunta, contexto, fontes, avisos, progresso) -> str:
    parciais = [None] * len(lotes)
    with ThreadPoolExecutor(CHAMADAS_PARALELAS) as pool:
        futuros = {pool.submit(_analisar, lote, pergunta, contexto, fontes, True): i
                   for i, lote in enumerate(lotes)}
        for feitos, futuro in enumerate(as_completed(futuros), 1):
            i = futuros[futuro]
            _avisar(progresso, f"Analisando o acervo em lotes ({feitos}/{len(lotes)})...")
            try:
                parciais[i] = futuro.result()[0]
            except ia.ErroIA as e:
                avisos.append(f"Um lote não pôde ser analisado: {e}")

    relevantes = []
    for lote, texto in zip(lotes, parciais):
        if texto and SEM_INFORMACAO not in texto[:40]:
            nomes = "; ".join(titulo(p.cct) for p in lote)
            relevantes.append(f"<analise ccts=\"{nomes}\">\n{texto}\n</analise>")
    if not relevantes:
        return "As CCTs do acervo não trazem informação sobre essa pergunta."

    _avisar(progresso, "Combinando as análises...")
    mensagem = f"{contexto}Pergunta: {pergunta}\n\n" + "\n\n".join(relevantes)
    texto, cortada = ia.conversar(SISTEMA_SINTESE, mensagem, max_tokens=16000)
    if cortada:
        avisos.append("A resposta atingiu o tamanho máximo e pode estar incompleta.")
    return texto


def _converter_citacoes(texto: str, lote: list[_Parte], fontes: _Fontes) -> str:
    """Troca [D1-12] pelo número da fonte. Códigos que não existem são descartados."""
    def trocar(m: re.Match) -> str:
        numeros = []
        for doc, bloco in _CODIGO.findall(m.group(1)):
            d, b = int(doc) - 1, int(bloco) - 1
            if 0 <= d < len(lote) and 0 <= b < len(lote[d].blocos):
                parte = lote[d]
                trecho, pagina = parte.blocos[b]
                numeros.append(fontes.numero(parte.cct, pagina, trecho))
        return "".join(f"[{n}]" for n in dict.fromkeys(numeros))

    return _CITACAO.sub(trocar, texto).strip()


def _renumerar(texto: str, itens: list[dict]) -> tuple[str, list[dict]]:
    """Mantém só as fontes citadas no texto final, numeradas na ordem em que aparecem."""
    novo: dict[int, int] = {}
    for m in re.finditer(r"\[(\d+)\]", texto):
        antigo = int(m.group(1))
        if 1 <= antigo <= len(itens) and antigo not in novo:
            novo[antigo] = len(novo) + 1
    texto = re.sub(r"\[(\d+)\]",
                   lambda m: f"[{novo[int(m.group(1))]}]" if int(m.group(1)) in novo else m.group(0),
                   texto)
    usadas = [{"n": n, **itens[antigo - 1]} for antigo, n in novo.items()]
    return texto, usadas


# ---------------------------------------------------------------- utilidades

def _contexto_cct(cct: dict) -> str:
    m = cct.get("metadados") or {}
    partes = [f"Arquivo: {cct['nome']}"]
    if m.get("vigencia_inicio") or m.get("vigencia_fim"):
        partes.append(f"Vigência: {m.get('vigencia_inicio') or '?'} a {m.get('vigencia_fim') or '?'}")
    for rotulo, chave in (("Data-base", "data_base"), ("Registro MTE", "numero_registro_mte")):
        if m.get(chave):
            partes.append(f"{rotulo}: {m[chave]}")
    for rotulo, chave in (("Categorias", "categorias_abrangidas"),
                          ("Abrangência", "abrangencia_territorial"),
                          ("Sindicatos de trabalhadores", "sindicatos_trabalhadores"),
                          ("Empregadores", "sindicatos_empregadores_ou_empresas")):
        if m.get(chave):
            partes.append(f"{rotulo}: {', '.join(m[chave])}")
    return ". ".join(partes)


def _contexto_conversa(historico: list[dict] | None) -> str:
    if not historico:
        return ""
    trocas = "\n\n".join(f"Usuário: {h['pergunta']}\nAssistente: {h['resposta'][:2000]}"
                         for h in historico[-3:])
    return f"<historico_da_conversa>\n{trocas}\n</historico_da_conversa>\n\n"


def _avisar(progresso: Progresso, mensagem: str):
    if progresso:
        progresso(mensagem)
