import io
import re
import zipfile
import pdfplumber
from pypdf import PdfReader, PdfWriter
import pytesseract
import streamlit as st
from PIL import Image, ImageEnhance

st.set_page_config(page_title="Separador por Colaborador", layout="wide")
st.title("Separador Automático de Documentos por Colaborador")
st.write(
    "Envie o PDF consolidado. O sistema extrairá o nome de cada página e gerará "
    "um PDF individual para cada colaborador (1 página = 1 arquivo)."
)

# Estado da sessão
if "zip_buffer" not in st.session_state:
    st.session_state.zip_buffer = None
if "relatorio" not in st.session_state:
    st.session_state.relatorio = []

# ─────────────────────────────────────────────────────────────────────────────
# SOBRENOMES COMUNS (para desgrudar quando OCR perde espaço entre palavras)
# ─────────────────────────────────────────────────────────────────────────────
SOBRENOMES_COMUNS = [
    "ALVES", "SILVA", "SANTOS", "OLIVEIRA", "SOUZA", "LIMA", "COSTA", "RODRIGUES",
    "FERREIRA", "ALMEIDA", "CARVALHO", "GOMES", "MARTINS", "MOREIRA", "XAVIER",
    "BRAGA", "BRITO", "CARDOSO", "CASTRO", "DIAS", "DUARTE", "FREITAS", "MACHADO",
    "MARQUES", "MENDES", "NASCIMENTO", "PEREIRA", "RIBEIRO", "ROCHA", "ARAUJO",
    "BASILIO", "VILHENA", "VALE", "LIZ", "PAIXAO", "ASSINK", "EUGENIO", "FRANCA",
    "AMARILDO", "GERALDO", "PHILIPE", "ALENCAR", "ALÍPIO", "FILHO", "NETO", "JUNIOR",
    "MATOS", "CECILIO", "ALLISON", "DE", "DA", "DO", "DOS", "DAS"
]

# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÕES AUXILIARES
# ─────────────────────────────────────────────────────────────────────────────

def preprocessar_imagem_ocr(img):
    """Melhora contraste e binariza a imagem antes do Tesseract."""
    img = img.convert("L")                     # escala de cinza
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)                # aumenta contraste
    img = img.point(lambda x: 0 if x < 128 else 255, "1")  # threshold
    return img


def extrair_texto(page_plumber):
    """Tenta extrair texto nativo; se falhar ou for muito curto, usa OCR."""
    texto = page_plumber.extract_text() or ""
    texto_limpo = texto.strip().replace("\n", "").replace(" ", "")

    # Fallback OCR apenas se a página parecer escaneada/imagem
    if len(texto.strip()) < 30 or len(texto_limpo) < 10:
        try:
            img = page_plumber.to_image(resolution=300).original
            img = preprocessar_imagem_ocr(img)
            try:
                texto_ocr = pytesseract.image_to_string(
                    img, lang="por", config="--psm 6"
                )
            except Exception:
                texto_ocr = pytesseract.image_to_string(
                    img, lang="eng", config="--psm 6"
                )
            if len(texto_ocr.strip()) > len(texto.strip()):
                texto = texto_ocr
        except Exception:
            pass
    return texto


def normalizar_texto(texto):
    """Normaliza espaços e quebras de linha em um único espaço."""
    return re.sub(r"\s+", " ", texto)


def cortar_stopwords(nome):
    """Corta o nome na primeira stop-word encontrada (palavra inteira apenas)."""
    stops = [
        "COLABORADOR", "DECLARO", "TERMO", "EMPRESA", "INSTRUÇÕES",
        "CPF", "RG", "MATRICULA", "MAT", "FUNÇÃO", "CARGO", "PORTADOR",
        "AUTORIZO", "ASSINATURA", "NOTA", "INDIANÓPOLIS"
    ]
    for stop in stops:
        # \b garante que só corta palavra inteira (não corta VIRGENS no meio)
        pattern = re.compile(r"\b" + re.escape(stop) + r"\b", re.IGNORECASE)
        nome = pattern.split(nome)[0].strip()
    return nome


