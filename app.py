import io
import re
import zipfile
import pdfplumber
from pypdf import PdfReader, PdfWriter
import pytesseract
import streamlit as st
from PIL import Image, ImageEnhance, ImageFilter

st.set_page_config(page_title="Separador por Colaborador", layout="wide")
st.title("Separador Automático de Documentos por Colaborador")
st.write(
    "Envie o PDF consolidado. O sistema extrairá o nome de cada página, "
    "girará para o sentido correto e gerará um PDF individual para cada colaborador."
)

# Estado da sessão
if "zip_buffer" not in st.session_state:
    st.session_state.zip_buffer = None
if "relatorio" not in st.session_state:
    st.session_state.relatorio = []


def preprocessar_para_handwriting(img):
    """
    Pré-processa a imagem para melhorar OCR de texto manuscrito (caneta).
    """
    # Converter para escala de cinza
    img = img.convert("L")

    # Aumentar contraste para destacar a caneta azul/preta
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5)

    # Aumentar nitidez
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(2.0)

    # Binarização: transforma em preto e branco puro
    # Isso ajuda muito o Tesseract a ler handwriting
    img = img.point(lambda x: 0 if x < 150 else 255, "1")
    
    return img.convert("L")


def extrair_texto_completo(page_plumber):
    """
    Extrai texto da página inteira com OCR otimizado para handwriting.
    """
    try:
        img = page_plumber.to_image(resolution=300).original
        
        # Se estiver em paisagem, rotaciona para retrato para OCR
        w, h = img.size
        if w > h:
            img = img.rotate(-90, expand=True)
        
        img_proc = preprocessar_para_handwriting(img)
        
        # Configuração do Tesseract:
        # --psm 6 = Assume um bloco de texto único (melhor para formulários)
        # --oem 3 = Modo de engine padrão (LSTM + legacy)
        config = r"--oem 3 --psm 6"
        
        texto = pytesseract.image_to_string(img_proc, lang="por", config=config)
        return texto
    except Exception:
        return ""


def extrair_roi_nome(page_plumber):
    """
    Extrai texto apenas da região onde o nome costuma estar (após 'Eu').
    Foca no topo-esquerdo do documento onde o campo está localizado.
    """
    try:
        img = page_plumber.to_image(resolution=300).original
        
        # Se estiver em paisagem, rotaciona para retrato
        w, h = img.size
        if w > h:
            img = img.rotate(-90, expand=True)
            w, h = img.size
        
        # Crop na região do "Eu, [NOME]" — aproximadamente topo 15%-40%, esquerda 10%-90%
        # Ajuste esses valores se o layout mudar muito
        left = int(w * 0.05)
        top = int(h * 0.10)
        right = int(w * 0.90)
        bottom = int(h * 0.45)
        
        roi = img.crop((left, top, right, bottom))
        roi_proc = preprocessar_para_handwriting(roi)
        
        config = r"--oem 3 --psm 6"
        texto = pytesseract.image_to_string(roi_proc, lang="por", config=config)
        return texto
    except Exception:
        return ""


def limpar_texto_ocr(texto):
    """Normaliza o texto do OCR removendo ruídos comuns em handwriting."""
    if not texto:
        return ""
    # Remove múltiplos espaços
    texto = re.sub(r"\s+", " ", texto)
    # Corrige caracteres comuns que o OCR confunde
    texto = texto.replace("|", "I").replace("0", "O")  # em nomes, 0 raramente aparece
    return texto.strip()


