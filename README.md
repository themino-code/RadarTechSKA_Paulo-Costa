# SKA Radar Tech

Portal de notícias agregadas e curadas automaticamente para o time de Engenharia
de Aplicações e Pré-Vendas da SKA. Todo dia, um robô busca notícias de
tecnologia industrial em ~12 fontes RSS, usa a IA do Gemini para traduzir,
classificar (categoria SKA, indústria do cliente e tecnologia específica) e
gerar um pitch comercial e um resumo de benefícios técnicos para cada uma, e
publica tudo num site estático.

Custo de operação: **R$ 0,00** — GitHub Pages (hospedagem), GitHub Actions
(agendamento diário) e a camada gratuita da API do Gemini são suficientes para
o volume de notícias das fontes cadastradas.

## O que tem neste pacote

| Arquivo | Para que serve |
|---|---|
| `index.html` | O site em si (o mesmo design que já validamos, agora lendo dados reais). |
| `noticias.json` | Onde as notícias já classificadas ficam guardadas. Começa vazio (`[]`). |
| `fontes.json` | Lista de fontes RSS monitoradas. |
| `coletar_noticias.py` | O script que busca, classifica e grava as notícias novas. |
| `requirements.txt` | Dependências Python do script. |
| `.github/workflows/atualizar-noticias.yml` | O agendamento: roda o script todo dia às 6h (horário de Brasília) e publica o resultado. |

**Importante — o que eu não consigo fazer por aqui:** eu não tenho acesso à sua
conta do GitHub nem do Google nesta sessão, então os passos abaixo dentro
dessas contas precisam ser feitos por você. Também não consegui testar a
conexão com os feeds RSS a partir daqui porque este ambiente só acessa uma
lista restrita de sites — isso não quer dizer que algum feed esteja quebrado,
só que a validação real só vai acontecer quando o GitHub Actions rodar (os
servidores do GitHub têm acesso livre à internet).

## Passo a passo para colocar no ar

### 1. Criar o repositório

Crie um repositório novo no GitHub (ex.: `ska-radar-tech`). Como decidimos,
ele será **público** por enquanto — é a única forma de o GitHub Pages
funcionar de graça. Se no futuro quiser deixar privado, aí entra plano pago do
GitHub ou outra forma de hospedagem.

Suba todos os arquivos deste pacote para a raiz do repositório (mantendo a
pasta `.github/workflows/` como está).

### 2. Gerar a chave do Gemini

Acesse [aistudio.google.com/apikey](https://aistudio.google.com/apikey), faça
login com uma conta Google e gere uma chave de API gratuita.

### 3. Cadastrar a chave como segredo do repositório

No repositório: **Settings → Secrets and variables → Actions → New repository
secret**.
Nome: `GEMINI_API_KEY`
Valor: a chave que você gerou no passo 2.

Isso mantém a chave fora do código-fonte — o workflow lê ela em tempo de
execução.

### 4. Ativar o GitHub Pages

No repositório: **Settings → Pages → Source: Deploy from a branch → Branch:
`main` / pasta `/ (root)`**. Salve. O GitHub mostra a URL do site (algo como
`https://<seu-usuario>.github.io/ska-radar-tech/`) — pode levar um ou dois
minutos para ficar no ar na primeira vez.

### 5. Rodar a coleta pela primeira vez

Nesse ponto o site já está publicado, mas `noticias.json` ainda está vazio.
Vá na aba **Actions** do repositório, clique no workflow "Atualizar noticias
do Radar Tech" e depois em **Run workflow** (isso dispara ele manualmente,
sem esperar o horário agendado). Acompanhe o log: ele mostra quantas fontes
leu, quantas notícias novas achou e se alguma fonte falhou.

Se der tudo certo, um commit automático vai atualizar `noticias.json` e o
site (após o Pages reprocessar, ~1 min) já mostra as primeiras notícias.

### 6. Deixar rodando sozinho

A partir daqui não precisa fazer mais nada — o workflow roda todo dia às 6h
(horário de Brasília) sozinho, sempre acrescentando as notícias novas ao
histórico (por isso o site tem o filtro de mês/dia: ele vai guardando o que
já foi publicado). Notícias com mais de ~400 dias são descartadas
automaticamente para o arquivo não crescer para sempre — dá para ajustar isso
em `RETENTION_DAYS`, no topo do `coletar_noticias.py`.

## Ajustes que você pode querer fazer

- **Trocar/adicionar fontes RSS**: edite `fontes.json` (formato `{"nome":
  "...", "url": "..."}`). Não precisa mexer em mais nada.
- **Mudar o horário da coleta diária**: edite o `cron` em
  `.github/workflows/atualizar-noticias.yml` (o horário ali é em UTC).
- **Quantas notícias processar por rodada**: `MAX_NOVAS_POR_EXECUCAO` no
  script, hoje em 40 — existe para não estourar a cota gratuita do Gemini se
  um dia aparecerem muitas notícias novas de uma vez.
- **Trocar o modelo do Gemini**: `GEMINI_MODEL` no script usa
  `gemini-flash-latest` (sempre o Flash gratuito mais recente). Se preferir
  travar numa versão fixa, troque por um ID específico.

## Por que a categoria SKA nunca aparece "errada"

Ao contrário do protótipo visual (onde eu classifiquei as notícias de exemplo
à mão), o script **não pede à IA a categoria genérica** (Design e Inovação /
Fábrica Inteligente / Governança da Informação) — ele pede só a tecnologia
específica (CAD, CAE, CAM, MES, APS, PLM, Manufatura Aditiva, Generative
Design ou Automação de Projetos) e deriva a categoria automaticamente a
partir dela, usando o mapeamento fixo `TECH_TO_CATEGORY` no início do script.
Isso elimina de raiz qualquer chance de uma notícia de PLM cair em "Fábrica
Inteligente" por engano, por exemplo.

## Testando localmente (opcional)

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="sua_chave_aqui"
python coletar_noticias.py
```

Depois abra `index.html` num servidor local simples (não pode ser
`file://` direto, porque o navegador bloqueia o `fetch` do `noticias.json`
nesse modo):

```bash
python -m http.server 8000
# depois acesse http://localhost:8000/
```
