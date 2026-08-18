import io
import re
import zipfile
import unicodedata
from difflib import SequenceMatcher

import pdfplumber
from pypdf import PdfReader, PdfWriter
import pytesseract
import streamlit as st
from PIL import Image, ImageEnhance


# =============================================================================
# CONFIGURAÇÃO DA PÁGINA
# =============================================================================

st.set_page_config(
    page_title="Separador Inteligente de Documentos",
    layout="wide",
)

st.title("📄 Separador Inteligente de Documentos")
st.write(
    "Envie um PDF consolidado. Você pode manter o modo original, que separa "
    "uma página por arquivo, ou usar o modo inteligente para juntar as páginas "
    "que pertencem ao mesmo documento/FDS."
)


# =============================================================================
# ESTADO DA SESSÃO
# =============================================================================

if "zip_buffer" not in st.session_state:
    st.session_state.zip_buffer = None

if "relatorio" not in st.session_state:
    st.session_state.relatorio = []

if "tipo_resultado" not in st.session_state:
    st.session_state.tipo_resultado = None

if "grupos" not in st.session_state:
    st.session_state.grupos = []


# =============================================================================
# SOBRENOMES COMUNS
# =============================================================================

SOBRENOMES_COMUNS = [
    "ALVES", "SILVA", "SANTOS", "OLIVEIRA", "SOUZA", "LIMA", "COSTA",
    "RODRIGUES", "FERREIRA", "ALMEIDA", "CARVALHO", "GOMES", "MARTINS",
    "MOREIRA", "XAVIER", "BRAGA", "BRITO", "CARDOSO", "CASTRO", "DIAS",
    "DUARTE", "FREITAS", "MACHADO", "MARQUES", "MENDES", "NASCIMENTO",
    "PEREIRA", "RIBEIRO", "ROCHA", "ARAUJO", "BASILIO", "VILHENA", "VALE",
    "LIZ", "PAIXAO", "ASSINK", "EUGENIO", "FRANCA", "AMARILDO", "GERALDO",
    "PHILIPE", "ALENCAR", "ALÍPIO", "FILHO", "NETO", "JUNIOR", "MATOS",
    "CECILIO", "ALLISON", "DE", "DA", "DO", "DOS", "DAS",
]


# =============================================================================
# FUNÇÕES ORIGINAIS - OCR E COLABORADOR
# =============================================================================


def preprocessar_imagem_ocr(img):
    """Melhora contraste e binariza a imagem antes do Tesseract."""
    img = img.convert("L")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    img = img.point(lambda x: 0 if x < 128 else 255, "1")
    return img


def extrair_texto(page_plumber):
    """Tenta extrair texto nativo; se estiver ruim, usa OCR."""

    try:
        texto = page_plumber.extract_text() or ""
    except Exception:
        texto = ""

    texto_limpo = texto.strip().replace("\n", "").replace(" ", "")

    if len(texto.strip()) < 30 or len(texto_limpo) < 10:
        try:
            img = page_plumber.to_image(resolution=300).original
            img = preprocessar_imagem_ocr(img)

            try:
                texto_ocr = pytesseract.image_to_string(
                    img,
                    lang="por",
                    config="--psm 6",
                )
            except Exception:
                texto_ocr = pytesseract.image_to_string(
                    img,
                    lang="eng",
                    config="--psm 6",
                )

            if len(texto_ocr.strip()) > len(texto.strip()):
                texto = texto_ocr

        except Exception:
            pass

    return texto


def normalizar_texto(texto):
    """Normaliza espaços e quebras de linha."""
    return re.sub(r"\s+", " ", texto or "").strip()


def cortar_stopwords(nome):
    """Corta o nome na primeira stop-word encontrada."""

    stops = [
        "COLABORADOR", "DECLARO", "TERMO", "EMPRESA", "INSTRUÇÕES",
        "CPF", "RG", "MATRICULA", "MAT", "FUNÇÃO", "CARGO", "PORTADOR",
        "AUTORIZO", "ASSINATURA", "NOTA", "INDIANÓPOLIS",
    ]

    for stop in stops:
        pattern = re.compile(
            r"\b" + re.escape(stop) + r"\b",
            re.IGNORECASE,
        )
        nome = pattern.split(nome)[0].strip()

    return nome


