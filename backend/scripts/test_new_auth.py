#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест нової автентифікації Google з використанням google-auth
"""

import os
import gspread
from google.oauth2 import service_account

# Перевіряємо обидва ключі
keys = [
    'backend/scripts/secure_creds/newproject2024-419923-b97ba80b12b0.json',
    'backend/scripts/secure_creds/newproject2024-419923-a5dba4c9f119.json'
]

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

for key_path in keys:
    print(f"\n🔑 Тестую ключ: {os.path.basename(key_path)}")
    print(f"   Шлях: {key_path}")
    print(f"   Файл існує: {os.path.exists(key_path)}")
    
    if not os.path.exists(key_path):
        print("   ❌ Файл не знайдено")
        continue
    
    try:
        # Новий спосіб автентифікації через google-auth
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=SCOPES
        )
        
        # Авторизація в gspread
        client = gspread.authorize(credentials)
        print("   ✅ Авторизація успішна!")
        
        # Тест: спробуємо отримати список документів
        try:
            spreadsheets = client.openall()
            print(f"   ✅ Знайдено {len(spreadsheets)} документів")
            
            # Спробуємо відкрити конкретні документи
            for doc_name in ['Журнал', 'Замовлення']:
                try:
                    doc = client.open(doc_name)
                    print(f"   ✅ Документ '{doc_name}' доступний")
                except Exception as e:
                    print(f"   ⚠️ Документ '{doc_name}' недоступний: {e}")
                    
        except Exception as e:
            print(f"   ⚠️ Помилка при отриманні документів: {e}")
            
    except Exception as e:
        print(f"   ❌ Помилка автентифікації: {e}")
        
        # Спробуємо прочитати вміст ключа для діагностики
        try:
            import json
            with open(key_path, 'r') as f:
                key_data = json.load(f)
                print(f"   📋 Тип акаунту: {key_data.get('type', 'невідомо')}")
                print(f"   📋 Проект ID: {key_data.get('project_id', 'невідомо')}")
                print(f"   📋 Client email: {key_data.get('client_email', 'невідомо')[:30]}...")
        except:
            pass

print("\n✅ Тестування завершено")
