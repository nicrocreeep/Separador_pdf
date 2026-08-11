import io
import re
import zipfile
import PyPDF2
import streamlit as st

# Configuração da página
st.set_page_config(page_title="Renomeador de Documentos", layout="centered")

st.title("Renomeador Automático de Termos de Ciência")
st.write(
    "Suba os PDFs sem classificação e baixe todos renomeados automaticamente em"
    " um arquivo ZIP."
)

# Área de upload (aceita múltiplos arquivos)
uploaded_files = st.file_uploader(
    "Selecione os PDFs", type="pdf", accept_multiple_files=True
)


def sanitize_filename(name: str) -> str:
  """Remove caracteres inválidos para nomes de arquivos no sistema operacional."""
  return re.sub(r'[\\/*?:"<>|]', "", name)


if uploaded_files:
  if st.button("Processar e Renomear", type="primary"):
    zip_buffer = io.BytesIO()
    processed_summary = []

    progress_bar = st.progress(0)
    total_files = len(uploaded_files)

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
      for idx, file in enumerate(uploaded_files):
        try:
          reader = PyPDF2.PdfReader(file)

          # Extrai o texto da primeira página (ou de todas caso a primeira venha vazia)
          text = ""
          for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
              text += extracted + "\n"

          # Regex otimizada: busca por "Nome:" e para antes de rótulos comuns (CPF, RG, Data, Cargo, etc.) ou quebra de linha
          match = re.search(
              r"(?:Nome|NOME):\s*([A-Za-zÀ-ÖØ-öø-ÿ\s]+?)(?=\s*(?:CPF|RG|Data|Cargo|Setor|\n|\r|$))",
              text,
          )

          if match:
            nome_extraido = match.group(1).strip()
            # Remove múltiplos espaços internos
            nome_extraido = " ".join(nome_extraido.split())
            # Sanitiza caracteres proibidoss
            nome_extraido = sanitize_filename(nome_extraido)

            if nome_extraido:
              novo_nome = f"DOCUMENTO_SEM_CLASSIFICACAO - {nome_extraido}.pdf"
            else:
              novo_nome = f"NOME_NAO_ENCONTRADO_{file.name}"
          else:
            novo_nome = f"NOME_NAO_ENCONTRADO_{file.name}"

          # Prevenção contra nomes duplicados no mesmo arquivo ZIP
          base_nome, ext = novo_nome.rsplit(".", 1)
          counter = 1
          nome_final_zip = novo_nome
          nomer_ja_usados = [item["Novo Nome"] for item in processed_summary]

          while nome_final_zip in nomer_ja_usados:
            nome_final_zip = f"{base_nome}_({counter}).{ext}"
            counter += 1

          file.seek(0)
          zip_file.writestr(nome_final_zip, file.read())

          processed_summary.append(
              {"Original": file.name, "Novo Nome": nome_final_zip}
          )

        except Exception as e:
          processed_summary.append(
              {"Original": file.name, "Novo Nome": f"ERRO: {str(e)}"}
          )

        # Atualiza a barra de progresso
        progress_bar.progress((idx + 1) / total_files)

    st.success("Arquivos processados com sucesso!")

    # Exibe visualização organizada dos arquivos processados
    with st.expander("Ver mapeamento dos arquivos renomeados"):
      st.dataframe(processed_summary, use_container_width=True)

    # Botão para baixar o arquivo ZIP
    st.download_button(
        label="Baixar PDFs Renomeados (.zip)",
        data=zip_buffer.getvalue(),
        file_name="termos_renomeados.zip",
        mime="application/zip",
    )
