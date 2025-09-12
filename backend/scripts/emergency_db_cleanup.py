#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ЕКСТРЕНЕ ОЧИЩЕННЯ ЗАВИСЛИХ З'ЄДНАНЬ PostgreSQL
"""

import os
import sys
import subprocess
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = os.getenv("DB_NAME", "bsstorage")

def kill_all_hanging_connections():
    """Завершує всі завислі з'єднання до БД."""
    logger.info("🚨 ЕКСТРЕНЕ ОЧИЩЕННЯ ЗАВИСЛИХ З'ЄДНАНЬ")
    logger.info("=" * 50)
    
    # Отримуємо список всіх postgres процесів пов'язаних з нашою БД
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        hanging_pids = []
        for line in lines:
            if 'postgres:' in line and DB_NAME in line and ('waiting' in line or 'idle in transaction' in line):
                parts = line.split()
                if len(parts) > 1:
                    pid = parts[1]
                    if pid.isdigit():
                        hanging_pids.append(pid)
        
        logger.info(f"Знайдено {len(hanging_pids)} завислих з'єднань")
        
        # Завершуємо завислі процеси
        killed_count = 0
        for pid in hanging_pids:
            try:
                subprocess.run(['kill', '-9', pid], check=True)
                killed_count += 1
                logger.info(f"Завершено процес PID {pid}")
            except subprocess.CalledProcessError:
                logger.warning(f"Не вдалося завершити процес PID {pid}")
        
        logger.info(f"✅ Завершено {killed_count} завислих з'єднань")
        
        return killed_count
        
    except Exception as e:
        logger.error(f"❌ Помилка очищення: {e}")
        return 0

def restart_postgresql():
    """Перезапускає PostgreSQL."""
    logger.info("🔄 Перезапуск PostgreSQL...")
    
    try:
        # Спробуємо через brew
        result = subprocess.run(['brew', 'services', 'restart', 'postgresql@14'], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("✅ PostgreSQL перезапущено через brew")
            return True
    except:
        pass
    
    try:
        # Альтернативний спосіб
        subprocess.run(['sudo', 'systemctl', 'restart', 'postgresql'], check=True)
        logger.info("✅ PostgreSQL перезапущено через systemctl")
        return True
    except:
        pass
    
    logger.error("❌ Не вдалося перезапустити PostgreSQL")
    return False

def main():
    """Головна функція екстреного очищення."""
    logger.info("🚨 ЕКСТРЕНЕ ВІДНОВЛЕННЯ РОБОТИ БД")
    logger.info("=" * 60)
    
    # Крок 1: Завершуємо завислі з'єднання
    killed = kill_all_hanging_connections()
    
    # Крок 2: Перезапускаємо PostgreSQL якщо багато завислих
    if killed > 10:
        logger.info("Занадто багато завислих з'єднань, перезапускаємо PostgreSQL...")
        restart_postgresql()
    
    logger.info("\n🎯 НАСТУПНІ КРОКИ:")
    logger.info("1. Перезапустіть програму BMS")
    logger.info("2. Спробуйте звичайний парсинг (не паралельний)")
    logger.info("3. Якщо проблема повториться - збільшіть max_connections в PostgreSQL")

if __name__ == "__main__":
    main()
