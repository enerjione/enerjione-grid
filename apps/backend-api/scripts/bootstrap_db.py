import sys

import psycopg2


def try_bootstrap() -> int:
    passwords = ["postgres", "1234", "admin", ""]
    for password in passwords:
        try:
            conn = psycopg2.connect(
                host="localhost",
                port=5432,
                dbname="postgres",
                user="postgres",
                password=password,
            )
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname='enerjione'")
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute("CREATE DATABASE enerjione")
                print("Database created: enerjione")
            else:
                print("Database already exists: enerjione")
            print(f"Working postgres password found: '{password}'")
            cur.close()
            conn.close()
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Attempt failed for password '{password}': {str(exc).splitlines()[0]}")

    print("Could not connect with common passwords. Please provide PostgreSQL password.")
    return 2


if __name__ == "__main__":
    sys.exit(try_bootstrap())
