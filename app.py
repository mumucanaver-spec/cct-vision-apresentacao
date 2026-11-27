"""Interface do CCT Vision (antigo Assistente de CCTs). Execute com:  streamlit run app.py

Funciona no computador (pasta `ccts` local) ou publicado na internet (Streamlit Community
Cloud), com o acervo e as conversas guardados num repositório privado do GitHub. Veja o README.
"""

import hashlib
import hmac
import logging
import os
import re
import threading

import streamlit as st

import visual

st.set_page_config(page_title=visual.NOME, page_icon=visual.ICONE, layout="wide")
visual.aplicar_estilo()


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
import atualizacao  # noqa: E402
import conversas  # noqa: E402
import ia  # noqa: E402
from config import PASTA_CCTS  # noqa: E402
from consulta import responder  # noqa: E402
from indice import (MARCADOR_PASTA, SEM_PASTA, atualizar_indice, carregar_indice,  # noqa: E402
                    eh_pasta_de_organizacao, excluir, excluir_todas, impressoes_digitais, pasta_da_cct,
                    pastas_de_organizacao, situacao, titulo, vencida_em)

ONLINE = armazenamento.configurado()
SENHA = os.getenv("SENHA_DE_ACESSO", "")
estado = st.session_state
registro = logging.getLogger("assistente_cct")

# ---------------------------------------------------------------- entrada

if "usuario" not in estado:
    if not (SENHA or ONLINE):
        estado.usuario = "local"  # no computador, sem senha configurada
    else:
        _, centro, _ = st.columns([1, 1.4, 1])
        with centro:
            visual.cabecalho_entrada()
            with st.form("entrada"):
                nome = st.text_input("Seu nome", help="Usado para separar o seu histórico de conversas.")
                digitada = st.text_input("Senha de acesso", type="password") if SENHA else ""
                if st.form_submit_button("Entrar", type="primary", use_container_width=True):
                    if not nome.strip():
                        st.error("Informe seu nome.")
                    elif SENHA and not hmac.compare_digest(digitada.encode(), SENHA.encode()):
                        st.error("Senha incorreta.")
                    else:
                        estado.usuario = " ".join(nome.split())
                        st.rerun()
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
    """Roda uma vez por servidor: traz do GitHub as CCTs, o índice e as conversas."""
    return armazenamento.baixar() if ONLINE else 0


try:
    _baixar_acervo()
except armazenamento.ErroArmazenamento as e:
    st.error(f"Não foi possível carregar o acervo do GitHub: {e}")
    st.stop()

if "conversa" not in estado:
    estado.conversa = conversas.nova()
estado.setdefault("notificacoes", [])

# ---------------------------------------------------------------- salvar no GitHub


def salvar_acervo(mensagem: str):
    """Salva e espera (usado depois de mudanças no acervo)."""
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


def salvar_em_segundo_plano(mensagem: str):
    """Salva sem fazer o usuário esperar (usado nas conversas)."""
    if not ONLINE:
        return

    def tarefa():
        try:
            armazenamento.sincronizar(mensagem)
        except Exception:
            registro.exception("Falha ao salvar conversas no GitHub")

    threading.Thread(target=tarefa, daemon=True).start()


# ---------------------------------------------------------------- conversas


def nova_conversa():
    estado.conversa = conversas.nova()
    estado.pop("confirmar_exclusao", None)


def abrir_conversa(id_conversa: str):
    conversa = conversas.carregar(estado.usuario, id_conversa)
    estado.conversa = conversa or conversas.nova()
    estado.pop("confirmar_exclusao", None)


def pedir_exclusao(id_conversa: str):
    estado.confirmar_exclusao = id_conversa


def cancelar_exclusao():
    estado.pop("confirmar_exclusao", None)


def excluir_conversa(id_conversa: str):
    conversas.excluir(estado.usuario, id_conversa)
    if estado.conversa["id"] == id_conversa:
        estado.conversa = conversas.nova()
    estado.pop("confirmar_exclusao", None)
    salvar_em_segundo_plano("Exclui uma conversa")


