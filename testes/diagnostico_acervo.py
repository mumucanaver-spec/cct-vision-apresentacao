"""Confere a saúde do acervo guardado no GitHub (sem alterar nada).

Uso:  python -X utf8 testes/diagnostico_acervo.py
Precisa de GITHUB_TOKEN e GITHUB_REPOSITORIO no ambiente.
"""
import collections, io, re, sys, tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import logging; logging.disable(logging.WARNING)
import armazenamento, extracao, ia, indice
from pypdf import PdfReader

base = Path(tempfile.mkdtemp())
armazenamento.PASTA_BASE = base
armazenamento.PASTAS_SINCRONIZADAS = (base / "ccts", base / "dados")
indice.PASTA_CCTS = base / "ccts"
indice.PASTA_INDICE = base / "dados" / "indice"
print("Baixando o acervo do GitHub...", armazenamento.baixar(), "arquivos\n")

fichas = indice.carregar_indice()
por_id = {f["id"]: f for f in fichas}
unidades = indice.descobrir_unidades()
problemas = collections.defaultdict(list)  # nome da CCT -> lista de problemas


def p(cct_nome, texto):
    problemas[cct_nome].append(texto)


# 1) Arquivos x fichas
ids_unidades = {}
for nome, arqs in unidades:
    ids_unidades[indice._id(nome)] = (nome, arqs)
    f = por_id.get(indice._id(nome))
    if not f:
        p(nome, "NUNCA PROCESSADA: o arquivo está no acervo, mas não tem ficha")
    elif not indice._atualizada(f, indice._hash(arqs)):
        p(nome, "PENDENTE: processamento incompleto (será refeito no próximo 'Atualizar índice')")
for f in fichas:
    if f["id"] not in ids_unidades:
        p(f["nome"], "FICHA SEM ARQUIVO: a ficha existe, mas o arquivo não está no acervo")

# 2) Conteúdo de cada ficha
hoje = date.today().isoformat()
for f in fichas:
    nome, m = f["nome"], f.get("metadados") or {}
    for aviso in f.get("avisos", []):
        p(nome, f"aviso do processamento: {aviso}")
    if not f.get("paginas"):
        p(nome, "SEM TEXTO: nenhuma página com texto")
        continue
    if not m:
        p(nome, "SEM FICHA: título, vigência e sindicatos não foram extraídos")
    else:
        if not m.get("vigencia_inicio") and not m.get("vigencia_fim"):
            p(nome, "vigência não identificada")
        if not m.get("sindicatos_trabalhadores") and not m.get("sindicatos_empregadores_ou_empresas"):
            p(nome, "sindicatos não identificados")
    for pg in f["paginas"]:
        t = pg["texto"]
        if not ia._transcricao_valida(t):
            p(nome, f"{pg['rotulo']}: transcrição contém o rascunho do modelo")
        if "[transcrição incompleta]" in t:
            p(nome, f"{pg['rotulo']}: transcrição cortada (página muito longa)")
        ilegiveis = t.count("[ilegível]")
        if ilegiveis >= 3:
            p(nome, f"{pg['rotulo']}: {ilegiveis} trechos ilegíveis")
        if len(t.strip()) < 40:
            p(nome, f"{pg['rotulo']}: quase sem texto ({len(t.strip())} caracteres)")
    # Páginas do PDF que não aparecem na ficha (ficaram sem texto)
    if nome in dict(unidades) and len(dict(unidades)[nome]) == 1 and nome.lower().endswith(".pdf"):
        arquivo = dict(unidades)[nome][0]
        try:
            total = len(PdfReader(io.BytesIO(arquivo.read_bytes())).pages)
            if total != len(f["paginas"]):
                p(nome, f"PDF tem {total} páginas, mas só {len(f['paginas'])} têm texto na ficha")
        except Exception as e:
            p(nome, f"não foi possível abrir o PDF para conferir ({e})")

# 3) Resumo
ativas = [f for f in fichas if not f.get("substituida_por")]
substituidas = [f for f in fichas if f.get("substituida_por")]
vencidas = [f for f in ativas if (f.get("metadados") or {}).get("vigencia_fim", "9999") < hoje
            and (f.get("metadados") or {}).get("vigencia_fim")]
pastas = collections.Counter(indice.pasta_da_cct(f) for f in fichas)
print(f"Arquivos/CCTs no acervo: {len(unidades)} | fichas: {len(fichas)} | ativas: {len(ativas)} "
      f"| deixadas de lado: {len(substituidas)}")
print("Por pasta:", dict(pastas))
print(f"Páginas com texto: {sum(len(f['paginas']) for f in fichas)}\n")

graves = {k: v for k, v in problemas.items()
          if any(x.split(':')[0].isupper() or "rascunho" in x or "cortada" in x for x in v)}
print(f"=== PROBLEMAS ({len(problemas)} CCTs com algum apontamento, {len(graves)} importantes) ===")
for nome in sorted(problemas, key=lambda n: (n not in graves, n.lower())):
    titulo = indice.titulo(por_id[indice._id(nome)]) if indice._id(nome) in por_id else nome
    print(f"\n{'❗' if nome in graves else '·'} {titulo}  [{nome}]")
    for x in problemas[nome][:8]:
        print("    -", x)
    if len(problemas[nome]) > 8:
        print(f"    ... e mais {len(problemas[nome]) - 8}")

print(f"\n=== DEIXADAS DE LADO ({len(substituidas)}) ===")
for f in substituidas:
    vale = por_id.get(f["substituida_por"])
    print(f"- {indice.titulo(f)}  ->  vale: {indice.titulo(vale) if vale else '???'}")
    print(f"    motivo: {f.get('motivo_substituicao')}")

print(f"\n=== ATIVAS COM VIGÊNCIA ENCERRADA ({len(vencidas)}) ===")
for f in vencidas:
    print(f"- {indice.titulo(f)} (até {f['metadados']['vigencia_fim']})")
