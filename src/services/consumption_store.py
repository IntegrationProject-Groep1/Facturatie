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
                    id                    INT AUTO_INCREMENT PRIMARY KEY,
                    consumption_order_id  VARCHAR(100) NOT NULL,
                    company_id            VARCHAR(100) NOT NULL DEFAULT '',
                    company_name          VARCHAR(255) NOT NULL DEFAULT '',
                    email                 VARCHAR(255) NOT NULL DEFAULT '',
                    badge_id              VARCHAR(100) NOT NULL DEFAULT '',
                    master_uuid           VARCHAR(36)  NOT NULL DEFAULT '',
                    description           VARCHAR(255) NOT NULL,
                    price                 DECIMAL(10,2) NOT NULL,
                    quantity              INT          NOT NULL DEFAULT 1,
                    vat_rate              VARCHAR(10),
                    received_at           DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_company_id  (company_id),
                    INDEX idx_consumption_order_id (consumption_order_id)
                )
            """)

            migrations = [
                ("company_name", "VARCHAR(255) NOT NULL DEFAULT ''"),
                ("email", "VARCHAR(255) NOT NULL DEFAULT ''"),
                ("consumption_order_id", "VARCHAR(100) NOT NULL DEFAULT ''"),
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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS company_accounts (
                    company_id            VARCHAR(100) PRIMARY KEY,
                    fossbilling_client_id INT NOT NULL,
                    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()
    except Exception as e:
        logging.error(f"[DB] Error initializing database: {e}")
        raise
    finally:
        conn.close()
    logging.info("[DB] Tables ready (pending_consumptions, company_accounts)")


def save_items(
    company_id: str,
    badge_id: str,
    master_uuid: str,
    items: list[dict],
    email: str = "",
    company_name: str = "",
    consumption_order_id: str = "",
) -> None:
    """Stores consumption items in MySQL for later invoicing."""
    if not items:
        return
    query = """
        INSERT INTO pending_consumptions
            (consumption_order_id, company_id, company_name, email, badge_id, master_uuid,
             description, price, quantity, vat_rate)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    data = [
        (consumption_order_id, company_id, company_name, email, badge_id, master_uuid,
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
    logging.info("[DB] Saved %d items | consumption_order_id=%s | company_id=%s",
                 len(items), consumption_order_id, company_id)


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
    """Returns email, company_name and other meta for a company from the first pending row."""
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
        return {"email": "", "company_name": "", "first_name": "", "last_name": "", "customer_id": ""}
    return {
        "email": row["email"],
        "company_name": row["company_name"],
        "first_name": "",
        "last_name": "",
        "customer_id": "",
    }


def get_items_by_correlation_id(correlation_id: str) -> tuple[list[dict], list[int]]:
    """Returns (items, row_ids) for a specific consumption_order matched by its message_id."""
    conn = _get_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT * FROM pending_consumptions WHERE consumption_order_id = %s ORDER BY received_at",
                (correlation_id,),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    items = [
        {
            "title": f"{row['description']} (badge: {row['badge_id']})" if row['badge_id'] else row['description'],
            "price": str(row["price"]),
            "quantity": row["quantity"],
            "vat_rate": row["vat_rate"] or "",
        }
        for row in rows
    ]
    row_ids = [row["id"] for row in rows]
    return items, row_ids


def update_meta_by_correlation_id(
    correlation_id: str,
    company_name: str,
    email: str,
) -> int:
    """Updates company_name and email on all rows matching a consumption_order_id.
    Returns the number of rows updated."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE pending_consumptions SET company_name = %s, email = %s "
                "WHERE consumption_order_id = %s",
                (company_name, email, correlation_id),
            )
            updated = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    logging.info("[DB] Updated meta for correlation_id=%s | rows=%d", correlation_id, updated)
    return updated


def get_company_client_id(company_id: str) -> int | None:
    """Returns the FossBilling billing client_id for a company, or None if not yet registered."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT fossbilling_client_id FROM company_accounts WHERE company_id = %s",
                (company_id,),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else None


def save_company_client_id(company_id: str, client_id: int) -> None:
    """Saves or updates the FossBilling billing client_id for a company."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO company_accounts (company_id, fossbilling_client_id) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE fossbilling_client_id = %s",
                (company_id, client_id, client_id),
            )
        conn.commit()
    finally:
        conn.close()
    logging.info("[DB] Company billing account saved: company_id=%s → client_id=%s", company_id, client_id)


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
