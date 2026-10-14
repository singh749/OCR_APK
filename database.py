import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_history.db")

def init_database():
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

def save_result(image_path, raw_text, cleaned_num, engine_used, confidence, notes=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO ocr_history 
        (timestamp, image_path, raw_text, cleaned_num, engine_used, confidence, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, image_path, raw_text, cleaned_num, engine_used, confidence, notes))
    conn.commit()
    conn.close()

def get_all_history(limit=100):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
         SELECT id, timestamp, image_path, raw_text, cleaned_num, 
                engine_used, confidence, notes
         FROM ocr_history ORDER BY id DESC LIMIT ?
     """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "timestamp": r[1], "image_path": r[2], "raw_text": r[3], 
             "cleaned_num": r[4], "engine": r[5], "confidence": r[6], "notes": r[7]} for r in rows]

init_database()
