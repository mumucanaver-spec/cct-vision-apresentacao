"""Teste de ponta a ponta com a API simulada."""
import json, re, shutil, sys, tempfile, zipfile
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
import ia, extracao, indice, consulta

tmp = Path(tempfile.mkdtemp())
ccts = tmp / "ccts"; ccts.mkdir()
indice.PASTA_CCTS = ccts
indice.PASTA_INDICE = tmp / "indice"
extracao.PASTA_CACHE_TRANSCRICAO = tmp / "cache"


def pdf_texto(caminho, paginas):
    objs, kids = [], []
    objs.append(None); objs.append(None)  # 1 catalog, 2 pages
    fonte = len(objs) + 1; objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for texto in paginas:
        linhas = "".join(f"({l}) Tj T* " for l in texto.split("\n"))
        stream = f"BT /F1 11 Tf 14 TL 50 780 Td {linhas} ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        c = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {c} 0 R "
                    f"/Resources << /Font << /F1 {fonte} 0 R >> >> >>")
        kids.append(len(objs))
    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"; offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    x = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
    caminho.write_bytes(out)


clausula = "CLAUSULA TERCEIRA - PISO SALARIAL\nFica assegurado aos vigilantes o piso de R$ 2.345,67 mensais.\n" * 3
pdf_texto(ccts / "CCT Vigilantes Campinas 2026.pdf",
          ["CONVENCAO COLETIVA DE TRABALHO 2026/2027\nSINDICATO DOS VIGILANTES DE CAMPINAS\n" * 2, clausula,
           "CLAUSULA QUINTA - HORAS EXTRAS\nAs horas extras serao pagas com adicional de 60 por cento.\n" * 3])

def foto(texto):
    im = Image.new("RGB", (1200, 1600), "white"); ImageDraw.Draw(im).text((50, 50), texto, fill="black"); return im

foto("CCT COMERCIARIOS").save(ccts / "cct_comercio.jpg")
pasta = ccts / "CCT Metalurgicos (fotos)"; pasta.mkdir()
foto("pag 1").save(pasta / "pagina 1.png"); foto("pag 2").save(pasta / "pagina 2.png"); foto("pag 10").save(pasta / "pagina 10.png")
foto("scan").save(ccts / "cct_escaneada.pdf")                       # PDF só com imagem
foto("a").save(ccts / "multi.tiff", save_all=True, append_images=[foto("b")])

from docx import Document
d = Document(); d.add_paragraph("CCT dos Bancarios - clausula 1: vale-refeicao de R$ 50,00 por dia.")
t = d.add_table(rows=2, cols=2); t.cell(0, 0).text = "Funcao"; t.cell(0, 1).text = "Piso"; t.cell(1, 0).text = "Caixa"; t.cell(1, 1).text = "R$ 3.000"
d.save(ccts / "bancarios.docx")
(ccts / "rodoviarios.txt").write_text("CCT Rodoviarios\n\nAdicional noturno de 30%.", encoding="cp1252")
(ccts / "hoteis.html").write_text("<html><body><p>CCT Hoteis</p><table><tr><td>Piso</td><td>R$ 1.900</td></tr></table></body></html>")
with zipfile.ZipFile(ccts / "saude.odt", "w") as z:
    z.writestr("content.xml", '<office:document-content><text:p>CCT Saude</text:p><text:p>Insalubridade 40%</text:p></office:document-content>')
(ccts / "antigo.doc").write_bytes(b"\xd0\xcf\x11\xe0 binario")
(ccts / "lixo.bin").write_bytes(bytes(range(256)) * 10)
(ccts / "~$temporario.docx").write_bytes(b"x")

# ------------------------------------------------ API simulada
chamadas = []

def falsa_transcricao(jpeg, instrucoes):
    chamadas.append("visao")
    assert jpeg[:2] == bytes([0xFF, 0xD8])  # JPEG válido
    return "CLAUSULA 1 - PISO R$ 1.111,00 (transcrito)", False

def falso_conversar(system, mensagem, max_tokens=16000):
    chamadas.append(system[:30])
    if system == consulta.SISTEMA_RESPOSTA:
        codigos = re.findall(r"\[(D\d+-\d+)\]", mensagem)
        assert codigos, "documentos sem códigos de trecho"
        return f"O piso é R$ 2.345,67 [{codigos[0]}][{codigos[-1]}] e algo inventado [D99-1] (D1-1, D1-2).", False
    if system == consulta.SISTEMA_SINTESE:
        marcadores = re.findall(r"\[\d+\]", mensagem)
        return "Síntese " + " ".join(marcadores[:3]), False
    raise AssertionError(system)

def falso_json(system, mensagem, max_tokens=16000):
    chamadas.append(system[:30])
    if system == indice.SISTEMA_METADADOS:
        nome = mensagem.splitlines()[0]
        return {"titulo": f"Ficha de {nome}", "vigencia_inicio": "2026-01-01", "categorias_abrangidas": "texto em vez de lista"}
    if system == consulta.SISTEMA_SELECAO:
        return {"codigos": re.findall(r'"codigo": "(C\d+)"', mensagem) + ["C999"]}
    raise AssertionError(system)

ia.transcrever_imagem = falsa_transcricao
ia.conversar = falso_conversar
ia.conversar_json = falso_json

print("unidades:", [n for n, _ in indice.descobrir_unidades()])
print("situacao antes:", indice.situacao())
r = indice.atualizar_indice(lambda m, f: None)
print("resumo:", {k: v for k, v in r.items() if k != "avisos"})
for a in r["avisos"]: print("  aviso:", a)
for c in indice.carregar_indice():
    print(f"  {c['nome']:35} paginas={[p['rotulo'] for p in c['paginas']]}")
print("situacao depois:", indice.situacao())
n = len(chamadas); indice.atualizar_indice(); print("reindexar sem mudanças -> chamadas novas:", len(chamadas) - n)

res = consulta.responder("Qual o piso dos vigilantes?", [{"pergunta": "oi", "resposta": "olá"}])
print("\nRESPOSTA:", res.texto[:200]); print("fontes:", [(f["n"], f["cct"], f["pagina"]) for f in res.fontes][:4], "...", len(res.fontes))

consulta.LIMITE_TOKENS_POR_CHAMADA = 200   # força análise em lotes
res = consulta.responder("Compare todos os pisos")
print("\nLOTES:", res.texto, "| fontes:", [(f["n"], f["pagina"]) for f in res.fontes])

shutil.rmtree(tmp)
print("\nOK")
