"""Mantém o índice das CCTs: texto de cada página + ficha com metadados.

Cada arquivo solto em `ccts/` é uma CCT. Uma subpasta inteira também é uma CCT
(útil quando a convenção está em várias fotos/arquivos: as páginas seguem a
ordem dos nomes). Só o que é novo ou mudou é reprocessado.
"""

import hashlib
import json
import re
import shutil
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import ia
from config import CHAMADAS_PARALELAS, PASTA_CCTS, PASTA_INDICE, VERSAO_INDICE
from extracao import Pagina, leitor_doc_disponivel, ler_arquivo, transcrever_pendentes

Progresso = Callable[[str, float], None] | None
LIMITE_CARACTERES_METADADOS = 700_000

CAMPOS_TEXTO = ["titulo", "tipo_instrumento", "vigencia_inicio", "vigencia_fim", "data_base",
                "numero_registro_mte", "resumo"]
CAMPOS_LISTA = ["sindicatos_trabalhadores", "sindicatos_empregadores_ou_empresas",
                "categorias_abrangidas", "abrangencia_territorial", "principais_clausulas"]

SISTEMA_METADADOS = """Você cataloga Convenções e Acordos Coletivos de Trabalho brasileiros.
Leia o documento e preencha a ficha. Ela será usada para decidir qual instrumento
coletivo se aplica a cada pergunta, então seja preciso sobre quem é abrangido e onde.

Responda SOMENTE com um objeto JSON neste formato:
{
  "titulo": "nome curto e descritivo, ex.: CCT Vigilantes de Campinas 2026/2027",
  "tipo_instrumento": "Convenção Coletiva, Acordo Coletivo, Termo Aditivo...",
  "sindicatos_trabalhadores": ["..."],
  "sindicatos_empregadores_ou_empresas": ["..."],
  "categorias_abrangidas": ["..."],
  "abrangencia_territorial": ["municípios ou estados"],
  "vigencia_inicio": "AAAA-MM-DD",
  "vigencia_fim": "AAAA-MM-DD",
  "data_base": "...",
  "numero_registro_mte": "...",
  "resumo": "2 a 3 frases",
  "principais_clausulas": ["Piso salarial vigilante: R$ ...", "Horas extras: ...%", "..."]
}
- Use "" ou [] quando o documento não informar.
- principais_clausulas: os direitos e valores mais consultados (pisos por função, reajuste,
  horas extras, adicional noturno, vale-alimentação, plano de saúde, jornada, estabilidades,
  multas...), cada item curto e com os valores exatos do documento."""


@dataclass
class _Pendente:
    id: str
    nome: str
    arquivos: list[Path]
    hash: str
    paginas: list[Pagina] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def carregar_indice() -> list[dict]:
    if not PASTA_INDICE.exists():
        return []
    ccts = [json.loads(a.read_text(encoding="utf-8")) for a in PASTA_INDICE.glob("*.json")]
    return sorted(ccts, key=lambda c: titulo(c).lower())


def titulo(cct: dict) -> str:
    return (cct.get("metadados") or {}).get("titulo") or cct["nome"]


MARCADOR_PASTA = ".pasta_do_acervo"  # arquivo vazio que identifica uma pasta de organização
SEM_PASTA = "Arquivos avulsos"


def descobrir_unidades() -> list[tuple[str, list[Path]]]:
    """Lista as CCTs da pasta: (nome, arquivos que a compõem).

    Dentro de `ccts/`:
      - arquivo solto ............................. uma CCT ("Arquivos avulsos")
      - subpasta comum ............................ uma CCT em vários arquivos (ex.: fotos)
      - pasta de organização (tem MARCADOR_PASTA) . agrupa CCTs: cada arquivo dentro dela é
        uma CCT e cada subpasta dentro dela é uma CCT em vários arquivos
    """
    PASTA_CCTS.mkdir(parents=True, exist_ok=True)
    unidades = []
    for item in _itens(PASTA_CCTS):
        if item.is_file():
            unidades.append((item.name, [item]))
        elif eh_pasta_de_organizacao(item):
            for sub in _itens(item):
                nome = f"{item.name}/{sub.name}"
                if sub.is_file():
                    unidades.append((nome, [sub]))
                elif arquivos := _arquivos_de(sub):
                    unidades.append((nome, arquivos))
        elif arquivos := _arquivos_de(item):
            unidades.append((item.name, arquivos))
    return unidades


