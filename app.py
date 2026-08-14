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


def normalizar_texto(texto):
    """Normaliza espaços e quebras de linha em um único espaço."""
    return re.sub(r"\s+", " ", texto)


def extrair_nome(texto):
    """
    Extrai o nome do colaborador usando múltiplos padrões.
    Adicione novos padrões no final da lista se surgir um documento novo.
    """
    if not texto:
        return None

    texto = normalizar_texto(texto)

    # ============================================================
    # LISTA DE PADRÕES — ordem: do mais específico ao mais genérico
    # ============================================================
    padroes = [
        # 1. FICHA REGISTRO: "NOME FUNCIONÁRIO  ALLISON CECILIO DE MATOS"
        (r"NOME\s*FUNCION[ÁA]RIO\s+([A-Z][A-Z\s]+?)(?:\s+MATR[ÍI]CULA|\s+REGISTRO|$)", re.IGNORECASE),

        # 2. ASO: "2 - Nome: ALISSON CECILIO DE MATOS"  (ou "Nome:" em geral)
        (r"Nome\s*:\s*([A-Z][A-Z\s]+?)(?:\s+CPF|\s+Cargo|\s+Fun[çc][ãa]o|\s+Admiss[ãa]o|\s+Idade|$)", re.IGNORECASE),

        # 3. FICHA EPI: "COLABORADOR: ALLISON CECILIO DE MATOS"
        (r"COLABORADOR[:\s]+([A-Z][A-Z\s]+?)(?:\s+CHAPA|\s+Fun[çc][ãa]o|$)", re.IGNORECASE),

        # 4. CONTRATO: "NOME: ALLISON CECILIO DE MATOS FUNÇÃO: ..."
        (r"NOME[:\s]+([A-Z][A-Z\s]+?)(?:\s+FUN[ÇC][ÃA]O|\s+CTPS|\s+Endere[çc]o|$)", re.IGNORECASE),

        # 5. TERMO PARTICIPAÇÃO PROCESSO SELETIVO: "CANDIDATO: ALLISON CECILIO DE MATOS CPF:"
        (r"CANDIDATO[:\s]+([A-Z][A-Z\s]+?)(?:\s+CPF|$)", re.IGNORECASE),

        # 6. DECLARAÇÃO CONTATO ELETRÔNICO: "Funcionário: ALLISON CECILIO DE MATOS"
        (r"Funcion[áa]rio[:\s]+([A-Z][A-Z\s]+?)(?:\s+CPF|$)", re.IGNORECASE),

        # 7. DECLARAÇÃO ESCOLARIDADE: "Declaro ... que eu, ALLISON CECILIO DE MATOS, portador..."
        (r"Declaro.*?eu,\s*([A-Z][A-Z\s]+?)(?:,\s*portador|\s+CTPS|$)", re.IGNORECASE),

        # 8. AUTORIZAÇÕES / TERMOS DocuSign (padrão principal): "Eu, ALLISON CECILIO DE MATOS, CPF..."
        (r"Eu,\s*([A-Z][A-Z\s]+?)(?:,\s*CPF|\s+declaro|\s+portador|\s+autorizo|\s+colaborador|$)", re.IGNORECASE),

        # 9. AUTORIZAÇÃO USO IMAGEM: "Eu, ALLISON CECILIO DE MATOS, portador da Cédula..."
        (r"Eu,\s*([A-Z][A-Z\s]+?)(?:,\s*portador|$)", re.IGNORECASE),

        # 10. ORDEM DE SERVIÇO: "1.1 Nome : ALLISON CECILIO DE MATOS 1.2 CPF..."
        (r"1\.1\s*Nome\s*:\s*([A-Z][A-Z\s]+?)(?:\s+1\.2|\s+CPF|$)", re.IGNORECASE),

        # 11. ACORDO COMPENSAÇÃO: "Nome Completo: ALLISON CECILIO DE MATOS Portador..."
        (r"Nome\s*Completo[:\s]+([A-Z][A-Z\s]+?)(?:\s+Portador|\s+CPF|$)", re.IGNORECASE),

        # 12. RECEBIMENTO CARTÃO TICKET: "NOME ALLISON CECILIO DE MATOS" (tabela)
        (r"\|NOME\|\s*([A-Z][A-Z\s]+?)(?:\||$)", re.IGNORECASE),

        # 13. REG. INTEGRAÇÃO: "Nome: ALLISON CECILIO DE MATOS" (com quebra de linha possível)
        (r"Nome[:\s]+([A-Z][A-Z\s]+?)(?:\s+Fun[çc][ãa]o|\s+Encanador|$)", re.IGNORECASE),

        # 14. TERMO CIÊNCIA PRAZO ATESTADO: "Eu, ALLISON CECILIO DE MATOS, CPF..."
        # Já coberto pelo padrão 8, mas deixo explícito para facilitar manutenção futura
        (r"Eu,\s*([A-Z][A-Z\s]+?)(?:,\s*CPF|\s+colaborador|$)", re.IGNORECASE),

        # 15. TERMO SIGILO: "Eu, ALLISON CECILIO DE MATOS. Portador..."
        (r"Eu,\s*([A-Z][A-Z\s]+?)\.\s*Portador", re.IGNORECASE),

        # 16. TERMO RECEBIMENTO CÓDIGO CONDUTA: "Eu , ALLISON CECILIO DE MATOS , portador..."
        (r"Eu\s*,\s*([A-Z][A-Z\s]+?)\s*,\s*portador", re.IGNORECASE),

        # 17. FALLBACK genérico "Eu, NOME" (se nada acima pegar)
        (r"Eu,\s*([A-Z][A-Z\s]+?)(?:\s+COLABORADOR|\s+DECLARO|\s+TERMO|\s+EMPRESA|$)", re.IGNORECASE),
    ]

    for padrao, flags in padroes:
        match = re.search(padrao, texto, flags)
        if match:
            nome = match.group(1).strip()

            # Corte de segurança: remove palavras que não fazem parte do nome
            stop_words = ["COLABORADOR", "DECLARO", "TERMO", "EMPRESA",
                          "INSTRUÇÕES", "CPF", "RG", "MATRICULA", "FUNÇÃO",
                          "CARGO", "PORTADOR", "AUTORIZO"]
            for stop in stop_words:
                nome = nome.split(stop)[0].strip()

            # Validação: entre 5 e 60 chars, pelo menos 2 palavras
            if 5 < len(nome) < 60 and len(nome.split()) >= 2:
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
