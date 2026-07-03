"""
Résumés des dernières lois — liste via l'API parlement.tricoteuses.fr, résumé
généré localement avec Gemini à partir du PDF officiel.

Flux :
  parlement.tricoteuses.fr/documents   (liste, triée par date de dépôt, avec pdfUrl)
  -> téléchargement du PDF (bucket public tricoteuses-assets.s3.fr-par.scw.cloud)
  -> Gemini (prompt maison, voir SUMMARY_PROMPT_TEMPLATE)
  -> affichage console + écriture de front/resumes.json (lu par le front statique)

Contrairement à la version précédente, on n'utilise plus le résumé déjà calculé
par l'API tricoteuses (`/documents/{uid}/resume`) : on le régénère nous-mêmes
pour garder la main sur le prompt. Toujours pas de base de données ni de bucket
GCS à nous : uniquement l'API tricoteuses + Gemini.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

API_BASE = "https://parlement.tricoteuses.fr"
MODEL_NAME = "gemini-2.5-flash"
OUTPUT_PATH = Path(__file__).parent.parent / "front" / "resumes.json"

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

SUMMARY_PROMPT_TEMPLATE = """Tu es un assistant spécialisé dans la vulgarisation de textes législatifs français à destination du grand public.

Titre du document : {titre}

Rédige un résumé neutre et factuel de ce texte, structuré ainsi :
1. Une phrase d'accroche (maximum 25 mots) qui résume l'essentiel du texte.
2. Une liste de 3 à 5 points clés (une ligne commençant par "- " chacune), qui présentent les mesures ou dispositions principales, dans un langage clair, sans jargon juridique.

Consignes :
- Base-toi uniquement sur le contenu du document fourni ; n'invente aucune information et ne suppose pas de contexte non mentionné dans le texte.
- Reste factuel et neutre : ne donne aucune opinion, ne prends pas parti sur l'opportunité du texte, n'utilise pas de qualificatifs favorables ou défavorables absents du texte.
- Ne commence pas la phrase d'accroche par "Ce texte" ou "Ce document" ; entre directement dans le sujet.
- Si le texte est de nature constitutionnelle ou organique, ou modifie un code existant, précise-le brièvement.
- N'ajoute ni introduction, ni conclusion, ni méta-commentaire sur le résumé lui-même."""


def get_recent_documents(type_codes: str, chambre: str, limit: int) -> list[dict]:
    """Liste les `limit` derniers documents (triés par date de dépôt décroissante)."""
    docs = []
    page = 1
    per_page = min(limit, 50)
    while len(docs) < limit:
        response = requests.get(f"{API_BASE}/documents", params={
            "perPage": per_page,
            "page": page,
            "sort": "dateDepot.desc",
            "typeCode": type_codes,
            "chambre": chambre,
        }, timeout=30)
        response.raise_for_status()
        batch = response.json()["data"]
        if not batch:
            break
        docs.extend(batch)
        page += 1
        if len(batch) < per_page:
            break
    return docs[:limit]


def download_pdf(pdf_url: str) -> bytes:
    response = requests.get(pdf_url, timeout=30)
    response.raise_for_status()
    return response.content


def summarize(pdf_bytes: bytes, titre: str) -> str:
    prompt = SUMMARY_PROMPT_TEMPLATE.format(titre=titre)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
    )
    return response.text.strip()


def main():
    parser = argparse.ArgumentParser(description="Résumés des N dernières lois, générés avec Gemini.")
    parser.add_argument("--limit", type=int, default=10, help="Nombre de lois à résumer (défaut 10)")
    parser.add_argument("--type", default="PION", help="typeCode(s) séparés par virgule (PION, PRJL, ...)")
    parser.add_argument("--chambre", default="AN", help="Chambre : AN ou SN (défaut AN)")
    args = parser.parse_args()

    docs = get_recent_documents(args.type, args.chambre, args.limit)
    print(f"\n{len(docs)} document(s) trouvé(s).\n")

    results = []
    ok = skipped = errors = 0
    for i, doc in enumerate(docs, 1):
        uid = doc["uid"]
        titre = doc.get("titrePrincipalCourt") or doc.get("titrePrincipal")
        date_depot = (doc.get("dateDepot") or "")[:10]
        pdf_url = doc.get("pdfUrl")
        print(f"[{i}/{len(docs)}] {uid} — {titre} (déposé le {date_depot})")

        if not pdf_url:
            print("   (pas de PDF disponible pour ce document, ignoré)\n")
            skipped += 1
            continue

        try:
            pdf_bytes = download_pdf(pdf_url)
            resume = summarize(pdf_bytes, titre)
            print(resume, "\n")
            results.append({
                "uid": uid,
                "titre": titre,
                "date_depot": date_depot,
                "resume": resume,
            })
            ok += 1
        except Exception as e:
            print(f"   ERREUR : {e}\n")
            errors += 1

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{ok} résumé(s) écrit(s) dans {OUTPUT_PATH} / {skipped} ignoré(s) / {errors} erreur(s).")


if __name__ == "__main__":
    main()
