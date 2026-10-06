# Assistente de CCTs

Responde perguntas sobre Convenções e Acordos Coletivos de Trabalho usando a IA
**gratuita** da NVIDIA (modelos Nemotron). Basta escrever a pergunta: o programa descobre
quais CCTs se aplicam, lê o texto integral delas e responde citando o trecho e a página de
cada informação.

Pode ser usado de dois jeitos:
- **Online (recomendado):** um site com senha, acessado de qualquer computador ou celular,
  sem instalar nada. As CCTs ficam guardadas num repositório privado do GitHub.
- **No computador:** com Python instalado, rodando localmente.

## Uso

1. Entre com **seu nome** e a senha. O nome separa o seu histórico de conversas.
2. Em **➕ Adicionar CCTs**, envie **arquivos** ou uma **pasta inteira** (cada arquivo vira uma
   CCT; cada subpasta vira uma CCT). Se forem várias fotos da mesma convenção, preencha
   **"Juntar tudo numa única CCT"**.
3. Faça as perguntas. Cada conversa fica no histórico da barra lateral, agrupada por data:
   clique para reabrir e continuar com o mesmo contexto, 🗑️ para excluir, ou
   **✏️ Nova conversa** para começar outra.
4. Em **📚 CCTs indexadas** estão as CCTs do acervo, com detalhes e opção de excluir.

**CCTs repetidas:** arquivos com conteúdo idêntico são recusados no envio. Quando arquivos
diferentes são a mesma CCT (versões de anos diferentes, cópia registrada e não registrada,
resumo e texto completo), vale sempre a **mais atual**; empatando a vigência, a mais completa.
As demais aparecem como "Deixadas de lado" e não são usadas nas respostas. Termos aditivos e
circulares de reajuste não contam como repetição, porque complementam a convenção.

## Formatos aceitos

| Formato | Como é lido |
|---|---|
| PDF com texto selecionável | Direto (instantâneo) |
| PDF escaneado, ou página com tabela colada como imagem | Modelo de visão (cerca de 10 s por página, 5 páginas ao mesmo tempo) |
| Imagens: JPG, PNG, TIFF (inclusive com várias páginas), BMP, WEBP, GIF, HEIC... | Modelo de visão |
| Word (.docx), LibreOffice (.odt), RTF, HTML, TXT, CSV, MD | Direto |
| Word antigo (.doc) | Direto na versão online (antiword); no computador, salve como .docx |

Cada página lida pela IA fica guardada, então nunca é lida duas vezes.

## Publicar na internet (Streamlit Community Cloud, gratuito)

Arquivos envolvidos: `requirements.txt` (bibliotecas Python), `packages.txt` (leitor de .doc)
e `.streamlit/secrets.toml.exemplo` (modelo das configurações secretas).

1. Crie dois repositórios **privados** no GitHub: um com este programa (o `.gitignore` já
   exclui chaves, CCTs e dados) e um vazio para o acervo (ex.: `assistente-cct-acervo`).
2. Gere um token do GitHub (github.com/settings/tokens/new) com o escopo **repo**.
3. Em https://share.streamlit.io entre com a conta do GitHub e clique em **Create app**:
   - Repository: o repositório do programa; Branch: `main`; Main file path: `app.py`.
   - Em **Advanced settings**, escolha Python 3.12 e cole em **Secrets** o conteúdo de
     `.streamlit/secrets.toml.exemplo` preenchido.
4. Clique em **Deploy**. O endereço do site aparece no topo (ex.: `assistente-cct.streamlit.app`).

O servidor gratuito "dorme" depois de alguns dias sem acesso; a primeira visita depois disso
leva cerca de 1 minuto para acordá-lo, e o acervo é recarregado do GitHub automaticamente.

## Uso no computador

1. Dê dois cliques em **`instalar.bat`** (precisa do Python instalado).
2. Abra o arquivo **`.env`** e coloque `NVIDIA_API_KEY=nvapi-...` (chave gratuita de https://build.nvidia.com).
3. Coloque as CCTs na pasta **`ccts`** (uma subpasta = uma CCT em vários arquivos) e dê dois
   cliques em **`iniciar.bat`**.

## Modelos usados (`config.py`)

| Função | Principal | Reservas (usadas na hora se a principal estiver sobrecarregada) |
|---|---|---|
| Respostas e fichas | `nvidia/nemotron-3-ultra-550b-a55b` | `nvidia/nemotron-3-super-120b-a12b` |
| Leitura de imagens | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` (sem raciocínio) | `meta/llama-3.2-11b-vision-instruct`, `meta/llama-3.2-90b-vision-instruct` |

No computador com Windows, se a API inteira estiver fora do ar, as páginas são lidas pelo OCR
do próprio Windows. Essas páginas são relidas pela IA na próxima atualização.

## Como funciona

1. **Indexação** (`indice.py`): o texto de cada página é extraído e a IA preenche uma ficha
   da CCT com sindicatos, categorias, cidades, vigência, data-base e principais cláusulas e valores.
2. **Seleção** (`consulta.py`): a cada pergunta, a IA lê as fichas e escolhe as CCTs relevantes.
3. **Resposta**: as CCTs escolhidas são enviadas na íntegra, divididas em trechos com códigos
   (ex.: `[D1-12]`). A IA cita os códigos e o programa os transforma em fontes verificáveis,
   com CCT, página e texto exato. Códigos inventados pela IA são descartados.
4. **Sem limite de quantidade**: se o material escolhido não couber numa única chamada
   (cerca de 250 mil tokens, ou 8 a 10 CCTs), ele é analisado em lotes, e as respostas
   parciais são combinadas numa resposta final.
5. **Acervo online** (`armazenamento.py`): ao iniciar, o site baixa o acervo do GitHub e,
   depois de cada mudança, envia só o que mudou.

## Testes

- `testes\teste_offline.py`: todo o fluxo com a IA simulada.
- `testes\teste_armazenamento.py`: o salvamento no GitHub, com uma imitação da API.
- `testes\teste_real.py`: 3 CCTs fictícias com a API de verdade (use `python -X utf8`).
- `testes\benchmark_leitura.py`: compara velocidade e precisão da leitura de imagens.

> As respostas são informativas e se baseiam no texto das CCTs. Para decisões
> jurídicas, confirme com um profissional.
