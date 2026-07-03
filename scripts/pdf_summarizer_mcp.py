"""
Résumés des dernières lois — liste via l'API parlement.tricoteuses.fr, résumé
généré localement avec Gemini à partir du texte du document.

Flux :
  parlement.tricoteuses.fr/documents   (liste, triée par date de dépôt, avec pdfUrl)
  -> texte du document, dans l'ordre de préférence :
       1. mirroir HTML opendata de l'Assemblée nationale (assemblee-nationale.fr/dyn/opendata/{uid}.html) :
          texte intégral, léger, directement tokenisable par Gemini
       2. à défaut (mirroir absent, Sénat, ...), PDF officiel (tricoteuses-assets.s3.fr-par.scw.cloud),
          envoyé à Gemini en mode multimodal (plus lent : rendu page par page côté modèle)
  -> Gemini, en sortie JSON structurée (prompt maison, voir SUMMARY_PROMPT_TEMPLATE) :
       categorie (thème, pour le regroupement côté front), accroche, points
  -> affichage console + écriture de front/resumes.json (lu par le front statique)

Deux optimisations de vitesse :
  - cache : les uid déjà résumés dans front/resumes.json ne sont pas retraités
    (utiliser --force pour tout régénérer) ;
  - parallélisme : le téléchargement + l'appel Gemini par document sont des
    opérations réseau indépendantes, traitées par un pool de threads
    (--workers, défaut 5) plutôt que séquentiellement.

Contrairement à la version précédente, on n'utilise plus le résumé déjà calculé
par l'API tricoteuses (`/documents/{uid}/resume`) : on le régénère nous-mêmes
pour garder la main sur le prompt. Toujours pas de base de données ni de bucket
GCS à nous : uniquement l'API tricoteuses + assemblee-nationale.fr + Gemini.
"""

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

API_BASE = "https://parlement.tricoteuses.fr"
AN_OPENDATA_HTML = "https://www.assemblee-nationale.fr/dyn/opendata/{uid}.html"
MODEL_NAME = "gemini-2.5-flash"
OUTPUT_PATH = Path(__file__).parent.parent / "front" / "resumes.json"

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# Catégories thématiques utilisées pour regrouper les résumés dans le front.
CATEGORIES = [
    "Santé",
    "Éducation et jeunesse",
    "Environnement et énergie",
    "Économie et travail",
    "Logement et urbanisme",
    "Justice et sécurité",
    "Institutions et démocratie",
    "Société et solidarités",
    "Numérique",
    "International et outre-mer",
    "Autre",
]

SUMMARY_PROMPT_TEMPLATE = """Tu es un assistant spécialisé dans la vulgarisation de textes législatifs français à destination du grand public.

Titre du document : {titre}

Analyse ce texte et réponds uniquement avec l'objet JSON demandé, contenant :
- "categorie" : le thème principal du texte, en choisissant la valeur la plus pertinente dans la liste autorisée.
- "accroche" : une phrase neutre et factuelle (maximum 25 mots) qui résume l'essentiel du texte.
- "points" : une liste de 3 à 5 points clés, dans un langage clair et sans jargon juridique, présentant les mesures ou dispositions principales.

Consignes :
- Base-toi uniquement sur le contenu du document fourni ; n'invente aucune information et ne suppose pas de contexte non mentionné dans le texte.
- Reste factuel et neutre : ne donne aucune opinion, ne prends pas parti sur l'opportunité du texte, n'utilise pas de qualificatifs favorables ou défavorables absents du texte.
- Ne commence pas l'accroche par "Ce texte" ou "Ce document" ; entre directement dans le sujet.
- Si le texte est de nature constitutionnelle ou organique, ou modifie un code existant, précise-le brièvement dans un des points."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "categorie": {"type": "string", "enum": CATEGORIES},
        "accroche": {"type": "string"},
        "points": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
        },
    },
    "required": ["categorie", "accroche", "points"],
}

GENERATION_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=RESPONSE_SCHEMA,
)


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


def load_cache() -> dict[str, dict]:
    """uid -> résumé déjà écrit lors d'un run précédent (front/resumes.json)."""
    if not OUTPUT_PATH.exists():
        return {}
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return {d["uid"]: d for d in payload.get("documents", []) if d.get("accroche")}
    except (json.JSONDecodeError, KeyError):
        return {}


