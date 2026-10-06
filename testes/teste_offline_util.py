"""Gera um PDF simples com texto selecionável (sem dependências) para os testes."""


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
