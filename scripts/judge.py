"""LLM-as-a-judge (Claude, avec repli Gemini optionnel) : vérification extractive
du résumé, score 0-100.

Chaque affirmation du résumé (accroche + points) reçoit un verdict ok/deforme/invente
avec citation, plus trois checks (catégorie, neutralité, correction du français). Le
judge n'est jamais une dépendance dure : indisponible ou réponse inexploitable -> None
(publication sans score) — sauf si `gemini_fallback=True` (voir judge_summary), auquel
cas Gemini prend le relais quand Claude est indisponible. Ce repli est marqué dans le
résultat (`judge_model: "gemini"`, flag `judge_non_independant`) : ce n'est plus une
vérification par un modèle indépendant de celui qui rédige, seulement un filet de
sécurité pour ne pas publier totalement sans contrôle.
"""

import json
import threading
import time

from anthropic import APIConnectionError, AuthenticationError
from google.genai import types

from clients import GEMINI_MODEL, JUDGE_MODEL, gemini_client, judge_client
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
- "neutralite" : true si aucune affirmation ne contient de qualificatif favorable ou défavorable absent du texte source ;
- "correction" : true si l'accroche et les points clés sont rédigés dans un français correct, sans faute d'orthographe, de grammaire ou de conjugaison.

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
        "correction": {"type": "boolean"},
    },
    "required": ["verdicts", "categorie_correcte", "neutralite", "correction"],
    "additionalProperties": False,
}

# Gemini (dialecte OpenAPI restreint) n'accepte pas `"type": ["string", "null"]`
# ni `additionalProperties` : variante dédiée pour le repli, mêmes champs sinon.
JUDGE_RESPONSE_SCHEMA_GEMINI = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["ok", "deforme", "invente"]},
                    "citation": {"type": "string", "nullable": True},
                },
                "required": ["claim", "verdict", "citation"],
            },
        },
        "categorie_correcte": {"type": "boolean"},
        "neutralite": {"type": "boolean"},
        "correction": {"type": "boolean"},
    },
    "required": ["verdicts", "categorie_correcte", "neutralite", "correction"],
}

VERDICT_POINTS = {"ok": 1.0, "deforme": 0.4, "invente": 0.0}

GEMINI_JUDGE_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=JUDGE_RESPONSE_SCHEMA_GEMINI,
)

# Appels Claude sérialisés (rate limit API), et désactivation du judge Claude
# pour tout le run dès la première panne de connexion ou clé invalide.
_judge_lock = threading.Lock()
_judge_down = False


def _ask_claude(prompt: str) -> dict | None:
    """None si Claude est injoignable ou la clé invalide — désactive alors le
    judge Claude pour le reste du run (_judge_down)."""
    global _judge_down
    if _judge_down:
        return None
    try:
        with _judge_lock:
            response = judge_client().messages.create(
                model=JUDGE_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": JUDGE_RESPONSE_SCHEMA}},
                temperature=0.0,
            )
        return json.loads(next(b.text for b in response.content if b.type == "text"))
    except (APIConnectionError, AuthenticationError) as e:
        _judge_down = True
        print(f"[JUDGE Claude injoignable — désactivé pour ce run] {e}")
        return None
    except Exception as e:
        print(f"[JUDGE Claude indisponible] {e}")
        return None


GEMINI_QUOTA_RETRIES = 4
GEMINI_QUOTA_BACKOFF = 65  # secondes ; la quota Gemini par minute se renouvelle par fenêtre glissante

# Un compte à court de crédits prépayés ne se rétablit pas tout seul (contrairement
# à un 429 de quota par minute) : inutile de patienter, et inutile de le redécouvrir
# à chaque document du run, d'où ce drapeau global (même logique que _judge_down).
_gemini_billing_exhausted = False


def _is_billing_exhausted(error: str) -> bool:
    return "prepayment credit" in error.lower() or "billing" in error.lower()


