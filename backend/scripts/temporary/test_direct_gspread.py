#!/usr/bin/env python3
import gspread
from google.oauth2.service_account import Credentials

print('🔍 Тестую прямий gspread доступ з Service Account...')

# Завантажуємо credentials через новий google-auth метод
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

try:
    # Спробуємо новий метод з google-auth
    credentials = Credentials.from_service_account_file(
        'newproject2024-419923-8aec36a3b0ce.json',
        scopes=SCOPES
    )
    
    client = gspread.authorize(credentials)
    print('✅ gspread авторизація успішна з новими credentials!')
    
    # Тестуємо доступ до файлів
    target_files = ['Журнал', 'Замовлення']
    
    for filename in target_files:
        try:
            sheet = client.open(filename)
            print(f'✅ {filename} - ДОСТУП Є!')
            print(f'   📊 ID: {sheet.id}')
            print(f'   🔗 URL: {sheet.url}')
            
            # Перевіряємо листи
            worksheets = sheet.worksheets()
            print(f'   📋 Листи: {[ws.title for ws in worksheets[:3]]}')
            
            # Перший лист
            if worksheets:
                ws = worksheets[0]
                print(f'   📏 Розмір: {ws.row_count}x{ws.col_count}')
                
                # Заголовки
                try:
                    headers = ws.row_values(1)[:5]
                    print(f'   🏷️  Заголовки: {headers}')
                except Exception as e:
                    print(f'   ⚠️ Помилка читання: {e}')
            
            print()
            
        except Exception as e:
            print(f'❌ {filename} - помилка: {e}')
            print()
    
except Exception as e:
    print(f'❌ Помилка авторизації: {e}')
    
    # Fallback - спробуємо старий метод oauth2client  
    try:
        print('\n🔄 Спробуємо старий метод oauth2client...')
        from oauth2client.service_account import ServiceAccountCredentials
        
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            'newproject2024-419923-8aec36a3b0ce.json',
            SCOPES
        )
        
        client = gspread.authorize(creds)
        print('✅ oauth2client авторизація успішна!')
        
        # Швидкий тест
        sheet = client.open('Журнал')
        print(f'✅ Журнал доступний! ID: {sheet.id}')
        
    except Exception as e2:
        print(f'❌ oauth2client також не працює: {e2}') 