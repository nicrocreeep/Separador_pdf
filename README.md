# Separador de PDF por Colaborador

App Streamlit que separa um PDF consolidado em arquivos individuais (1 por página), nomeando cada arquivo com o nome do colaborador extraído do texto.

## Deploy no Streamlit Cloud

1. Crie um repositório no GitHub com estes arquivos.
2. Conecte o repo ao [Streamlit Cloud](https://streamlit.io/cloud).
3. O `packages.txt` instala o Tesseract OCR automaticamente no servidor.

## Estrutura

```
├── app.py              # Código principal
├── requirements.txt    # Bibliotecas Python
└── packages.txt        # Dependências de sistema (Tesseract)
```

## Como usar

1. Faça upload do PDF consolidado.
2. Clique em **"Separar e Renomear Páginas"**.
3. Baixe o `.zip` com todos os PDFs individuais.
