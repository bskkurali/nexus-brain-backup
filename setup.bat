@echo off
echo Installing AiTrader Pro...
pip install -r requirements.txt
if not exist .env copy .env.example .env && notepad .env
echo Done! Run: python main.py --mode paper
pause
