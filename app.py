import io
import re
import zipfile
import pdfplumber
from pypdf import PdfReader, PdfWriter
import pytesseract
import streamlit as st

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


def extrair_texto(page_plumber):
    """Tenta extrair texto nativo; se falhar ou for muito curto, usa OCR."""
    texto = page_plumber.extract_text() or ""
    texto_limpo = texto.strip().replace("\n", "").replace(" ", "")

    # Fallback OCR apenas se a página parecer escaneada/imagem
    if len(texto.strip()) < 30 or len(texto_limpo) < 10:
        try:
            img = page_plumber.to_image(resolution=300).original
            try:
                texto_ocr = pytesseract.image_to_string(img, lang="por")
            except Exception:
                texto_ocr = pytesseract.image_to_string(img, lang="eng")
            if len(texto_ocr.strip()) > len(texto.strip()):
                texto = texto_ocr
        except Exception:
            pass
    return texto


def extrair_nome(texto):
    """Extrai o nome baseado no padrão exato do Termo de Ciência."""
    if not texto:
        return None

    # Normaliza espaços e quebras de linha em um único espaço
    texto = re.sub(r"\s+", " ", texto)

    # PADRÃO 1 (principal): "Eu, NOME COMPLETO colaborador da empresa"
    # O nome está sempre em maiúsculas antes da palavra "colaborador"
    padrao = r"Eu,\s+([A-Z][A-Z\s]+?)\s+colaborador\b"
    match = re.search(padrao, texto, re.IGNORECASE)
    if match:
        nome = match.group(1).strip()
        if 3 < len(nome) < 60:
            return nome

    # PADRÃO 2 (fallback): campo "Nome:" no rodapé do documento
    padrao2 = r"Nome:\s*([A-Z][A-Z\s]+?)(?:\s+RG:|\s+MAT:|$)"
    match = re.search(padrao2, texto)
    if match:
        nome = match.group(1).strip()
        if 3 < len(nome) < 60:
            return nome

    # PADRÃO 3 (último recurso): qualquer coisa após "Eu," em maiúsculas
    padrao3 = r"Eu,\s+([A-Z][A-Z\s]+)"
    match = re.search(padrao3, texto)
    if match:
        nome = match.group(1).strip()
        # Corta se encontrar palavras que não fazem parte do nome
        for stop in ["COLABORADOR", "DECLARO", "TERMO", "EMPRESA", "INSTRUÇÕES"]:
            nome = nome.split(stop)[0].strip()
        if 3 < len(nome) < 60:
            return nome

    return None


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
