import psycopg2
import os

def setup():
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = int(os.getenv("DB_PORT", "5433"))
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "fumate")

    conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname="postgres")
    conn.autocommit = True
    cur = conn.cursor()

    for db in ["gut_health", "gut_health_test"]:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{db}"')
            print(f"Database {db} created successfully.")
        else:
            print(f"Database {db} already exists.")

    cur.close()
    conn.close()

if __name__ == "__main__":
    setup()