def eh_pasta_de_organizacao(caminho: Path) -> bool:
    return caminho.is_dir() and (caminho / MARCADOR_PASTA).exists()


def pastas_de_organizacao() -> list[str]:
    PASTA_CCTS.mkdir(parents=True, exist_ok=True)
    return [p.name for p in _itens(PASTA_CCTS) if eh_pasta_de_organizacao(p)]


def pasta_da_cct(cct: dict) -> str:
    """Nome da pasta de organização de onde a CCT veio."""
    return cct["nome"].split("/", 1)[0] if "/" in cct["nome"] else SEM_PASTA


def _itens(pasta: Path) -> list[Path]:
    return [p for p in sorted(pasta.iterdir(), key=lambda p: _ordem_natural(p.name)) if not _ignorar(p)]


def _arquivos_de(pasta: Path) -> list[Path]:
    arquivos = [a for a in pasta.rglob("*") if a.is_file() and not _ignorar(a)]
    return sorted(arquivos, key=lambda a: _ordem_natural(a.relative_to(pasta).as_posix()))


def excluir(cct: dict):
    """Apaga da pasta os arquivos de uma CCT (o índice é limpo na próxima atualização)."""
    alvo = (PASTA_CCTS / cct["nome"]).resolve()
    if not alvo.is_relative_to(PASTA_CCTS.resolve()) or alvo == PASTA_CCTS.resolve():
        raise ValueError("caminho inválido")
    if alvo.is_dir():
        shutil.rmtree(alvo)
    elif alvo.exists():
        alvo.unlink()
    # Pasta de organização que ficou vazia (só com o marcador) também sai.
    pai = alvo.parent
    if pai != PASTA_CCTS.resolve() and eh_pasta_de_organizacao(pai) and not _itens(pai):
        shutil.rmtree(pai)


def excluir_todas() -> int:
    """Apaga todas as CCTs do acervo (arquivos e fichas). As conversas são mantidas, e as
    transcrições ficam guardadas: se as mesmas CCTs voltarem, não precisam ser relidas.
    Devolve quantas CCTs foram removidas."""
    if not _trava_indexacao.acquire(blocking=False):
        raise RuntimeError("Há uma atualização do acervo em andamento. Aguarde terminar.")
    try:
        quantidade = len(descobrir_unidades())
        for item in PASTA_CCTS.iterdir():
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        if PASTA_INDICE.exists():
            for ficha in PASTA_INDICE.glob("*.json"):
                ficha.unlink()
        return quantidade
    finally:
        _trava_indexacao.release()


def situacao() -> dict:
    """Quantas CCTs estão na pasta e quantas precisam ser (re)processadas."""
    existentes = {c["id"]: c for c in carregar_indice()}
    unidades = descobrir_unidades()
    pendentes = sum(1 for nome, arqs in unidades
                    if not _atualizada(existentes.get(_id(nome)), _hash(arqs)))
    ids = {_id(nome) for nome, _ in unidades}
    removidas = sum(1 for i in existentes if i not in ids)
    return {"na_pasta": len(unidades), "pendentes": pendentes, "removidas": removidas}


_trava_indexacao = threading.Lock()


def atualizar_indice(progresso: Progresso = None) -> dict:
    """Processa CCTs novas/alteradas e remove do índice as que saíram da pasta.
    Só uma atualização roda por vez no servidor (várias pessoas podem clicar ao mesmo tempo)."""
    if not _trava_indexacao.acquire(blocking=False):
        return {"processadas": 0, "removidas": 0, "total": len(descobrir_unidades()),
                "substituidas": 0,
                "avisos": ["Já existe uma atualização do acervo em andamento (talvez iniciada por "
                           "outra pessoa). Aguarde alguns minutos e confira de novo."]}
    try:
        return _atualizar_indice(progresso)
    finally:
        _trava_indexacao.release()


