"""Identidade visual do CCT Vision: nome, logo e estilos da interface.

Só cuida da aparência; nenhuma regra de funcionamento do assistente fica aqui.
O tema claro e o escuro vêm de .streamlit/config.toml; as cores próprias abaixo usam
light-dark(), que segue o tema escolhido no menu do app sem recarregar a página.
"""

from pathlib import Path

import streamlit as st

NOME = "CCT Vision"
SLOGAN = "Respostas sobre convenções coletivas, sempre com a fonte."
VERSAO = "1.1"
PASTA_ASSETS = Path(__file__).resolve().parent / "assets"
LOGO = str(PASTA_ASSETS / "logo.png")
ICONE = str(PASTA_ASSETS / "icone.png")
AVISO = ("As respostas são informativas e se baseiam no texto das convenções. "
         "Para decisões jurídicas, confirme com um profissional.")

_CSS = """
<style>
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #F1F5FA 0%, #E6EEF7 100%);
    background: linear-gradient(180deg, light-dark(#F1F5FA, #111C2E) 0%, light-dark(#E6EEF7, #0B1422) 100%);
    border-right: 1px solid light-dark(#DDE5EE, #1F2E46);
}
[data-testid="stSidebarHeader"] { padding-bottom: 0; }
[data-testid="stChatMessage"] {
    background: #FFFFFF;
    background: light-dark(#FFFFFF, #142035);
    border: 1px solid light-dark(#E3E9F0, #22324C);
    border-radius: 14px;
    padding: 0.9rem 1.1rem;
    box-shadow: 0 1px 3px light-dark(rgba(11, 37, 69, 0.06), rgba(0, 0, 0, 0.35));
}
[data-testid="stExpander"] details { border-radius: 12px; }
.cv-marca { display: inline-block; vertical-align: middle; }
.cv-marca svg { display: block; height: 100%; width: auto; }
.cv-cct { fill: #0B2545; fill: light-dark(#0B2545, #F1F5F9); }
.cv-vision { fill: #0E9F94; fill: light-dark(#0E9F94, #2DD4BF); }
.cv-lateral { margin: -0.4rem 0 0.4rem; }
.cv-lateral .cv-marca { height: 42px; }
.cv-hero { text-align: center; padding: 2.2rem 0 0.4rem; }
.cv-hero .cv-marca { height: 88px; }
.cv-titulo {
    font-size: 1.75rem; font-weight: 700; color: light-dark(#0B2545, #F1F5F9);
    margin: 1.3rem 0 0.4rem; line-height: 1.25;
}
.cv-sub {
    color: light-dark(#4A5B6E, #A7B4C5); font-size: 1.02rem;
    margin-left: auto !important; margin-right: auto !important;
    max-width: 640px; text-align: center;
}
.cv-cartoes {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
    margin: 1.8rem auto 0.6rem; max-width: 920px;
}
.cv-cartao {
    background: #FFFFFF;
    background: light-dark(#FFFFFF, #142035);
    border: 1px solid light-dark(#E3E9F0, #22324C); border-radius: 14px;
    padding: 1.1rem 1.15rem; text-align: left;
    box-shadow: 0 1px 3px light-dark(rgba(11, 37, 69, 0.06), rgba(0, 0, 0, 0.35));
}
.cv-num {
    display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; border-radius: 9px; margin-bottom: 0.6rem;
    background: linear-gradient(135deg, #14B8A6, #0EA5E9); color: #FFFFFF; font-weight: 700;
}
.cv-cartao-titulo { font-weight: 700; color: light-dark(#0B2545, #F1F5F9); margin-bottom: 0.25rem; }
.cv-cartao-texto { color: light-dark(#4A5B6E, #A7B4C5); font-size: 0.92rem; line-height: 1.45; }
.cv-selo {
    display: inline-block; margin-top: 1.2rem; padding: 0.35rem 0.95rem; border-radius: 999px;
    background: light-dark(#E3F5F3, rgba(45, 212, 191, 0.14));
    color: light-dark(#0B6E66, #5EEAD4); font-weight: 600; font-size: 0.86rem;
}
.cv-aviso { text-align: center; color: light-dark(#6B7A8C, #8B99AB); font-size: 0.8rem; margin-top: 0.9rem; }
.cv-slogan { text-align: center; color: light-dark(#4A5B6E, #A7B4C5); margin: 0.6rem 0 1.2rem; }
.cv-entrada { text-align: center; padding-top: 2.5rem; }
.cv-entrada .cv-marca { height: 76px; }
.cv-rodape { color: light-dark(#6B7A8C, #8B99AB); font-size: 0.78rem; line-height: 1.4; }
.cv-tema { color: light-dark(#4A5B6E, #A7B4C5); font-size: 0.8rem; margin-bottom: 0.5rem; }
.cv-tema b { color: light-dark(#0B2545, #E4EBF3); }
@media (max-width: 760px) { .cv-cartoes { grid-template-columns: 1fr; } }
</style>
"""

