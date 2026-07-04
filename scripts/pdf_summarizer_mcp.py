"""
Résumés des dernières lois — liste via l'API parlement.tricoteuses.fr, résumé
généré localement avec Gemini à partir du texte du document.

Modules :
  clients.py     connexions LLM (Gemini, Claude) et configuration .env
  summarizer.py  prompt + schema du résumé Gemini (categorie / accroche / points)
  judge.py       vérification extractive locale, score 0-100, seuils de publication
  (ce fichier)   CLI : récupération des documents, cache, parallélisme, écriture

Flux :
  parlement.tricoteuses.fr/documents   (liste, triée par date de dépôt, avec pdfUrl)
  -> texte du document, dans l'ordre de préférence :
       1. mirroir HTML opendata de l'Assemblée nationale (assemblee-nationale.fr/dyn/opendata/{uid}.html) :
          texte intégral, léger, directement tokenisable par Gemini
       2. à défaut (mirroir absent, Sénat, ...), PDF officiel (tricoteuses-assets.s3.fr-par.scw.cloud),
          envoyé à Gemini en mode multimodal (plus lent : rendu page par page côté modèle)
  -> Gemini, en sortie JSON structurée (voir summarizer.py) :
       categorie (thème, pour le regroupement côté front), accroche, points
  -> vérification par un judge local (voir judge.py) :
       verdict extractif par affirmation -> quality_score 0-100 + quality_flags ;
       score < 60 : non publié (retenté aux runs suivants, abandonné après
       MAX_REJECT_ATTEMPTS rejets — compteur "rejets" dans resumes.json) ;
       judge éteint, réponse inexploitable ou document PDF : publié sans score ;
       texte tronqué (> 40k caractères) : jamais rejeté, badge seulement
  -> affichage console + écriture (atomique) de front/resumes.json (lu par le front statique)

Deux optimisations de vitesse :
  - cache : les uid déjà résumés dans front/resumes.json ne sont pas retraités
    (utiliser --force pour régénérer la fenêtre courante) ;
  - parallélisme : le téléchargement + l'appel Gemini par document sont des
    opérations réseau indépendantes, traitées par un pool de threads
    (--workers, défaut 5) plutôt que séquentiellement.
"""

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import requests

from judge import MAX_REJECT_ATTEMPTS, REJECT_THRESHOLD, compute_score, judge_summary
from summarizer import SOURCE_END, SOURCE_START, summarize_pdf, summarize_text

API_BASE = "https://parlement.tricoteuses.fr"
AN_OPENDATA_HTML = "https://www.assemblee-nationale.fr/dyn/opendata/{uid}.html"
OUTPUT_PATH = Path(__file__).parent.parent / "front" / "resumes.json"