def extrair_nome(texto_completo, texto_roi):
    """
    Extrai o nome usando múltiplas estratégias.
    Prioriza o ROI (região do nome), mas usa o texto completo como fallback.
    """
    # Tenta primeiro no ROI (mais preciso)
    for texto in [texto_roi, texto_completo]:
        if not texto:
            continue
            
        texto = limpar_texto_ocr(texto)
        texto_upper = texto.upper()
        
        # PADRÃO 1: "Eu, NOME COMPLETO, ocupante" ou "Eu NOME COMPLETO ocupante"
        # Regex flexível para handwriting: aceita letras maiúsculas/minúsculas e espaços
        padrao = r"Eu[,\s]+([A-Za-zÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÃÕÇáéíóúàèìòùâêîôûãõç\s]+?)[,\s]+(?:ocupante|de|cargo|declaro)"
        match = re.search(padrao, texto, re.IGNORECASE)
        if match:
            nome = match.group(1).strip()
            # Remove quebras e normaliza espaços
            nome = re.sub(r"\s+", " ", nome)
            # Remove palavras que não fazem parte do nome
            stopwords = ["OCUPANTE", "DO", "CARGO", "DECLARO", "ESTOU", "CIENTE", 
                        "TERMO", "COMPROMISSO", "POLITICA", "PROTECAO"]
            for sw in stopwords:
                nome = nome.split(sw)[0].strip()
            if len(nome) > 3:
                return nome
        
        # PADRÃO 2: "Nome: [NOME]" ou "Assinatura: [NOME]" (fallback)
        padrao2 = r"(?:Nome|Assinatura)[\s:]+([A-Za-zÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÃÕÇáéíóúàèìòùâêîôûãõç\s]+?)(?:\n|$|,|Data)"
        match = re.search(padrao2, texto, re.IGNORECASE)
        if match:
            nome = match.group(1).strip()
            nome = re.sub(r"\s+", " ", nome)
            if len(nome) > 3:
                return nome
        
        # PADRÃO 3: Qualquer coisa após "Eu," até a vírgula ou quebra
        padrao3 = r"Eu[,\s]+([A-Z][a-zA-ZÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÃÕÇáéíóúàèìòùâêîôûãõç\s]+)"
        match = re.search(padrao3, texto)
        if match:
            nome = match.group(1).strip()
            for sw in ["OCUPANTE", "DECLARO", "TERMO", "DE", "CARGO"]:
                nome = nome.split(sw)[0].strip()
            if len(nome) > 3:
                return nome
    
    return None


def extrair_primeiro_nome(nome_completo):
    """
    Se o nome completo for muito confuso, tenta retornar pelo menos o primeiro nome.
    """
    if not nome_completo:
        return None
    partes = nome_completo.split()
    if partes:
        return partes[0]
    return None


def rotacionar_pagina_retrato(page_obj):
    """
    Adiciona rotação de 90° no PDF para deixar em retrato.
    Retorna o page_obj modificado.
    """
    # /Rotate é a propriedade do PDF que define a rotação em graus
    # Se a página já tiver rotação, somamos
    rot_atual = page_obj.get("/Rotate", 0)
    page_obj[NameObject("/Rotate")] = NumberObject(rot_atual + 90)
    return page_obj


# Import necessário para manipulação de objetos PDF
from pypdf.generic import NameObject, NumberObject


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
                    page_plumber = pdf_plumber.pages[idx]
                    
                    # Extrai textos
                    texto_completo = extrair_texto_completo(page_plumber)
                    texto_roi = extrair_roi_nome(page_plumber)
                    
                    # Tenta extrair nome
                    nome = extrair_nome(texto_completo, texto_roi)
                    
                    # Fallback: se não achou nada, tenta usar o primeiro nome do ROI
                    if not nome and texto_roi:
                        nome = extrair_primeiro_nome(texto_roi.strip())
                    
                    # Limpa o nome para usar como nome de arquivo
                    if nome:
                        # Remove caracteres estranhos mas mantém acentos
                        nome_base = re.sub(r'[\\/*?:"<>|0-9]', "", nome)
                        nome_base = nome_base.strip()
                    else:
                        nome_base = f"NAO_IDENTIFICADO_PAGINA_{idx + 1}"

                    status.text(
                        f"Processando página {idx + 1}/{total} — "
                        f"Nome: {nome_base or 'NÃO ENCONTRADO'}"
                    )

                    # Pega a página do pypdf e rotaciona se necessário
                    page_pdf = reader.pages[idx]
                    
                    # Verifica se precisa rotacionar (se largura > altura)
                    # Usamos a caixa de mídia (media box) para verificar dimensões
                    mb = page_pdf.mediabox
                    largura = float(mb.width)
                    altura = float(mb.height)
                    
                    if largura > altura:
                        # Está em paisagem, rotaciona 90° para virar retrato
                        rot_atual = page_pdf.get("/Rotate", 0)
                        page_pdf[NameObject("/Rotate")] = NumberObject(rot_atual + 90)

                    # Cria PDF com APENAS esta página
                    writer = PdfWriter()
                    writer.add_page(page_pdf)
                    pdf_out = io.BytesIO()
                    writer.write(pdf_out)

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
                        "Texto ROI": (texto_roi[:100].replace("\n", " ") + "...") if texto_roi else "—",
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
