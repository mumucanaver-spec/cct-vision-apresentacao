"""Identidade visual do CCT Vision: nome, logo e estilos da interface.

Só cuida da aparência; nenhuma regra de funcionamento do assistente fica aqui.
"""

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

NOME = "CCT Vision"
SLOGAN = "Respostas sobre convenções coletivas, sempre com a fonte."
VERSAO = "1.0"
PASTA_ASSETS = Path(__file__).resolve().parent / "assets"
LOGO = str(PASTA_ASSETS / "logo.png")
ICONE = str(PASTA_ASSETS / "icone.png")
AVISO = ("As respostas são informativas e se baseiam no texto das convenções. "
         "Para decisões jurídicas, confirme com um profissional.")

_CSS = """
<style>
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #F1F5FA 0%, #E6EEF7 100%);
    border-right: 1px solid #DDE5EE;
}
[data-testid="stChatMessage"] {
    background: #FFFFFF;
    border: 1px solid #E3E9F0;
    border-radius: 14px;
    padding: 0.9rem 1.1rem;
    box-shadow: 0 1px 3px rgba(11, 37, 69, 0.06);
}
[data-testid="stExpander"] details {
    border-radius: 12px;
}
.cv-hero { text-align: center; padding: 2.2rem 0 0.4rem; }
.cv-hero img { height: 88px; max-width: 100%; }
.cv-titulo {
    font-size: 1.75rem; font-weight: 700; color: #0B2545;
    margin: 1.3rem 0 0.4rem; line-height: 1.25;
}
.cv-sub { color: #4A5B6E; font-size: 1.02rem; margin: 0 auto; max-width: 640px; text-align: center; }
.cv-cartoes {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
    margin: 1.8rem auto 0.6rem; max-width: 920px;
}
.cv-cartao {
    background: #FFFFFF; border: 1px solid #E3E9F0; border-radius: 14px;
    padding: 1.1rem 1.15rem; text-align: left;
    box-shadow: 0 1px 3px rgba(11, 37, 69, 0.06);
}
.cv-num {
    display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; border-radius: 9px; margin-bottom: 0.6rem;
    background: linear-gradient(135deg, #14B8A6, #0EA5E9); color: #FFFFFF; font-weight: 700;
}
.cv-cartao-titulo { font-weight: 700; color: #0B2545; margin-bottom: 0.25rem; }
.cv-cartao-texto { color: #4A5B6E; font-size: 0.92rem; line-height: 1.45; }
.cv-selo {
    display: inline-block; margin-top: 1.2rem; padding: 0.35rem 0.95rem; border-radius: 999px;
    background: #E3F5F3; color: #0B6E66; font-weight: 600; font-size: 0.86rem;
}
.cv-aviso { text-align: center; color: #6B7A8C; font-size: 0.8rem; margin-top: 0.9rem; }
.cv-slogan { text-align: center; color: #4A5B6E; margin: 0.2rem 0 1.2rem; }
.cv-entrada { text-align: center; padding-top: 2.5rem; }
.cv-entrada img { height: 76px; max-width: 100%; }
.cv-rodape { color: #6B7A8C; font-size: 0.78rem; line-height: 1.4; }
@media (max-width: 760px) { .cv-cartoes { grid-template-columns: 1fr; } }
</style>
"""


@lru_cache(maxsize=None)
def _imagem_embutida(caminho: str) -> str:
    """Imagem como data URI, para usar dentro do HTML das telas."""
    return "data:image/png;base64," + base64.b64encode(Path(caminho).read_bytes()).decode()


def aplicar_estilo():
    """Logo no canto da tela e estilos gerais. Chamar uma vez por execução."""
    st.logo(LOGO, icon_image=ICONE, size="large")
    st.markdown(_CSS, unsafe_allow_html=True)


def cabecalho_entrada():
    st.markdown(f'<div class="cv-entrada"><img src="{_imagem_embutida(LOGO)}" alt="{NOME}"></div>'
                f'<p class="cv-slogan">{SLOGAN}</p>', unsafe_allow_html=True)


def boas_vindas(total_ccts: int):
    """Tela inicial, mostrada enquanto a conversa ainda não tem mensagens."""
    cartoes = [
        ("Encontra as CCTs certas",
         "Descobre quais convenções valem para a categoria, a cidade e o período da pergunta."),
        ("Lê o texto completo",
         "Analisa a convenção inteira, inclusive PDFs escaneados, fotos e arquivos do Word."),
        ("Mostra a fonte",
         "Cada informação vem com a convenção, a página e o trecho de onde foi tirada."),
    ]
    html_cartoes = "".join(
        f'<div class="cv-cartao"><div class="cv-num">{i}</div>'
        f'<div class="cv-cartao-titulo">{titulo}</div><div class="cv-cartao-texto">{texto}</div></div>'
        for i, (titulo, texto) in enumerate(cartoes, 1))
    selo = (f"{total_ccts} convenção no acervo" if total_ccts == 1 else
            f"{total_ccts} convenções no acervo") if total_ccts else "Acervo vazio: adicione CCTs na barra lateral"
    st.markdown(
        f'<div class="cv-hero"><img src="{_imagem_embutida(LOGO)}" alt="{NOME}">'
        f'<div class="cv-titulo">Pergunte sobre convenções coletivas</div>'
        f'<p class="cv-sub">O {NOME} encontra as CCTs aplicáveis, lê o texto integral e responde '
        f'citando a página de onde tirou cada informação.</p>'
        f'<div class="cv-cartoes">{html_cartoes}</div>'
        f'<span class="cv-selo">{selo}</span>'
        f'<p class="cv-aviso">{AVISO}</p></div>',
        unsafe_allow_html=True)


def rodape_lateral():
    st.markdown(f'<div class="cv-rodape"><b>{NOME}</b> · versão {VERSAO}<br>{AVISO}</div>',
                unsafe_allow_html=True)