def _ask_gemini(prompt: str) -> dict | None:
    """Repli quand Claude est indisponible : même prompt/schéma, envoyé à Gemini
    (même clé que la rédaction du résumé). Pas de désactivation globale sur une
    simple erreur ponctuelle — si Gemini est totalement en panne, le résumé
    lui-même échouera de toute façon avant d'arriver jusqu'ici.

    Sur un dépassement de quota par minute (429 RESOURCE_EXHAUSTED, fréquent en
    usage massif via --rejudge-only), on attend et on réessaie. Sur un compte à
    court de crédits, en revanche, on abandonne immédiatement pour tout le run :
    voir _gemini_billing_exhausted.
    """
    global _gemini_billing_exhausted
    if _gemini_billing_exhausted:
        return None
    for attempt in range(GEMINI_QUOTA_RETRIES):
        try:
            response = gemini_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=[prompt],
                config=GEMINI_JUDGE_CONFIG,
            )
            return json.loads(response.text)
        except Exception as e:
            err = str(e)
            if _is_billing_exhausted(err):
                _gemini_billing_exhausted = True
                print(f"[JUDGE Gemini — crédits épuisés, repli désactivé pour ce run] {e}")
                return None
            if ("RESOURCE_EXHAUSTED" in err or "429" in err) and attempt < GEMINI_QUOTA_RETRIES - 1:
                print(f"[JUDGE Gemini quota atteint, pause {GEMINI_QUOTA_BACKOFF}s "
                      f"({attempt + 1}/{GEMINI_QUOTA_RETRIES})]")
                time.sleep(GEMINI_QUOTA_BACKOFF)
                continue
            print(f"[JUDGE Gemini (repli) indisponible] {e}")
            return None
    return None


def judge_summary(text: str, summary: dict, gemini_fallback: bool = False) -> dict | None:
    """Vérifie le résumé. None si le judge est indisponible ou si sa réponse est
    inexploitable : la publication ne dépend jamais du judge.

    Essaie Claude en premier (modèle indépendant de celui qui rédige). Si Claude
    est indisponible et `gemini_fallback` est vrai, retente avec Gemini : moins
    fiable (même famille de modèle que la rédaction), donc marqué comme tel dans
    le résultat (`judge_model`, flag `judge_non_independant` via compute_score).
    """
    claims = [summary["accroche"], *summary["points"]]
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        texte=text[:JUDGE_TEXT_MAX_CHARS],
        categorie=summary["categorie"],
        categories=", ".join(CATEGORIES),
        claims="\n".join("- " + c.replace("\n", " ") for c in claims),
    )

    judgment, judge_model = _ask_claude(prompt), "claude"
    if judgment is None and gemini_fallback:
        judgment, judge_model = _ask_gemini(prompt), "gemini"
    if judgment is None:
        return None

    # Un judge qui ne rend pas exactement un verdict conforme par affirmation est
    # inexploitable : mieux vaut publier sans score que noter sur des données fausses.
    verdicts = judgment.get("verdicts", [])
    if len(verdicts) != len(claims) or any(v.get("verdict") not in VERDICT_POINTS for v in verdicts):
        print(f"[JUDGE réponse inexploitable] {len(verdicts)} verdict(s) pour {len(claims)} affirmation(s)")
        return None
    judgment["texte_tronque"] = len(text) > JUDGE_TEXT_MAX_CHARS
    judgment["judge_model"] = judge_model
    return judgment


def compute_score(judgment: dict) -> tuple[int, list[str]]:
    """Score 0-100 dérivé des verdicts par affirmation + checks catégorie/neutralité/correction."""
    verdicts = [v.get("verdict") for v in judgment.get("verdicts", [])]
    points = [VERDICT_POINTS.get(v, 0.0) for v in verdicts]
    points.append(1.0 if judgment.get("categorie_correcte") else 0.0)
    points.append(1.0 if judgment.get("neutralite") else 0.0)
    points.append(1.0 if judgment.get("correction") else 0.0)
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
    if not judgment.get("correction"):
        flags.append("faute_francais")
    if judgment.get("judge_model") == "gemini":
        flags.append("judge_non_independant")
    if judgment.get("texte_tronque"):
        flags.append("texte_tronque")
    return score, flags