def sair():
    for chave in list(estado.keys()):
        del estado[chave]


def pedir_atualizacao(id_cct: str, pergunta: str | None = None):
    """Botão "Buscar versão atualizada": a busca roda na conversa, com o passo a passo à vista.
    `pergunta`: a pergunta cuja resposta avisou do vencimento (para responder de novo depois)."""
    estado.atualizar = {"id": id_cct, "pergunta": pergunta}


def repetir_pergunta(pergunta: str):
    estado.repetir = pergunta


BUSCA_DESATIVADA = ("A busca na internet não está ativada: falta a chave TAVILY_API_KEY "
                    "(no .env ou em Settings → Secrets).")


# ---------------------------------------------------------------- acervo


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
    texto = (f"{resumo['processadas']} CCT(s) processada(s), {resumo['removidas']} removida(s). "
             f"Total no acervo: {resumo['total']}.")
    if resumo.get("substituidas"):
        texto += (f" {resumo['substituidas']} CCT(s) repetida(s) ou de versão anterior "
                  "foram deixadas de lado; vale sempre a mais atual.")
    estado.notificacoes.append(("success", texto))
    estado.notificacoes.extend(("warning", aviso) for aviso in resumo["avisos"])


def _nome_seguro(nome: str) -> str:
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome).strip(" .")
    return nome or "sem nome"


def _sem_conflito(destino):
    """Se já existe um arquivo (diferente) com esse nome, usa 'nome (2).pdf'."""
    numero = 2
    original = destino
    while destino.exists():
        destino = original.with_name(f"{original.stem} ({numero}){original.suffix}")
        numero += 1
    return destino


def _pasta_de_organizacao(nome: str):
    """Pasta que agrupa CCTs (criada se não existir). Se já houver uma pasta comum com esse
    nome (uma CCT em vários arquivos), usa outro nome para não misturar as duas coisas."""
    base = PASTA_CCTS / _nome_seguro(nome)
    numero = 2
    while base.exists() and not eh_pasta_de_organizacao(base):
        base = PASTA_CCTS / f"{_nome_seguro(nome)} ({numero})"
        numero += 1
    base.mkdir(parents=True, exist_ok=True)
    (base / MARCADOR_PASTA).touch()
    return base


def salvar_envios(enviados: list, grupo: str, pasta_inteira: bool,
                  pasta_destino: str | None) -> tuple[int, list[str]]:
    """Grava os arquivos enviados no acervo, recusando os que já existem.
    No envio de pasta, os arquivos ficam numa pasta de organização com o nome dela; no envio
    de arquivos, na pasta de destino escolhida (ou soltos, em "Arquivos avulsos").
    Devolve (quantos foram gravados, descrições dos repetidos)."""
    conhecidos = impressoes_digitais()
    gravados, repetidos = 0, []
    for arquivo in enviados:
        partes = [p for p in re.split(r"[\\/]", arquivo.name) if p]
        nome = partes[-1]
        if nome.startswith((".", "~$")) or nome.lower() in {"desktop.ini", "thumbs.db"}:
            continue  # arquivos de sistema que vêm junto ao enviar uma pasta
        dados = arquivo.getvalue()
        impressao = hashlib.sha256(dados).hexdigest()
        if impressao in conhecidos:
            repetidos.append(f"{nome} (igual a “{conhecidos[impressao]}”)")
            continue
        if pasta_inteira and len(partes) > 1:
            pasta, partes = partes[0], partes[1:]  # a pasta escolhida vira a pasta de organização
        else:
            pasta, partes = pasta_destino, partes[-1:]
        base = _pasta_de_organizacao(pasta) if pasta and pasta.strip() else PASTA_CCTS
        if grupo.strip():
            destino = base / _nome_seguro(grupo) / _nome_seguro(nome)
        elif len(partes) > 1:  # arquivo dentro de uma subpasta: a subpasta é uma CCT
            destino = base / _nome_seguro(partes[0]) / _nome_seguro(nome)
        else:
            destino = base / _nome_seguro(nome)
        destino = _sem_conflito(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados)
        conhecidos[impressao] = destino.relative_to(PASTA_CCTS).as_posix()
        gravados += 1
    return gravados, repetidos


