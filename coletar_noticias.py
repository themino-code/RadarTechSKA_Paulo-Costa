#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Radar Tecnologico SKA - coletor diario de noticias.

O que este script faz, em ordem:
  1. Le a lista de fontes RSS em fontes.json.
  2. Baixa cada feed e junta todas as noticias encontradas.
  3. Compara com o noticias.json ja existente (pelo link da noticia) para
     descobrir quais sao realmente novas - noticias ja processadas em dias
     anteriores nunca sao reenviadas para a IA (evita gasto de cota e
     duplicidade no site).
  4. Para cada noticia nova, chama a API do Gemini pedindo, em um unico
     request estruturado (JSON): traducao/resumo em PT-BR, classificacao na
     tecnologia especifica da SKA (CAD, CAE, CAM, MES, APS, PLM,
     Manufatura Aditiva, Generative Design ou Automacao de Projetos),
     classificacao na industria do cliente, pitch comercial e um resumo de
     beneficios tecnicos.
  5. A categoria "guarda-chuva" (Design e Inovacao / Fabrica Inteligente /
     Governanca da Informacao) NAO e pedida a IA - e derivada automaticamente
     da tecnologia escolhida, usando o mapeamento TECH_TO_CATEGORY. Isso
     elimina qualquer inconsistencia entre tecnologia e categoria.
  6. Acrescenta as noticias novas ao historico existente em noticias.json
     (o arquivo cresce dia a dia - e o que alimenta o filtro de mes/dia do
     site) e descarta itens mais antigos que RETENTION_DAYS.

Variavel de ambiente necessaria:
  GEMINI_API_KEY - chave gratuita gerada em https://aistudio.google.com/apikey

Uso local (fora do GitHub Actions):
  pip install -r requirements.txt
  export GEMINI_API_KEY="sua_chave_aqui"
  python coletar_noticias.py
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

FONTES_PATH = os.path.join(os.path.dirname(__file__), "fontes.json")
NOTICIAS_PATH = os.path.join(os.path.dirname(__file__), "noticias.json")

# Modelo Gemini. "gemini-flash-latest" e um alias mantido pela Google que
# sempre aponta para o Flash gratuito mais recente - evita ter que atualizar
# este script sempre que um novo modelo sai. Se preferir travar numa versao
# especifica (mais previsivel, porem exige atualizacao manual no futuro),
# troque por algo como "gemini-2.5-flash".
GEMINI_MODEL = "gemini-flash-latest"

# Limite de noticias novas processadas por execucao, para nao estourar a
# cota gratuita da API (free tier gira em torno de 1000-1500 requisicoes/dia
# e ~10-15 por minuto, variando por modelo). O que sobrar fica pendente e e
# processado na proxima execucao diaria, sem duplicar.
MAX_NOVAS_POR_EXECUCAO = 40

# Pausa entre chamadas a IA, em segundos, para respeitar o limite por minuto.
PAUSA_ENTRE_CHAMADAS = 4.5

# Ha quantos dias manter noticias no historico do site.
RETENTION_DAYS = 400

# Quantos itens no maximo olhar por fonte (feeds RSS normalmente ja trazem
# so os mais recentes, isso e so uma trava de seguranca).
MAX_ITENS_POR_FONTE = 40

# ---------------------------------------------------------------------------
# Taxonomia SKA (a mesma usada no site)
# ---------------------------------------------------------------------------

TECH_TO_CATEGORY = {
    "CAD": ("design", "Design e Inovação"),
    "CAE": ("design", "Design e Inovação"),
    "Automação de Projetos": ("design", "Design e Inovação"),
    "Generative Design": ("design", "Design e Inovação"),
    "CAM": ("fabrica", "Fábrica Inteligente"),
    "Manufatura Aditiva": ("fabrica", "Fábrica Inteligente"),
    "MES": ("governanca", "Governança da Informação"),
    "APS": ("governanca", "Governança da Informação"),
    "PLM": ("governanca", "Governança da Informação"),
}

TECH_VALUES = list(TECH_TO_CATEGORY.keys())

# Pistas de portfolio SKA por tecnologia, so para dar contexto ao pitch
# comercial gerado pela IA (nao deve ser tratado como lista oficial e
# definitiva - ajuste conforme o portfolio real mudar).
SKA_PORTFOLIO_HINTS = {
    "CAD": "SolidWorks, CATIA, 3DEXPERIENCE",
    "CAE": "SolidWorks Simulation, 3DEXPERIENCE (SIMULIA)",
    "Automação de Projetos": "DriveWorks",
    "Generative Design": "3DEXPERIENCE (aplicativos de design generativo)",
    "CAM": "Hexagon WorkNC, Hexagon VISI, Lantek",
    "Manufatura Aditiva": "portfolio de manufatura aditiva da SKA",
    "MES": "SKA MES",
    "APS": "modulos de planejamento avancado do portfolio SKA",
    "PLM": "3DEXPERIENCE, DELMIA",
}

