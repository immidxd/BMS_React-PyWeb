#!/usr/bin/env python3
import sys
import os
import logging
import argparse

# Додаємо батьківську директорію до шляху імпорту
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from models.database import db_session
from models.seed_data import reset_database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Reset database and populate with test data")
    parser.add_argument('--force', '-f', action='store_true', help="Skip confirmation prompt")
    args = parser.parse_args()
    
    logger.info("Starting database reset process")
    
    if not args.force:
        confirmation = input("Це скине всі дані у базі даних і створить нові тестові дані. Продовжити? (так/ні): ")
        if confirmation.lower() not in ['так', 'yes', 'y', 't']:
            logger.info("Операцію скасовано користувачем")
            return
    
    try:
        session = db_session()
        reset_database(session)
        logger.info("Базу даних успішно скинуто і заповнено тестовими даними")
    except Exception as e:
        logger.error(f"Помилка при скиданні бази даних: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main() 