def mostrar_notificacoes():
    while estado.notificacoes:
        tipo, texto = estado.notificacoes.pop(0)
        getattr(st, tipo)(texto)


def _vigencia(cct: dict) -> str:
    m = cct.get("metadados") or {}
    if m.get("vigencia_inicio") or m.get("vigencia_fim"):
        return f"{m.get('vigencia_inicio') or '?'} a {m.get('vigencia_fim') or '?'}"
    return "vigência não informada"


def sem_formula(texto: str) -> str:
    """O Streamlit trata '$...$' como fórmula matemática, o que deforma valores em R$."""
    return texto.replace("$", r"\$")


def mostrar_detalhes_cct(cct: dict):
    m = cct.get("metadados") or {}
    st.caption(f"Arquivo: {cct['nome']} · {len(cct['paginas'])} página(s)/trecho(s)")
    st.write(f"**Vigência:** {_vigencia(cct)}")
    fim = vencida_em(cct)
    if fim and not cct.get("substituida_por"):
        st.warning(f"Vencida em {fim:%d/%m/%Y}. Pode já existir uma versão nova.")
        st.button("Buscar versão atualizada", key=f"atualizar_lateral_{cct['id']}",
                  icon=":material/travel_explore:", on_click=pedir_atualizacao, args=(cct["id"],),
                  use_container_width=True, disabled=not atualizacao.chave_configurada(),
                  help="A IA procura na internet a versão nova desta convenção, confere e guarda "
                       "no acervo." if atualizacao.chave_configurada() else BUSCA_DESATIVADA)
    if m.get("categorias_abrangidas"):
        st.write("**Categorias:** " + sem_formula("; ".join(m["categorias_abrangidas"])))
    if m.get("abrangencia_territorial"):
        st.write("**Abrangência:** " + "; ".join(m["abrangencia_territorial"]))
    if m.get("resumo"):
        st.write(sem_formula(m["resumo"]))
    for aviso in cct.get("avisos", []):
        st.warning(aviso)
    if st.checkbox("Quero excluir esta CCT", key=f"confirmar_{cct['id']}"):
        if st.button("Excluir definitivamente", key=f"excluir_cct_{cct['id']}", type="primary"):
            excluir(cct)
            executar_indexacao()
            st.rerun()


def _curto(texto: str, limite: int = 42) -> str:
    return texto if len(texto) <= limite else texto[:limite - 1].rstrip() + "…"


# ---------------------------------------------------------------- barra lateral