def get_recent_documents(type_codes: str, chambre: str, limit: int) -> list[dict]:
    """Liste les `limit` derniers documents (triés par date de dépôt décroissante)."""
    docs = []
    page = 1
    per_page = min(limit, 50)
    while len(docs) < limit:
        # L'API coupe parfois la réponse en plein transfert (ChunkedEncodingError)
        # ou renvoie un 5xx passager : erreurs transitoires, on retente.
        for attempt in range(5):
            try:
                response = requests.get(f"{API_BASE}/documents", params={
                    "perPage": per_page,
                    "page": page,
                    "sort": "dateDepot.desc",
                    "typeCode": type_codes,
                    "chambre": chambre,
                }, timeout=30)
                response.raise_for_status()
                break
            except (requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as e:
                is_5xx = isinstance(e, requests.exceptions.HTTPError) and response.status_code >= 500
                if attempt == 4 or (isinstance(e, requests.exceptions.HTTPError) and not is_5xx):
                    raise
                time.sleep(3 * (attempt + 1))
        batch = response.json()["data"]
        if not batch:
            break
        docs.extend(batch)
        page += 1
        if len(batch) < per_page:
            break
    return docs[:limit]


def load_output() -> tuple[dict[str, dict], dict[str, int]]:
    """(uid -> résumé publié, uid -> nombre de rejets) depuis front/resumes.json."""
    if not OUTPUT_PATH.exists():
        return {}, {}
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        cache = {d["uid"]: d for d in payload.get("documents", []) if d.get("accroche")}
        rejects = {uid: int(n) for uid, n in (payload.get("rejets") or {}).items()}
        return cache, rejects
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        return {}, {}


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
    # Anti-injection : un document ne doit pas pouvoir fermer le bloc délimité des prompts.
    text = text.replace(SOURCE_START, " ").replace(SOURCE_END, " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def download_pdf(pdf_url: str) -> bytes:
    response = requests.get(pdf_url, timeout=30)
    response.raise_for_status()
    return response.content


def process_document(doc: dict) -> dict:
    """Télécharge + résume + vérifie un document. Ne lève jamais : le statut est dans le retour."""
    uid = doc["uid"]
    titre = doc.get("titrePrincipalCourt") or doc.get("titrePrincipal")
    date_depot = (doc.get("dateDepot") or "")[:10]
    pdf_url = doc.get("pdfUrl")

    text = download_document_text(uid)
    if not text and not pdf_url:
        return {"uid": uid, "titre": titre, "status": "skipped"}

    try:
        summary = summarize_text(text, titre) if text else summarize_pdf(download_pdf(pdf_url), titre)

        # Le judge ne lit que du texte : les documents résumés depuis le PDF
        # (Sénat, mirroir absent) sont publiés sans score plutôt que non vérifiés à tort.
        quality_score: int | None = None
        quality_flags: list[str] = []
        if text:
            judgment = judge_summary(text, summary)
            if judgment is not None:
                quality_score, quality_flags = compute_score(judgment)
                # Texte tronqué : les "invente" peuvent viser la partie manquante — badge, pas de rejet.
                if quality_score < REJECT_THRESHOLD and "texte_tronque" not in quality_flags:
                    bad_claims = [v for v in judgment["verdicts"] if v.get("verdict") != "ok"]
                    return {
                        "uid": uid,
                        "titre": titre,
                        "status": "rejected",
                        "quality_score": quality_score,
                        "bad_claims": bad_claims,
                    }

        # Lien vers le texte source : le mirroir HTML opendata quand il existe
        # (c'est celui qui a servi au résumé), sinon le PDF officiel en repli.
        link = AN_OPENDATA_HTML.format(uid=uid) if text else pdf_url

        return {
            "status": "ok",
            "uid": uid,
            "titre": titre,
            "date_depot": date_depot,
            "categorie": summary["categorie"],
            "link": link,
            "accroche": summary["accroche"],
            "points": summary["points"],
            "quality_score": quality_score,
            "quality_flags": quality_flags,
        }
    except Exception as e:
        return {"uid": uid, "titre": titre, "status": "error", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Résumés des N dernières lois, générés avec Gemini.")
    parser.add_argument("--limit", type=int, default=10, help="Nombre de lois à résumer (défaut 10)")
    parser.add_argument("--type", default="PION", help="typeCode(s) séparés par virgule (PION, PRJL, ...)")
    parser.add_argument("--chambre", default="AN", help="Chambre : AN ou SN (défaut AN)")
    parser.add_argument("--workers", type=int, default=5, help="Requêtes en parallèle (défaut 5)")
    parser.add_argument("--force", action="store_true", help="Ignorer le cache et régénérer la fenêtre courante")
    args = parser.parse_args()

    docs = get_recent_documents(args.type, args.chambre, args.limit)
    print(f"\n{len(docs)} document(s) trouvé(s).\n")

    # Le cache complet est chargé pour préserver les résumés hors fenêtre ; --force
    # ne contrôle que la réutilisation des documents de la fenêtre courante.
    cache, rejects = load_output()
    reuse = {} if args.force else cache
    abandoned = {d["uid"] for d in docs
                 if d["uid"] not in reuse and rejects.get(d["uid"], 0) >= MAX_REJECT_ATTEMPTS}
    to_fetch = [d for d in docs if d["uid"] not in reuse and d["uid"] not in abandoned]
    cached_count = len(docs) - len(to_fetch) - len(abandoned)
    if cached_count or abandoned:
        print(f"{cached_count} déjà en cache, {len(abandoned)} abandonné(s) "
              f"(≥ {MAX_REJECT_ATTEMPTS} rejets), {len(to_fetch)} à générer.\n")

    fresh_results: dict[str, dict] = {}
    failed_uids: set[str] = set()
    skipped = errors = rejected = 0
    if to_fetch:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_document, doc): doc for doc in to_fetch}
            for future in as_completed(futures):
                result = future.result()
                uid, titre = result["uid"], result["titre"]
                if result["status"] == "ok":
                    score = result.get("quality_score")
                    score_label = f", fiabilité {score}/100" if score is not None else ""
                    print(f"[OK] ({result['categorie']}{score_label}) {uid} — {titre}")
                    print(result["accroche"])
                    for point in result["points"]:
                        print(f"  - {point}")
                    print()
                    fresh_results[uid] = result
                    rejects.pop(uid, None)
                elif result["status"] == "rejected":
                    rejects[uid] = rejects.get(uid, 0) + 1
                    failed_uids.add(uid)
                    print(f"[REJETÉ {rejects[uid]}/{MAX_REJECT_ATTEMPTS}] {uid} — {titre} "
                          f"(score {result['quality_score']}/100, non publié)")
                    for v in result["bad_claims"]:
                        print(f"  - [{v.get('verdict')}] {v.get('claim')}")
                    print()
                    rejected += 1
                elif result["status"] == "skipped":
                    failed_uids.add(uid)
                    print(f"[SKIP] {uid} — {titre} (ni mirroir HTML ni PDF disponibles)\n")
                    skipped += 1
                else:
                    failed_uids.add(uid)
                    print(f"[ERREUR] {uid} — {titre} : {result['error']}\n")
                    errors += 1

    # Fusion cache + frais. Un document dont la régénération vient d'échouer ou d'être
    # rejetée ne doit pas être ressuscité depuis son ancienne version (cas --force).
    merged = {**cache, **fresh_results}
    for uid in failed_uids:
        merged.pop(uid, None)
    results = sorted(merged.values(), key=lambda d: d.get("date_depot") or "", reverse=True)
    for entry in results:
        entry.pop("status", None)

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    payload = json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": results,
        "rejets": rejects,
    }, ensure_ascii=False, indent=2)
    # Écriture atomique : un crash en cours d'écriture ne doit pas tronquer le
    # fichier, qui sert à la fois de cache et de source du front.
    tmp_path = OUTPUT_PATH.with_name(OUTPUT_PATH.name + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, OUTPUT_PATH)
    print(f"{len(results)} résumé(s) au total ({cached_count} en cache, {len(fresh_results)} générés) "
          f"écrit(s) dans {OUTPUT_PATH} / {skipped} ignoré(s) / {rejected} rejeté(s) / {errors} erreur(s).")


if __name__ == "__main__":
    main()
