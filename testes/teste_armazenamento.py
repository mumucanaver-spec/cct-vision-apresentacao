"""Testa o salvamento do acervo no GitHub contra uma imitação da API em memória."""
import base64, hashlib, io, json, os, shutil, sys, tarfile, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import armazenamento as arm


class Resp:
    def __init__(self, status, dados=None, bruto=b""):
        self.status_code = status
        self.content = bruto or (json.dumps(dados).encode() if dados is not None else b"")
        self.text = self.content.decode(errors="replace")
    def json(self): return json.loads(self.content)
    def iter_content(self, n):
        for i in range(0, len(self.content), n): yield self.content[i:i + n]


class GitHubFalso:
    def __init__(self):
        self.blobs, self.commits, self.arvores, self.topo = {}, {}, {}, None
        self.chamadas = []
        self.atrapalhar_uma_vez = False  # simula outro usuário salvando no meio

    def _sha(self, x): return hashlib.sha1(json.dumps(x, sort_keys=True, default=str).encode() + os.urandom(4)).hexdigest()

    def _novo_commit(self, arquivos, msg):
        arvore = self._sha(arquivos); self.arvores[arvore] = dict(arquivos)
        c = self._sha(msg); self.commits[c] = {"tree": arvore, "parent": self.topo}; self.topo = c
        return c

    def arquivos(self): return self.arvores[self.commits[self.topo]["tree"]] if self.topo else {}

    def __call__(self, metodo, url, headers=None, json=None, timeout=None, stream=False):
        assert headers["Authorization"] == "Bearer tok"
        caminho = url.split("/repos/eu/acervo", 1)[1]
        self.chamadas.append(f"{metodo} {caminho.split('/')[1] if caminho else ''}")
        if caminho == "":
            return Resp(200, {"default_branch": "main"})
        if caminho == "/git/ref/heads/main":
            return Resp(409, {"message": "Git Repository is empty."}) if not self.topo else \
                Resp(200, {"object": {"sha": self.topo}})
        if metodo == "PUT" and caminho == "/contents/LEIAME.md":
            self._novo_commit({"LEIAME.md": base64.b64decode(json["content"])}, json["message"])
            return Resp(201, {})
        if metodo == "POST" and caminho == "/git/blobs":
            sha = hashlib.sha1(json["content"].encode()).hexdigest(); self.blobs[sha] = base64.b64decode(json["content"])
            return Resp(201, {"sha": sha})
        if caminho.startswith("/git/commits/"):
            return Resp(200, {"tree": {"sha": self.commits[caminho.rsplit("/", 1)[1]]["tree"]}})
        if metodo == "POST" and caminho == "/git/trees":
            arquivos = dict(self.arvores[json["base_tree"]])
            for e in json["tree"]:
                if e["sha"] is None: arquivos.pop(e["path"])
                else: arquivos[e["path"]] = self.blobs[e["sha"]]
            sha = self._sha(arquivos); self.arvores[sha] = arquivos
            return Resp(201, {"sha": sha})
        if metodo == "POST" and caminho == "/git/commits":
            c = self._sha(json); self.commits[c] = {"tree": json["tree"], "parent": json["parents"][0]}
            return Resp(201, {"sha": c})
        if metodo == "PATCH" and caminho == "/git/refs/heads/main":
            if self.atrapalhar_uma_vez:
                self.atrapalhar_uma_vez = False
                self._novo_commit({**self.arquivos(), "dados/de_outro_usuario.txt": b"oi"}, "outro")
            if self.commits[json["sha"]]["parent"] != self.topo:
                return Resp(422, {"message": "Update is not a fast forward"})
            self.topo = json["sha"]; return Resp(200, {})
        if caminho == "/tarball/main":
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as t:
                for nome, dados in self.arquivos().items():
                    info = tarfile.TarInfo(f"eu-acervo-abc123/{nome}"); info.size = len(dados)
                    t.addfile(info, io.BytesIO(dados))
                info = tarfile.TarInfo("eu-acervo-abc123/../../fora.txt"); info.size = 1
                t.addfile(info, io.BytesIO(b"x"))  # tentativa de escrever fora da pasta: deve ser ignorada
            return Resp(200, bruto=buf.getvalue())
        raise AssertionError(f"{metodo} {caminho}")