def _atualizar_indice(progresso: Progresso) -> dict:
    PASTA_INDICE.mkdir(parents=True, exist_ok=True)
    existentes = {c["id"]: c for c in carregar_indice()}
    unidades = descobrir_unidades()

    ids_na_pasta = set()
    pendentes: list[_Pendente] = []
    for nome, arquivos in unidades:
        id_, hash_ = _id(nome), _hash(arquivos)
        ids_na_pasta.add(id_)
        if not _atualizada(existentes.get(id_), hash_):
            pendentes.append(_Pendente(id_, nome, arquivos, hash_))

    removidas = [i for i in existentes if i not in ids_na_pasta]
    for id_ in removidas:
        (PASTA_INDICE / f"{id_}.json").unlink(missing_ok=True)

    # 1) Leitura local dos arquivos
    for i, p in enumerate(pendentes, 1):
        _avisar(progresso, f"Lendo arquivos ({i}/{len(pendentes)}): {p.nome}", i / len(pendentes))
        pasta = PASTA_CCTS / p.nome
        for arquivo in p.arquivos:
            prefixo = arquivo.relative_to(pasta).as_posix() if len(p.arquivos) > 1 else ""
            p.paginas += ler_arquivo(arquivo, prefixo, p.avisos)

    # 2) Transcrição pela IA das páginas escaneadas e imagens (todas as CCTs juntas)
    avisos_gerais: list[str] = []
    todas = [(p, pg) for p in pendentes for pg in p.paginas if pg.gerar_conteudo]
    transcrever_pendentes([pg for _, pg in todas], avisos_gerais, progresso)
    falhas_por_cct = {p.id: 0 for p in pendentes}
    for p in pendentes:
        nao_lidas = sum(1 for pg in p.paginas if pg.gerar_conteudo)  # continuou pendente = falhou
        provisorias = sum(1 for pg in p.paginas if pg.provisoria)
        falhas_por_cct[p.id] = nao_lidas + provisorias
        if nao_lidas:
            p.avisos.append(f"{nao_lidas} página(s) não puderam ser lidas; "
                            "elas serão tentadas de novo na próxima atualização.")
        if provisorias:
            p.avisos.append(f"{provisorias} página(s) foram lidas pelo OCR do Windows porque a IA "
                            "estava indisponível (pode haver erros em valores). Clique em "
                            "Atualizar índice mais tarde para a IA relê-las.")

    # 3) Ficha de metadados de cada CCT e gravação
    concluidas = 0
    erros = []
    with ThreadPoolExecutor(CHAMADAS_PARALELAS) as pool:
        futuros = {pool.submit(_finalizar, p, falhas_por_cct[p.id] == 0): p for p in pendentes}
        for futuro in as_completed(futuros):
            concluidas += 1
            p = futuros[futuro]
            try:
                futuro.result()
            except Exception as e:
                erros.append(f"{p.nome}: {e}")
            _avisar(progresso, f"Catalogando CCTs ({concluidas}/{len(pendentes)})",
                    concluidas / len(pendentes))

    # 4) CCTs repetidas: sempre que o acervo muda (ou se nunca foi verificado)
    substituidas = 0
    if pendentes or removidas or any("substituida_por" not in c for c in carregar_indice()):
        _avisar(progresso, "Verificando CCTs repetidas...", 1.0)
        try:
            substituidas = analisar_repetidas(progresso)
        except Exception as e:  # a verificação é um extra: não pode impedir o uso do acervo
            erros.append(f"Não foi possível verificar CCTs repetidas agora ({e}).")

    return {
        "processadas": len(pendentes),
        "removidas": len(removidas),
        "total": len(ids_na_pasta),
        "substituidas": substituidas,
        "avisos": [a if a.startswith(p.nome) else f"{p.nome}: {a}"
                   for p in pendentes for a in p.avisos]
                  + avisos_gerais + erros,
    }


