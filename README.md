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
