# Résumés parlementaires

Ce projet transforme les propositions de loi françaises récentes en résumés courts et
en langage clair, vérifiés automatiquement, et les publie sur un site web statique.

Concrètement : un script Python va chercher les derniers textes déposés à l'Assemblée
nationale, les fait résumer par une IA (Gemini), fait vérifier ce résumé par une
seconde IA indépendante (Claude), puis écrit tout ça dans un fichier JSON. Un site
100 % statique (pas de serveur, pas de base de données) affiche ce fichier.

## En bref

- **Source** : l'API publique du projet [Tricoteuses](https://parlement.tricoteuses.fr)
  et les textes officiels de l'Assemblée nationale.
- **Résumé** : chaque proposition de loi devient une accroche + 3-4 points clés,
  écrits pour un lecteur sans connaissance juridique.
- **Vérification** : un second modèle d'IA relit chaque résumé et lui attribue un
  score de fiabilité sur 100 (voir plus bas). En dessous de 60/100, le résumé n'est
  pas publié.
- **Site** : HTML/CSS/JS vanilla, sans framework ni build, hébergé sur OVH et déployé
  automatiquement à chaque push sur `main`.

## Comment ça marche

```
parlement.tricoteuses.fr (liste des textes)
  -> texte intégral (mirroir HTML opendata de l'AN, sinon PDF officiel)
  -> résumé structuré par Gemini (catégorie, accroche, points clés)
  -> vérification par Claude (modèle indépendant) : score de fiabilité 0-100
  -> état d'avancement du texte (dossier législatif : en commission, adopté, ...)
  -> front/resumes.json, seul fichier lu par le site statique
```

Le script (`scripts/pdf_summarizer_mcp.py`) est le seul point d'entrée. Il fait tout :
récupérer les textes, appeler les deux IA, et écrire le résultat. Le site (`front/`)
ne fait ensuite qu'afficher ce fichier JSON, sans jamais interroger d'API lui-même.

## Score de fiabilité

Le vérificateur (un modèle Claude indépendant de celui qui rédige) relit l'accroche et
chaque point clé un par un, et attribue à chacun l'une de ces trois notes :

| Verdict | Points | Signification |
| --- | --- | --- |
| Vérifiée | 1 | L'information figure telle quelle dans le texte officiel |
| Approximative | 0,4 | Elle s'appuie sur un passage réel, mais l'exagère ou l'extrapole |
| Inventée | 0 | Elle ne se retrouve nulle part dans le texte officiel |

Deux contrôles supplémentaires s'ajoutent, notés eux aussi 1 ou 0 point : la catégorie
attribuée correspond-elle à l'une des rubriques définies, et le ton reste-t-il neutre
(aucune opinion ni qualificatif absent du texte source) ?

Le score sur 100 est la moyenne de tous ces points (accroche, points clés, catégorie,
neutralité). En dessous de 60/100, le résumé est jugé trop peu fiable et n'est pas
publié (3 tentatives, puis le document est abandonné). Entre 60 et 85, il est publié
avec un badge de prudence. Sans clé Claude, tout fonctionne quand même : les résumés
sont simplement publiés sans score.

## Structure du projet

```
scripts/
  pdf_summarizer_mcp.py   CLI : récupère les textes, orchestre tout, écrit resumes.json
  summarizer.py           prompt + schéma du résumé Gemini
  judge.py                prompt + schéma du vérificateur Claude, seuils de publication
  clients.py              connexions Gemini/Claude (config .env)
  judge_criteria.yaml      grille de critères (documentation, non utilisée par le code)
  single_doc.py           teste tout le pipeline sur un seul document, sans rien écrire

front/                    site statique, aucune dépendance, aucun build
  index.html              accueil : recherche, "à la une", stats, rubriques
  categorie.html          toutes les propositions d'une rubrique
  loi.html                page de détail d'une proposition
  app.js                  logique et rendu partagés entre les pages
  styles.css              toute la mise en forme
  resumes.json            données générées par le script, lues par le site

.github/workflows/        déploiement automatique (voir plus bas)
```

## Installation

```bash
pip install -r requirements.txt
```

Créer un fichier `.env` à la racine (voir `.env.example`) :

```
GEMINI_API_KEY=...        # obligatoire : génère les résumés
CLAUDE_API_KEY=...        # optionnel : active la vérification (score de fiabilité)
```

## Utilisation

```bash
# Générer/rafraîchir les résumés
python3 scripts/pdf_summarizer_mcp.py --limit 10                 # 10 derniers textes
python3 scripts/pdf_summarizer_mcp.py --force                    # régénère la fenêtre courante
python3 scripts/pdf_summarizer_mcp.py --workers 10                # plus de parallélisme

# Rafraîchir uniquement l'état d'avancement des textes déjà résumés (aucun appel IA)
python3 scripts/pdf_summarizer_mcp.py --limit 0

# Tester tout le pipeline sur un seul texte, sans toucher à resumes.json
python3 scripts/single_doc.py PIONANR5L17BTC3004
```

`front/resumes.json` sert de cache : seuls les documents absents sont générés, les
autres sont préservés. Le fichier est commité dans le dépôt : le site fonctionne donc
sans que personne n'ait besoin de relancer le script.