# ---------------------------------------------------------------- CCTs repetidas

LIMIAR_SEMELHANCA = 0.18        # semelhança de sindicatos/categorias/cidades/título
LIMIAR_TITULO = 0.5             # semelhança só do título (pega resumos com ficha pobre)
MAXIMO_PARES_POR_CCT = 8

RELACOES_MESMA_CCT = {"mesma_cct_outra_vigencia", "copia_do_mesmo_documento",
                      "resumo_e_texto_completo"}

SISTEMA_PAR = """Você compara dois documentos de um acervo de Convenções e Acordos Coletivos de
Trabalho e diz qual é a relação entre eles. Considere as partes (sindicatos e empresas, que podem
estar escritos por extenso, abreviados ou com siglas), a categoria e a base territorial.

Relações possíveis:
- "mesma_cct_outra_vigencia": o mesmo instrumento (mesmas partes, categoria e território) em
  anos/vigências diferentes;
- "copia_do_mesmo_documento": o mesmo documento em duas cópias (registrada e não registrada,
  digitalizada e digital, nomes de arquivo diferentes);
- "resumo_e_texto_completo": um é resumo ou extrato do outro;
- "complementar": termo aditivo, circular, comunicado de reajuste ou acordo que complementa o
  outro sem substituí-lo;
- "diferente": categorias, partes ou territórios diferentes.

Na dúvida entre uma relação de mesma CCT e "complementar" ou "diferente", escolha a segunda.
Responda SOMENTE com JSON:
{"relacao": "...", "motivo": "frase curta em português, sem citar 'documento A' ou 'B'"}"""


def analisar_repetidas(progresso: Progresso = None) -> int:
    """Marca como substituídas as CCTs repetidas, mantendo sempre a mais atual.
    CCTs substituídas continuam no acervo, mas não são usadas nas respostas.

    Para ser estável, cada par de CCTs parecidas é julgado separadamente pela IA e a decisão
    fica guardada: nas próximas atualizações, só pares novos são avaliados.
    Devolve quantas ficaram marcadas como substituídas."""
    ccts = [c for c in carregar_indice() if c.get("paginas")]
    substituicoes: dict[str, tuple[str, str]] = {}  # id -> (id da que vale, motivo)

    # 1) Arquivos com conteúdo idêntico (mesmo com nomes diferentes): fica a indexada primeiro.
    por_conteudo: dict[tuple, dict] = {}
    for cct in sorted(ccts, key=lambda c: c.get("indexado_em") or ""):
        chave = tuple(sorted(_sha_arquivo(PASTA_CCTS / a) for a in cct["arquivos"]
                             if (PASTA_CCTS / a).exists()))
        if not chave:
            continue
        if chave in por_conteudo:
            substituicoes[cct["id"]] = (por_conteudo[chave]["id"], "arquivo idêntico (mesmo conteúdo)")
        else:
            por_conteudo[chave] = cct

    # 2) A mesma CCT em arquivos diferentes: pares parecidos, julgados um a um pela IA.
    candidatas = [c for c in ccts if c["id"] not in substituicoes]
    decisoes = _carregar_decisoes()
    pares = _pares_parecidos(candidatas)
    pendentes = [(a, b) for a, b in pares if _chave_par(a, b) not in decisoes]
    falhas = 0
    if pendentes:
        with ThreadPoolExecutor(CHAMADAS_PARALELAS) as pool:
            futuros = {pool.submit(_julgar_par, a, b): (a, b) for a, b in pendentes}
            for feitos, futuro in enumerate(as_completed(futuros), 1):
                a, b = futuros[futuro]
                try:
                    decisoes[_chave_par(a, b)] = futuro.result()
                except Exception:
                    falhas += 1  # sem decisão: o par é tentado de novo na próxima atualização
                _avisar(progresso, f"Comparando CCTs parecidas ({feitos}/{len(pendentes)})",
                        feitos / len(pendentes))
        _salvar_decisoes(decisoes, {c["id"] for c in ccts})

    # Junta os pares "mesma CCT" em grupos (A=B e B=C -> A, B, C) e mantém a mais atual de cada.
    grupo_de = {c["id"]: c["id"] for c in candidatas}

    def raiz(i):
        while grupo_de[i] != i:
            grupo_de[i] = grupo_de[grupo_de[i]]
            i = grupo_de[i]
        return i

    motivos: dict[frozenset, str] = {}
    for a, b in pares:
        decisao = decisoes.get(_chave_par(a, b))
        if decisao and decisao["relacao"] in RELACOES_MESMA_CCT:
            grupo_de[raiz(a["id"])] = raiz(b["id"])
            motivos[frozenset((a["id"], b["id"]))] = decisao["motivo"]
    grupos: dict[str, list[dict]] = {}
    for cct in candidatas:
        grupos.setdefault(raiz(cct["id"]), []).append(cct)
    for membros in grupos.values():
        if len(membros) < 2:
            continue
        vencedora = max(membros, key=_mais_atual)
        for cct in membros:
            if cct is not vencedora:
                motivo = (motivos.get(frozenset((cct["id"], vencedora["id"])))
                          or next((m for par, m in motivos.items() if cct["id"] in par), "mesma CCT"))
                substituicoes[cct["id"]] = (vencedora["id"], motivo)

    # 3) Grava nas fichas (e limpa marcações antigas que deixaram de valer).
    for cct in carregar_indice():
        vale, motivo = substituicoes.get(cct["id"], (None, ""))
        if cct.get("substituida_por", "ausente") != vale or cct.get("motivo_substituicao", "") != motivo:
            cct["substituida_por"] = vale
            cct["motivo_substituicao"] = motivo
            (PASTA_INDICE / f"{cct['id']}.json").write_text(
                json.dumps(cct, ensure_ascii=False, indent=1), encoding="utf-8")
    if falhas:
        raise ia.ErroIA(f"{falhas} comparação(ões) não puderam ser feitas agora; serão refeitas "
                        "na próxima atualização.")
    return len(substituicoes)


