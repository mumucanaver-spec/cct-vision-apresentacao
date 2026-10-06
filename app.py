"""Interface do Assistente de CCTs. Execute com:  streamlit run app.py

Funciona no computador (pasta `ccts` local) ou publicado na internet (Streamlit Community
Cloud), com o acervo guardado num repositório privado do GitHub. Veja o README.
"""

import hmac
import os
import re

import streamlit as st

st.set_page_config(page_title="Assistente de CCTs", page_icon="⚖️", layout="wide")


def _carregar_segredos():
    """Na hospedagem, as chaves ficam em "Secrets" (st.secrets) em vez do arquivo .env."""
    try:
        segredos = dict(st.secrets)
    except Exception:  # sem secrets.toml: uso local com .env
        return
    for chave, valor in segredos.items():
        if isinstance(valor, (str, int)):
            os.environ.setdefault(chave, str(valor))


_carregar_segredos()

import armazenamento  # noqa: E402  (precisam das variáveis carregadas acima)
import ia  # noqa: E402
from config import PASTA_CCTS  # noqa: E402
from consulta import responder  # noqa: E402
from indice import atualizar_indice, carregar_indice, excluir, situacao, titulo  # noqa: E402

ONLINE = armazenamento.configurado()

# ---------------------------------------------------------------- senha

senha = os.getenv("SENHA_DE_ACESSO", "")
if senha and not st.session_state.get("autenticado"):
    st.title("⚖️ Assistente de Convenções Coletivas")
    with st.form("login"):
        digitada = st.text_input("Senha de acesso", type="password")
        if st.form_submit_button("Entrar"):
            if hmac.compare_digest(digitada.encode(), senha.encode()):
                st.session_state.autenticado = True
                st.rerun()
            st.error("Senha incorreta.")
    st.stop()

if not ia.chave_configurada():
    st.error("Chave da API da NVIDIA não encontrada.")
    st.markdown("**No computador:** crie um arquivo **`.env`** na pasta do programa com a linha "
                "`NVIDIA_API_KEY=nvapi-sua-chave-aqui` e reinicie o programa.\n\n"
                "**Na internet (Streamlit Cloud):** adicione `NVIDIA_API_KEY` em "
                "*Settings → Secrets* do app.\n\n"
                "A chave é gratuita e é gerada em https://build.nvidia.com.")
    st.stop()


@st.cache_resource(show_spinner="Carregando o acervo de CCTs...")
def _baixar_acervo() -> int:
    """Roda uma vez por servidor: traz do GitHub as CCTs e o índice."""
    return armazenamento.baixar() if ONLINE else 0


try:
    _baixar_acervo()
except armazenamento.ErroArmazenamento as e:
    st.error(f"Não foi possível carregar o acervo do GitHub: {e}")
    st.stop()


def salvar_acervo(mensagem: str):
    if not ONLINE:
        return
    with st.spinner("Salvando o acervo..."):
        try:
            _, avisos = armazenamento.sincronizar(mensagem)
        except armazenamento.ErroArmazenamento as e:
            st.error(f"As alterações não foram salvas no GitHub: {e}")
            return
    for aviso in avisos:
        st.warning(aviso)


def executar_indexacao():
    barra = st.progress(0.0, text="Verificando arquivos...")
    try:
        resumo = atualizar_indice(lambda msg, f: barra.progress(min(f, 1.0), text=msg))
    except ia.ErroIA as e:
        barra.empty()
        st.error(str(e))
        return
    finally:
        # Mesmo se algo falhar no meio, o que já foi lido fica guardado.
        salvar_acervo("Atualiza o índice de CCTs")
    barra.empty()
    st.success(f"{resumo['processadas']} CCT(s) processada(s), {resumo['removidas']} removida(s). "
               f"Total no acervo: {resumo['total']}.")
    for aviso in resumo["avisos"]:
        st.warning(aviso)


def _nome_seguro(nome: str) -> str:
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome).strip(" .")
    return nome or "sem nome"


# ---------------------------------------------------------------- barra lateral

