from update_products_table import update_products_table
from seed_reference_data import seed_reference_data
from seed_test_data import seed_test_data

def run_migrations():
    """Run all database migrations"""
    print("Starting database migrations...")
    
    try:
        # Update products table structure
        print("Updating products table structure...")
        update_products_table()
        
        # Seed reference data
        print("Seeding reference data...")
        seed_reference_data()
        
        # Seed test data
        print("Seeding test data...")
        seed_test_data()
        
        print("All migrations completed successfully")
        
    except Exception as e:
        print(f"Error during migrations: {str(e)}")
        raise

if __name__ == "__main__":
    run_migrations() 