_PALAVRAS_COMUNS = set("""de da do das dos e em no na nos nas a o os as para com por sindicato sindicatos
sind trabalhadores empregados empresas estado sp regiao cct acordo convencao coletiva trabalho act
sao paulo ltda""".split())


def _palavras(texto: str) -> set[str]:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return {p for p in re.findall(r"[a-z]{3,}", texto) if p not in _PALAVRAS_COMUNS}


def _semelhanca(x: set, y: set) -> float:
    return len(x & y) / len(x | y) if x and y else 0.0


def _pares_parecidos(ccts: list[dict]) -> list[tuple[dict, dict]]:
    """Pré-seleção sem IA: pares com sindicatos, categorias, cidades ou título parecidos."""
    def tudo(c):
        m = c.get("metadados") or {}
        return _palavras(" ".join([m.get("titulo", "")] + [" ".join(m.get(k) or []) for k in (
            "sindicatos_trabalhadores", "sindicatos_empregadores_ou_empresas",
            "categorias_abrangidas", "abrangencia_territorial")]))

    palavras = {c["id"]: tudo(c) for c in ccts}
    titulos = {c["id"]: _palavras(titulo(c)) for c in ccts}
    notas = []
    for i, a in enumerate(ccts):
        for b in ccts[i + 1:]:
            nota = max(_semelhanca(palavras[a["id"]], palavras[b["id"]]),
                       _semelhanca(titulos[a["id"]], titulos[b["id"]]) * LIMIAR_SEMELHANCA / LIMIAR_TITULO)
            if nota >= LIMIAR_SEMELHANCA:
                notas.append((nota, a, b))
    notas.sort(key=lambda n: -n[0])
    por_cct: dict[str, int] = {}
    escolhidos = []
    for _, a, b in notas:  # limita os pares por CCT, ficando com os mais parecidos
        if por_cct.get(a["id"], 0) < MAXIMO_PARES_POR_CCT and por_cct.get(b["id"], 0) < MAXIMO_PARES_POR_CCT:
            escolhidos.append((a, b))
            por_cct[a["id"]] = por_cct.get(a["id"], 0) + 1
            por_cct[b["id"]] = por_cct.get(b["id"], 0) + 1
    return escolhidos


