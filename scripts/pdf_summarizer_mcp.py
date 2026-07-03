"""
Résumés des dernières lois via l'API parlement.tricoteuses.fr (MCP tricoteuses)

Flux :
  parlement.tricoteuses.fr/documents          (liste, triée par date de dépôt)
  -> parlement.tricoteuses.fr/documents/{uid}/resume  (résumé IA déjà calculé côté tricoteuses)

Pas de base de données locale, pas d'appel Gemini local ici : le résumé est
directement celui exposé par l'API tricoteuses (statut "completed" / "pending"
/ "not_eligible"). C'est la même API que celle utilisée en interne par les
outils MCP `list_parlement_items` / `get_parlement_item` (recette
`utiliser_api_parlement`) ; on l'appelle ici en HTTP simple pour éviter
d'implémenter la négociation de session MCP dans un script autonome.
"""

import argparse
import time
import requests

API_BASE = "https://parlement.tricoteuses.fr"


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


def get_resume(uid: str, wait: bool = False, poll_interval: float = 5.0, max_wait: float = 60.0):
    """Récupère le résumé IA d'un document. Si `wait`, patiente pendant qu'il est en cours de calcul."""
    elapsed = 0.0
    while True:
        response = requests.get(f"{API_BASE}/documents/{uid}/resume", timeout=30)
        response.raise_for_status()
        data = response.json()["data"]
        status = data["status"]
        if status != "pending" or not wait or elapsed >= max_wait:
            return status, data.get("resume")
        time.sleep(poll_interval)
        elapsed += poll_interval


def main():
    parser = argparse.ArgumentParser(description="Résumés des N dernières lois via l'API tricoteuses.")
    parser.add_argument("--limit", type=int, default=10, help="Nombre de lois à résumer (défaut 10)")
    parser.add_argument("--type", default="PION", help="typeCode(s) séparés par virgule (PION, PRJL, ...)")
    parser.add_argument("--chambre", default="AN", help="Chambre : AN ou SN (défaut AN)")
    parser.add_argument("--wait", action="store_true", help="Attendre la génération des résumés encore en attente")
    args = parser.parse_args()

    docs = get_recent_documents(args.type, args.chambre, args.limit)
    print(f"\n{len(docs)} document(s) trouvé(s).\n")

    for i, doc in enumerate(docs, 1):
        uid = doc["uid"]
        titre = doc.get("titrePrincipalCourt") or doc.get("titrePrincipal")
        date_depot = (doc.get("dateDepot") or "")[:10]
        print(f"[{i}/{len(docs)}] {uid} — {titre} (déposé le {date_depot})")

        status, resume = get_resume(uid, wait=args.wait)
        if status == "completed" and resume:
            print(resume["content"])
        elif status == "pending":
            print("   (résumé en cours de génération côté tricoteuses, réessayer plus tard)")
        else:
            print(f"   (pas de résumé disponible : {status})")
        print()


if __name__ == "__main__":
    main()
