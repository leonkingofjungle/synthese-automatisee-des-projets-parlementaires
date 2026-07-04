"""Test unitaire sur un seul document : fetch par uid -> résumé Gemini -> judge Claude.

Affiche chaque étape en détail (source du texte, résumé, verdicts, score, décision
de publication) sans jamais toucher front/resumes.json.

    python3 scripts/single_doc.py PIONANR5L17BTC3004
"""

import argparse
import json
import sys

import requests

from judge import REJECT_THRESHOLD, compute_score, judge_summary
from pdf_summarizer_mcp import API_BASE, download_document_text, download_pdf
from summarizer import summarize_pdf, summarize_text


def fetch_document(uid: str) -> dict:
    response = requests.get(f"{API_BASE}/documents/{uid}", timeout=30)
    response.raise_for_status()
    return response.json()["data"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Résume et juge un document par uid, sans écrire resumes.json.")
    parser.add_argument("uid", help="uid du document (ex. PIONANR5L17BTC3004)")
    args = parser.parse_args()

    doc = fetch_document(args.uid)
    titre = doc.get("titrePrincipalCourt") or doc.get("titrePrincipal")
    print(f"=== Document ===\nuid    : {doc['uid']}\ntitre  : {titre}\ndépôt  : {(doc.get('dateDepot') or '')[:10]}")

    text = download_document_text(args.uid)
    pdf_url = doc.get("pdfUrl")
    if text:
        print(f"source : mirroir HTML opendata ({len(text)} caractères)")
    elif pdf_url:
        print(f"source : PDF officiel ({pdf_url})")
    else:
        sys.exit("Ni mirroir HTML ni PDF disponibles : document non traitable.")

    print("\n=== Résumé (Gemini) ===")
    summary = summarize_text(text, titre) if text else summarize_pdf(download_pdf(pdf_url), titre)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not text:
        print("\n=== Judge ===\nDocument résumé depuis le PDF : publié sans score (le judge ne lit que du texte).")
        return

    print("\n=== Judge (Claude) ===")
    judgment = judge_summary(text, summary)
    if judgment is None:
        print("Judge indisponible ou réponse inexploitable : publication sans score.")
        return

    for v in judgment["verdicts"]:
        print(f"[{v['verdict'].upper():7}] {v['claim']}")
        if v.get("citation"):
            print(f"          citation : {v['citation'][:120]}")
    print(f"catégorie correcte : {judgment['categorie_correcte']} / neutralité : {judgment['neutralite']}"
          + (" / texte tronqué" if judgment.get("texte_tronque") else ""))

    score, flags = compute_score(judgment)
    decision = "PUBLIÉ" if score >= REJECT_THRESHOLD or "texte_tronque" in flags else "REJETÉ"
    print(f"\nscore : {score}/100  flags : {flags or 'aucun'}  ->  {decision}")


if __name__ == "__main__":
    main()
