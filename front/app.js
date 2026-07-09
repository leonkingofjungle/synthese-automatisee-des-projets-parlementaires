// Ordre d'affichage des sections thématiques (doit rester cohérent avec
// CATEGORIES dans scripts/pdf_summarizer_mcp.py). Toute catégorie inconnue
// (ancien cache, valeur inattendue) atterrit dans "Autre".
export const CATEGORY_ORDER = [
  "Institutions et démocratie",
  "Justice et sécurité",
  "Santé",
  "Éducation et jeunesse",
  "Environnement et énergie",
  "Économie et travail",
  "Logement et urbanisme",
  "Société et solidarités",
  "Numérique",
  "International et outre-mer",
  "Autre",
];

// Slug (pour data-cat / CSS) + libellé court affiché à la place du nom
// complet de la catégorie (badges et en-têtes de section).
export const CATEGORY_META = {
  "Institutions et démocratie": { slug: "institutions", short: "Institutions" },
  "Justice et sécurité": { slug: "justice", short: "Justice" },
  "Santé": { slug: "sante", short: "Santé" },
  "Éducation et jeunesse": { slug: "education", short: "Éducation" },
  "Environnement et énergie": { slug: "environnement", short: "Environnement" },
  "Économie et travail": { slug: "economie", short: "Économie" },
  "Logement et urbanisme": { slug: "logement", short: "Logement" },
  "Société et solidarités": { slug: "societe", short: "Société" },
  "Numérique": { slug: "numerique", short: "Numérique" },
  "International et outre-mer": { slug: "outremer", short: "Outre-mer" },
  "Autre": { slug: "autre", short: "Autre" },
};

export function resolveCategory(raw) {
  return CATEGORY_ORDER.includes(raw) ? raw : "Autre";
}

export function getMeta(raw) {
  return CATEGORY_META[resolveCategory(raw)];
}

export function formatDate(iso) {
  if (!iso) return "date inconnue";
  return new Date(iso).toLocaleDateString("fr-FR", { day: "2-digit", month: "long", year: "numeric" });
}

export function hasUsableResume(doc) {
  return typeof doc.accroche === "string" && doc.accroche.trim().length > 0;
}

// Sous-titre = ce qui suit "vise à" jusqu'au premier point. Les accroches
// qui n'utilisent pas cette tournure ("Simplifie…", "Cette proposition de
// loi…", etc.) n'ont pas de "vise à" à extraire : on retombe alors sur
// l'accroche complète (toujours une phrase unique se terminant par un point).
export function extractSubtitle(accroche) {
  const text = (accroche || "").trim();
  if (!text) return "";
  const marker = text.match(/vise\s*à\s+/i);
  if (marker) {
    const start = marker.index + marker[0].length;
    const rest = text.slice(start);
    const dotIdx = rest.indexOf(".");
    const cut = (dotIdx !== -1 ? rest.slice(0, dotIdx) : rest).trim();
    return cut.charAt(0).toUpperCase() + cut.slice(1);
  }
  return text.replace(/\.$/, "");
}

export function byDateDesc(a, b) {
  return (b.date_depot || "").localeCompare(a.date_depot || "");
}

function searchIndexOf(doc) {
  return `${doc.titre} ${doc.accroche} ${(doc.points || []).join(" ")}`.toLowerCase();
}

/* ---------- État d'avancement (dossier législatif) ----------
   Le pipeline persiste l'`etat` et le `statut` bruts du dossier
   (ex. "En cours" / "1ère lecture en commission") ; on les traduit en une
   petite liste d'étapes lisibles par tout le monde. Vocabulaire inconnu :
   on affiche le libellé officiel tel quel plutôt que rien. */