def desgrudar_sobrenomes(nome):
    """Tenta separar palavras grudadas pelo OCR, ex.: GERALDOALVES."""

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
    """Extrai o nome do colaborador usando múltiplos padrões."""

    if not texto:
        return None

    texto = normalizar_texto(texto)

    padrao_nome_rodape = (
        r"Nome[:\s]+([A-Z][A-Z\s]*?)"
        r"(?=\s*\b(?:RG|MAT|CPF|Assinatura|AUTORIZO)\b|$)"
    )

    match = re.search(
        padrao_nome_rodape,
        texto,
        re.IGNORECASE,
    )

    if match:
        nome = cortar_stopwords(match.group(1).strip())
        nome = desgrudar_sobrenomes(nome)

        if 5 < len(nome) < 60 and len(nome.split()) >= 2:
            return nome

    padrao_eu = (
        r"Eu[,\s]+([A-Z][A-Z\s]*?)"
        r"(?:\s*(?:colaborador|declaro|portador|autorizo|da\s+empresa)|$)"
    )

    match = re.search(
        padrao_eu,
        texto,
        re.IGNORECASE,
    )

    if match:
        nome = cortar_stopwords(match.group(1).strip())
        nome = desgrudar_sobrenomes(nome)

        if 5 < len(nome) < 60 and len(nome.split()) >= 2:
            return nome

    padrao_fallback = r"Eu[,\s]+([A-Z][A-Z\s]*?)(?:\s+DECLARO|$)"

    match = re.search(
        padrao_fallback,
        texto,
        re.IGNORECASE,
    )

    if match:
        nome = cortar_stopwords(match.group(1).strip())
        nome = desgrudar_sobrenomes(nome)

        if 5 < len(nome) < 60 and len(nome.split()) >= 2:
            return nome

    return None


# =============================================================================
# NOVAS FUNÇÕES - IDENTIFICAÇÃO DE FDS/DOCUMENTOS MULTIPÁGINAS
# =============================================================================