with st.sidebar:
    st.header("Acervo de CCTs")

    with st.form("envio", clear_on_submit=True):
        enviados = st.file_uploader("Adicionar arquivos (PDF, imagens, Word, qualquer formato)",
                                    accept_multiple_files=True)
        grupo = st.text_input("Juntar numa única CCT (opcional)",
                              placeholder="Ex.: CCT Comerciários Sorocaba 2026",
                              help="Preencha quando os arquivos forem partes da mesma convenção, "
                                   "como várias fotos de páginas. Deixe vazio para que cada "
                                   "arquivo seja uma CCT.")
        if st.form_submit_button("Adicionar e processar", use_container_width=True) and enviados:
            destino = PASTA_CCTS / _nome_seguro(grupo) if grupo.strip() else PASTA_CCTS
            destino.mkdir(parents=True, exist_ok=True)
            for arquivo in enviados:
                (destino / _nome_seguro(arquivo.name)).write_bytes(arquivo.getbuffer())
            executar_indexacao()

    estado = situacao()
    if estado["pendentes"] or estado["removidas"]:
        st.info(f"{estado['pendentes']} CCT(s) nova(s) ou alterada(s)"
                + (f" e {estado['removidas']} removida(s)" if estado["removidas"] else "")
                + ". Clique em **Atualizar índice**.")
    if st.button("Atualizar índice", use_container_width=True,
                 type="primary" if estado["pendentes"] or estado["removidas"] else "secondary"):
        executar_indexacao()

    if ONLINE:
        st.caption("Acervo compartilhado: todos que acessam este endereço veem as mesmas CCTs.")
    else:
        st.caption(f"Pasta monitorada: `{PASTA_CCTS}`  \n"
                   "Cada arquivo é uma CCT. Uma subpasta inteira também é uma CCT "
                   "(ex.: várias fotos da mesma convenção).")

    ccts = carregar_indice()
    st.subheader(f"{len(ccts)} CCT(s) indexada(s)")
    for cct in ccts:
        m = cct.get("metadados") or {}
        with st.expander(titulo(cct)):
            st.caption(f"Arquivo: {cct['nome']} · {len(cct['paginas'])} página(s)/trecho(s)")
            if m.get("vigencia_inicio") or m.get("vigencia_fim"):
                st.write(f"**Vigência:** {m.get('vigencia_inicio') or '?'} a {m.get('vigencia_fim') or '?'}")
            if m.get("categorias_abrangidas"):
                st.write("**Categorias:** " + "; ".join(m["categorias_abrangidas"]))
            if m.get("abrangencia_territorial"):
                st.write("**Abrangência:** " + "; ".join(m["abrangencia_territorial"]))
            if m.get("resumo"):
                st.write(m["resumo"])
            for aviso in cct.get("avisos", []):
                st.warning(aviso)
            if st.checkbox("Quero excluir esta CCT", key=f"confirmar_{cct['id']}"):
                if st.button("Excluir definitivamente", key=f"excluir_{cct['id']}", type="primary"):
                    excluir(cct)
                    executar_indexacao()
                    st.rerun()

    if st.button("Limpar conversa", use_container_width=True):
        st.session_state.conversa = []


# ---------------------------------------------------------------- conversa

st.title("⚖️ Assistente de Convenções Coletivas")
st.caption("Faça qualquer pergunta. O assistente encontra as CCTs aplicáveis, lê o texto "
           "integral e responde citando a página de onde tirou cada informação.")

if "conversa" not in st.session_state:
    st.session_state.conversa = []


def mostrar_resposta(item: dict):
    st.markdown(item["resposta"])
    for aviso in item.get("avisos", []):
        st.warning(aviso)
    if item.get("consultadas"):
        st.caption("CCTs analisadas: " + " · ".join(item["consultadas"]))
    if item.get("fontes"):
        with st.expander(f"Fontes ({len(item['fontes'])})"):
            for f in item["fontes"]:
                trecho = f["trecho"] if len(f["trecho"]) <= 800 else f["trecho"][:800] + "…"
                citacao = "\n".join("> " + linha for linha in trecho.splitlines() if linha.strip())
                st.markdown(f"**[{f['n']}] {f['cct']}** — {f['pagina']}  \n"
                            f"<small>Arquivo: {f['arquivo']}</small>\n\n{citacao}",
                            unsafe_allow_html=True)


for item in st.session_state.conversa:
    with st.chat_message("user"):
        st.markdown(item["pergunta"])
    with st.chat_message("assistant"):
        mostrar_resposta(item)

if pergunta := st.chat_input("Ex.: Qual o piso salarial dos vigilantes em Campinas?"):
    with st.chat_message("user"):
        st.markdown(pergunta)
    with st.chat_message("assistant"):
        with st.status("Analisando as CCTs...", expanded=False) as status:
            try:
                resultado = responder(pergunta, st.session_state.conversa,
                                      progresso=lambda msg: status.update(label=msg))
                status.update(label="Pronto", state="complete")
            except ia.ErroIA as e:
                status.update(label="Erro", state="error")
                st.error(str(e))
                st.stop()
        item = {"pergunta": pergunta, "resposta": resultado.texto, "fontes": resultado.fontes,
                "consultadas": resultado.ccts_consultadas, "avisos": resultado.avisos}
        mostrar_resposta(item)
    st.session_state.conversa.append(item)
