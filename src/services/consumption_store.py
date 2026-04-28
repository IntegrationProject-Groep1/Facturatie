import logging
import os

import mysql.connector
import mysql.connector.pooling
from dotenv import load_dotenv

load_dotenv()

_pool: mysql.connector.pooling.MySQLConnectionPool | None = None


def _get_pool() -> mysql.connector.pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name="facturatie_pool",
            pool_size=5,
            host=os.getenv("MYSQL_HOST", "mysql"),
            database=os.getenv("MYSQL_DATABASE", "fossbilling"),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
        )
    return _pool


def _get_connection():
    return _get_pool().get_connection()


def init_db() -> None:
    """Creates the pending_consumptions table if it does not exist, and migrates schema if needed."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_consumptions (
                    id           INT AUTO_INCREMENT PRIMARY KEY,
                    company_id   VARCHAR(100) NOT NULL,
                    company_name VARCHAR(255) NOT NULL DEFAULT '',
                    email        VARCHAR(255) NOT NULL DEFAULT '',
                    badge_id     VARCHAR(100) NOT NULL,
                    master_uuid  VARCHAR(36)  NOT NULL,
                    description  VARCHAR(255) NOT NULL,
                    price        DECIMAL(10,2) NOT NULL,
                    quantity     INT          NOT NULL DEFAULT 1,
                    vat_rate     VARCHAR(10),
                    received_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_company_id  (company_id),
                    INDEX idx_master_uuid (master_uuid)
                )
            """)

            migrations = [
                ("company_name", "VARCHAR(255) NOT NULL DEFAULT ''"),
                ("email", "VARCHAR(255) NOT NULL DEFAULT ''"),
            ]

            for col_name, col_def in migrations:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = 'pending_consumptions' "
                    "AND COLUMN_NAME = %s",
                    (col_name,),
                )
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f"ALTER TABLE pending_consumptions ADD COLUMN {col_name} {col_def}")
                    logging.info(f"[DB] Migration: Added column {col_name} to pending_consumptions")

        conn.commit()
    except Exception as e:
        logging.error(f"[DB] Error initializing database: {e}")
        raise
    finally:
        conn.close()
    logging.info("[DB] pending_consumptions table ready")


def save_items(
    company_id: str,
    badge_id: str,
    master_uuid: str,
    items: list[dict],
    email: str = "",
    company_name: str = "",
) -> None:
    """Stores consumption items in MySQL for later invoicing."""
    query = """
        INSERT INTO pending_consumptions
            (company_id, company_name, email, badge_id, master_uuid, description, price, quantity, vat_rate)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    data = [
        (company_id, company_name, email, badge_id, master_uuid,
         i["description"], i["price"], i.get("quantity", 1), i.get("vat_rate", ""))
        for i in items
    ]
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(query, data)
        conn.commit()
    finally:
        conn.close()
    logging.info("[DB] Saved %d items for company_id=%s badge_id=%s", len(items), company_id, badge_id)


def get_pending_company_ids() -> list[str]:
    """Returns all company_ids that have pending (uninvoiced) consumptions."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT company_id FROM pending_consumptions")
            companies = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()
    return companies


def get_company_meta(company_id: str) -> dict:
    """Returns email and company_name for a company from the first pending row."""
    conn = _get_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT email, company_name FROM pending_consumptions WHERE company_id = %s LIMIT 1",
                (company_id,),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    if row is None:
        return {"email": "", "company_name": ""}
    return {"email": row["email"], "company_name": row["company_name"]}


def get_items_for_company(company_id: str) -> tuple[list[dict], list[int]]:
    """Returns (items, row_ids) for a company to prevent race conditions."""
    conn = _get_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT * FROM pending_consumptions WHERE company_id = %s ORDER BY received_at",
                (company_id,),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    items = [
        {
            "title": f"{row['description']} (badge: {row['badge_id']})",
            "price": str(row["price"]),
            "quantity": row["quantity"],
            "vat_rate": row["vat_rate"] or "",
        }
        for row in rows
    ]
    row_ids = [row["id"] for row in rows]
    return items, row_ids


def clear_by_ids(row_ids: list[int]) -> None:
    """Deletes only the specific processed rows by their database IDs."""
    if not row_ids:
        return

    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            placeholders = ', '.join(['%s'] * len(row_ids))
            query = f"DELETE FROM pending_consumptions WHERE id IN ({placeholders})"
            cursor.execute(query, tuple(row_ids))
        conn.commit()
        logging.info(f"[DB] Cleared {len(row_ids)} processed rows.")
    finally:
        conn.close()
