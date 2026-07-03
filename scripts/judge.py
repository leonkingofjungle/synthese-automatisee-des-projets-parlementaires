"""
LLM-as-judge (100% local, via Ollama) : note chaque résumé de front/resumes.json
en le comparant au texte source du document (même mirroir HTML opendata que
pdf_summarizer_mcp.py), selon la grille définie dans judge_criteria.yaml.

Deux familles de vérification, définies dans judge_criteria.yaml :
  - `criteria` : jugés par le LLM, un appel Ollama par critère (température 0),
    avec des ancrages de notation (5/3/1) pour limiter la variance ;
  - `checks` : vérifiés de façon déterministe en Python (longueur de l'accroche,
    nombre de points clés, ...) — pas besoin d'un LLM pour compter des mots.

Un mécanisme de veto (`veto` dans le YAML) plafonne la note globale si le
critère d'exactitude factuelle est trop bas : un résumé qui invente des faits
ne doit pas être sauvé par un bon score de clarté ou de mise en forme.

Aucun appel à une API cloud : tout tourne sur un modèle Ollama local, servi
par défaut sur http://localhost:11434. Installer Ollama (https://ollama.com)
et récupérer un modèle avant utilisation, par exemple :

  ollama pull qwen2.5:7b-instruct
  ollama serve   # si pas déjà lancé

Usage :
  python3 scripts/judge.py
  python3 scripts/judge.py --limit 5
  python3 scripts/judge.py --input front/resumes.json --criteria scripts/judge_criteria.yaml

Le rapport est écrit dans front/judge_report.json, lu et affiché par le front
statique (front/index.html) dans la fenêtre de détail de chaque résumé.

Limite connue (v1, expérimental) : seuls les documents dont le mirroir HTML
opendata de l'Assemblée nationale est disponible peuvent être jugés (pas de
repli PDF ici, contrairement à pdf_summarizer_mcp.py) — les autres sont
listés comme ignorés.
"""

import argparse
import json
import re
from html import unescape
from pathlib import Path

import requests
import yaml

RESUMES_PATH = Path(__file__).parent.parent / "front" / "resumes.json"
CRITERIA_PATH = Path(__file__).parent / "judge_criteria.yaml"
REPORT_PATH = Path(__file__).parent.parent / "front" / "judge_report.json"
AN_OPENDATA_HTML = "https://www.assemblee-nationale.fr/dyn/opendata/{uid}.html"

# Utilisé pour évaluer les `checks` du YAML (ex. "len(accroche.split()) <= 25") :
# pas d'accès aux builtins Python, seulement ce dont ces règles ont besoin.
SAFE_BUILTINS = {"len": len}


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def download_document_text(uid: str) -> str | None:
    """Texte intégral depuis le mirroir HTML opendata de l'Assemblée nationale."""
    response = requests.get(AN_OPENDATA_HTML.format(uid=uid), timeout=30)
    if response.status_code != 200:
        return None
    html = response.text
    html = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def build_prompt(doc: dict, source_text: str, criterion: dict, scale: dict) -> str:
    anchors = "\n".join(
        f"  {score} : {desc}" for score, desc in sorted(criterion["anchors"].items(), reverse=True)
    )
    rapport = (
        f"Catégorie attribuée : {doc['categorie']}\n"
        f"Accroche : {doc['accroche']}\n"
        "Points clés :\n" + "\n".join(f"- {p}" for p in doc.get("points", []))
    )
    return f"""Tu es un évaluateur indépendant et exigeant de résumés de textes législatifs français.

Voici le texte source intégral du document :
---
{source_text}
---

Voici le rapport (résumé) généré automatiquement à partir de ce texte :
---
{rapport}
---

Évalue uniquement le critère suivant : "{criterion['label']}".
{criterion['description'].strip()}

Repères de notation (échelle {scale['min']} à {scale['max']}) :
{anchors}

Base-toi uniquement sur une comparaison rigoureuse avec le texte source (pas sur ton opinion
du sujet traité par la loi). Réponds uniquement avec un objet JSON de la forme
{{"score": <entier>, "justification": "<une phrase>"}}, sans aucun texte avant ou après."""


def call_ollama(ollama_cfg: dict, prompt: str) -> dict:
    response = requests.post(f"{ollama_cfg['base_url']}/api/chat", json={
        "model": ollama_cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "format": ollama_cfg.get("format", "json"),
        "stream": False,
        "options": ollama_cfg.get("options", {}),
    }, timeout=180)
    response.raise_for_status()
    return json.loads(response.json()["message"]["content"])


