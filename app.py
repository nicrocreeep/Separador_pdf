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
# INTERFACE STREAMLIT
# ─────────────────────────────────────────────────────────────────────────────

arquivo = st.file_uploader("Selecione o PDF consolidado", type=["pdf"])

if arquivo is not None:
    if st.button("Separar e Renomear Páginas", type="primary"):
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
