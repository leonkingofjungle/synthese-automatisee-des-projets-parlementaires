# synthese-automatisee-des-projets-parlementaires

## Structure

- `front/index.html` — front statique (vanilla JS, sans backend) qui affiche les 10
  dernières propositions de loi et leur résumé, en interrogeant directement l'API
  publique `parlement.tricoteuses.fr`.
  Se lancer **depuis le dossier `front/`**, jamais depuis la racine du repo :
  `python3 -m http.server` sert tout le répertoire courant sans exception, y compris
  `old/.env` si on le lance à la racine.
  ```
  cd front && python3 -m http.server 8000
  ```
  puis ouvrir `http://localhost:8000/`.

- `scripts/pdf_summarizer_mcp.py` — script CLI qui affiche en console le résumé des
  N dernières lois via la même API (`--limit`, `--type`, `--chambre`, `--wait`).
  Aucune clé API, aucune base de données requise.
  ```
  pip install -r requirements.txt
  python3 scripts/pdf_summarizer_mcp.py --limit 10
  ```

- `old/` — ancienne pipeline (dépréciée) : télécharge les PDF depuis un bucket GCS
  (table Cloud SQL `DOCUMENTS_LIENS`), les résume avec l'API Gemini directement, et
  écrit le résultat dans Cloud SQL (`DOCUMENT_RESUMES`). Nécessite `old/.env`
  (voir `old/.env.example`) et `pip install -r old/requirements.txt`.
