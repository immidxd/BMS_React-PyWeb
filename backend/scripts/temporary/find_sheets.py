#!/usr/bin/env python3
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Використовуємо робочі credentials
creds = ServiceAccountCredentials.from_json_keyfile_name(
    'secure_creds/newproject2024-419923-a5dba4c9f119.json', 
    ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
)
client = gspread.authorize(creds)

print('🔍 Шукаю файли з твого скріншота...')

# Файли з скріншота
files_to_find = ['Воркспейс', 'Замовлення']

for filename in files_to_find:
    try:
        sheet = client.open(filename)
        print(f'📄 {filename} - ЗНАЙДЕНО!')
        print(f'   📊 ID: {sheet.id}')
        print(f'   🔗 URL: {sheet.url}')
        
        # Перевіримо листи
        worksheets = sheet.worksheets()
        print(f'   📋 Листи: {[ws.title for ws in worksheets]}')
        
        # Перший лист
        if worksheets:
            ws = worksheets[0]
            print(f'   📏 Розмір: {ws.row_count}x{ws.col_count}')
            
            # Перші кілька рядків
            try:
                values = ws.get_all_values()[:3]  # перші 3 рядки
                for i, row in enumerate(values, 1):
                    print(f'   Рядок {i}: {row[:5]}{"..." if len(row) > 5 else ""}')
            except Exception as e:
                print(f'   ⚠️ Не вдалося отримати дані: {e}')
        
        print()
        
    except gspread.SpreadsheetNotFound:
        print(f'❌ {filename} - НЕ ЗНАЙДЕНО (файл не існує або немає доступу)')
    except Exception as e:
        print(f'⚠️ {filename} - помилка: {e}')
        print()

print('✅ Перевірка завершена!') 