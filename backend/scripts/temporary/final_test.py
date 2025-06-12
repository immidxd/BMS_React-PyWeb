#!/usr/bin/env python3
from googlesheets_pars import get_google_sheet_client
import os

# Отримуємо клієнт через оригінальну функцію
client = get_google_sheet_client()

if not client:
    print("❌ Не вдалося створити Google Sheets клієнт")
    exit(1)

print("🔍 Тестую доступ до файлів через оригінальний клієнт...")

# Отримуємо назви з .env
spreadsheet_name = os.getenv('GOOGLE_SHEETS_DOCUMENT_NAME', 'Журнал')
orders_name = os.getenv('GOOGLE_SHEETS_DOCUMENT_NAME_ORDERS', 'Замовлення')

files_to_test = [spreadsheet_name, orders_name]

for filename in files_to_test:
    try:
        sheet = client.open(filename)
        print(f"✅ {filename} - ДОСТУП Є!")
        print(f"   📊 ID: {sheet.id}")
        print(f"   🔗 URL: {sheet.url}")
        
        # Отримуємо листи
        worksheets = sheet.worksheets()
        print(f"   📋 Листи ({len(worksheets)}): {[ws.title for ws in worksheets[:5]]}")
        
        # Перевіряємо перший лист
        if worksheets:
            ws = worksheets[0]
            print(f"   📏 Розмір першого листа: {ws.row_count}x{ws.col_count}")
            
            # Заголовки
            try:
                headers = ws.row_values(1)[:8]
                print(f"   🏷️  Заголовки: {headers}")
                
                # Лічимо кількість рядків з даними
                data_rows = len([row for row in ws.get_all_values()[1:] if any(cell.strip() for cell in row)])
                print(f"   📊 Рядків з даними: {data_rows}")
                
            except Exception as e:
                print(f"   ⚠️ Помилка читання даних: {e}")
        
        print()
        
    except Exception as e:
        print(f"❌ {filename} - помилка: {e}")
        print()

print("✅ Тестування завершено!") 