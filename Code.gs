/**
 * API de gestion de stock — à coller dans Google Sheets :
 * Extensions → Apps Script → remplacer le contenu par ce fichier.
 *
 * 1) Changez SECRET (mot de passe partagé avec l'application Streamlit).
 * 2) Déployer → Nouveau déploiement → Application Web
 *      Exécuter en tant que : Moi
 *      Qui a accès : Tout le monde
 * 3) Copiez l'URL de l'application Web (elle finit par /exec).
 */

const SECRET = "CHANGEZ-MOI";
const SHEET_NAME = "Inventaire";
const LOG_SHEET = "Mouvements";
const SEUIL_BAS = 2; // quantité <= 2  ->  "Stock Bas"

const C_CODE = "Code ID";
const C_ARTICLE = "Article / Description";
const C_CAT = "Catégorie";
const C_SOUSCAT = "Sub-Catégorie";
const C_MODELE = "Modèle / Réf.";
const C_QTE = "Qté en Stock";
const C_PRIX = "Prix Unitaire";
const C_STATUT = "État / Statut";

function doGet() {
  return reponse({ ok: true, message: "API stock active" });
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
    const d = JSON.parse(e.postData.contents);
    if (d.secret !== SECRET) return reponse({ ok: false, erreur: "Secret invalide" });

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sh = ss.getSheetByName(SHEET_NAME);
    if (!sh) throw new Error("Onglet introuvable : " + SHEET_NAME);
    const info = lireEntetes(sh);

    let res;
    if (d.action === "mouvement") res = mouvement(sh, info, d);
    else if (d.action === "ajout") res = ajout(sh, info, d);
    else throw new Error("Action inconnue : " + d.action);

    journal(ss, d, res);
    SpreadsheetApp.flush();
    return reponse(Object.assign({ ok: true }, res));
  } catch (err) {
    return reponse({ ok: false, erreur: String(err.message || err) });
  } finally {
    lock.releaseLock();
  }
}

// ---------------------------------------------------------------------------

function reponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(
    ContentService.MimeType.JSON
  );
}

function lireEntetes(sh) {
  const values = sh.getDataRange().getValues();
  let hIdx = -1;
  for (let i = 0; i < values.length; i++) {
    if (values[i].map((c) => String(c).trim()).indexOf(C_CODE) >= 0) {
      hIdx = i;
      break;
    }
  }
  if (hIdx < 0) throw new Error("En-tête '" + C_CODE + "' introuvable");
  const col = {}; // nom de colonne -> numéro de colonne (1 = A)
  values[hIdx].forEach((c, j) => {
    const nom = String(c).trim();
    if (nom) col[nom] = j + 1;
  });
  [C_CODE, C_ARTICLE, C_QTE].forEach((n) => {
    if (!col[n]) throw new Error("Colonne manquante : " + n);
  });
  return { values: values, hIdx: hIdx, col: col };
}

function nombre(v) {
  return Number(String(v).replace(/[\s\u00a0]/g, "").replace(",", ".")) || 0;
}

function texte(row, col, nom) {
  return col[nom] ? String(row[col[nom] - 1]).trim() : "";
}

function majStatut(sh, ligne, col, qte) {
  if (!col[C_STATUT]) return;
  const cell = sh.getRange(ligne, col[C_STATUT]);
  if (!cell.getFormula()) cell.setValue(qte <= SEUIL_BAS ? "Stock Bas" : "En Stock");
}

