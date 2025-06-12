#!/usr/bin/env python3
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Використовуємо робочі credentials
creds = ServiceAccountCredentials.from_json_keyfile_name(
    'secure_creds/newproject2024-419923-a5dba4c9f119.json', 
    ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
)
client = gspread.authorize(creds)

print('🔍 Тестую правильні назви файлів з .env...')

# Правильні назви з .env файлу
files_to_check = ['Журнал', 'Замовлення']

for filename in files_to_check:
    try:
        sheet = client.open(filename)
        print(f'✅ {filename} - ЗНАЙДЕНО!')
        print(f'   📊 ID: {sheet.id}')
        print(f'   🔗 URL: {sheet.url}')
        
        # Перевіряємо листи
        worksheets = sheet.worksheets()
        print(f'   📋 Листи: {[ws.title for ws in worksheets[:5]]}')
        
        # Перший лист
        if worksheets:
            ws = worksheets[0]
            print(f'   📏 Розмір: {ws.row_count}x{ws.col_count}')
            
            # Перші кілька рядків
            try:
                headers = ws.row_values(1)[:10]  # перші 10 заголовків
                print(f'   🏷️  Заголовки: {headers}')
                
                # Перший рядок даних
                if ws.row_count > 1:
                    first_data_row = ws.row_values(2)[:10]
                    print(f'   📄 Перший рядок: {first_data_row}')
            except Exception as e:
                print(f'   ⚠️ Не вдалося отримати дані: {e}')
        
        print()
        
    except gspread.SpreadsheetNotFound:
        print(f'❌ {filename} - НЕ ЗНАЙДЕНО (файл не існує або немає доступу)')
    except Exception as e:
        print(f'⚠️ {filename} - помилка: {e}')
        print()

print('✅ Перевірка завершена!') 