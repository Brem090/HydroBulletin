@echo off
chcp 65001 > nul
python -m unittest discover -s tests -v
pause