with st.sidebar:
    visual.marca_lateral()
    st.button("Nova conversa", icon=":material/edit_square:", on_click=nova_conversa,
              use_container_width=True, type="primary")

    lista = conversas.listar(estado.usuario)
    if not lista:
        st.caption("Suas conversas vão aparecer aqui.")
    for rotulo, itens in conversas.agrupar_por_data(lista):
        st.caption(rotulo)
        for c in itens:
            titulo_conversa = c["titulo"] or "Conversa sem título"
            if estado.get("confirmar_exclusao") == c["id"]:
                st.warning(f"Excluir “{_curto(titulo_conversa, 60)}”?")
                sim, nao = st.columns(2)
                sim.button("Excluir", key=f"sim_{c['id']}", on_click=excluir_conversa,
                           args=(c["id"],), type="primary", use_container_width=True)
                nao.button("Cancelar", key=f"nao_{c['id']}", on_click=cancelar_exclusao,
                           use_container_width=True)
                continue
            abrir, lixeira = st.columns([7, 1], vertical_alignment="center", gap="small")
            abrir.button(_curto(titulo_conversa), key=f"abrir_{c['id']}", help=titulo_conversa,
                         on_click=abrir_conversa, args=(c["id"],), use_container_width=True,
                         type="secondary" if c["id"] == estado.conversa["id"] else "tertiary")
            lixeira.button(":material/delete:", key=f"excluir_{c['id']}", help="Excluir conversa",
                           on_click=pedir_exclusao, args=(c["id"],), type="tertiary")

    st.divider()
    with st.container():  # sempre presente: evita que as seções abaixo se fechem sozinhas
        mostrar_notificacoes()

    with st.expander("Adicionar CCTs", icon=":material/upload_file:", key="secao_adicionar",
                     on_change="rerun"):
        modo = st.radio("Enviar", ["Arquivos", "Pasta inteira"], horizontal=True,
                        label_visibility="collapsed")
        pasta_inteira = modo == "Pasta inteira"
        with st.form("envio", clear_on_submit=True, border=False):
            enviados = st.file_uploader(
                "Selecione a pasta com as CCTs" if pasta_inteira else
                "Selecione os arquivos (PDF, imagens, Word, qualquer formato)",
                accept_multiple_files="directory" if pasta_inteira else True,
                help="A pasta aparece com o mesmo nome no acervo. Cada arquivo vira uma CCT; "
                     "uma subpasta vira uma única CCT." if pasta_inteira else None)
            pasta_destino = None
            if not pasta_inteira:
                pasta_destino = st.selectbox(
                    "Pasta de destino (opcional)", pastas_de_organizacao(), index=None,
                    accept_new_options=True, placeholder=SEM_PASTA,
                    help="Escolha uma pasta existente ou digite o nome de uma nova. "
                         f"Sem pasta, os arquivos ficam em “{SEM_PASTA}”.")
            grupo = st.text_input("Juntar tudo numa única CCT (opcional)",
                                  placeholder="Ex.: CCT Comerciários Sorocaba 2026",
                                  help="Preencha quando os arquivos forem partes da mesma "
                                       "convenção, como várias fotos de páginas.")
            enviar = st.form_submit_button("Adicionar e processar", use_container_width=True)
        if enviar and enviados:
            gravados, repetidos = salvar_envios(enviados, grupo, pasta_inteira, pasta_destino)
            if repetidos:
                estado.notificacoes.append(
                    ("info", f"{len(repetidos)} arquivo(s) já estavam no acervo e foram ignorados: "
                             + "; ".join(repetidos)))
            if gravados:
                executar_indexacao()
            st.rerun()

    situacao_acervo = situacao()
    if situacao_acervo["pendentes"] or situacao_acervo["removidas"]:
        st.info(f"{situacao_acervo['pendentes']} CCT(s) nova(s) ou alterada(s)"
                + (f" e {situacao_acervo['removidas']} removida(s)" if situacao_acervo["removidas"] else "")
                + " aguardando processamento.")
        if st.button("Atualizar índice", type="primary", use_container_width=True):
            executar_indexacao()
            st.rerun()

    ccts = carregar_indice()
    ativas = [c for c in ccts if not c.get("substituida_por")]
    substituidas = [c for c in ccts if c.get("substituida_por")]
    por_id = {c["id"]: c for c in ccts}
    with st.expander(f"CCTs indexadas ({len(ativas)})", icon=":material/library_books:",
                     key="secao_ccts", on_change="rerun"):
        if not ccts:
            st.caption("Nenhuma CCT no acervo ainda.")
        # Agrupadas pela pasta de onde vieram ("Arquivos avulsos" por último), um cartão por CCT.
        por_pasta: dict[str, list[dict]] = {}
        for cct in ativas:
            por_pasta.setdefault(pasta_da_cct(cct), []).append(cct)
        ordem = sorted(por_pasta, key=lambda p: (p == SEM_PASTA, p.lower()))
        for pasta in ordem:
            st.markdown(f":material/folder: **{pasta}** · {len(por_pasta[pasta])}")
            for cct in por_pasta[pasta]:
                with st.expander(titulo(cct), key=f"cartao_{cct['id']}", on_change="rerun"):
                    mostrar_detalhes_cct(cct)
        if substituidas:
            st.caption(f"Deixadas de lado ({len(substituidas)}): repetidas ou versões anteriores, "
                       "não usadas nas respostas.")
            for cct in substituidas:
                with st.expander(f"⤷ {titulo(cct)}", key=f"cartao_{cct['id']}", on_change="rerun"):
                    vale = por_id.get(cct["substituida_por"])
                    st.info(f"Vale **{titulo(vale) if vale else '?'}**: "
                            f"{cct.get('motivo_substituicao') or 'mesma CCT'}")
                    mostrar_detalhes_cct(cct)

        if ccts:
            st.divider()
            if st.checkbox(f":material/delete_forever: Remover todas as {len(ccts)} CCTs do acervo",
                           key="remover_todas"):
                st.warning("Isto apaga **todas** as CCTs (arquivos e fichas) para **todos** os "
                           "usuários. As conversas são mantidas.")
                confirmacao = st.text_input("Para confirmar, digite EXCLUIR", key="confirmar_remover_todas")
                if st.button("Remover todas as CCTs", type="primary", use_container_width=True,
                             disabled=confirmacao.strip().upper() != "EXCLUIR"):
                    try:
                        removidas = excluir_todas()
                    except RuntimeError as e:
                        st.error(str(e))
                    else:
                        salvar_acervo("Remove todas as CCTs do acervo")
                        estado.notificacoes.append(
                            ("success", f"{removidas} CCT(s) removida(s). O acervo está vazio."))
                        for chave in ("remover_todas", "confirmar_remover_todas"):
                            estado.pop(chave, None)
                        st.rerun()

    st.divider()
    usuario_col, sair_col = st.columns([3, 1], vertical_alignment="center")
    usuario_col.caption(f"Conectado como **{estado.usuario}**")
    if SENHA or ONLINE:
        sair_col.button("Sair", on_click=sair, type="tertiary")
    visual.rodape_lateral()


