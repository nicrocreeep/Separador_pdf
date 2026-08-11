import streamlit as st
import PyPDF2
import re
import io
import zipfile

# Configuração da página
st.set_page_config(page_title="Renomeador de Documentos", layout="centered")

st.title("Renomeador Automático de Termos de Ciência")
st.write("Suba os PDFs sem classificação e baixe todos renomeados automaticamente em um arquivo ZIP.")

# Área de upload (aceita múltiplos arquivos)
uploaded_files = st.file_uploader("Selecione os PDFs", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("Processar e Renomear"):
        # Cria um arquivo ZIP na memória para não precisar salvar na máquina do servidor
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file in uploaded_files:
                try:
                    # Lê o PDF
                    reader = PyPDF2.PdfReader(file)
                    text = reader.pages[0].extract_text()
                    
                    # Pira do Regex: Procura "Nome:" seguido de qualquer quantidade de espaços, 
                    # e captura letras (com acentos) e espaços até o final da linha.
                    match = re.search(r'Nome:\s*([A-Za-zÀ-ÖØ-öø-ÿ\s]+)', text)
                    
                    if match:
                        nome_extraido = match.group(1).strip()
                        # Limpa espaços extras no meio do nome se houver
                        nome_extraido = " ".join(nome_extraido.split())
                        
                        # Formata o novo nome do arquivo
                        novo_nome = f"DOCUMENTO_SEM_CLASSIFICACAO - {nome_extraido}.pdf"
                    else:
                        # Se der erro na leitura ou o documento for diferente, avisa no nome
                        novo_nome = f"NOME_NAO_ENCONTRADO_{file.name}"
                        
                    # Volta o ponteiro do arquivo para o início antes de colocar no ZIP
                    file.seek(0)
                    
                    # Salva o arquivo dentro do ZIP com o novo nome
                    zip_file.writestr(novo_nome, file.read())
                    
                except Exception as e:
                    st.error(f"Erro ao processar o arquivo {file.name}: {e}")
        
        st.success("Arquivos processados com sucesso! Clique abaixo para baixar.")
        
        # Botão para baixar o arquivo ZIP com tudo renomeado
        st.download_button(
            label="Baixar PDFs Renomeados (.zip)",
            data=zip_buffer.getvalue(),
            file_name="termos_renomeados.zip",
            mime="application/zip"
        )