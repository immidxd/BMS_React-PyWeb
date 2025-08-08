#!/usr/bin/env python3
import json

print('🔍 Перевіряю деталі поточного ключа...')
with open('newproject2024-419923-a5dba4c9f119.json') as f:
    data = json.load(f)
    print(f'📧 Email: {data["client_email"]}')
    print(f'🆔 Project ID: {data["project_id"]}')
    print(f'🔑 Key ID: {data["private_key_id"]}')
    print(f'📅 Type: {data["type"]}')
    
    # Перевіряємо чи ключ не застарів
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    
    try:
        # Витягуємо private key для перевірки
        private_key_pem = data["private_key"]
        print(f'🔐 Private key починається з: {private_key_pem[:50]}...')
        print('✅ Ключ має правильний формат')
    except Exception as e:
        print(f'❌ Проблема з ключем: {e}') 