# ---------------------------------------------------------------- conversa


def mostrar_resposta(item: dict, posicao: int):
    st.markdown(sem_formula(item["resposta"]))
    for aviso in item.get("avisos", []):
        st.warning(aviso)
    mostrar_vencidas(item, posicao)
    if item.get("etapas"):
        with st.expander(f"Passo a passo ({len(item['etapas'])} etapas)", icon=":material/checklist:"):
            st.markdown(sem_formula("\n".join(f"{n}. {e}" for n, e in enumerate(item["etapas"], 1))))
    pergunta_original = (item.get("atualizacao") or {}).get("pergunta")
    if (item.get("atualizacao") or {}).get("nova_id") and pergunta_original and \
            not any(m["pergunta"] == pergunta_original for m in mensagens[posicao + 1:]):
        st.button("Responder de novo com a versão nova", key=f"repetir_{posicao}", type="primary",
                  icon=":material/refresh:", on_click=repetir_pergunta, args=(pergunta_original,))
    if item.get("consultadas"):
        st.caption("CCTs analisadas: " + " · ".join(item["consultadas"]))
    if item.get("fontes"):
        with st.expander(f"Fontes ({len(item['fontes'])})"):
            for f in item["fontes"]:
                trecho = f["trecho"] if len(f["trecho"]) <= 800 else f["trecho"][:800] + "…"
                citacao = "\n".join("> " + linha for linha in trecho.splitlines() if linha.strip())
                st.markdown(f"**[{f['n']}] {f['cct']}** — {f['pagina']}  \n"
                            f"<small>Arquivo: {f['arquivo']}</small>\n\n{sem_formula(citacao)}",
                            unsafe_allow_html=True)


def mostrar_vencidas(item: dict, posicao: int):
    """Aviso de CCT vencida (conferido pelo programa), com a opção de buscar a versão nova."""
    for v in item.get("vencidas", []):
        atual = por_id.get(v["id"])
        if atual is None:
            continue  # a CCT saiu do acervo
        if atual.get("substituida_por"):
            nova = por_id.get(atual["substituida_por"])
            st.success(f"A CCT **{v['titulo']}** venceu, mas o acervo já tem a versão mais nova: "
                       f"**{titulo(nova) if nova else '?'}**.", icon=":material/check_circle:")
            continue
        fim = vencida_em(atual)
        st.warning(f"A CCT **{v['titulo']}** venceu em "
                   f"{fim:%d/%m/%Y}." if fim else f"A CCT **{v['titulo']}** está vencida.",
                   icon=":material/event_busy:")
        st.button("Buscar versão atualizada", key=f"atualizar_{posicao}_{v['id']}",
                  icon=":material/travel_explore:", on_click=pedir_atualizacao,
                  args=(v["id"], item["pergunta"]), disabled=not atualizacao.chave_configurada(),
                  help="A IA procura na internet a versão nova desta convenção, confere se é a "
                       "mesma convenção e se está vigente, e guarda no acervo."
                  if atualizacao.chave_configurada() else BUSCA_DESATIVADA)


