"""Interface web do Agente SDR.

Ponto de entrada para a aplicação Streamlit. A interface se comunica com o
ConversationService para processar mensagens de leads.

Execução:
    streamlit run app.py

Requisitos:
    - Arquivo .env configurado
    - Dependências instaladas (pip install -r requirements.txt)
"""
from src.interface.streamlit_app import main

if __name__ == "__main__":
    main()
