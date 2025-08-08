#!/usr/bin/env python3
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

print('🔍 Тестую другий credentials ключ...')

# Завантажуємо другий credentials файл
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']
CREDS_FILE = 'newproject2024-419923-8aec36a3b0ce.json'

try:
    credentials = service_account.Credentials.from_service_account_file(
        CREDS_FILE, 
        scopes=SCOPES
    )
    
    print(f'✅ Credentials завантажено з {CREDS_FILE}!')
    
    # Створюємо Drive service
    drive_service = build('drive', 'v3', credentials=credentials)
    
    print('✅ Google Drive API підключення успішне!')
    
    # Тестуємо доступ до файлів
    print('🔍 Шукаю доступні файли...')
    
    results = drive_service.files().list(
        q="mimeType='application/vnd.google-apps.spreadsheet'",
        fields='files(id, name)'
    ).execute()
    
    files = results.get('files', [])
    print(f'📊 Знайдено {len(files)} Google Sheets файлів доступних для Service Account:')
    
    target_files = ['Журнал', 'Замовлення']
    found_files = []
    
    for file in files:
        file_name = file['name']
        file_id = file['id']
        print(f'  📄 {file_name} (ID: {file_id})')
        
        if file_name in target_files:
            found_files.append(file_name)
    
    print(f'\n🎯 Цільові файли: {target_files}')
    print(f'✅ Знайдено: {found_files}')
    
    missing = set(target_files) - set(found_files)
    if missing:
        print(f'❌ Не знайдено: {list(missing)}')
    else:
        print('🎉 Всі файли доступні!')
        
except Exception as e:
    print(f'❌ Помилка: {e}') 