INDUSTRY_LABELS = {
    "industrial-equipment": "Industrial Equipment",
    "transportation-mobility": "Transportation & Mobility",
    "aerospace-defense": "Aerospace & Defense",
    "high-tech": "High Tech",
    "home-lifestyle": "Home & LifeStyle",
    "infra-energy-materials": "Infrastructure, Energy & Materials",
    "business-service": "Business Service",
    "cities-public-services": "Cities & Public Services",
    "aec": "Architecture, Engineering & Construction",
    "cpg-retail": "Consumer Packaged Goods – Retails",
    "life-sciences-healthcare": "Life Sciences & Healthcare",
}

INDUSTRY_IDS = list(INDUSTRY_LABELS.keys())

# Formato JSON Schema padrao (usado via response_json_schema) - tipos em
# minusculo, ao contrario do Schema proprio do Vertex/Gemini (que usa
# "OBJECT"/"STRING" em maiusculo e vai no campo response_schema).
RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["headline", "summary", "pitch", "techBenefits", "tech", "industryId"],
    "properties": {
        "headline": {
            "type": "string",
            "description": "Titulo da noticia traduzido/adaptado para o portugues do Brasil, direto e factual.",
        },
        "summary": {
            "type": "string",
            "description": "Resumo breve em portugues (1 a 2 frases) do que a noticia diz.",
        },
        "pitch": {
            "type": "string",
            "description": (
                "Pitch comercial em portugues, 1 a 2 frases, escrito para o time de "
                "pre-vendas da SKA usar em conversa com cliente: como essa noticia "
                "vira argumento de venda para o portfolio SKA."
            ),
        },
        "techBenefits": {
            "type": "string",
            "description": (
                "Resumo tecnico em portugues (1 a 2 frases) dos beneficios de "
                "engenharia/producao da tecnologia mencionada, sem tom comercial."
            ),
        },
        "tech": {"type": "string", "enum": TECH_VALUES},
        "industryId": {"type": "string", "enum": INDUSTRY_IDS},
    },
}

PROMPT_TEMPLATE = """Voce e um analista tecnico-comercial da SKA, empresa brasileira de \
tecnologia industrial (CAD, CAE, CAM, MES, APS, PLM e manufatura aditiva).

Leia a noticia abaixo (em ingles ou portugues) e responda SOMENTE com o JSON pedido, \
seguindo exatamente o schema fornecido. Nao invente fatos que nao estejam no texto \
original. Se a noticia nao tiver relacao clara com nenhuma tecnologia industrial da \
lista, escolha a mais proxima possivel dentro das opcoes permitidas.

Tecnologias possiveis: {tecnologias}
Industrias possiveis (escolha a que o assunto da noticia mais afeta): {industrias}

Contexto de portfolio SKA por tecnologia (use como inspiracao, nao cite features que \
nao existam): {portfolio}

--- NOTICIA ORIGINAL ---
Fonte: {fonte}
Titulo: {titulo}
Trecho/Resumo: {resumo}
------------------------
"""

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def log(msg):
    print(f"[radar-tech] {msg}", flush=True)


def carregar_json(path, padrao):
    if not os.path.exists(path):
        return padrao
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            log(f"AVISO: {path} nao e um JSON valido, usando valor padrao.")
            return padrao


def salvar_json(path, dados):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")


def limpar_html(texto):
    if not texto:
        return ""
    sem_tags = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", sem_tags).strip()