def download_document_text(uid: str) -> str | None:
    """Texte intégral depuis le mirroir HTML opendata de l'Assemblée nationale.

    Retourne None si le mirroir n'existe pas pour cet uid (ex. documents Sénat),
    auquel cas on se rabat sur le PDF.
    """
    response = requests.get(AN_OPENDATA_HTML.format(uid=uid), timeout=30)
    if response.status_code != 200:
        return None
    html = response.text
    html = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def download_pdf(pdf_url: str) -> bytes:
    response = requests.get(pdf_url, timeout=30)
    response.raise_for_status()
    return response.content


def summarize_text(text: str, titre: str) -> dict:
    prompt = SUMMARY_PROMPT_TEMPLATE.format(titre=titre)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[text, prompt],
        config=GENERATION_CONFIG,
    )
    return json.loads(response.text)


def summarize_pdf(pdf_bytes: bytes, titre: str) -> dict:
    prompt = SUMMARY_PROMPT_TEMPLATE.format(titre=titre)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
        config=GENERATION_CONFIG,
    )
    return json.loads(response.text)


def process_document(doc: dict) -> dict:
    """Télécharge + résume un document. Ne lève jamais : le statut est dans le retour."""
    uid = doc["uid"]
    titre = doc.get("titrePrincipalCourt") or doc.get("titrePrincipal")
    date_depot = (doc.get("dateDepot") or "")[:10]
    pdf_url = doc.get("pdfUrl")

    text = download_document_text(uid)
    if not text and not pdf_url:
        return {"uid": uid, "titre": titre, "status": "skipped"}

    try:
        summary = summarize_text(text, titre) if text else summarize_pdf(download_pdf(pdf_url), titre)
        return {
            "status": "ok",
            "uid": uid,
            "titre": titre,
            "date_depot": date_depot,
            "categorie": summary["categorie"],
            "accroche": summary["accroche"],
            "points": summary["points"],
        }
    except Exception as e:
        return {"uid": uid, "titre": titre, "status": "error", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Résumés des N dernières lois, générés avec Gemini.")
    parser.add_argument("--limit", type=int, default=10, help="Nombre de lois à résumer (défaut 10)")
    parser.add_argument("--type", default="PION", help="typeCode(s) séparés par virgule (PION, PRJL, ...)")
    parser.add_argument("--chambre", default="AN", help="Chambre : AN ou SN (défaut AN)")
    parser.add_argument("--workers", type=int, default=5, help="Requêtes en parallèle (défaut 5)")
    parser.add_argument("--force", action="store_true", help="Ignorer le cache et tout régénérer")
    args = parser.parse_args()

    docs = get_recent_documents(args.type, args.chambre, args.limit)
    print(f"\n{len(docs)} document(s) trouvé(s).\n")

    cache = {} if args.force else load_cache()
    to_fetch = [d for d in docs if d["uid"] not in cache]
    cached_count = len(docs) - len(to_fetch)
    if cached_count:
        print(f"{cached_count} déjà en cache (front/resumes.json), {len(to_fetch)} à générer.\n")

    fresh_results: dict[str, dict] = {}
    skipped = errors = 0
    if to_fetch:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_document, doc): doc for doc in to_fetch}
            for future in as_completed(futures):
                result = future.result()
                uid, titre = result["uid"], result["titre"]
                if result["status"] == "ok":
                    print(f"[OK] ({result['categorie']}) {uid} — {titre}")
                    print(result["accroche"])
                    for point in result["points"]:
                        print(f"  - {point}")
                    print()
                    fresh_results[uid] = result
                elif result["status"] == "skipped":
                    print(f"[SKIP] {uid} — {titre} (ni mirroir HTML ni PDF disponibles)\n")
                    skipped += 1
                else:
                    print(f"[ERREUR] {uid} — {titre} : {result['error']}\n")
                    errors += 1

    # On réassemble dans l'ordre d'origine (date de dépôt décroissante), en
    # combinant cache + résultats fraîchement générés.
    results = []
    for doc in docs:
        uid = doc["uid"]
        if uid in cache:
            results.append(cache[uid])
        elif uid in fresh_results:
            results.append(fresh_results[uid])

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(results)} résumé(s) au total ({cached_count} en cache, {len(fresh_results)} générés) "
          f"écrit(s) dans {OUTPUT_PATH} / {skipped} ignoré(s) / {errors} erreur(s).")


if __name__ == "__main__":
    main()