### Servir le site en local

À lancer **depuis `front/`**, jamais depuis la racine du dépôt (sinon le serveur
exposerait aussi `.env`) :

```bash
cd front && python3 -m http.server 8000
```

puis ouvrir <http://localhost:8000/>.

## Déploiement

Chaque push sur `main` déclenche `.github/workflows/deploy.yml`, qui envoie le
contenu de `front/` par FTP vers l'hébergement OVH. Rien d'autre n'est déployé.

## Pour aller plus loin

Le détail technique complet (fonctionnement du cache, du juge, des statuts
législatifs, conventions internes...) est documenté dans [CLAUDE.md](CLAUDE.md).



# Parliamentary Summaries (EN)

This project transforms recent French bills into short, plain-language summaries—automatically verified—and publishes them on a static website.

In practical terms: a Python script retrieves the latest bills tabled at the National Assembly, has them summarized by an AI (Gemini), has that summary verified by a second, independent AI (Claude), and then writes the result to a JSON file. A 100% static site (no server, no database) displays this file.

## In brief

- **Source**: the public API from the [Tricoteuses](https://parlement.tricoteuses.fr) project and official texts from the French National Assembly.
- **Summary**: each bill becomes a short hook + 3–4 key points written for readers without legal background.
- **Verification**: a second AI model independently reviews each summary and assigns a reliability score out of 100 (see below). Below 60/100, the summary is not published.
- **Site**: vanilla HTML/CSS/JS, no framework or build step, hosted on OVH and automatically deployed on every push to `main`.

## How it works

```
parlement.tricoteuses.fr API (list of bills)
  -> full text (HTML mirror from the French National Assembly open data, otherwise the official PDF)
  -> structured summary generated by Gemini (category, headline, key points)
  -> verification by a second local AI model (LM Studio): reliability score from 0 to 100
  -> front/resumes.json, read by the static website at front/index.html
```

The script (`scripts/pdf_summarizer_mcp.py`) is the single entry point. It does everything: fetching texts, calling both AIs, and writing the output. The frontend (`front/`) only renders the JSON file and never calls any API.

## Reliability score

The verifier (a Claude model independent from the one that writes summaries) reviews the hook and each key point individually and assigns one of three scores:

| Verdict | Points | Meaning |
| --- | --- | --- |
| Verified | 1 | The information appears exactly in the official text |
| Approximate | 0.4 | It is based on a real passage but exaggerates or extrapolates it |
| Invented | 0 | The information does not appear anywhere in the official text |

Two additional checks are added, also scored 1 or 0: does the assigned category match one of the defined sections, and is the tone neutral (no opinion or added qualifiers not present in the source text)?

The final score (out of 100) is the average of all these components (hook, key points, category, neutrality). Below 60/100, the summary is considered too unreliable and is not published (3 attempts, then the document is discarded). Between 60 and 85, it is published with a caution badge. Without a Claude key, everything still works, but summaries are published without a score.

## Project structure

```
scripts/
  pdf_summarizer_mcp.py   CLI: fetches texts, orchestrates everything, writes resumes.json
  summarizer.py           prompt + schema for Gemini summarization
  judge.py                prompt + schema for Claude verifier, publication thresholds
  clients.py              Gemini/Claude connections (.env config)
  judge_criteria.yaml     criteria grid (documentation only, not used in code)
  single_doc.py           tests the full pipeline on a single document, without writing output

front/                    static site, no dependencies, no build step
  index.html home:        search, "featured", stats, categories
  categorie.html          list of proposals by category
  loi.html                proposal detail page
  app.js                  shared logic and rendering across pages
  styles.css              all styling
  resumes.json            generated dataset consumed by the site

.github/workflows/        automatic deployment (see below)
```


## Installation

```bash
pip install -r requirements.txt
```

Create a .env file at the project root (see .env.example):

```bash
GEMINI_API_KEY=...        # required: generates summaries
CLAUDE_API_KEY=...        # optional: enables verification (reliability scoring)
```
## Usage

```bash
# Generate / refresh summaries
python3 scripts/pdf_summarizer_mcp.py --limit 10        # last 10 documents
python3 scripts/pdf_summarizer_mcp.py --force           # regenerate current window
python3 scripts/pdf_summarizer_mcp.py --workers 10      # increase parallelism

# Refresh only legislative status for already-summarized texts (no AI calls)
python3 scripts/pdf_summarizer_mcp.py --limit 0

# Test the full pipeline on a single document without touching resumes.json
python3 scripts/single_doc.py PIONANR5L17BTC3004
```

`front/resumes.json` acts as a cache: only missing documents are generated, existing ones are preserved. The file is committed to the repository, so the site works without anyone needing to rerun the script.


### Open the website locally

Must be run **from `front/`**, never from the repository root (otherwise the server
would expose `.env`) :

```bash
cd front && python3 -m http.server 8000
```
Then open http://localhost:8000/.


## Deployment

Each push to `main` triggers `.github/workflows/deploy.yml`, which uploads the contents of `front/` via FTP to OVH hosting. Nothing else is deployed.

## Further reading

The full technical details (cache behavior, judge logic, legislative status tracking, internal conventions, etc.) are documented in [CLAUDE.md](CLAUDE.md).