def normalizar_para_comparacao(texto):
    """Normaliza texto para comparações mais tolerantes a OCR."""

    if not texto:
        return ""

    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(
        char for char in texto
        if not unicodedata.combining(char)
    )

    texto = texto.upper()
    texto = re.sub(r"[^A-Z0-9À-ÿ\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()


def extrair_produto(texto):
    """
    Procura o nome do produto em formatos comuns de FDS/FISPQ.

    Não depende apenas de 'Produto:' porque existem documentos que usam:
    - Nome do produto:
    - Produto:
    - Identificador GHS do produto:
    - um nome do produto no cabeçalho da primeira página
    """

    if not texto:
        return None

    # -------------------------------------------------------------------------
    # Primeiro: campos explícitos.
    # -------------------------------------------------------------------------

    linhas = [
        linha.strip()
        for linha in texto.splitlines()
        if linha.strip()
    ]

    padroes_linha = [
        r"^Nome\s+do\s+produto\s*[:\-]\s*(.+)$",
        r"^Produto\s*[:\-]\s*(.+)$",
        r"^Identificador\s+GHS\s+do\s+produto\s*[:\-]\s*(.+)$",
    ]

    for linha in linhas[:40]:
        linha_limpa = re.sub(r"\s+", " ", linha).strip()

        for padrao in padroes_linha:
            match = re.search(
                padrao,
                linha_limpa,
                re.IGNORECASE,
            )

            if match:
                produto = match.group(1).strip(" .:-")

                if 3 <= len(produto) <= 120:
                    return produto

    # Alguns documentos escrevem o produto dentro da frase
    # "Identificação do produto".
    padroes_identificacao = [
        # Alguns PDFs colocam o valor no meio da frase:
        # "Identificação do GEL DECAPANTE produto:"
        r"Identifica[cç][aã]o\s+do\s+(.{3,120}?)\s+produto\s*[:\-]",
        r"Identifica[cç][aã]o\s+do\s+produto\s*[:\-]?\s*(.+?)(?=\s+(?:1\.2\.|Detalhes do fornecedor|Uso do produto|$))",
        r"Identificador\s+GHS\s+do\s+produto\s*[:\-]\s*(.+?)(?=\s+(?:C[oó]digo|Outras maneiras|Tipo do produto|Descri[cç][aã]o|Usos|$))",
    ]

    texto_normalizado = normalizar_texto(texto)

    for padrao in padroes_identificacao:
        match = re.search(
            padrao,
            texto_normalizado,
            re.IGNORECASE,
        )

        if match:
            produto = match.group(1).strip(" .:-")

            if 3 <= len(produto) <= 120:
                return produto

    # -------------------------------------------------------------------------
    # Segundo: procura no texto corrido, caso o OCR tenha destruído as linhas.
    # -------------------------------------------------------------------------

    texto_normalizado = normalizar_texto(texto)

    padroes_texto = [
        r"\bNome\s+do\s+produto\s*[:\-]\s*(.{3,120}?)(?=\s+(?:Nome da Empresa|Nome da empresa|Uso do produto|Usos recomendados|2[.\-]|$))",
        r"\bProduto\s*[:\-]\s*(.{3,120}?)(?=\s+(?:Vers[aã]o|Data|Elaborad[ao]|Revis[aã]o|P[áa]gina|FDS|1[.\-]|$))",
        r"\bIdentificador\s+GHS\s+do\s+produto\s*[:\-]\s*(.{3,120}?)(?=\s+(?:C[oó]digo|Outras maneiras|Tipo do produto|Descri[cç][aã]o|Usos|$))",
    ]

    for padrao in padroes_texto:
        match = re.search(
            padrao,
            texto_normalizado,
            re.IGNORECASE,
        )

        if match:
            produto = match.group(1).strip(" .:-")

            if 3 <= len(produto) <= 120:
                return produto

    # -------------------------------------------------------------------------
    # Terceiro: alguns documentos colocam o produto diretamente no cabeçalho.
    # Usamos apenas as primeiras linhas para evitar pegar texto do meio.
    # -------------------------------------------------------------------------

    termos_proibidos = [
        "FICHA COM DADOS",
        "FICHA DE DADOS",
        "DATA:",
        "REVISÃO",
        "REVISAO",
        "VERSÃO",
        "VERSAO",
        "PÁGINA",
        "PAGINA",
        "SEÇÃO",
        "SECAO",
        "MECANOCHEMIE",
        "IND. QUIMICAS",
        "PRODUTOS AUTOMOTIVOS",
        "TRUCK & CAR",
        "CHESIQUIMICA",
    ]

    for linha in linhas[:12]:
        candidata = re.sub(r"\s+", " ", linha).strip()

        if len(candidata) < 5 or len(candidata) > 100:
            continue

        candidata_normalizada = normalizar_para_comparacao(candidata)

        if any(
            termo in candidata_normalizada
            for termo in [normalizar_para_comparacao(x) for x in termos_proibidos]
        ):
            continue

        if re.match(r"^(FDS|FISPQ)\s*[:\-]", candidata_normalizada):
            continue

        if re.search(r"\b(?:RUA|AVENIDA|ROD\.|CEP|TELEFONE|EMAIL|E-MAIL)\b", candidata_normalizada):
            continue

        if re.search(r"\b(?:DATA|REVISAO|VERSAO|PAGINA)\b", candidata_normalizada):
            continue

        # Código puro, telefone ou data não serve como nome do produto.
        if re.fullmatch(r"[A-Z0-9\-_/ .]+", candidata) and not re.search(r"[A-Z]{4,}", candidata):
            continue

        # O título da FDS normalmente é uma linha curta, com letras,
        # e costuma estar logo no cabeçalho.
        quantidade_letras = len(re.findall(r"[A-Za-zÀ-ÿ]", candidata))
        quantidade_digitos = len(re.findall(r"\d", candidata))

        if quantidade_letras < 5:
            continue

        if quantidade_digitos > quantidade_letras:
            continue

        if len(candidata.split()) >= 2:
            return candidata

    return None


def extrair_marcadores_pagina(texto):
    """
    Retorna todos os possíveis marcadores de página encontrados.

    Exemplos aceitos:
        Página 2 de 13
        Página: 2 de 13
        Página 2/13
        2/13
        1/131

    A função devolve uma lista para podermos escolher o marcador correto
    quando existir algum '3/13' perdido dentro do texto.
    """

    if not texto:
        return []

    texto = normalizar_texto(texto)
    encontrados = []

    padroes_explicitos = [
        r"\bP[ÁA]GINA\s*:?\s*(\d{1,3})\s*(?:DE|/)\s*(\d{1,3})\b",
        r"\bP[ÁA]GINA(\d{1,3})\s*DE\s*(\d{1,3})\b",
    ]

    for padrao in padroes_explicitos:
        for match in re.finditer(
            padrao,
            texto,
            re.IGNORECASE,
        ):
            atual = int(match.group(1))
            total = int(match.group(2))

            if 1 <= atual <= total <= 999:
                encontrados.append((atual, total, True))

    # Caso comum de OCR: "Página: l de 13" ou "Página: ldel3".
    # Quando aparece "l3" entendemos como "13".
    padrao_ocr_primeira = r"\bP[ÁA]GINA\s*:?\s*[lI1]\s*(?:DE|d[eé])\s*([lI1]?)(\d{1,3})\b"

    for match in re.finditer(
        padrao_ocr_primeira,
        texto,
        re.IGNORECASE,
    ):
        prefixo = match.group(1)
        numero = match.group(2)

        if prefixo in {"l", "I", "1"} and len(numero) <= 2:
            total = int("1" + numero)
        else:
            total = int(numero)

        if 1 <= total <= 999:
            encontrados.append((1, total, True))

    # Por último, marcadores genéricos 1/131, 2/131 etc.
    # Eles têm prioridade menor porque podem aparecer em tabelas/textos.
    for match in re.finditer(
        r"(?<!\d)(\d{1,3})\s*/\s*(\d{1,3})(?!\d)",
        texto,
    ):
        atual = int(match.group(1))
        total = int(match.group(2))

        if 1 <= atual <= total <= 999:
            encontrados.append((atual, total, False))

    # Remove duplicados preservando a ordem.
    resultado = []
    vistos = set()

    for item in encontrados:
        chave = tuple(item)

        if chave not in vistos:
            vistos.add(chave)
            resultado.append(item)

    return resultado


def escolher_marcador_pagina(texto, pagina_pdf, grupo_atual=None):
    """
    Escolhe o melhor marcador entre os encontrados.

    A prioridade é:
    1. Marcador explícito "Página X/Y".
    2. Marcador que continue a sequência do grupo atual.
    3. Marcador com total compatível com o grupo atual.
    4. Marcador genérico mais plausível.
    """

    candidatos = extrair_marcadores_pagina(texto)

    if not candidatos:
        return None, None

    # -------------------------------------------------------------------------
    # Se já estamos dentro de um grupo, procuramos primeiro a continuação.
    # -------------------------------------------------------------------------

    if grupo_atual:
        ultima = grupo_atual.get("ultima_pagina_documento")
        total_esperado = grupo_atual.get("total_esperado")

        if ultima is not None:
            proxima = ultima + 1

            for atual, total, explicito in candidatos:
                if atual == proxima:
                    if total_esperado is None or total == total_esperado:
                        return atual, total

        # Mesmo que a sequência esteja ruim, se o total bater, é uma pista forte.
        if total_esperado is not None:
            compatíveis = [
                (atual, total, explicito)
                for atual, total, explicito in candidatos
                if total == total_esperado
            ]

            if compatíveis:
                compatíveis.sort(
                    key=lambda item: (not item[2], abs(item[0] - (ultima or item[0] + 1)))
                )
                return compatíveis[0][0], compatíveis[0][1]

    # -------------------------------------------------------------------------
    # Página inicial: preferimos um marcador explícito.
    # -------------------------------------------------------------------------

    explicitos = [
        item for item in candidatos
        if item[2]
    ]

    if explicitos:
        # Na primeira página do PDF, o mais provável é 1/X.
        primeiros = [item for item in explicitos if item[0] == 1]

        if primeiros:
            return primeiros[0][0], primeiros[0][1]

        return explicitos[0][0], explicitos[0][1]

    # -------------------------------------------------------------------------
    # Fallback genérico.
    # -------------------------------------------------------------------------

    candidatos.sort(
        key=lambda item: (item[0] != pagina_pdf, item[1])
    )

    return candidatos[0][0], candidatos[0][1]


def produtos_parecidos(produto1, produto2):
    """Compara dois nomes de produto de forma tolerante a OCR."""

    if not produto1 or not produto2:
        return False

    p1 = normalizar_para_comparacao(produto1)
    p2 = normalizar_para_comparacao(produto2)

    if not p1 or not p2:
        return False

    if p1 == p2:
        return True

    palavras1 = {
        palavra
        for palavra in p1.split()
        if len(palavra) >= 3
    }

    palavras2 = {
        palavra
        for palavra in p2.split()
        if len(palavra) >= 3
    }

    if not palavras1 or not palavras2:
        return False

    intersecao = len(
        palavras1.intersection(palavras2)
    )

    menor = min(
        len(palavras1),
        len(palavras2),
    )

    if (intersecao / menor) >= 0.60:
        return True

    # Segunda tentativa: compara o texto inteiro.
    # Isso ajuda quando o OCR muda uma ou duas letras, por exemplo:
    # JOTUN -> JOFUN.
    similaridade = SequenceMatcher(
        None,
        p1,
        p2,
    ).ratio()

    return similaridade >= 0.82


def criar_grupos_documentos(paginas_info):
    """
    Agrupa as páginas de documentos multipágina.

    Regra principal:
    - se houver "Página 1/X", começa um novo documento;
    - se houver "Página 2/X", "3/X" etc. em sequência, continua;
    - se não houver marcador, usa produto como apoio;
    - se o OCR falhar no meio, não quebra o documento sem motivo.
    """

    grupos = []
    grupo_atual = None

    for info in paginas_info:
        numero_pagina = info["pagina"]
        produto = info["produto"]

        # O marcador é escolhido considerando o grupo atual.
        pagina_doc, total_doc = escolher_marcador_pagina(
            info["texto"],
            numero_pagina,
            grupo_atual,
        )

        info["pagina_doc"] = pagina_doc
        info["total_doc"] = total_doc

        # ---------------------------------------------------------------------
        # Primeiro grupo
        # ---------------------------------------------------------------------

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

        ultima = grupo_atual.get("ultima_pagina_documento")
        esperado = grupo_atual.get("total_esperado")

        # ---------------------------------------------------------------------
        # Regra mais forte: Página 1 = novo documento.
        # Nunca deixamos a comparação do produto sobrescrever essa regra.
        # ---------------------------------------------------------------------

        if pagina_doc == 1 and ultima != 0:
            continuar = False

        # ---------------------------------------------------------------------
        # Continuação explícita X/X.
        # ---------------------------------------------------------------------

        elif pagina_doc is not None and ultima is not None:
            if pagina_doc == ultima + 1:
                # A sequência X, X+1 é mais confiável que o total do rodapé,
                # porque o OCR às vezes transforma 131 em 13, por exemplo.
                continuar = True

            elif esperado is not None and pagina_doc <= esperado and pagina_doc > ultima:
                # Pequenas falhas do OCR ainda podem ser aceitas se a página
                # continuar avançando dentro do documento.
                continuar = True

        # ---------------------------------------------------------------------
        # Se não há marcador confiável, usamos produto.
        # ---------------------------------------------------------------------

        elif pagina_doc is None:
            if (
                produto
                and grupo_atual["produto"]
                and produtos_parecidos(
                    grupo_atual["produto"],
                    produto,
                )
            ):
                continuar = True

            elif not produto:
                # OCR não achou produto nem marcador.
                # Mantemos no grupo atual porque é melhor preservar uma FDS
                # do que quebrá-la em uma página isolada.
                if esperado is None or len(grupo_atual["paginas"]) < esperado:
                    continuar = True

        # ---------------------------------------------------------------------
        # Quando há marcador, produto só serve como validação.
        # ---------------------------------------------------------------------

        if pagina_doc is not None and pagina_doc != 1:
            if not continuar:
                if (
                    produto
                    and grupo_atual["produto"]
                    and produtos_parecidos(
                        grupo_atual["produto"],
                        produto,
                    )
                ):
                    continuar = True

        # ---------------------------------------------------------------------
        # Adiciona ao grupo atual ou cria outro.
        # ---------------------------------------------------------------------

        if continuar:
            grupo_atual["paginas"].append(numero_pagina)

            if produto and not grupo_atual["produto"]:
                grupo_atual["produto"] = produto

            if pagina_doc is not None:
                grupo_atual["ultima_pagina_documento"] = pagina_doc

            if total_doc is not None:
                if grupo_atual["total_esperado"] is None:
                    grupo_atual["total_esperado"] = total_doc

        else:
            grupo_atual = {
                "produto": produto,
                "paginas": [numero_pagina],
                "total_esperado": total_doc,
                "ultima_pagina_documento": pagina_doc,
            }

            grupos.append(grupo_atual)

    return grupos


# =============================================================================
# FUNÇÕES DE ARQUIVO
# =============================================================================


def limpar_nome_arquivo(nome):
    """Remove caracteres inválidos para nomes de arquivo do Windows."""

    nome = normalizar_texto(nome)
    nome = re.sub(r'[\\/*?:"<>|]', "", nome)
    nome = nome.rstrip(" .")

    return nome or "DOCUMENTO_SEM_NOME"


def criar_nome_unico(nome_base, contadores):
    """Evita sobrescrever arquivos com o mesmo nome."""

    nome_base = limpar_nome_arquivo(nome_base)

    if nome_base not in contadores:
        contadores[nome_base] = 1
        return f"{nome_base}.pdf"

    contadores[nome_base] += 1

    return f"{nome_base} ({contadores[nome_base]}).pdf"


def criar_pdf_com_paginas(reader, paginas):
    """Cria um PDF novo com as páginas indicadas."""

    writer = PdfWriter()

    for pagina in paginas:
        writer.add_page(reader.pages[pagina - 1])

    pdf_out = io.BytesIO()
    writer.write(pdf_out)

    return pdf_out.getvalue()


# =============================================================================
# INTERFACE
# =============================================================================

arquivo = st.file_uploader(
    "Selecione o PDF consolidado",
    type=["pdf"],
)

if arquivo is not None:

    col1, col2 = st.columns(2)

    with col1:
        separar_paginas = st.button(
            "📄 Separar página por página",
            type="primary",
            use_container_width=True,
        )

    with col2:
        juntar_documentos = st.button(
            "📚 Juntar documentos multipáginas",
            use_container_width=True,
        )

    # =========================================================================
    # MODO ORIGINAL: 1 PÁGINA = 1 PDF
    # =========================================================================

    if separar_paginas:

        st.session_state.zip_buffer = None
        st.session_state.relatorio = []
        st.session_state.grupos = []
        st.session_state.tipo_resultado = "paginas"

        pdf_bytes = arquivo.getvalue()
        reader = PdfReader(io.BytesIO(pdf_bytes))
        total = len(reader.pages)

        zip_buffer = io.BytesIO()
        relatorio = []
        contadores = {}

        barra = st.progress(0)
        status = st.empty()

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf_plumber:
            with zipfile.ZipFile(
                zip_buffer,
                "w",
                zipfile.ZIP_DEFLATED,
            ) as zf:

                for idx in range(total):
                    page = pdf_plumber.pages[idx]
                    texto = extrair_texto(page)
                    nome = extrair_nome(texto)

                    status.text(
                        f"Processando página {idx + 1}/{total} — "
                        f"Nome: {nome or 'NÃO ENCONTRADO'}"
                    )

                    pdf_out = criar_pdf_com_paginas(
                        reader,
                        [idx + 1],
                    )

                    if nome:
                        nome_base = nome
                    else:
                        nome_base = (
                            f"NOME_NAO_ENCONTRADO_PAGINA_{idx + 1}"
                        )

                    nome_arquivo = criar_nome_unico(
                        nome_base,
                        contadores,
                    )

                    zf.writestr(
                        nome_arquivo,
                        pdf_out,
                    )

                    relatorio.append({
                        "Arquivo": nome_arquivo,
                        "Página Original": idx + 1,
                        "Nome Extraído": nome or "—",
                    })

                    barra.progress((idx + 1) / total)

        st.session_state.zip_buffer = zip_buffer.getvalue()
        st.session_state.relatorio = relatorio

        barra.empty()
        status.empty()

        st.success(
            f"✅ {total} páginas separadas em arquivos individuais."
        )

    # =========================================================================
    # NOVO MODO: JUNTAR DOCUMENTOS MULTIPÁGINAS
    # =========================================================================

    if juntar_documentos:

        st.session_state.zip_buffer = None
        st.session_state.relatorio = []
        st.session_state.grupos = []
        st.session_state.tipo_resultado = "documentos"

        pdf_bytes = arquivo.getvalue()
        reader = PdfReader(io.BytesIO(pdf_bytes))
        total = len(reader.pages)

        barra = st.progress(0)
        status = st.empty()

        paginas_info = []

        # ---------------------------------------------------------------------
        # Lê cada página apenas uma vez.
        # ---------------------------------------------------------------------

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf_plumber:

            for idx in range(total):
                page = pdf_plumber.pages[idx]
                texto = extrair_texto(page)
                produto = extrair_produto(texto)

                paginas_info.append({
                    "pagina": idx + 1,
                    "produto": produto,
                    "texto": texto,
                    "pagina_doc": None,
                    "total_doc": None,
                })

                status.text(
                    f"🔎 Analisando página {idx + 1}/{total} — "
                    f"{produto or 'documento sem nome identificado'}"
                )

                barra.progress((idx + 1) / total)

        # ---------------------------------------------------------------------
        # Agrupamento inteligente.
        # ---------------------------------------------------------------------

        status.text("🧠 Agrupando documentos...")
        grupos = criar_grupos_documentos(paginas_info)

        zip_buffer = io.BytesIO()
        relatorio = []
        contadores = {}

        with zipfile.ZipFile(
            zip_buffer,
            "w",
            zipfile.ZIP_DEFLATED,
        ) as zf:

            for numero, grupo in enumerate(grupos, start=1):

                produto = grupo["produto"]

                if produto:
                    nome_base = produto
                else:
                    nome_base = f"DOCUMENTO_{numero:03d}"

                nome_arquivo = criar_nome_unico(
                    nome_base,
                    contadores,
                )

                pdf_out = criar_pdf_com_paginas(
                    reader,
                    grupo["paginas"],
                )

                zf.writestr(
                    nome_arquivo,
                    pdf_out,
                )

                relatorio.append({
                    "Documento": numero,
                    "Arquivo": nome_arquivo,
                    "Produto": produto or "—",
                    "Página inicial": grupo["paginas"][0],
                    "Página final": grupo["paginas"][-1],
                    "Quantidade de páginas": len(grupo["paginas"]),
                    "Total indicado na FDS": (
                        grupo["total_esperado"]
                        or "—"
                    ),
                })

        st.session_state.zip_buffer = zip_buffer.getvalue()
        st.session_state.relatorio = relatorio
        st.session_state.grupos = grupos

        barra.empty()
        status.empty()

        st.success(
            f"✅ {len(grupos)} documentos identificados em "
            f"{total} páginas."
        )

        # ---------------------------------------------------------------------
        # Visualização do agrupamento.
        # ---------------------------------------------------------------------

        with st.expander(
            "🔎 Ver como o sistema agrupou os documentos",
            expanded=True,
        ):

            for numero, grupo in enumerate(grupos, start=1):
                produto = grupo["produto"] or "PRODUTO NÃO IDENTIFICADO"
                paginas = grupo["paginas"]
                esperado = grupo["total_esperado"]

                if esperado and len(paginas) == esperado:
                    icone = "✅"
                elif esperado:
                    icone = "⚠️"
                else:
                    icone = "🔎"

                st.write(
                    f"{icone} **Documento {numero}:** {produto} — "
                    f"páginas {paginas[0]} até {paginas[-1]} "
                    f"({len(paginas)} páginas)"
                )

                if esperado and len(paginas) != esperado:
                    st.warning(
                        f"Esse documento informa {esperado} páginas, "
                        f"mas foram agrupadas {len(paginas)} páginas. "
                        "Confira antes de usar o arquivo final."
                    )


# =============================================================================
# RESULTADO / DOWNLOAD
# =============================================================================

if st.session_state.zip_buffer is not None:

    st.divider()

    if st.session_state.tipo_resultado == "paginas":
        st.subheader("📋 Páginas separadas")
    else:
        st.subheader("📋 Documentos agrupados")

    with st.expander(
        "Ver relatório",
        expanded=True,
    ):
        st.dataframe(
            st.session_state.relatorio,
            use_container_width=True,
            hide_index=True,
        )

    if st.session_state.tipo_resultado == "paginas":
        nome_zip = "documentos_separados_paginas.zip"
        texto_botao = "⬇️ Baixar páginas separadas (.zip)"
    else:
        nome_zip = "documentos_agrupados.zip"
        texto_botao = "⬇️ Baixar documentos agrupados (.zip)"

    st.download_button(
        label=texto_botao,
        data=st.session_state.zip_buffer,
        file_name=nome_zip,
        mime="application/zip",
        use_container_width=True,
    )
