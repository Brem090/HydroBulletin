@echo off
chcp 65001 > nul
python main.py --batch-folder demo_data\week3 --date 12.07.2026 --map --level-chart --discharge-chart --chart-station 79726 --start-date 11.07.2026 --end-date 12.07.2026
pause