# Mesmo desenho de assets/logo.svg, embutido na página para o texto acompanhar o tema.
_MARCA = """<span class="cv-marca"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 470 128" role="img" aria-label="CCT Vision">
<defs>
<linearGradient id="cv-fundo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0B2545"/><stop offset="1" stop-color="#1D4E89"/></linearGradient>
<linearGradient id="cv-olho" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#14B8A6"/><stop offset="1" stop-color="#0EA5E9"/></linearGradient>
</defs>
<rect width="128" height="128" rx="28" fill="url(#cv-fundo)"/>
<path d="M40 22h34l20 20v62a6 6 0 0 1-6 6H40a6 6 0 0 1-6-6V28a6 6 0 0 1 6-6z" fill="#FFFFFF"/>
<path d="M74 22v14a6 6 0 0 0 6 6h14z" fill="#C9DDF2"/>
<rect x="44" y="46" width="28" height="5" rx="2.5" fill="#9DB4CC"/>
<rect x="44" y="57" width="40" height="5" rx="2.5" fill="#9DB4CC"/>
<path d="M38 87c7-11 16-16 26-16s19 5 26 16c-7 11-16 16-26 16s-19-5-26-16z" fill="url(#cv-olho)"/>
<circle cx="64" cy="87" r="10" fill="#FFFFFF"/><circle cx="64" cy="87" r="5.5" fill="#0B2545"/><circle cx="66.5" cy="84.5" r="1.8" fill="#FFFFFF"/>
<text x="152" y="84" font-family="'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif" font-size="62" letter-spacing="-1"><tspan class="cv-cct" font-weight="800">CCT</tspan><tspan class="cv-vision" font-weight="600" dx="14">Vision</tspan></text>
</svg></span>"""


def aplicar_estilo():
    """Estilos gerais da interface. Chamar uma vez por execução."""
    st.markdown(_CSS, unsafe_allow_html=True)


def marca_lateral():
    """Logo no topo da barra lateral."""
    st.markdown(f'<div class="cv-lateral">{_MARCA}</div>', unsafe_allow_html=True)


def cabecalho_entrada():
    st.markdown(f'<div class="cv-entrada">{_MARCA}</div><p class="cv-slogan">{SLOGAN}</p>',
                unsafe_allow_html=True)


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
        f'<div class="cv-hero">{_MARCA}'
        f'<div class="cv-titulo">Pergunte sobre convenções coletivas</div>'
        f'<p class="cv-sub">O {NOME} encontra as CCTs aplicáveis, lê o texto integral e responde '
        f'citando a página de onde tirou cada informação.</p>'
        f'<div class="cv-cartoes">{html_cartoes}</div>'
        f'<span class="cv-selo">{selo}</span>'
        f'<p class="cv-aviso">{AVISO}</p></div>',
        unsafe_allow_html=True)


def rodape_lateral():
    st.markdown('<div class="cv-tema">Modo noturno: menu <b>⋮</b> no canto superior direito '
                '→ <b>Dark</b>. Para voltar, <b>Light</b>.</div>'
                f'<div class="cv-rodape"><b>{NOME}</b> · versão {VERSAO}<br>{AVISO}</div>',
                unsafe_allow_html=True)
