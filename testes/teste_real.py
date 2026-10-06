"""Teste de ponta a ponta com a API real, usando 3 CCTs fictícias numa pasta temporária."""
import shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
import extracao, indice, consulta
from teste_offline_util import pdf_texto

tmp = Path(tempfile.mkdtemp())
ccts = tmp / "ccts"; ccts.mkdir()
indice.PASTA_CCTS = ccts
indice.PASTA_INDICE = tmp / "indice"
extracao.PASTA_CACHE_TRANSCRICAO = tmp / "cache"

# 1) PDF com texto: vigilantes de Campinas
pdf_texto(ccts / "CCT Vigilantes Campinas.pdf", [
    "CONVENCAO COLETIVA DE TRABALHO 2026/2027\nSINDICATO DOS EMPREGADOS EM EMPRESAS DE SEGURANCA E VIGILANCIA DE CAMPINAS E REGIAO\n"
    "E SINDICATO DAS EMPRESAS DE SEGURANCA PRIVADA DO ESTADO DE SAO PAULO\nVigencia: 01/01/2026 a 31/12/2026. Data-base: 1o de janeiro.\n"
    "Abrangencia: Campinas, Valinhos, Vinhedo e Sumare/SP.",
    "CLAUSULA TERCEIRA - PISO SALARIAL\nVigilante: R$ 2.345,67 mensais.\nVigilante lider: R$ 2.812,40 mensais.\n"
    "CLAUSULA QUARTA - REAJUSTE\nOs salarios serao reajustados em 5,5% a partir de 1o de janeiro de 2026.",
    "CLAUSULA DECIMA - HORAS EXTRAS\nAs horas extras serao remuneradas com adicional de 60% sobre a hora normal.\n"
    "CLAUSULA DECIMA PRIMEIRA - VALE-ALIMENTACAO\nVale-alimentacao de R$ 32,00 por dia trabalhado.",
])

# 2) Página escaneada (imagem): comerciários de Sorocaba
fonte = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
img = Image.new("RGB", (1600, 1100), (248, 246, 240)); d = ImageDraw.Draw(img)
for i, linha in enumerate([
        "CONVENÇÃO COLETIVA DE TRABALHO 2025/2026 - COMERCIÁRIOS DE SOROCABA",
        "Sindicato dos Empregados no Comércio de Sorocaba e Sindicato do Comércio Varejista de Sorocaba",
        "Vigência: 01/09/2025 a 31/08/2026. Abrangência: Sorocaba/SP.",
        "", "CLÁUSULA 5ª - PISOS SALARIAIS", "Empregado em geral ............ R$ 2.010,00",
        "Caixa .................................. R$ 2.150,00", "",
        "CLÁUSULA 9ª - HORAS EXTRAS", "Adicional de 70% para as duas primeiras horas e 100% para as demais.",
        "", "CLÁUSULA 14ª - TRABALHO AOS DOMINGOS", "Pagamento em dobro e folga compensatória na semana."]):
    d.text((60, 50 + i * 75), linha, fill="black", font=fonte)
img.rotate(0.8, expand=True, fillcolor="white").save(ccts / "comerciarios_sorocaba_scan.jpg", quality=85)

# 3) Word: metalúrgicos de Jundiaí
from docx import Document
doc = Document()
for p in ["ACORDO COLETIVO DE TRABALHO 2026 - METALÚRGICOS DE JUNDIAÍ",
          "Sindicato dos Metalúrgicos de Jundiaí e Empresa Metal Forte Ltda. Vigência: 01/03/2026 a 28/02/2027.",
          "CLÁUSULA 2 - PISO SALARIAL: R$ 2.900,00 para ajudante e R$ 3.600,00 para operador.",
          "CLÁUSULA 6 - HORAS EXTRAS: adicional de 75% de segunda a sábado e 100% aos domingos e feriados.",
          "CLÁUSULA 8 - PLR: R$ 4.000,00 pagos em duas parcelas, em julho e dezembro."]:
    doc.add_paragraph(p)
doc.save(ccts / "ACT Metalurgicos Jundiai.docx")

t = time.time()
r = indice.atualizar_indice(lambda m, f: print(f"  [{time.time()-t:5.0f}s] {m}", flush=True))
print("\nINDEXAÇÃO:", {k: v for k, v in r.items() if k != "avisos"}, f"{time.time()-t:.0f}s")
for a in r["avisos"]: print("  aviso:", a)
for c in indice.carregar_indice():
    m = c["metadados"]
    print(f"\n- {m.get('titulo')} | vigência {m.get('vigencia_inicio')} a {m.get('vigencia_fim')}")
    print("  abrangência:", m.get("abrangencia_territorial"), "| categorias:", m.get("categorias_abrangidas"))
    print("  cláusulas:", m.get("principais_clausulas")[:4])
    if c["nome"].endswith(".jpg"):
        print("  TRANSCRIÇÃO:", c["paginas"][0]["texto"][:600].replace("\n", " / "))

historico = []
for pergunta in ["Qual o piso salarial de um vigilante em Campinas?",
                 "Compare o adicional de horas extras entre todas as convenções.",
                 "E quanto ganha um caixa?"]:
    t = time.time()
    res = consulta.responder(pergunta, historico, progresso=lambda m: print("  ...", m, flush=True))
    print(f"\n================ {pergunta}  ({time.time()-t:.0f}s)")
    print(res.texto)
    print("CCTs analisadas:", res.ccts_consultadas)
    for f in res.fontes:
        print(f"  [{f['n']}] {f['cct']} — {f['pagina']}: {f['trecho'][:110]!r}")
    for a in res.avisos: print("  aviso:", a)
    historico.append({"pergunta": pergunta, "resposta": res.texto})

shutil.rmtree(tmp)