def id_da_noticia(link):
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def data_da_entrada(entry):
    for campo in ("published_parsed", "updated_parsed"):
        valor = getattr(entry, campo, None)
        if valor:
            return datetime(*valor[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Coleta dos feeds RSS
# ---------------------------------------------------------------------------


def coletar_entradas_novas(fontes, ja_processadas):
    novas = []
    for fonte in fontes:
        nome = fonte.get("nome", "Fonte desconhecida")
        url = fonte.get("url")
        if not url:
            continue
        try:
            feed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001 - queremos continuar mesmo se uma fonte falhar
            log(f"ERRO ao baixar feed de '{nome}': {e}")
            continue

        if getattr(feed, "bozo", False) and not feed.entries:
            log(f"AVISO: feed de '{nome}' parece invalido ou fora do ar (bozo=1, 0 entradas).")
            continue

        entradas = feed.entries[:MAX_ITENS_POR_FONTE]
        log(f"'{nome}': {len(entradas)} entradas no feed.")

        for entry in entradas:
            link = getattr(entry, "link", None)
            titulo = getattr(entry, "title", None)
            if not link or not titulo:
                continue
            if id_da_noticia(link) in ja_processadas:
                continue

            resumo = limpar_html(
                getattr(entry, "summary", None) or getattr(entry, "description", "")
            )[:800]

            novas.append(
                {
                    "id": id_da_noticia(link),
                    "fonte": nome,
                    "titulo_original": titulo,
                    "resumo_original": resumo,
                    "url": link,
                    "data": data_da_entrada(entry),
                }
            )

    # mais antigas primeiro, para processar em ordem cronologica
    novas.sort(key=lambda x: x["data"])
    return novas


# ---------------------------------------------------------------------------
# Classificacao via Gemini
# ---------------------------------------------------------------------------


def classificar_com_gemini(client, entrada):
    prompt = PROMPT_TEMPLATE.format(
        tecnologias=", ".join(TECH_VALUES),
        industrias=", ".join(f"{k} ({v})" for k, v in INDUSTRY_LABELS.items()),
        portfolio="; ".join(f"{k}: {v}" for k, v in SKA_PORTFOLIO_HINTS.items()),
        fonte=entrada["fonte"],
        titulo=entrada["titulo_original"],
        resumo=entrada["resumo_original"] or "(sem resumo disponivel)",
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
            temperature=0.4,
        ),
    )

    dados = json.loads(response.text)

    tech = dados["tech"]
    category_id, category_label = TECH_TO_CATEGORY[tech]
    industry_id = dados["industryId"]

    return {
        "id": entrada["id"],
        "categoryId": category_id,
        "categoryLabel": category_label,
        "industryId": industry_id,
        "industryLabel": INDUSTRY_LABELS[industry_id],
        "tech": tech,
        "headline": dados["headline"].strip(),
        "summary": dados["summary"].strip(),
        "pitch": dados["pitch"].strip(),
        "techBenefits": dados["techBenefits"].strip(),
        "source": entrada["fonte"],
        "date": entrada["data"].strftime("%d/%m/%Y"),
        "url": entrada["url"],
    }


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log("ERRO: variavel de ambiente GEMINI_API_KEY nao definida.")
        sys.exit(1)
    if genai is None:
        log("ERRO: pacote 'google-genai' nao instalado (pip install -r requirements.txt).")
        sys.exit(1)

    fontes = carregar_json(FONTES_PATH, [])
    if not fontes:
        log(f"ERRO: nenhuma fonte encontrada em {FONTES_PATH}.")
        sys.exit(1)

    noticias_existentes = carregar_json(NOTICIAS_PATH, [])
    ja_processadas = {n["id"] for n in noticias_existentes if "id" in n}

    log(f"{len(fontes)} fontes cadastradas, {len(noticias_existentes)} noticias no historico.")

    candidatas = coletar_entradas_novas(fontes, ja_processadas)
    log(f"{len(candidatas)} noticias novas encontradas nos feeds.")

    a_processar = candidatas[:MAX_NOVAS_POR_EXECUCAO]
    adiadas = len(candidatas) - len(a_processar)
    if adiadas > 0:
        log(f"{adiadas} noticias novas ficarao para a proxima execucao (limite por rodada).")

    client = genai.Client(api_key=api_key)

    novas_classificadas = []
    for i, entrada in enumerate(a_processar, start=1):
        log(f"[{i}/{len(a_processar)}] classificando: {entrada['titulo_original'][:70]}")
        try:
            item = classificar_com_gemini(client, entrada)
            novas_classificadas.append(item)
        except Exception as e:  # noqa: BLE001
            log(f"ERRO ao classificar '{entrada['titulo_original'][:50]}': {e}")
        time.sleep(PAUSA_ENTRE_CHAMADAS)

    todas = noticias_existentes + novas_classificadas

    limite_retencao = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    def dentro_da_retencao(item):
        try:
            d = datetime.strptime(item["date"], "%d/%m/%Y").replace(tzinfo=timezone.utc)
            return d >= limite_retencao
        except (KeyError, ValueError):
            return True

    todas = [item for item in todas if dentro_da_retencao(item)]

    def chave_ordenacao(item):
        try:
            return datetime.strptime(item["date"], "%d/%m/%Y")
        except (KeyError, ValueError):
            return datetime.min

    todas.sort(key=chave_ordenacao, reverse=True)

    salvar_json(NOTICIAS_PATH, todas)
    log(f"Concluido: {len(novas_classificadas)} noticias novas adicionadas, {len(todas)} no total.")


if __name__ == "__main__":
    main()