// Entrée / sortie sur un article existant (delta > 0 : entrée, delta < 0 : sortie)
function mouvement(sh, info, d) {
  const { values, hIdx, col } = info;
  const delta = Number(d.delta);
  if (!delta) throw new Error("Quantité invalide");

  // Le Code ID n'est pas toujours unique : on croise code + article + modèle
  for (let i = hIdx + 1; i < values.length; i++) {
    const row = values[i];
    if (/^total/i.test(texte(row, col, C_CODE))) break;
    if (
      texte(row, col, C_CODE) === String(d.code).trim() &&
      texte(row, col, C_ARTICLE) === String(d.article).trim() &&
      texte(row, col, C_MODELE) === String(d.modele).trim()
    ) {
      const ancien = nombre(row[col[C_QTE] - 1]);
      const nouveau = ancien + delta;
      if (nouveau < 0) throw new Error("Stock insuffisant (stock actuel : " + ancien + ")");

      const ligne = i + 1;
      sh.getRange(ligne, col[C_QTE]).setValue(nouveau);
      majStatut(sh, ligne, col, nouveau);

      return {
        type: delta < 0 ? "SORTIE" : "ENTRÉE",
        code: texte(row, col, C_CODE),
        article: texte(row, col, C_ARTICLE),
        modele: texte(row, col, C_MODELE),
        categorie: texte(row, col, C_CAT),
        quantite: Math.abs(delta),
        ancien: ancien,
        nouveau: nouveau,
      };
    }
  }
  throw new Error("Article introuvable dans la feuille (a-t-il été modifié ?)");
}

// Nouvel article : ajouté en bas de la liste, avant la ligne « Total Général »
function ajout(sh, info, d) {
  const { values, hIdx, col } = info;
  const qte = Number(d.quantite);
  if (!d.article || !qte || qte < 1) throw new Error("Données invalides");

  let maxN = 0;
  let totalRow = -1;
  let derniere = -1;
  for (let i = hIdx + 1; i < values.length; i++) {
    const code = texte(values[i], col, C_CODE);
    if (/^total/i.test(code)) {
      totalRow = i + 1;
      break;
    }
    if (code) derniere = i + 1;
    const m = code.match(/(\d+)$/);
    if (m) maxN = Math.max(maxN, parseInt(m[1], 10));
  }
  const code = "INV-" + String(maxN + 1).padStart(3, "0");
  const lastCol = sh.getLastColumn();

  let cible;
  if (totalRow > 0 && derniere > hIdx + 1) {
    // On insère AVANT la dernière ligne (donc à l'intérieur des formules SOMME),
    // puis on remonte la dernière ligne d'un cran et on écrit le nouvel article en dessous.
    sh.insertRowBefore(derniere);
    sh.getRange(derniere + 1, 1, 1, lastCol).copyTo(sh.getRange(derniere, 1, 1, lastCol));
    cible = derniere + 1;
  } else if (totalRow > 0) {
    sh.insertRowBefore(totalRow);
    cible = totalRow;
  } else {
    cible = (derniere > 0 ? derniere : sh.getLastRow()) + 1;
    if (derniere > 0) sh.getRange(derniere, 1, 1, lastCol).copyTo(sh.getRange(cible, 1, 1, lastCol));
  }

  const ecrire = (nom, v) => {
    if (col[nom]) sh.getRange(cible, col[nom]).setValue(v);
  };
  ecrire(C_CODE, code);
  ecrire(C_ARTICLE, String(d.article).trim());
  ecrire(C_CAT, d.categorie || "Divers");
  ecrire(C_SOUSCAT, d.sous_categorie || "Général");
  ecrire(C_MODELE, String(d.modele || "-").trim());
  ecrire(C_PRIX, "");
  ecrire(C_QTE, qte);
  majStatut(sh, cible, col, qte);

  return {
    type: "ENTRÉE (nouvel article)",
    code: code,
    article: String(d.article).trim(),
    modele: String(d.modele || "-").trim(),
    categorie: d.categorie || "Divers",
    quantite: qte,
    ancien: 0,
    nouveau: qte,
  };
}

// Journal des mouvements (onglet « Mouvements », créé automatiquement).
// Sert de base pour les notifications plus tard.
function journal(ss, d, r) {
  let j = ss.getSheetByName(LOG_SHEET);
  if (!j) {
    j = ss.insertSheet(LOG_SHEET);
    j.appendRow([
      "Date", "Type", "Code ID", "Article", "Modèle / Réf.",
      "Quantité", "Stock après", "Opérateur", "Zone",
    ]);
    j.setFrozenRows(1);
  }
  j.appendRow([
    new Date(), r.type, r.code, r.article, r.modele,
    r.quantite, r.nouveau, d.operateur || "", r.categorie || "",
  ]);
}
