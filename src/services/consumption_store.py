import logging
import os

import mysql.connector
from dotenv import load_dotenv

load_dotenv()


def _get_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "mysql"),
        database=os.getenv("MYSQL_DATABASE", "fossbilling"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
    )


def init_db() -> None:
    """Creates the pending_consumptions table if it does not exist."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_consumptions (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    company_id  VARCHAR(100) NOT NULL,
                    badge_id    VARCHAR(100) NOT NULL,
                    master_uuid VARCHAR(36)  NOT NULL,
                    description VARCHAR(255) NOT NULL,
                    price       DECIMAL(10,2) NOT NULL,
                    quantity    INT          NOT NULL DEFAULT 1,
                    vat_rate    VARCHAR(10),
                    received_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_company_id  (company_id),
                    INDEX idx_master_uuid (master_uuid)
                )
            """)
        conn.commit()
    finally:
        conn.close()
    logging.info("[DB] pending_consumptions table ready")


def save_items(company_id: str, badge_id: str, master_uuid: str, items: list[dict]) -> None:
    """Stores consumption items in MySQL for later invoicing."""
    query = """
        INSERT INTO pending_consumptions
            (company_id, badge_id, master_uuid, description, price, quantity, vat_rate)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    data = [
        (company_id, badge_id, master_uuid, i["description"], i["price"],
         i.get("quantity", 1), i.get("vat_rate", ""))
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


def get_items_for_company(company_id: str) -> list[dict]:
    """Returns all pending items for a company, formatted for FossBilling invoice creation."""
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

    return [
        {
            "title": f"{row['description']} (badge: {row['badge_id']})",
            "price": str(row["price"]),
            "quantity": row["quantity"],
            "vat_rate": row["vat_rate"] or "",
        }
        for row in rows
    ]


def clear_company(company_id: str) -> None:
    """Removes all pending consumptions for a company after invoicing."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM pending_consumptions WHERE company_id = %s", (company_id,))
        conn.commit()
    finally:
        conn.close()
    logging.info("[DB] Cleared pending consumptions for company_id=%s", company_id)
