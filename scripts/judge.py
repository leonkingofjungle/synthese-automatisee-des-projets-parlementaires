"""LLM-as-a-judge (Claude) : vérification extractive du résumé, score 0-100.

Chaque affirmation du résumé (accroche + points) reçoit un verdict ok/deforme/invente
avec citation, plus deux checks (catégorie, neutralité). Le judge n'est jamais une
dépendance dure : indisponible ou réponse inexploitable -> None (publication sans score).
"""

import json
import threading

from anthropic import APIConnectionError, AuthenticationError

from clients import JUDGE_MODEL, judge_client
from summarizer import CATEGORIES, INJECTION_GUARD, SOURCE_END, SOURCE_START

JUDGE_TEXT_MAX_CHARS = 40_000
REJECT_THRESHOLD = 60  # score < 60 : non publié (retenté aux runs suivants)
MAX_REJECT_ATTEMPTS = 3  # au-delà, le document est abandonné (plus d'appels Gemini)

JUDGE_PROMPT_TEMPLATE = f"""Tu es un vérificateur factuel. Tu reçois un texte législatif source et les affirmations d'un résumé qui en a été généré. Ta seule tâche est de vérifier la fidélité du résumé au texte source — tu n'évalues ni le style ni la lisibilité.

Le texte source est délimité par {SOURCE_START} et {SOURCE_END}. {INJECTION_GUARD}

{SOURCE_START}
{{texte}}
{SOURCE_END}

Catégorie attribuée au résumé : {{categorie}}
Catégories autorisées : {{categories}}

Affirmations à vérifier (la première est l'accroche) :
{{claims}}

Pour CHAQUE affirmation, dans l'ordre, produis un objet avec :
- "claim" : l'affirmation recopiée telle quelle ;
- "verdict" : "ok" si elle est entièrement supportée par le texte source, "deforme" si elle s'appuie sur un passage réel mais l'exagère ou le déforme, "invente" si elle contient une information absente du texte source ;
- "citation" : le court passage du texte source qui la supporte, ou null si aucun.

Indique aussi :
- "categorie_correcte" : true si la catégorie attribuée correspond au thème principal du texte source ;
- "neutralite" : true si aucune affirmation ne contient de qualificatif favorable ou défavorable absent du texte source.

Ne corrige rien, ne reformule rien : signale uniquement les problèmes. Réponds uniquement avec l'objet JSON demandé."""

JUDGE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["ok", "deforme", "invente"]},
                    "citation": {"type": ["string", "null"]},
                },
                "required": ["claim", "verdict", "citation"],
                "additionalProperties": False,
            },
        },
        "categorie_correcte": {"type": "boolean"},
        "neutralite": {"type": "boolean"},
    },
    "required": ["verdicts", "categorie_correcte", "neutralite"],
    "additionalProperties": False,
}

VERDICT_POINTS = {"ok": 1.0, "deforme": 0.4, "invente": 0.0}

# Appels sérialisés (rate limit API), et désactivation du judge pour tout
# le run dès la première panne de connexion ou clé invalide.
_judge_lock = threading.Lock()
_judge_down = False


def judge_summary(text: str, summary: dict) -> dict | None:
    """Vérifie le résumé avec le modèle local. None si le judge est indisponible ou
    si sa réponse est inexploitable : la publication ne dépend jamais du judge."""
    global _judge_down
    if _judge_down:
        return None
    claims = [summary["accroche"], *summary["points"]]
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        texte=text[:JUDGE_TEXT_MAX_CHARS],
        categorie=summary["categorie"],
        categories=", ".join(CATEGORIES),
        claims="\n".join("- " + c.replace("\n", " ") for c in claims),
    )
    try:
        with _judge_lock:
            response = judge_client().messages.create(
                model=JUDGE_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": JUDGE_RESPONSE_SCHEMA}},
                temperature=0.0,
            )
        judgment = json.loads(next(b.text for b in response.content if b.type == "text"))
    except (APIConnectionError, AuthenticationError) as e:
        _judge_down = True
        print(f"[JUDGE injoignable — désactivé pour ce run] {e}")
        return None
    except Exception as e:
        print(f"[JUDGE indisponible] {e}")
        return None

    # Un judge qui ne rend pas exactement un verdict conforme par affirmation est
    # inexploitable : mieux vaut publier sans score que noter sur des données fausses.
    verdicts = judgment.get("verdicts", [])
    if len(verdicts) != len(claims) or any(v.get("verdict") not in VERDICT_POINTS for v in verdicts):
        print(f"[JUDGE réponse inexploitable] {len(verdicts)} verdict(s) pour {len(claims)} affirmation(s)")
        return None
    judgment["texte_tronque"] = len(text) > JUDGE_TEXT_MAX_CHARS
    return judgment


def compute_score(judgment: dict) -> tuple[int, list[str]]:
    """Score 0-100 dérivé des verdicts par affirmation + checks catégorie/neutralité."""
    verdicts = [v.get("verdict") for v in judgment.get("verdicts", [])]
    points = [VERDICT_POINTS.get(v, 0.0) for v in verdicts]
    points.append(1.0 if judgment.get("categorie_correcte") else 0.0)
    points.append(1.0 if judgment.get("neutralite") else 0.0)
    score = round(100 * sum(points) / len(points))

    flags = []
    if "invente" in verdicts:
        flags.append("invention")
    if "deforme" in verdicts:
        flags.append("deformation")
    if not judgment.get("categorie_correcte"):
        flags.append("categorie_incertaine")
    if not judgment.get("neutralite"):
        flags.append("neutralite_douteuse")
    if judgment.get("texte_tronque"):
        flags.append("texte_tronque")
    return score, flags