def desgrudar_sobrenomes(nome):
    """Tenta separar palavras grudadas pelo OCR, ex: GERALDOALVES -> GERALDO ALVES."""
    palavras = nome.split()
    resultado = []
    for palavra in palavras:
        if len(palavra) <= 10:
            resultado.append(palavra)
            continue
        separado = False
        for sob in sorted(SOBRENOMES_COMUNS, key=len, reverse=True):
            if palavra.endswith(sob) and palavra != sob:
                prefixo = palavra[:-len(sob)]
                if len(prefixo) >= 3 and prefixo.isalpha():
                    resultado.append(prefixo)
                    resultado.append(sob)
                    separado = True
                    break
        if not separado:
            resultado.append(palavra)
    return " ".join(resultado)


def extrair_nome(texto):
    """
    Extrai o nome do colaborador usando múltiplos padrões.
    Prioriza o campo 'Nome:' do rodapé (mais confiável em OCR ruim).
    """
    if not texto:
        return None

    texto = normalizar_texto(texto)

    # ═══════════════════════════════════════════════════════════════════════
    # 1ª TENTATIVA: campo "Nome:" no rodapé do documento
    #    Geralmente mais limpo que o "Eu, NOME" no início.
    #    Usa \b (word boundary) para não parar em "RG" dentro de "VIRGENS".
    # ═══════════════════════════════════════════════════════════════════════
    padrao_nome_rodape = (
        r"Nome[:\s]+([A-Z][A-Z\s]*?)"
        r"(?=\s*\b(?:RG|MAT|CPF|Assinatura|AUTORIZO)\b|$)"
    )
    match = re.search(padrao_nome_rodape, texto, re.IGNORECASE)
    if match:
        nome = cortar_stopwords(match.group(1).strip())
        nome = desgrudar_sobrenomes(nome)
        if 5 < len(nome) < 60 and len(nome.split()) >= 2:
            return nome

    # ═══════════════════════════════════════════════════════════════════════
    # 2ª TENTATIVA: "Eu, NOME" com delimitadores flexíveis
    #    \s* (zero ou mais espaços) aceita quando OCR gruda: SILVAcolaborador
    # ═══════════════════════════════════════════════════════════════════════
    padrao_eu = (
        r"Eu[,\s]+([A-Z][A-Z\s]*?)"
        r"(?:\s*(?:colaborador|declaro|portador|autorizo|da\s+empresa)|$)"
    )
    match = re.search(padrao_eu, texto, re.IGNORECASE)
    if match:
        nome = cortar_stopwords(match.group(1).strip())
        nome = desgrudar_sobrenomes(nome)
        if 5 < len(nome) < 60 and len(nome.split()) >= 2:
            return nome

    # ═══════════════════════════════════════════════════════════════════════
    # 3ª TENTATIVA: fallback genérico "Eu, NOME"
    # ═══════════════════════════════════════════════════════════════════════
    padrao_fallback = r"Eu[,\s]+([A-Z][A-Z\s]*?)(?:\s+DECLARO|$)"
    match = re.search(padrao_fallback, texto, re.IGNORECASE)
    if match:
        nome = cortar_stopwords(match.group(1).strip())
        nome = desgrudar_sobrenomes(nome)
        if 5 < len(nome) < 60 and len(nome.split()) >= 2:
            return nome

    return None
# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÕES PARA AGRUPAMENTO DE DOCUMENTOS MULTIPÁGINAS
# ─────────────────────────────────────────────────────────────────────────────

def extrair_produto(texto):
    """
    Tenta identificar o nome do produto/documento em FDS/FISPQ.
    Exemplos:
    Produto: Tinta Spray Uso Geral - Mundial Prime
    Nome do produto: ÁGUA DESMINERALIZADA
    """
    if not texto:
        return None

    texto_normalizado = normalizar_texto(texto)

    padroes = [
        r"\bProduto\s*:\s*(.{3,120}?)(?=\s+(?:Versão|Versao|Data|Elaborada|Revisão|Revisao|Página|Pagina|FDS|1[\.\-]))",
        r"\bNome do produto\s*:\s*(.{3,120}?)(?=\s+(?:Nome da Empresa|Nome da empresa|Uso do produto|Usos recomendados|2[\.\-]|$))",
    ]

    for padrao in padroes:
        match = re.search(
            padrao,
            texto_normalizado,
            re.IGNORECASE
        )

        if match:
            produto = match.group(1).strip(" .:-")

            if 3 <= len(produto) <= 120:
                return produto

    return None


