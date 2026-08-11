import io
import re
import zipfile
import pdfplumber
from pypdf import PdfReader, PdfWriter
import pytesseract
import streamlit as st

st.set_page_config(page_title="Separador Automático por Nome", layout="wide")
st.title("Separador Automático de Documentos por Colaborador")
st.write(
    "Envie o PDF consolidado. O sistema lerá cada página via OCR, identificará"
    " a troca de nomes e gerará um arquivo PDF individual para cada pessoa,"
    " nomeado apenas com o nome extraído."
)


def extrair_texto_com_ocr(page_plumber):
  """Extrai texto nativo da página ou força OCR via Tesseract se for imagem/escaneado."""
  texto = page_plumber.extract_text() or ""
  texto_limpo = texto.strip().replace("\n", "").replace(" ", "")

  # Se houver pouco ou nenhum texto extraível, aciona o OCR
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


def extrair_nome_colaborador(texto_pagina):
  """Identifica o nome da pessoa através de padrões genéricos de expressões regulares."""
  if not texto_pagina:
    return None

  padroes = [
      r"(?:NOME|PACIENTE|AVALIADO|COLABORADOR|CANDIDATO|EMPREGADO|TRABALHADOR|NOME\s+DO\s+TRABALHADOR|SR\(A\)|FUNCIONÁRIO)\s*[:\.]*\s*([A-ZÁÉÍÓÚÃÕÇÂÊÎÔÛa-záéíóúãõçâêîôû\s]{3,60})(?=\n|CPF|RG|DATA|SEXO|CARGO|EMPRESA|IDADE|SETOR|\.|$)",
      r"NOME\.{2,}\s*:\s*\d*[-–]?\s*([A-ZÁÉÍÓÚÃÕÇÂÊÎÔÛa-záéíóúãõçâêîôû\s]{3,60})(?=\n|CPF|DATA|SEXO|\.|$)",
      r"FUNCIONÁRIO\s*\(CÓDIGO\s*/\s*NOME\)\s*\n?\s*\d+\s*/\s*([A-ZÁÉÍÓÚÃÕÇÂÊÎÔÛa-záéíóúãõçâêîôû\s]{3,60})(?=\n|EMPRESA|RG|CPF|\.|$)",
      r"FUNCIONÁRIO:\s*\d+\s*-\s*([A-ZÁÉÍÓÚÃÕÇÂÊÎÔÛa-záéíóúãõçâêîôû\s]{3,60})(?=\n|UNIDADE|CNPJ|RG|CPF|\.|$)",
  ]

  palavras_proibidas = [
      "APRESENTOU",
      "DESEMPENHO",
      "RESULTADO",
      "EXAME",
      "DENTRO",
      "SOLICITANTE",
      "RELATOR",
      "LAUDO",
      "DECLARA",
      "AVALIADO",
      "CONCLUSAO",
      "CONCLUSÃO",
      "PROTOCOLO",
  ]

  for padrao in padroes:
    match = re.search(padrao, texto_pagina, re.IGNORECASE | re.MULTILINE)
    if match:
      nome = match.group(1).split("\n")[0].strip()
      stops = [
          "SEXO",
          "CARGO",
          "CPF",
          "RG",
          "DATA",
          "IDADE",
          "PIS",
          "CTPS",
          "CADASTRO",
          "ATEND",
          "UNIDADE",
          "SETOR",
          "EMPRESA",
          "CNPJ",
          "MÉDICO",
          "MEDICO",
          "PROTOCOLO",
          "CONVÊNIO",
          "CONVENIO",
          "EMISSÃO",
          "EMISSAO",
      ]
      for stop in stops:
        nome = re.split(rf"\b{stop}\b", nome, flags=re.IGNORECASE)[0]

      nome_limpo = re.sub(r'[\\/*?:"<>|]', "", nome)
      nome_final = re.sub(r"\s+", " ", nome_limpo).upper().strip()
      nome_final = re.sub(r"^\d+[\s\-–]+", "", nome_final)

      if len(nome_final) > 3 and not any(
          p in nome_final for p in palavras_proibidas
      ):
        return nome_final

  return None


def salvar_documento_no_zip(
    zip_file, paginas, nome_pessoa, contadores_nomes, relatorio
):
  """Escreve as páginas acumuladas em um único PDF renomeado dentro do arquivo ZIP."""
  if not paginas:
    return

  writer = PdfWriter()
  for page in paginas:
    writer.add_page(page)

  pdf_out = io.BytesIO()
  writer.write(pdf_out)

  nome_base = nome_pessoa if nome_pessoa else "NOME_NAO_ENCONTRADO"

  if nome_base in contadores_nomes:
    contadores_nomes[nome_base] += 1
    nome_arquivo_final = f"{nome_base} ({contadores_nomes[nome_base]}).pdf"
  else:
    contadores_nomes[nome_base] = 1
    nome_arquivo_final = f"{nome_base}.pdf"

  zip_file.writestr(nome_arquivo_final, pdf_out.getvalue())
  relatorio.append(
      {"Nome Gerado": nome_arquivo_final, "Qtd Páginas": len(paginas)}
  )


arquivo_enviado = st.file_uploader(
    "Selecione o arquivo PDF consolidado", type=["pdf"]
)

if arquivo_enviado is not None:
  if st.button("Separar e Renomear Páginas", type="primary"):
    reader_pypdf = PdfReader(arquivo_enviado)
    total_paginas = len(reader_pypdf.pages)

    zip_buffer = io.BytesIO()
    relatorio_processamento = []
    contadores_nomes = {}

    barra_progresso = st.progress(0)
    status_texto = st.empty()

    with pdfplumber.open(arquivo_enviado) as pdf_plumber:
      with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        paginas_acumuladas = []
        nome_atual = None

        for idx in range(total_paginas):
          page_plumber = pdf_plumber.pages[idx]
          texto_pagina = extrair_texto_com_ocr(page_plumber)
          nome_detectado = extrair_nome_colaborador(texto_pagina)

          status_texto.text(
              f"Lendo página {idx + 1} de {total_paginas}... (Detectado:"
              f" {nome_detectado or 'Continuação/Indefinido'})"
          )

          # Se encontrou um novo nome e já existiam páginas acumuladas de outra pessoa
          if nome_detectado and nome_atual and (nome_detectado != nome_atual):
            salvar_documento_no_zip(
                zip_file,
                paginas_acumuladas,
                nome_atual,
                contadores_nomes,
                relatorio_processamento,
            )
            paginas_acumuladas = []
            nome_atual = nome_detectado
          elif nome_detectado and not nome_atual:
            nome_atual = nome_detectado

          # Adiciona a página atual ao lote do documento do colaborador
          paginas_acumuladas.append(reader_pypdf.pages[idx])

          # Atualiza a barra de progresso visual
          barra_progresso.progress((idx + 1) / total_paginas)

        # Salva o último grupo de páginas pendente ao final do loop
        if paginas_acumuladas:
          salvar_documento_no_zip(
              zip_file,
              paginas_acumuladas,
              nome_atual,
              contadores_nomes,
              relatorio_processamento,
          )

    status_texto.empty()
    st.success(
        f"Processamento concluído! {len(relatorio_processamento)} arquivos"
        " separados gerados."
    )

    with st.expander("Mapeamento dos Arquivos Separados"):
      st.dataframe(relatorio_processamento, use_container_width=True)

    st.download_button(
        label="Baixar PDFs Separados (.zip)",
        data=zip_buffer.getvalue(),
        file_name="documentos_separados.zip",
        mime="application/zip",
    )