def judge_criterion(doc: dict, source_text: str, criterion: dict, scale: dict, ollama_cfg: dict) -> dict:
    prompt = build_prompt(doc, source_text, criterion, scale)
    data = call_ollama(ollama_cfg, prompt)
    score = max(scale["min"], min(scale["max"], int(data["score"])))
    return {"score": score, "justification": str(data.get("justification", "")).strip()}


def run_checks(doc: dict, checks: list[dict]) -> list[dict]:
    context = {
        "accroche": doc.get("accroche", ""),
        "points_cles": doc.get("points", []),
    }
    results = []
    for check in checks:
        try:
            passed = bool(eval(check["rule"], {"__builtins__": SAFE_BUILTINS}, context))
        except Exception as e:
            passed, check = False, {**check, "error": str(e)}
        results.append({"name": check["name"], "rule": check["rule"], "passed": passed})
    return results


def judge_document(doc: dict, config: dict) -> dict | None:
    source_text = download_document_text(doc["uid"])
    if not source_text:
        return None

    scale, ollama_cfg, criteria = config["scale"], config["ollama"], config["criteria"]

    scores = {c["name"]: judge_criterion(doc, source_text, c, scale, ollama_cfg) for c in criteria}

    total_weight = sum(c["weight"] for c in criteria)
    weighted = sum(scores[c["name"]]["score"] * c["weight"] for c in criteria) / total_weight

    capped = False
    veto = config.get("veto")
    if veto and scores[veto["criterion"]]["score"] <= veto["threshold"]:
        weighted = min(weighted, veto["cap"])
        capped = True

    return {
        "uid": doc["uid"],
        "titre": doc["titre"],
        "scores": scores,
        "checks": run_checks(doc, config.get("checks", [])),
        "note_globale": round(weighted, 2),
        "plafonnee_par_veto": capped,
    }


def check_ollama(ollama_cfg: dict):
    try:
        response = requests.get(f"{ollama_cfg['base_url']}/api/tags", timeout=3)
        response.raise_for_status()
    except requests.RequestException:
        raise SystemExit(f"Impossible de joindre Ollama sur {ollama_cfg['base_url']} — lancer `ollama serve`.")
    installed = {m["name"] for m in response.json().get("models", [])}
    if ollama_cfg["model"] not in installed and not any(
        name.startswith(ollama_cfg["model"] + ":") or ollama_cfg["model"] == name.split(":")[0]
        for name in installed
    ):
        raise SystemExit(
            f"Le modèle '{ollama_cfg['model']}' n'est pas installé localement — "
            f"lancer `ollama pull {ollama_cfg['model']}`."
        )


def main():
    parser = argparse.ArgumentParser(description="Note les résumés de resumes.json avec un LLM juge local (Ollama).")
    parser.add_argument("--input", default=str(RESUMES_PATH))
    parser.add_argument("--criteria", default=str(CRITERIA_PATH))
    parser.add_argument("--limit", type=int, default=None, help="Limiter le nombre de documents jugés")
    args = parser.parse_args()

    config = load_config(Path(args.criteria))
    check_ollama(config["ollama"])

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    docs = [d for d in payload.get("documents", []) if d.get("accroche")]
    if args.limit:
        docs = docs[: args.limit]

    results = []
    skipped = 0
    scale = config["scale"]
    for i, doc in enumerate(docs, 1):
        print(f"[{i}/{len(docs)}] {doc['uid']} — {doc['titre']}")
        result = judge_document(doc, config)
        if result is None:
            print("   (texte source introuvable, ignoré)\n")
            skipped += 1
            continue

        note_str = f"{result['note_globale']} / {scale['max']}"
        if result["plafonnee_par_veto"]:
            note_str += f" (plafonnée par veto sur '{config['veto']['criterion']}')"
        print(f"   note globale : {note_str}")
        for c in config["criteria"]:
            s = result["scores"][c["name"]]
            print(f"     - {c['label']}: {s['score']}/{scale['max']} — {s['justification']}")
        for check in result["checks"]:
            status = "OK" if check["passed"] else "ÉCHEC"
            print(f"     [{status}] {check['name']} ({check['rule']})")
        print()
        results.append(result)

    if results:
        avg = round(sum(r["note_globale"] for r in results) / len(results), 2)
        print(f"Note moyenne globale : {avg} / {scale['max']} sur {len(results)} document(s), {skipped} ignoré(s).")

    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps({
        "scale": scale,
        "criteria": [{"name": c["name"], "label": c["label"]} for c in config["criteria"]],
        "checks": [{"name": c["name"], "rule": c["rule"]} for c in config.get("checks", [])],
        "veto": config.get("veto"),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rapport écrit dans {REPORT_PATH}")


if __name__ == "__main__":
    main()
