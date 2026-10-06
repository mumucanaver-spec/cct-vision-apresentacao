"""Configurações do Assistente de CCTs."""

from pathlib import Path

PASTA_BASE = Path(__file__).resolve().parent
PASTA_CCTS = PASTA_BASE / "ccts"                       # coloque aqui os arquivos das CCTs
PASTA_DADOS = PASTA_BASE / "dados"
PASTA_INDICE = PASTA_DADOS / "indice"                  # uma ficha .json por CCT
PASTA_CACHE_TRANSCRICAO = PASTA_DADOS / "transcricoes"  # páginas já lidas pela IA

# API gratuita da NVIDIA (build.nvidia.com). A chave fica no arquivo .env (NVIDIA_API_KEY).
URL_API = "https://integrate.api.nvidia.com/v1"

# Modelos em ordem de preferência: se o primeiro estiver sobrecarregado, usa o seguinte.
MODELOS_TEXTO = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
]
MODELOS_VISAO = [  # leem páginas escaneadas e imagens (o raciocínio é desligado: 10x mais rápido)
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",  # ~10 s/página, a mais fiel nos testes
    "meta/llama-3.2-11b-vision-instruct",             # ~10 s/página
    "meta/llama-3.2-90b-vision-instruct",             # ~40 s/página (às vezes muito mais)
]

# Se a API estiver fora do ar, lê a página com o OCR do próprio Windows (instantâneo, mas
# menos preciso). Essas páginas são relidas pela IA na próxima atualização do índice.
USAR_OCR_DO_WINDOWS_COMO_RESERVA = True

# Quantidade máxima (estimada) de tokens de documentos enviados numa única chamada.
# Acima disso as CCTs são analisadas em lotes e as respostas parciais são combinadas.
LIMITE_TOKENS_POR_CHAMADA = 250_000

# Chamadas simultâneas à API. O plano gratuito limita as requisições por minuto (~40).
CHAMADAS_PARALELAS = 3
LEITURAS_DE_IMAGEM_PARALELAS = 5

# False: PDFs com texto selecionável são lidos localmente; só páginas escaneadas ou com
#        imagens grandes (ex.: tabela salarial colada como figura) vão para a IA.
# True:  toda página de PDF é lida pela IA de visão (mais fiel em tabelas complexas, bem mais lento).
TRANSCREVER_TODOS_OS_PDFS = False

# Resolução usada para transformar páginas escaneadas de PDF em imagem.
DPI_PAGINAS_ESCANEADAS = 200

# Mudar este número força a reindexação de todas as CCTs.
VERSAO_INDICE = 2