def extrair_paginas_documento(texto):
    """
    Detecta padrões como:
    Página 1 de 13
    Página 1/13
    Página 01 de 06
    """

    if not texto:
        return None, None

    texto_normalizado = normalizar_texto(texto)

    padroes = [
        r"\bP[ÁA]GINA\s+(\d{1,2})\s*(?:DE|/)\s*(\d{1,2})\b",
        r"\bP[ÁA]GINA(\d{1,2})DE(\d{1,2})\b",
    ]

    for padrao in padroes:
        match = re.search(
            padrao,
            texto_normalizado,
            re.IGNORECASE
        )

        if match:
            pagina_atual = int(match.group(1))
            total_paginas = int(match.group(2))

            if 1 <= pagina_atual <= total_paginas <= 99:
                return pagina_atual, total_paginas

    return None, None


def normalizar_produto(produto):
    """
    Normaliza o nome do produto para facilitar comparação.
    """

    if not produto:
        return ""

    texto = produto.upper()

    texto = re.sub(
        r"[^A-Z0-9À-ÿ\s]",
        " ",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    return texto.strip()


def produtos_parecidos(produto1, produto2):
    """
    Compara dois produtos.
    Usa principalmente palavras em comum, que funciona melhor
    quando o OCR altera alguns caracteres.
    """

    if not produto1 or not produto2:
        return False

    p1 = normalizar_produto(produto1)
    p2 = normalizar_produto(produto2)

    if p1 == p2:
        return True

    palavras1 = set(
        palavra
        for palavra in p1.split()
        if len(palavra) >= 3
    )

    palavras2 = set(
        palavra
        for palavra in p2.split()
        if len(palavra) >= 3
    )

    if not palavras1 or not palavras2:
        return False

    intersecao = len(
        palavras1.intersection(palavras2)
    )

    menor = min(
        len(palavras1),
        len(palavras2)
    )

    return (
        intersecao / menor >= 0.60
    )


def criar_grupos_documentos(paginas_info):
    """
    Agrupa páginas consecutivas que pertencem ao mesmo documento.

    Retorna uma lista no formato:

    [
        {
            "produto": "...",
            "paginas": [1, 2, 3, 4]
        },
        ...
    ]
    """

    grupos = []

    grupo_atual = None

    for info in paginas_info:

        numero_pagina = info["pagina"]
        produto = info["produto"]
        pagina_doc = info["pagina_doc"]
        total_doc = info["total_doc"]

        # ---------------------------------------------------------------
        # Primeira página do processamento
        # ---------------------------------------------------------------

        if grupo_atual is None:

            grupo_atual = {
                "produto": produto,
                "paginas": [numero_pagina],
                "total_esperado": total_doc,
                "ultima_pagina_documento": pagina_doc,
            }

            grupos.append(grupo_atual)
            continue

        continuar = False

        # ---------------------------------------------------------------
        # Caso o documento informe explicitamente Página X/Y
        # ---------------------------------------------------------------

        if pagina_doc is not None:

            ultima = grupo_atual[
                "ultima_pagina_documento"
            ]

            esperado = (
                grupo_atual[
                    "total_esperado"
                ]
            )

            # Exemplo:
            # página anterior = 3/13
            # atual = 4/13
            if (
                ultima is not None
                and pagina_doc == ultima + 1
            ):
                continuar = True

            # Se mudou para Página 1, é praticamente certeza
            # que começou um documento novo.
            if pagina_doc == 1:
                continuar = False

            # Se o documento atual já chegou ao total informado,
            # não pode continuar.
            if (
                esperado is not None
                and ultima == esperado
            ):
                continuar = False

        # ---------------------------------------------------------------
        # Caso não exista marcador de página
        # ---------------------------------------------------------------

        else:

            if (
                grupo_atual["produto"]
                and produto
                and produtos_parecidos(
                    grupo_atual["produto"],
                    produto
                )
            ):
                continuar = True

        # ---------------------------------------------------------------
        # Confirma pelo produto
        # ---------------------------------------------------------------

        if produto and grupo_atual["produto"]:

            if produtos_parecidos(
                grupo_atual["produto"],
                produto
            ):
                continuar = True

            elif pagina_doc is None:
                continuar = False

        # ---------------------------------------------------------------
        # Se não conseguiu identificar nada na página,
        # mantém no grupo anterior.
        # Isso é importante para OCR ruim.
        # ---------------------------------------------------------------

        if (
            not produto
            and pagina_doc is None
        ):
            continuar = True

        # ---------------------------------------------------------------
        # Adiciona ao grupo ou cria outro
        # ---------------------------------------------------------------

        if continuar:

            grupo_atual["paginas"].append(
                numero_pagina
            )

            if produto and not grupo_atual["produto"]:
                grupo_atual["produto"] = produto

            if pagina_doc is not None:
                grupo_atual[
                    "ultima_pagina_documento"
                ] = pagina_doc

            if total_doc is not None:
                grupo_atual[
                    "total_esperado"
                ] = total_doc

        else:

            grupo_atual = {
                "produto": produto,
                "paginas": [numero_pagina],
                "total_esperado": total_doc,
                "ultima_pagina_documento": pagina_doc,
            }

            grupos.append(grupo_atual)

    return grupos

# ─────────────────────────────────────────────────────────────────────────────
# INTERFACE STREAMLIT
# ─────────────────────────────────────────────────────────────────────────────

arquivo = st.file_uploader("Selecione o PDF consolidado", type=["pdf"])

if arquivo is not None:

    col1, col2 = st.columns(2)

    with col1:
        separar_paginas = st.button(
            "📄 Separar página por página",
            type="primary",
            use_container_width=True
        )

    with col2:
        juntar_documentos = st.button(
            "📚 Juntar documentos parecidos",
            use_container_width=True
        )

    if separar_paginas:
        reader = PdfReader(arquivo)
        total = len(reader.pages)

        zip_buffer = io.BytesIO()
        relatorio = []
        contadores = {}

        barra = st.progress(0)
        status = st.empty()

        with pdfplumber.open(arquivo) as pdf_plumber:
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

                for idx in range(total):
                    page = pdf_plumber.pages[idx]
                    texto = extrair_texto(page)
                    nome = extrair_nome(texto)

                    status.text(
                        f"Processando página {idx + 1}/{total} — "
                        f"Nome: {nome or 'NÃO ENCONTRADO'}"
                    )

                    # Cria PDF com APENAS esta página
                    writer = PdfWriter()
                    writer.add_page(reader.pages[idx])
                    pdf_out = io.BytesIO()
                    writer.write(pdf_out)

                    # Define nome do arquivo
                    if nome:
                        nome_base = nome
                    else:
                        nome_base = f"NOME_NAO_ENCONTRADO_PAGINA_{idx + 1}"

                    # Remove caracteres inválidos para nome de arquivo
                    nome_base = re.sub(r'[\\/*?:"<>|]', "", nome_base)

                    # Evita sobrescrever caso existam nomes idênticos
                    if nome_base in contadores:
                        contadores[nome_base] += 1
                        nome_arquivo = f"{nome_base} ({contadores[nome_base]}).pdf"
                    else:
                        contadores[nome_base] = 1
                        nome_arquivo = f"{nome_base}.pdf"

                    zf.writestr(nome_arquivo, pdf_out.getvalue())
                    relatorio.append({
                        "Arquivo": nome_arquivo,
                        "Página Original": idx + 1,
                        "Nome Extraído": nome or "—",
                    })

                    barra.progress((idx + 1) / total)

        st.session_state.zip_buffer = zip_buffer.getvalue()
        st.session_state.relatorio = relatorio
        status.empty()
            # ─────────────────────────────────────────────────────────────────────────
    # MODO: JUNTAR DOCUMENTOS MULTIPÁGINAS
    # ─────────────────────────────────────────────────────────────────────────

    if juntar_documentos:

        reader = PdfReader(arquivo)
        total = len(reader.pages)

        barra = st.progress(0)
        status = st.empty()

        paginas_info = []

        # ==============================================================
        # ANALISA TODAS AS PÁGINAS
        # ==============================================================

        with pdfplumber.open(arquivo) as pdf_plumber:

            for idx in range(total):

                page = pdf_plumber.pages[idx]

                texto = extrair_texto(page)

                produto = extrair_produto(texto)

                pagina_doc, total_doc = (
                    extrair_paginas_documento(texto)
                )

                paginas_info.append({
                    "pagina": idx + 1,
                    "produto": produto,
                    "pagina_doc": pagina_doc,
                    "total_doc": total_doc,
                })

                if pagina_doc and total_doc:

                    status.text(
                        f"Analisando página {idx + 1}/{total} — "
                        f"{produto or 'PRODUTO NÃO ENCONTRADO'} — "
                        f"FDS {pagina_doc}/{total_doc}"
                    )

                else:

                    status.text(
                        f"Analisando página {idx + 1}/{total} — "
                        f"{produto or 'PRODUTO NÃO ENCONTRADO'}"
                    )

                barra.progress(
                    (idx + 1) / total
                )

        # ==============================================================
        # AGRUPA AS PÁGINAS
        # ==============================================================

        status.text(
            "🧠 Agrupando documentos..."
        )

        grupos = criar_grupos_documentos(
            paginas_info
        )

        # ==============================================================
        # CRIA O ZIP
        # ==============================================================

        zip_buffer = io.BytesIO()
        relatorio = []
        contadores = {}

        with zipfile.ZipFile(
            zip_buffer,
            "w",
            zipfile.ZIP_DEFLATED
        ) as zf:

            for numero, grupo in enumerate(
                grupos,
                start=1
            ):

                produto = grupo["produto"]

                if produto:

                    nome_base = produto

                else:

                    nome_base = (
                        f"DOCUMENTO_{numero}"
                    )

                # Remove caracteres inválidos
                nome_base = re.sub(
                    r'[\\/*?:"<>|]',
                    "",
                    nome_base
                ).strip()

                if not nome_base:
                    nome_base = (
                        f"DOCUMENTO_{numero}"
                    )

                # ======================================================
                # EVITA NOMES DUPLICADOS
                # ======================================================

                if nome_base in contadores:

                    contadores[nome_base] += 1

                    nome_arquivo = (
                        f"{nome_base} "
                        f"({contadores[nome_base]}).pdf"
                    )

                else:

                    contadores[nome_base] = 1

                    nome_arquivo = (
                        f"{nome_base}.pdf"
                    )

                # ======================================================
                # JUNTA AS PÁGINAS
                # ======================================================

                writer = PdfWriter()

                for pagina in grupo["paginas"]:

                    writer.add_page(
                        reader.pages[pagina - 1]
                    )

                pdf_out = io.BytesIO()

                writer.write(pdf_out)

                zf.writestr(
                    nome_arquivo,
                    pdf_out.getvalue()
                )

                # ======================================================
                # RELATÓRIO
                # ======================================================

                relatorio.append({
                    "Arquivo": nome_arquivo,
                    "Produto": produto or "—",
                    "Página inicial": grupo["paginas"][0],
                    "Página final": grupo["paginas"][-1],
                    "Quantidade de páginas": len(
                        grupo["paginas"]
                    ),
                    "Total indicado na FDS": (
                        grupo["total_esperado"]
                        or "—"
                    ),
                })

        # ==============================================================
        # SALVA RESULTADO
        # ==============================================================

        st.session_state.zip_buffer = (
            zip_buffer.getvalue()
        )

        st.session_state.relatorio = relatorio

        barra.empty()
        status.empty()

        st.success(
            f"✅ {len(grupos)} documentos agrupados "
            f"a partir de {total} páginas!"
        )

        # ==============================================================
        # MOSTRA COMO O SISTEMA AGRUPOU
        # ==============================================================

        with st.expander(
            "🔎 Ver agrupamento detectado",
            expanded=True
        ):

            for numero, grupo in enumerate(
                grupos,
                start=1
            ):

                produto = (
                    grupo["produto"]
                    or "PRODUTO NÃO IDENTIFICADO"
                )

                paginas = grupo["paginas"]

                st.write(
                    f"**Documento {numero}:** "
                    f"{produto} — "
                    f"páginas {paginas[0]} até {paginas[-1]} "
                    f"({len(paginas)} páginas)"
                )

# Exibe resultados (persiste após recarregar)
if st.session_state.zip_buffer is not None:
    st.success(
        f"✅ {len(st.session_state.relatorio)} arquivos PDF gerados com sucesso!"
    )

    with st.expander("📋 Mapeamento dos Arquivos Separados", expanded=True):
        st.dataframe(st.session_state.relatorio, use_container_width=True)

    st.download_button(
        label="⬇️ Baixar todos os PDFs (.zip)",
        data=st.session_state.zip_buffer,
        file_name="documentos_separados.zip",
        mime="application/zip",
    )