export const STAGES = {
  deposee:    { label: "Déposée",            tone: "neutral" },
  commission: { label: "En commission",      tone: "progress" },
  seance:     { label: "En séance publique", tone: "progress" },
  discussion: { label: "En discussion",      tone: "progress" },
  cmp:        { label: "Commission mixte",   tone: "progress" },
  conseil:    { label: "Conseil constit.",   tone: "progress" },
  adoptee:    { label: "Adoptée",            tone: "success" },
  promulguee: { label: "Promulguée",         tone: "success" },
  rejetee:    { label: "Rejetée",            tone: "danger" },
  retiree:    { label: "Retirée",            tone: "muted" },
  caduque:    { label: "Caduque",            tone: "muted" },
};
export const STAGE_ORDER = [...Object.keys(STAGES), "autre"];

function normalize(text) {
  return (text || "").toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");
}

// Ordre significatif : issues définitives d'abord (« Rejeté en 1ère lecture »
// doit donner Rejetée, pas En discussion), puis étapes en cours du plus
// spécifique au plus générique ("1ère lecture en commission" contient aussi
// "lecture", "Commission mixte paritaire" contient aussi "commission").
const STAGE_RULES = [
  ["promulg", "promulguee"],
  ["rejet", "rejetee"],
  ["retrait", "retiree"],
  ["retir", "retiree"],
  ["caduc", "caduque"],
  ["mixte paritaire", "cmp"],
  ["conseil constitutionnel", "conseil"],
  ["commission", "commission"],
  ["seance", "seance"],
  ["lecture", "discussion"],
  ["discussion", "discussion"],
  ["adopt", "adoptee"],
  ["depose", "deposee"],
];

const ETAT_RULES = [
  ["promulg", "promulguee"],
  ["adopt", "adoptee"],
  ["rejet", "rejetee"],
  ["retir", "retiree"],
  ["caduc", "caduque"],
  ["en cours", "discussion"],
  ["depose", "deposee"],
];

export function getStage(doc) {
  const raw = doc.statut || doc.etat || "";
  for (const [rules, value] of [[STAGE_RULES, normalize(doc.statut)], [ETAT_RULES, normalize(doc.etat)]]) {
    for (const [needle, key] of rules) {
      if (value.includes(needle)) return { key, ...STAGES[key], raw };
    }
  }
  if (raw) return { key: "autre", label: raw, tone: "neutral", raw };
  return null;
}

export function renderStatusBadge(doc) {
  const stage = getStage(doc);
  if (!stage) return null;
  const span = document.createElement("span");
  span.className = "status-badge";
  span.dataset.tone = stage.tone;
  span.textContent = stage.label;
  if (stage.raw && stage.raw !== stage.label) span.title = stage.raw;
  return span;
}

/* ---------- Fiabilité (judge) ----------
   quality_score : 0-100 ou null (résumé depuis le PDF, non vérifiable).
   En dessous du seuil, ou dès qu'un flag est levé, le badge passe en ambre. */
export const QUALITY_BADGE_THRESHOLD = 85;

export const FLAG_LABELS = {
  invention: "une affirmation du résumé n'a pas été retrouvée dans le texte officiel",
  deformation: "une affirmation du résumé déforme le texte officiel",
  categorie_incertaine: "la rubrique attribuée est incertaine",
  neutralite_douteuse: "la neutralité du résumé est douteuse",
  faute_francais: "le résumé contient une ou plusieurs fautes de français",
  judge_non_independant: "vérifié par repli, avec le même modèle que celui qui a rédigé le résumé",
  texte_tronque: "texte long : seule la première partie a pu être vérifiée",
};

export function qualityLevel(doc) {
  const flags = doc.quality_flags || [];
  const hasScore = typeof doc.quality_score === "number";
  if ((hasScore && doc.quality_score < QUALITY_BADGE_THRESHOLD) || flags.length > 0) return "warn";
  return hasScore ? "ok" : null;
}

export function describeFlags(doc) {
  return (doc.quality_flags || []).map(f => FLAG_LABELS[f] || f);
}

