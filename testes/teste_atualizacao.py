"""Teste da busca da versão atualizada de uma CCT vencida, com a IA e a internet simuladas.

Roda numa cópia temporária do programa (com o acervo real), para não alterar nada aqui.
Cobre: o aviso de CCT vencida na resposta, o botão "Buscar versão atualizada", a conferência
(outra cidade e versão antiga são descartadas), a gravação no acervo, a versão antiga em
"Deixadas de lado", a ligação respeitada na reorganização do acervo, o botão "Responder de
novo com a versão nova" e o caso em que nada serve (o acervo não muda).

Uso:  .venv\\Scripts\\python.exe -X utf8 testes\\teste_atualizacao.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ORIGEM = Path(__file__).resolve().parents[1]
copia = Path(tempfile.mkdtemp()) / "app"
shutil.copytree(ORIGEM, copia, ignore=shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".env", "cache_pesquisa", "conversas", "testes"))
os.chdir(copia)
sys.path.insert(0, str(copia))
os.environ.update({"NVIDIA_API_KEY": "nvapi-teste", "SENHA_DE_ACESSO": "", "TAVILY_API_KEY": "tvly-teste"})
os.environ.pop("BRAVE_API_KEY", None)
os.environ.pop("GITHUB_TOKEN", None)

import atualizacao  # noqa: E402
import consulta  # noqa: E402
import ia  # noqa: E402
import indice  # noqa: E402

ALVO = "CCT Comércio Varejista Bauru - Motoristas 2025/2026"
antiga = next(c for c in indice.carregar_indice() if indice.titulo(c) == ALVO)
assert indice.vencida_em(antiga), "a CCT de teste deveria estar vencida"
ativas_antes = sum(1 for c in indice.carregar_indice() if not c.get("substituida_por"))

# ---------------------------------------------------------------- internet simulada
CLAUSULAS = "\n".join(f"CLÁUSULA {i} - texto da cláusula {i} da convenção dos motoristas." for i in range(1, 80))
WEB = {
    "https://sincomercio.exemplo/cct-motoristas-2026-2027.txt": ("nova", "Motoristas Bauru 2026/2027"),
    "https://outro.exemplo/cct-motoristas-campinas-2026.txt": ("outra_cidade", "Motoristas Campinas 2026/2027"),
    "https://sincomercio.exemplo/cct-motoristas-2025-2026.txt": ("antiga", "Motoristas Bauru 2025/2026"),
    "https://noticias.exemplo/reajuste.html": ("noticia", "Notícia"),
}
FICHAS = {
    "nova": {"titulo": "CCT Comércio Varejista Bauru - Motoristas 2026/2027", "tipo_instrumento": "Convenção Coletiva",
             "vigencia_inicio": "2026-10-01", "vigencia_fim": "2027-09-30", "numero_registro_mte": "SP999999/2026",
             "abrangencia_territorial": ["Bauru/SP"], "categorias_abrangidas": ["Motoristas do comércio"]},
    "outra_cidade": {"titulo": "CCT Motoristas Campinas 2026/2027", "tipo_instrumento": "Convenção Coletiva",
                     "vigencia_inicio": "2026-10-01", "vigencia_fim": "2027-09-30",
                     "abrangencia_territorial": ["Campinas/SP"]},
    "antiga": {"titulo": "CCT Comércio Varejista Bauru - Motoristas 2025/2026", "tipo_instrumento": "Convenção Coletiva",
               "vigencia_inicio": "2025-10-01", "vigencia_fim": "2026-09-30", "abrangencia_territorial": ["Bauru/SP"]},
}
resultados_da_busca = list(WEB)
buscas_feitas, baixados = [], []


def falsa_busca(consultas):
    buscas_feitas.append(consultas)
    return [{"url": u, "title": WEB[u][1], "content": "convenção coletiva"} for u in resultados_da_busca]


def falso_baixar(url):
    baixados.append(url)
    chave, nome = WEB[url]
    if chave == "noticia":
        return b"<html><body>noticia sem cct</body></html>", "text/html"
    return f"CONVENÇÃO COLETIVA DE TRABALHO {nome}\n{CLAUSULAS}".encode("utf-8"), "text/plain"


atualizacao._buscar = falsa_busca
atualizacao._baixar = falso_baixar

# ---------------------------------------------------------------- IA simulada
chamadas = []


def falso_json(system, mensagem, max_tokens=16000):
    chamadas.append(system[:25])
    if system.startswith(atualizacao.SISTEMA_BUSCAS[:60]):
        assert "vigencia_fim" in mensagem and "2026/2027" in system
        return {"buscas": ["convenção coletiva motoristas comércio Bauru 2026/2027 pdf", "sincomercio bauru motoristas"]}
    if system.startswith(atualizacao.SISTEMA_TRIAGEM[:60]):
        return {"codigos": re.findall(r"^(R\d+) \|", mensagem, re.M)}
    if system == indice.SISTEMA_METADADOS:
        texto = mensagem
        for chave, ficha in FICHAS.items():
            if WEB_POR_CHAVE[chave] in texto:
                return ficha
        return {}
    if system == atualizacao.SISTEMA_CONFERENCIA:
        docs = re.findall(r'<documento codigo="(D\d+)" site="[^"]+">\n<ficha>(.*?)</ficha>', mensagem)
        saida = []
        for codigo, ficha in docs:
            outra = "Campinas" in ficha
            saida.append({"codigo": codigo, "tipo": "Convenção Coletiva", "mesma_convencao": not outra,
                          "motivo": "Outra cidade (Campinas)." if outra else "Mesmos sindicatos, categoria e cidade."})
        return {"documentos": saida}
    if system == consulta.SISTEMA_SELECAO:
        return {"codigos": [c for c, linha in re.findall(r'"codigo": "(C\d+)"(.*)', mensagem) if "Motoristas" in linha]}
    if system == indice.SISTEMA_PAR:
        return {"relacao": "diferente", "motivo": "teste"}
    raise AssertionError(system[:80])


WEB_POR_CHAVE = {chave: f"CONVENÇÃO COLETIVA DE TRABALHO {nome}" for _, (chave, nome) in WEB.items()}


def falso_conversar(system, mensagem, max_tokens=16000):
    codigos = re.findall(r"\[(D\d+-\d+)\]", mensagem)
    titulos = re.findall(r"<titulo>(.*?)</titulo>", mensagem)
    return f"Resposta sobre {', '.join(titulos)}: piso dos motoristas [{codigos[0]}].", False


ia.conversar_json = falso_json
ia.conversar = falso_conversar

# ---------------------------------------------------------------- tela (Streamlit AppTest)
from streamlit.testing.v1 import AppTest  # noqa: E402

at = AppTest.from_file(str(copia / "app.py"), default_timeout=120)
at.run()
assert not at.exception, at.exception
at.chat_input[0].set_value("Qual o piso dos motoristas do comércio em Bauru?").run()
assert not at.exception, at.exception
avisos = [w.value for w in at.warning]
print("avisos na resposta:", avisos)
assert any("venceu em 30/09/2026" in a and "Motoristas" in a for a in avisos)
botao = next(b for b in at.button if b.label == "Buscar versão atualizada" and (b.key or "").startswith("atualizar_0_"))
assert not botao.disabled

botao.click().run()
assert not at.exception, at.exception
textos = [m.value for m in at.markdown]
assert any("Encontrei a versão atualizada" in t for t in textos), textos[-5:]
assert any("SP999999/2026" in t for t in textos)
sucesso = [s.value for s in at.success]
print("aviso depois da atualização:", sucesso)
assert any("já tem a versão mais nova" in s for s in sucesso)
assert not any(b.label == "Buscar versão atualizada" and (b.key or "").startswith("atualizar_0_") for b in at.button), \
    "o botão deveria sumir depois da atualização"
etapas = next(e for e in at.expander if e.label.startswith("Passo a passo"))
print("passo a passo:", etapas.label)
passo = " ".join(m.value for m in etapas.markdown)
assert "Campinas" in passo and "descartei" in passo, passo
assert "não é mais nova" in passo, passo

fichas = {c["id"]: c for c in indice.carregar_indice()}
velha = fichas[antiga["id"]]
nova = fichas[velha["substituida_por"]]
print("versão antiga deixada de lado ->", indice.titulo(nova), "| pasta:", indice.pasta_da_cct(nova))
assert indice.titulo(nova) == FICHAS["nova"]["titulo"] and not nova.get("substituida_por")
assert indice.pasta_da_cct(nova) == indice.pasta_da_cct(antiga)
assert (indice.PASTA_CCTS / nova["arquivos"][0]).exists()
assert sum(1 for c in fichas.values() if not c.get("substituida_por")) == ativas_antes
assert indice.situacao()["pendentes"] == 0, indice.situacao()
assert len(buscas_feitas) == 1 and len(buscas_feitas[0]) == 2

# A ligação antiga -> nova sobrevive à reorganização do acervo (mesmo com a IA dizendo "diferente").
indice.analisar_repetidas()
assert indice.carregar_indice() and {c["id"]: c for c in indice.carregar_indice()}[antiga["id"]]["substituida_por"] == nova["id"]

# Responder de novo: agora a resposta sai da versão nova, sem aviso de vencida.
repetir = next(b for b in at.button if b.label == "Responder de novo com a versão nova")
repetir.click().run()
assert not at.exception, at.exception
ultima = [m.value for m in at.markdown if m.value.startswith("Resposta sobre")][-1]
print("resposta de novo:", ultima)
assert "Motoristas 2026/2027" in ultima and "Motoristas 2025/2026" not in ultima
assert not any(b.label == "Responder de novo com a versão nova" for b in at.button)

# ---------------------------------------------------------------- nada serve: o acervo não muda
outra = next(c for c in indice.carregar_indice()
             if indice.titulo(c) == "CCT Petshops Estado de São Paulo 2025/2026")
antes = sorted(p.name for p in indice.PASTA_INDICE.glob("*.json"))
resultados_da_busca = ["https://outro.exemplo/cct-motoristas-campinas-2026.txt"]
r = atualizacao.buscar_versao_atual(outra)
print("sem versão nova:", r.texto.splitlines()[0])
assert r.nova_id is None and "Não encontrei" in r.texto and "Nada foi trocado" in r.texto
assert sorted(p.name for p in indice.PASTA_INDICE.glob("*.json")) == antes

# Sem chave de busca: explica e não chama nada.
os.environ.pop("TAVILY_API_KEY")
n = len(chamadas)
r = atualizacao.buscar_versao_atual(outra)
assert r.nova_id is None and "TAVILY_API_KEY" in r.texto and len(chamadas) == n
print("\nOK: busca da versão atualizada funcionando.")
shutil.rmtree(copia.parent, ignore_errors=True)