def processar_pergunta(pergunta: str):
    with st.chat_message("user"):
        st.markdown(sem_formula(pergunta))
    with st.chat_message("assistant", avatar=visual.ICONE):
        with st.status("Analisando as CCTs...", expanded=False) as status:
            try:
                # O histórico desta conversa vai junto: perguntas de seguimento mantêm o contexto.
                resultado = responder(pergunta, mensagens,
                                      progresso=lambda msg: status.update(label=msg))
                status.update(label="Pronto", state="complete")
            except ia.ErroIA as e:
                status.update(label="Erro", state="error")
                st.error(str(e))
                st.stop()
    item = {"pergunta": pergunta, "resposta": resultado.texto, "fontes": resultado.fontes,
            "consultadas": resultado.ccts_consultadas, "avisos": resultado.avisos,
            "vencidas": resultado.vencidas}
    conversas.adicionar_mensagem(estado.usuario, estado.conversa, item)
    salvar_em_segundo_plano("Atualiza conversa")
    st.rerun()  # redesenha a tela já com a conversa no histórico da barra lateral


def processar_atualizacao(pedido: dict):
    """Busca a versão nova de uma CCT vencida, mostrando cada ação enquanto acontece."""
    cct = por_id.get(pedido["id"])
    if cct is None or cct.get("substituida_por"):
        return  # já atualizada (ex.: clique repetido) ou removida do acervo
    rotulo = f"Buscar a versão atualizada de {titulo(cct)}"
    with st.chat_message("user"):
        st.markdown(sem_formula(rotulo))
    with st.chat_message("assistant", avatar=visual.ICONE):
        with st.status("Buscando a versão atualizada na internet...", expanded=True) as status:
            def progresso(mensagem: str):
                # expanded=True a cada passo: sem isso a caixa fecha ao trocar o título e o
                # usuário deixa de ver a lista das ações já feitas.
                status.update(label=mensagem, expanded=True)
                status.write(f":material/check_small: {mensagem}")

            try:
                resultado = atualizacao.buscar_versao_atual(cct, progresso)
                status.update(label="Pronto", state="complete", expanded=False)
            except ia.ErroIA as e:
                status.update(label="Erro", state="error")
                st.error(str(e))
                return
    if resultado.nova_id:
        salvar_acervo("Versão atualizada de uma CCT vencida, encontrada pela IA")
    item = {"pergunta": rotulo, "resposta": resultado.texto, "etapas": resultado.etapas,
            "atualizacao": {"nova_id": resultado.nova_id, "pergunta": pedido.get("pergunta")}}
    conversas.adicionar_mensagem(estado.usuario, estado.conversa, item)
    salvar_em_segundo_plano("Atualiza conversa")
    st.rerun()


mensagens = estado.conversa["mensagens"]
if not mensagens:
    visual.boas_vindas(len(ativas))
else:
    st.subheader(sem_formula(estado.conversa["titulo"] or "Conversa"))

for posicao, item in enumerate(mensagens):
    with st.chat_message("user"):
        st.markdown(sem_formula(item["pergunta"]))
    with st.chat_message("assistant", avatar=visual.ICONE):
        mostrar_resposta(item, posicao)

pergunta = st.chat_input("Ex.: Qual o piso salarial dos vigilantes em Campinas?")
if pedido := estado.pop("atualizar", None):
    processar_atualizacao(pedido)
elif repetida := estado.pop("repetir", None):
    processar_pergunta(repetida)
elif pergunta:
    processar_pergunta(pergunta)
