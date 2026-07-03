"""
Pipeline d'ingestion PDF → résumé LLM (Gemini)

Flux :
  DOCUMENTS_LIENS (Cloud SQL) → pdf_gcs_uri → GCS → Gemini API → DOCUMENT_RESUMES (Cloud SQL)

Gestion des versions : chaque uid est une version unique d'un document.
Plusieurs documents peuvent être liés via dossier_ref.
"""

import os
import io
import time
import sqlalchemy
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
from google.cloud import storage
from google.cloud.sql.connector import Connector
from google import genai
from google.genai import types

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

CONN_NAME  = os.environ["CLOUD_SQL_CONNECTION_NAME"]
DB_USER    = os.environ["CLOUD_SQL_USER"]
DB_PASS    = os.environ["CLOUD_SQL_PASSWORD"]
DB_NAME    = os.getenv("CLOUD_SQL_DB", "assemblee_nationale")
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL_NAME = "gemini-2.5-flash"

client = genai.Client(api_key=GEMINI_KEY)


# ---------------------------------------------------------------------------
# Cloud SQL
# ---------------------------------------------------------------------------

def make_engine(connector: Connector):
    def getconn():
        return connector.connect(CONN_NAME, "pg8000", user=DB_USER, password=DB_PASS, db=DB_NAME)
    return sqlalchemy.create_engine("postgresql+pg8000://", creator=getconn, poolclass=NullPool)


def ensure_table(engine):
    """Crée la table DOCUMENT_RESUMES si elle n'existe pas."""
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("""
            CREATE TABLE IF NOT EXISTS "DOCUMENT_RESUMES" (
                uid            TEXT        PRIMARY KEY,
                dossier_ref    TEXT,
                titre          TEXT,
                legislature    INTEGER,
                date_depot     DATE,
                resume         TEXT        NOT NULL,
                modele_llm     TEXT        DEFAULT 'gemini-2.0-flash',
                created_at     TIMESTAMP   DEFAULT NOW(),
                updated_at     TIMESTAMP   DEFAULT NOW()
            )
        """))
        conn.commit()
    print("Table DOCUMENT_RESUMES prête.")


def get_pending_docs(engine, limit: int = 50):
    """Retourne les documents qui ont un PDF GCS mais pas encore de résumé."""
    with engine.connect() as conn:
        result = conn.execute(sqlalchemy.text("""
            SELECT dl.uid, dl.dossier_ref, dl.titre_principal,
                   dl.legislature, dl.date_depot, dl.pdf_gcs_uri
            FROM "DOCUMENTS_LIENS" dl
            LEFT JOIN "DOCUMENT_RESUMES" dr ON dl.uid = dr.uid
            WHERE dl.pdf_gcs_uri IS NOT NULL
              AND dr.uid IS NULL
            ORDER BY dl.date_depot DESC NULLS LAST
            LIMIT :limit
        """), {"limit": limit})
        return result.mappings().all()


def save_resume(engine, uid, dossier_ref, titre, legislature, date_depot, resume):
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("""
            INSERT INTO "DOCUMENT_RESUMES"
                (uid, dossier_ref, titre, legislature, date_depot, resume, modele_llm)
            VALUES
                (:uid, :dossier_ref, :titre, :legislature, :date_depot, :resume, :modele)
            ON CONFLICT (uid) DO UPDATE SET
                resume     = EXCLUDED.resume,
                modele_llm = EXCLUDED.modele_llm,
                updated_at = NOW()
        """), {
            "uid": uid, "dossier_ref": dossier_ref, "titre": titre,
            "legislature": legislature, "date_depot": str(date_depot) if date_depot else None,
            "resume": resume, "modele": MODEL_NAME,
        })
        conn.commit()


# ---------------------------------------------------------------------------
# GCS
# ---------------------------------------------------------------------------

def download_pdf(gcs_uri: str) -> bytes:
    """Télécharge un PDF depuis gs://bucket/path."""
    path = gcs_uri.replace("gs://", "")
    bucket_name, blob_path = path.split("/", 1)
    gcs = storage.Client()
    return gcs.bucket(bucket_name).blob(blob_path).download_as_bytes()


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def summarize(pdf_bytes: bytes, titre: str) -> str:
    """Envoie le PDF à Gemini et retourne un résumé en quelques phrases."""
    prompt = f"""Tu es un assistant spécialisé dans l'analyse de textes législatifs français.

Document : {titre}

Résume ce document parlementaire en 4 à 6 phrases claires et accessibles pour un citoyen non-spécialiste.
Explique : de quoi il s'agit, quel est l'objectif principal, et les points clés du texte.
Sois direct et informatif, sans commencer par "Ce document" ou "Ce texte"."""

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
    )
    return response.text.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(limit: int = 50, delay: float = 2.0):
    connector = Connector()
    engine = make_engine(connector)

    ensure_table(engine)

    docs = get_pending_docs(engine, limit=limit)
    total = len(docs)
    print(f"\n{total} document(s) à résumer.\n")

    ok = errors = 0
    for i, doc in enumerate(docs, 1):
        titre = doc["titre_principal"] or doc["uid"]
        print(f"[{i}/{total}] {doc['uid']} — {titre[:70]}")

        try:
            pdf_bytes = download_pdf(doc["pdf_gcs_uri"])
            print(f"         PDF téléchargé ({len(pdf_bytes)//1024} Ko)")

            resume = summarize(pdf_bytes, titre)
            save_resume(
                engine,
                uid=doc["uid"],
                dossier_ref=doc["dossier_ref"],
                titre=titre,
                legislature=doc["legislature"],
                date_depot=doc["date_depot"],
                resume=resume,
            )
            print(f"         Résumé : {resume[:100]}...")
            ok += 1

        except Exception as e:
            print(f"         ERREUR : {e}")
            errors += 1

        if i < total:
            time.sleep(delay)

    print(f"\n{ok} OK / {errors} erreur(s) sur {total} documents.")
    connector.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50, help="Nombre de docs à traiter")
    parser.add_argument("--delay", type=float, default=2.0, help="Délai entre chaque doc (s)")
    args = parser.parse_args()
    main(limit=args.limit, delay=args.delay)