def _julgar_par(a: dict, b: dict) -> dict:
    def descricao(c):
        ficha = _ficha_para_comparar(c)
        ficha["inicio_do_texto"] = "\n".join(p["texto"] for p in c["paginas"])[:1500]
        return json.dumps(ficha, ensure_ascii=False)

    resposta = ia.conversar_json(SISTEMA_PAR, f"<documento_A>\n{descricao(a)}\n</documento_A>\n\n"
                                              f"<documento_B>\n{descricao(b)}\n</documento_B>")
    relacao = str(resposta.get("relacao") or "diferente").strip().lower()
    motivo = re.sub(r"(?i)\b(o |do |no )?documento [ab]\b", "", str(resposta.get("motivo") or "")).strip()
    return {"relacao": relacao, "motivo": motivo or relacao.replace("_", " ")}


def _chave_par(a: dict, b: dict) -> str:
    """Muda se uma das CCTs for reprocessada (aí o par é julgado de novo)."""
    return "|".join(sorted(f"{c['id']}@{c.get('indexado_em', '')}" for c in (a, b)))


def _carregar_decisoes() -> dict:
    try:
        return json.loads(_arquivo_decisoes().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _salvar_decisoes(decisoes: dict, ids_existentes: set[str]):
    # Esquece pares de CCTs que saíram do acervo.
    validas = {k: v for k, v in decisoes.items()
               if all(parte.split("@")[0] in ids_existentes for parte in k.split("|"))}
    _arquivo_decisoes().parent.mkdir(parents=True, exist_ok=True)
    _arquivo_decisoes().write_text(json.dumps(validas, ensure_ascii=False, indent=1, sort_keys=True),
                                encoding="utf-8")


def _arquivo_decisoes() -> Path:
    """Decisões já tomadas sobre pares de CCTs (guardadas junto do acervo)."""
    return PASTA_INDICE.parent / "repetidas.json"


def _ficha_para_comparar(cct: dict) -> dict:
    m = cct.get("metadados") or {}
    ficha = {"arquivo": cct["nome"], "paginas": len(cct["paginas"])}
    for campo in ("titulo", "tipo_instrumento", "sindicatos_trabalhadores",
                  "sindicatos_empregadores_ou_empresas", "categorias_abrangidas",
                  "abrangencia_territorial", "vigencia_inicio", "vigencia_fim",
                  "numero_registro_mte", "resumo"):
        if m.get(campo):
            ficha[campo] = m[campo]
    if not m:
        ficha["inicio_do_texto"] = cct["paginas"][0]["texto"][:800]
    return ficha


def _mais_atual(cct: dict) -> tuple:
    """Critério para escolher qual versão vale: vigência mais recente; empatando,
    o documento mais completo (mais texto); depois, o adicionado por último."""
    m = cct.get("metadados") or {}
    tamanho = sum(len(p["texto"]) for p in cct["paginas"])
    return (m.get("vigencia_inicio") or "", m.get("vigencia_fim") or "", tamanho,
            cct.get("indexado_em") or "")


def _finalizar(p: _Pendente, completa: bool):
    paginas = [{"rotulo": pg.rotulo, "texto": pg.texto.strip()}
               for pg in p.paginas if pg.texto.strip()]
    metadados = {}
    if paginas:
        try:
            metadados = _extrair_metadados(p.nome, paginas)
        except Exception as e:
            p.avisos.append(f"não foi possível extrair a ficha ({e}); a CCT ainda pode ser consultada.")
            completa = False
    else:
        p.avisos.append("nenhum texto foi encontrado neste documento.")
        if any(a.suffix.lower() == ".doc" for a in p.arquivos) and not leitor_doc_disponivel():
            completa = False  # .doc não lido aqui: tenta de novo no servidor online, que lê
    ficha = {
        "id": p.id,
        "nome": p.nome,
        "arquivos": [a.relative_to(PASTA_CCTS).as_posix() for a in p.arquivos],
        # hash vazio = será reprocessada na próxima atualização
        "hash": p.hash if completa else "",
        "versao": VERSAO_INDICE,
        "indexado_em": datetime.now().isoformat(timespec="seconds"),
        "avisos": p.avisos,
        "metadados": metadados,
        "paginas": paginas,
    }
    (PASTA_INDICE / f"{p.id}.json").write_text(
        json.dumps(ficha, ensure_ascii=False, indent=1), encoding="utf-8")


def _extrair_metadados(nome: str, paginas: list[dict]) -> dict:
    texto = "\n\n".join(f"=== {pg['rotulo']} ===\n{pg['texto']}" for pg in paginas)
    if len(texto) > LIMITE_CARACTERES_METADADOS:  # só para a ficha; a consulta usa o texto completo
        texto = texto[:LIMITE_CARACTERES_METADADOS] + "\n[...documento continua...]"
    bruto = ia.conversar_json(SISTEMA_METADADOS, f"Arquivo: {nome}\n\n{texto}")
    # Normaliza: o modelo pode omitir campos ou trocar lista por texto.
    ficha = {campo: str(bruto.get(campo) or "").strip() for campo in CAMPOS_TEXTO}
    for campo in CAMPOS_LISTA:
        valor = bruto.get(campo) or []
        ficha[campo] = [str(v).strip() for v in (valor if isinstance(valor, list) else [valor]) if v]
    return ficha


def _atualizada(ficha: dict | None, hash_: str) -> bool:
    return bool(ficha) and ficha.get("hash") == hash_ and ficha.get("versao") == VERSAO_INDICE


def _id(nome: str) -> str:
    return hashlib.sha1(nome.encode("utf-8")).hexdigest()[:12]


_cache_hash: dict[tuple, str] = {}


def _assinatura(arquivos: list[Path]) -> tuple:
    """Muda sempre que um arquivo muda, sem precisar ler o conteúdo."""
    return tuple((str(a), a.stat().st_mtime_ns, a.stat().st_size) for a in arquivos)


def _hash(arquivos: list[Path]) -> str:
    chave = ("unidade",) + _assinatura(arquivos)
    if chave not in _cache_hash:  # ler todos os PDFs a cada clique deixaria a tela lenta
        h = hashlib.sha256()
        for arquivo in arquivos:
            h.update(arquivo.name.encode("utf-8"))
            with arquivo.open("rb") as f:
                for bloco in iter(lambda: f.read(1 << 20), b""):
                    h.update(bloco)
        _cache_hash[chave] = h.hexdigest()
    return _cache_hash[chave]


def _sha_arquivo(arquivo: Path) -> str:
    """Impressão digital do conteúdo de um arquivo (ignora o nome)."""
    chave = ("arquivo",) + _assinatura([arquivo])
    if chave not in _cache_hash:
        _cache_hash[chave] = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    return _cache_hash[chave]


def impressoes_digitais() -> dict[str, str]:
    """Conteúdo de cada arquivo do acervo -> caminho dele (para recusar envios repetidos)."""
    PASTA_CCTS.mkdir(parents=True, exist_ok=True)
    return {_sha_arquivo(a): a.relative_to(PASTA_CCTS).as_posix()
            for a in PASTA_CCTS.rglob("*") if a.is_file() and not _ignorar(a)}


def _ignorar(caminho: Path) -> bool:
    return caminho.name.startswith((".", "~$")) or caminho.name.lower() in {"desktop.ini", "thumbs.db"}


def _ordem_natural(texto: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", texto)]


def _avisar(progresso: Progresso, mensagem: str, fracao: float):
    if progresso:
        progresso(mensagem, fracao)
