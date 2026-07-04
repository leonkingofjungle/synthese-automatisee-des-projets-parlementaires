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
