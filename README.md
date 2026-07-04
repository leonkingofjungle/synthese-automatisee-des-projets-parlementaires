# synthese-automatisee-des-projets-parlementaires

Résumés en langage clair des dernières propositions de loi françaises, générés par
IA à partir des textes officiels et publiés sur un site statique.

## Fonctionnement

```
API parlement.tricoteuses.fr (liste des textes)
  -> texte intégral (mirroir HTML opendata de l'AN, sinon PDF officiel)
  -> résumé structuré par Gemini (catégorie, accroche, points clés)
  -> vérification par un second modèle IA local (LM Studio) : score de fiabilité 0-100
  -> front/resumes.json, lu par le site statique front/index.html
```

Le score de fiabilité est calculé par un modèle indépendant de celui qui rédige
(vérification extractive : chaque affirmation du résumé doit figurer dans le texte
source). Sous 60/100 le résumé n'est pas publié (3 tentatives maximum, puis le
document est abandonné) ; sous 85/100 ou en cas d'anomalie détectée, le site
affiche un badge de prudence. Sans LM Studio, tout fonctionne : les résumés sont
simplement publiés sans score.

## Prérequis

- Python 3.11+ et `pip install -r requirements.txt`
- Une clé Gemini dans `.env` à la racine : `GEMINI_API_KEY=...` (voir `.env.example`)
- Optionnel — le judge local : [LM Studio](https://lmstudio.ai) en mode serveur avec
  un modèle chargé (par défaut `mistral-nemo-instruct-2407`). Variables `.env`
  optionnelles : `JUDGE_BASE_URL` (défaut `http://localhost:1234/v1`) et
  `JUDGE_MODEL` (l'identifiant exact affiché par LM Studio). Prévoir une fenêtre de
  contexte d'au moins 16k tokens.

## Générer les résumés

```bash
python3 scripts/pdf_summarizer_mcp.py --limit 10                 # 10 derniers textes
python3 scripts/pdf_summarizer_mcp.py --type PION --chambre AN   # défauts affichés
python3 scripts/pdf_summarizer_mcp.py --force                    # régénère la fenêtre courante
python3 scripts/pdf_summarizer_mcp.py --workers 10               # plus de parallélisme
```

`front/resumes.json` sert de cache : seuls les documents absents sont générés.
`--force` régénère les documents de la fenêtre `--limit` courante ; les résumés
hors fenêtre sont toujours préservés (pour tout régénérer : supprimer
`front/resumes.json` ou passer un `--limit` couvrant tout le corpus).

## Servir le site

À lancer **depuis `front/`**, jamais depuis la racine (le serveur exposerait `.env`) :

```bash
cd front && python3 -m http.server 8000
```

puis ouvrir <http://localhost:8000/>.
