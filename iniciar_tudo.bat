@echo off
REM Inicia o Sr. Agim: interface web (Streamlit) e bot do Telegram, cada um na sua janela.
REM Para parar: feche as janelas (ou Ctrl+C em cada uma).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
start "Sr. Agim - Streamlit" cmd /k "streamlit run app.py"
start "Sr. Agim - Telegram" cmd /k "python telegram_bot.py"
