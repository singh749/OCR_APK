"""
Database Manager - OCR History Store
SQLite backend - free, local, fast
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_history.db")


def init_database():
    """Initialize database schema and tables if not present"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            image_path  TEXT    NOT NULL,
            raw_text    TEXT,
            cleaned_num TEXT,
            engine_used TEXT,
            confidence  REAL,
            notes       TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"[DB] Database ready: {DB_PATH}")


def save_result(image_path, raw_text, cleaned_num, engine_used, confidence, notes=""):
    """
    Save OCR result to history database
    Returns: new record id
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
        INSERT INTO ocr_history 
        (timestamp, image_path, raw_text, cleaned_num, engine_used, confidence, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, image_path, raw_text, cleaned_num, engine_used, confidence, notes))
    
    record_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    print(f"[DB] Saved record #{record_id}: {cleaned_num}")
    return record_id


def get_all_history(limit=100):
    """
    Fetch all history records, newest first
    Returns: list of dicts
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, timestamp, image_path, raw_text, cleaned_num, 
               engine_used, confidence, notes
        FROM ocr_history
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    history = []
    for row in rows:
        history.append({
            "id":          row[0],
            "timestamp":   row[1],
            "image_path":  row[2],
            "raw_text":    row[3],
            "cleaned_num": row[4],
            "engine":      row[5],
            "confidence":  row[6],
            "notes":       row[7],
        })
    
    return history


def delete_record(record_id):
    """Delete a single history record by ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_history WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    print(f"[DB] Deleted record #{record_id}")


def search_history(query):
    """
    Search history by serial number or notes
    Matches against cleaned_num or notes
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, timestamp, image_path, raw_text, cleaned_num, 
               engine_used, confidence, notes
        FROM ocr_history
        WHERE cleaned_num LIKE ? OR notes LIKE ?
        ORDER BY id DESC
    """, (f"%{query}%", f"%{query}%"))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": r[0], "timestamp": r[1], "image_path": r[2],
            "raw_text": r[3], "cleaned_num": r[4], "engine": r[5],
            "confidence": r[6], "notes": r[7]
        }
        for r in rows
    ]


def get_stats():
    """Retrieve record count and average confidence statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM ocr_history")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT AVG(confidence) FROM ocr_history WHERE confidence > 0")
    avg_conf = cursor.fetchone()[0] or 0
    
    conn.close()
    return {"total": total, "avg_confidence": round(avg_conf, 1)}


# Initialize database on import
init_database()