def apontar_para(pasta: Path):
    arm.PASTA_BASE = pasta; arm.PASTAS_SINCRONIZADAS = (pasta / "ccts", pasta / "dados")


def conteudo_local(pasta: Path):
    return {p.relative_to(pasta).as_posix(): p.read_bytes() for p in pasta.rglob("*") if p.is_file()}


os.environ.update(GITHUB_TOKEN="tok", GITHUB_REPOSITORIO="eu/acervo")
gh = GitHubFalso(); arm.requests.request = gh
base = Path(tempfile.mkdtemp()); apontar_para(base)

assert arm.configurado()
# Arquivo que já estava na pasta local ANTES de existir acervo no GitHub (caso real que falhou):
(base / "ccts").mkdir(parents=True); (base / "ccts" / "ja estava aqui.pdf").write_bytes(b"%PDF local antigo")
print("1) repositório vazio -> baixados:", arm.baixar())

(base / "ccts" / "Fotos CCT").mkdir(parents=True); (base / "dados" / "indice").mkdir(parents=True)
(base / "ccts" / "cct1.pdf").write_bytes(b"%PDF fake " * 1000)
(base / "ccts" / "Fotos CCT" / "pag 1.jpg").write_bytes(bytes(range(256)) * 50)
(base / "dados" / "indice" / "a.json").write_text('{"titulo": "Comerciários"}', encoding="utf-8")
n, avisos = arm.sincronizar("primeiro envio")
print("2) primeiro envio:", n, "arquivos | no GitHub:", sorted(gh.arquivos()))
assert gh.arquivos()["ccts/Fotos CCT/pag 1.jpg"] == bytes(range(256)) * 50
assert gh.arquivos()["ccts/ja estava aqui.pdf"] == b"%PDF local antigo", "arquivo local anterior não foi enviado"
# Arquivo local que não veio do GitHub, presente na hora de baixar: também deve ser enviado depois.
(base / "ccts" / "so local.pdf").write_bytes(b"%PDF so local")
arm.baixar()
n, _ = arm.sincronizar("envia o que só existia localmente")
assert gh.arquivos()["ccts/so local.pdf"] == b"%PDF so local", "arquivo só local foi tratado como salvo"
print("   arquivos que já existiam localmente foram enviados:", n)

print("3) sem mudanças -> enviados:", arm.sincronizar("nada")[0])

(base / "dados" / "indice" / "a.json").write_text('{"titulo": "Comerciários 2026"}', encoding="utf-8")
(base / "ccts" / "cct1.pdf").unlink()
gh.atrapalhar_uma_vez = True
n, _ = arm.sincronizar("altera e remove")
print("4) alteração + exclusão com outro usuário salvando no meio:", n, "| no GitHub:", sorted(gh.arquivos()))
assert "ccts/cct1.pdf" not in gh.arquivos() and "dados/de_outro_usuario.txt" in gh.arquivos()
assert gh.arquivos()["dados/indice/a.json"] == '{"titulo": "Comerciários 2026"}'.encode()

# Servidor reiniciou: pasta nova e vazia
novo = Path(tempfile.mkdtemp()); apontar_para(novo); arm._salvos.clear()
print("5) reinício -> baixados:", arm.baixar())
esperado = {k: v for k, v in gh.arquivos().items() if k.startswith(("ccts/", "dados/"))}
assert conteudo_local(novo) == esperado, (sorted(conteudo_local(novo)), sorted(esperado))
assert not (novo.parent / "fora.txt").exists()
print("   conteúdo local idêntico ao do GitHub; caminho malicioso ignorado")
print("6) após baixar, nada a enviar:", arm.sincronizar("nada")[0])
shutil.rmtree(base); shutil.rmtree(novo)
print("OK")