export function renderQualityBadge(doc) {
  const level = qualityLevel(doc);
  if (!level) return null;
  const span = document.createElement("span");
  span.className = "quality-badge";
  span.dataset.level = level;
  span.textContent = typeof doc.quality_score === "number"
    ? `Fiabilité ${doc.quality_score}/100`
    : "Fiabilité à vérifier";
  const details = describeFlags(doc);
  span.title = details.length > 0
    ? `Points de vigilance : ${details.join(" ; ")}.`
    : "Score de vérification automatique : part des affirmations du résumé retrouvées dans le texte officiel.";
  return span;
}

/* ---------- Rendu partagé d'une proposition (index + page rubrique) ---------- */
export function metaLine(doc) {
  const meta = getMeta(doc.categorie);
  const p = document.createElement("p");
  p.className = "law-meta";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.dataset.cat = meta.slug;
  badge.title = resolveCategory(doc.categorie);
  badge.textContent = meta.short;
  const time = document.createElement("time");
  if (doc.date_depot) time.dateTime = doc.date_depot;
  time.textContent = formatDate(doc.date_depot);
  p.append(badge, time);
  const status = renderStatusBadge(doc);
  if (status) p.appendChild(status);
  const quality = renderQualityBadge(doc);
  if (quality) p.appendChild(quality);
  return p;
}

export function baseLink(doc, className) {
  const a = document.createElement("a");
  a.className = className;
  a.href = `loi.html?uid=${encodeURIComponent(doc.uid)}`;
  a.dataset.cat = getMeta(doc.categorie).slug;
  a.dataset.category = resolveCategory(doc.categorie);
  a.dataset.searchIndex = searchIndexOf(doc);
  return a;
}

export function renderRow(doc) {
  const row = baseLink(doc, "law-row");
  const title = document.createElement("h3");
  title.className = "law-title";
  title.textContent = doc.titre;
  row.appendChild(title);
  const subtitle = extractSubtitle(doc.accroche);
  if (subtitle) {
    const p = document.createElement("p");
    p.className = "law-excerpt";
    p.textContent = subtitle;
    row.appendChild(p);
  }
  row.appendChild(metaLine(doc));
  return row;
}

/* ---------- Statistiques (bloc « En chiffres ») ----------
   Fenêtre glissante sur la date de dépôt ; days = null pour tout l'historique.
   Comparaison de chaînes suffisante : dates ISO YYYY-MM-DD. */
export function computeStats(docs, days) {
  let windowDocs = docs;
  if (days) {
    const cutoff = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
    windowDocs = docs.filter(d => (d.date_depot || "") >= cutoff);
  }

  const catCounts = new Map();
  const stageCounts = new Map();
  let adopted = 0;
  let verified = 0;
  for (const doc of windowDocs) {
    const cat = resolveCategory(doc.categorie);
    catCounts.set(cat, (catCounts.get(cat) || 0) + 1);
    const stage = getStage(doc);
    if (stage) {
      stageCounts.set(stage.key, (stageCounts.get(stage.key) || 0) + 1);
      if (stage.key === "adoptee" || stage.key === "promulguee") adopted++;
    }
    if (typeof doc.quality_score === "number" && doc.quality_score >= QUALITY_BADGE_THRESHOLD) verified++;
  }

  const byCategory = [...catCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([cat, count]) => ({ categorie: cat, ...CATEGORY_META[cat], count }));
  const byStage = STAGE_ORDER
    .filter(key => stageCounts.has(key))
    .map(key => ({ key, label: key === "autre" ? "Autre étape" : STAGES[key].label, count: stageCounts.get(key) }));

  return { total: windowDocs.length, byCategory, byStage, adopted, verified };
}

export async function getResumes() {
  const res = await fetch("resumes.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`http ${res.status}`);
  return res.json();
}

export function renderNotice(kind, text) {
  const div = document.createElement("div");
  div.className = `notice ${kind}`;
  div.textContent = text;
  return div;
}
