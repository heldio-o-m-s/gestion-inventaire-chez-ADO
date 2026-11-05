import re
import unicodedata
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Gestion de Stock - QR", page_icon="📦", layout="centered")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SPREADSHEET_ID = "1yjYXiqY7uX1VpV4JVwTTKS3GIW5n1ktrReTxPWBi8WM"
SHEET_NAME = "Inventaire"  # nom exact de l'onglet
CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq"
    f"?tqx=out:csv&sheet={quote(SHEET_NAME)}"
)

COL_CODE = "Code ID"
COL_ARTICLE = "Article / Description"
COL_CAT = "Catégorie"
COL_SOUSCAT = "Sub-Catégorie"
COL_MODELE = "Modèle / Réf."
COL_QTE = "Qté en Stock"
COL_STATUT = "État / Statut"

TOUTES = "Toutes les zones"


# ---------------------------------------------------------------------------
# Lecture du Google Sheets (CSV public)
# ---------------------------------------------------------------------------
def _en_entier(serie: pd.Series) -> pd.Series:
    propre = (
        serie.fillna("")
        .astype(str)
        .str.replace(r"[\s\u00a0]", "", regex=True)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(propre, errors="coerce").fillna(0).astype(int)


def _norm(s) -> str:
    """Minuscules, sans accents, espaces normalisés : 'Qté  en Stock' -> 'qte en stock'."""
    s = unicodedata.normalize("NFKD", str(s).replace("\u00a0", " "))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().lower()


_CANON = {
    _norm(c): c
    for c in (COL_CODE, COL_ARTICLE, COL_CAT, COL_SOUSCAT, COL_MODELE, COL_QTE, COL_STATUT)
}
_FALLBACKS = [
    (r"^q.*stock", COL_QTE),
    (r"^(etat|statut)", COL_STATUT),
    (r"^(modele|marque|ref)", COL_MODELE),
    (r"^article|^description|^designation", COL_ARTICLE),
    (r"^sub|^sous", COL_SOUSCAT),
    (r"^categorie", COL_CAT),
]


def _nom_canonique(c) -> str:
    n = _norm(c)
    if n in _CANON:
        return _CANON[n]
    for motif, canon in _FALLBACKS:
        if re.search(motif, n):
            return canon
    return str(c).strip()


@st.cache_data(ttl=20)
def charger_donnees() -> pd.DataFrame:
    raw = pd.read_csv(CSV_URL, header=None, dtype=str)

    header_idx = None
    for idx, row in raw.iterrows():
        if row.astype(str).map(_norm).eq(_norm(COL_CODE)).any():
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError(
            f"Colonne '{COL_CODE}' introuvable dans l'onglet '{SHEET_NAME}'. "
            f"Première ligne lue : {[str(x) for x in raw.iloc[0].tolist()]}"
        )

    df = raw.iloc[header_idx + 1:].copy()
    noms = [_nom_canonique(c) for c in raw.iloc[header_idx]]

    # Google (gviz) efface le titre des colonnes numériques : « Qté en Stock »,
    # « Prix Unitaire » et « Valeur Totale » arrivent donc sans nom.
    # On les nomme d'après leur position, juste après « Modèle / Réf. ».
    if COL_QTE not in noms and COL_MODELE in noms:
        i = noms.index(COL_MODELE)
        for decalage, nom in enumerate((COL_QTE, "Prix Unitaire", "Valeur Totale"), start=1):
            if i + decalage < len(noms) and noms[i + decalage] == "nan":
                noms[i + decalage] = nom
            else:
                break

    df.columns = noms
    df = df.loc[:, df.columns != "nan"]

    requises = [COL_CODE, COL_ARTICLE, COL_CAT, COL_SOUSCAT, COL_MODELE, COL_QTE, COL_STATUT]
    manquantes = [c for c in requises if c not in df.columns]
    if manquantes:
        raise ValueError(
            f"Colonnes introuvables : {manquantes}. "
            f"Colonnes lues dans le Sheets : {[str(c) for c in df.columns]}"
        )

    df = df.dropna(subset=[COL_CODE])
    df = df[~df[COL_CODE].astype(str).str.contains("Total", case=False, na=False)]
    df = df.fillna("").reset_index(drop=True)
    df["_qte"] = _en_entier(df[COL_QTE])
    return df


# ---------------------------------------------------------------------------
# Écriture via Google Apps Script (voir Code.gs)
# ---------------------------------------------------------------------------
def ecriture_possible() -> bool:
    try:
        return "APPS_SCRIPT_URL" in st.secrets and "APPS_SCRIPT_SECRET" in st.secrets
    except Exception:
        return False


def envoyer(payload: dict) -> dict:
    payload = {**payload, "secret": st.secrets["APPS_SCRIPT_SECRET"]}
    r = requests.post(st.secrets["APPS_SCRIPT_URL"], json=payload, timeout=40)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            "Réponse inattendue d'Apps Script. Vérifiez le déploiement "
            "(Exécuter en tant que : moi / Accès : tout le monde)."
        )
    if not data.get("ok"):
        raise RuntimeError(data.get("erreur", "Erreur inconnue"))
    return data


def valider(payload: dict, message) -> None:
    """Envoie le mouvement, puis recharge la page avec un message de confirmation."""
    if not ecriture_possible():
        st.error(
            "Écriture non configurée : ajoutez APPS_SCRIPT_URL et APPS_SCRIPT_SECRET "
            "dans les Secrets de l'application."
        )
        return
    try:
        with st.spinner("Enregistrement…"):
            rep = envoyer(payload)
    except Exception as e:
        st.error(f"❌ Échec de l'enregistrement : {e}")
        return
    charger_donnees.clear()
    st.session_state["msg"] = message(rep)
    st.rerun()


# ---------------------------------------------------------------------------
# Utilitaires d'affichage
# ---------------------------------------------------------------------------
def filtrer(d: pd.DataFrame, texte: str) -> pd.DataFrame:
    if not texte:
        return d
    masque = (
        d[COL_ARTICLE].str.contains(texte, case=False, na=False, regex=False)
        | d[COL_CODE].str.contains(texte, case=False, na=False, regex=False)
        | d[COL_MODELE].str.contains(texte, case=False, na=False, regex=False)
    )
    return d[masque]


def libelle(r: pd.Series) -> str:
    modele = str(r[COL_MODELE]).strip()
    suffixe = f" [{modele}]" if modele not in ("", "-") else ""
    return f"{r[COL_CODE]} - {str(r[COL_ARTICLE]).strip()}{suffixe} (stock : {r['_qte']})"


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
st.title("📦 Gestion de Stock")

try:
    df = charger_donnees()
except Exception as e:
    st.error(f"❌ Erreur de connexion ou de chargement Google Sheets : {e}")
    st.caption(f"Source : {CSV_URL}")
    st.stop()

if "msg" in st.session_state:
    st.success(st.session_state.pop("msg"))

# Zone : préselectionnée par le QR Code (?zone=Plomberie)
categories = sorted(c for c in df[COL_CAT].unique() if str(c).strip())
options_zone = [TOUTES] + categories
zone_url = st.query_params.get("zone", "")
index_defaut = options_zone.index(zone_url) if zone_url in options_zone else 0
zone = st.selectbox("📍 Zone / Catégorie :", options_zone, index=index_defaut)

df_zone = df if zone == TOUTES else df[df[COL_CAT] == zone]

c1, c2, c3 = st.columns(3)
c1.metric("Articles", len(df_zone))
c2.metric("Quantité totale", int(df_zone["_qte"].sum()))
c3.metric("Stock bas", int(df_zone[COL_STATUT].str.contains("Bas", case=False, na=False).sum()))

operation = st.radio(
    "Opération :", ["SORTIE 🔴", "ENTRÉE 🟢", "Consulter 📋"], horizontal=True
)
operateur_defaut = st.session_state.get("operateur", "")

# ---------------------------------------------------------------------------
# SORTIE : équipement + quantité
# ---------------------------------------------------------------------------
if operation == "SORTIE 🔴":
    filtre = st.text_input("🔍 Rechercher (code, nom, modèle) :", key="f_sortie")
    df_sel = filtrer(df_zone, filtre)

    if df_sel.empty:
        st.warning("Aucun article ne correspond.")
    else:
        idx = st.selectbox(
            "Équipement :", list(df_sel.index), format_func=lambda i: libelle(df.loc[i])
        )
        ligne = df.loc[idx]
        stock = int(ligne["_qte"])
        st.info(f"**Stock actuel :** {stock} | **Statut :** {ligne[COL_STATUT]}")

        if stock <= 0:
            st.error("Stock épuisé : sortie impossible.")
        else:
            qte = st.number_input(
                "Quantité sortie :",
                min_value=1,
                max_value=stock,
                step=1,
                value=1,
                key=f"qte_sortie_{idx}_{stock}",
            )
            operateur = st.text_input("Votre nom (optionnel) :", value=operateur_defaut, key="op_sortie")

            if st.button("Valider la sortie", type="primary"):
                st.session_state["operateur"] = operateur
                nom = str(ligne[COL_ARTICLE]).strip()
                valider(
                    {
                        "action": "mouvement",
                        "code": ligne[COL_CODE],
                        "article": ligne[COL_ARTICLE],
                        "modele": ligne[COL_MODELE],
                        "delta": -int(qte),
                        "operateur": operateur,
                    },
                    lambda rep: f"✅ Sortie enregistrée : {int(qte)} × {nom}. Nouveau stock : {rep['nouveau']}",
                )

# ---------------------------------------------------------------------------
# ENTRÉE : équipement existant, ou nouvel article (équipement + marque + quantité)
# ---------------------------------------------------------------------------
elif operation == "ENTRÉE 🟢":
    mode = st.radio("Type d'entrée :", ["Article existant", "Nouvel article"], horizontal=True)

    if mode == "Article existant":
        filtre = st.text_input("🔍 Rechercher (code, nom, modèle) :", key="f_entree")
        df_sel = filtrer(df_zone, filtre)

        if df_sel.empty:
            st.warning("Aucun article ne correspond. Essayez « Nouvel article ».")
        else:
            idx = st.selectbox(
                "Équipement :", list(df_sel.index), format_func=lambda i: libelle(df.loc[i])
            )
            ligne = df.loc[idx]
            stock = int(ligne["_qte"])
            marque = str(ligne[COL_MODELE]).strip() or "-"
            st.info(f"**Marque / modèle :** {marque} | **Stock actuel :** {stock}")

            qte = st.number_input(
                "Quantité reçue :", min_value=1, step=1, value=1, key=f"qte_entree_{idx}_{stock}"
            )
            operateur = st.text_input("Votre nom (optionnel) :", value=operateur_defaut, key="op_entree")

            if st.button("Valider l'entrée", type="primary"):
                st.session_state["operateur"] = operateur
                nom = str(ligne[COL_ARTICLE]).strip()
                valider(
                    {
                        "action": "mouvement",
                        "code": ligne[COL_CODE],
                        "article": ligne[COL_ARTICLE],
                        "modele": ligne[COL_MODELE],
                        "delta": int(qte),
                        "operateur": operateur,
                    },
                    lambda rep: f"✅ Entrée enregistrée : {int(qte)} × {nom}. Nouveau stock : {rep['nouveau']}",
                )

    else:
        designation = st.text_input("Équipement / désignation :", key="n_designation")
        marque = st.text_input("Marque / modèle / réf. (mettre - si inconnu) :", key="n_marque")

        cat_defaut = categories.index(zone) if zone in categories else 0
        cat_new = st.selectbox("Catégorie :", categories, index=cat_defaut, key="n_cat")
        souscats = sorted(
            {s for s in df[df[COL_CAT] == cat_new][COL_SOUSCAT].unique() if str(s).strip()} | {"Général"}
        )
        souscat_new = st.selectbox("Sous-catégorie :", souscats, key="n_souscat")

        qte = st.number_input("Quantité reçue :", min_value=1, step=1, value=1, key="n_qte")
        operateur = st.text_input("Votre nom (optionnel) :", value=operateur_defaut, key="op_nouveau")

        if st.button("Ajouter le nouvel article", type="primary"):
            if not designation.strip() or not marque.strip():
                st.error("Indiquez l'équipement et la marque (ou - si inconnue).")
            else:
                doublon = df[
                    (df[COL_ARTICLE].str.strip().str.lower() == designation.strip().lower())
                    & (df[COL_MODELE].str.strip().str.lower() == marque.strip().lower())
                ]
                if not doublon.empty:
                    st.warning(
                        f"Cet article existe déjà ({doublon.iloc[0][COL_CODE]}). "
                        "Utilisez « Article existant »."
                    )
                else:
                    st.session_state["operateur"] = operateur
                    valider(
                        {
                            "action": "ajout",
                            "article": designation.strip(),
                            "modele": marque.strip(),
                            "categorie": cat_new,
                            "sous_categorie": souscat_new,
                            "quantite": int(qte),
                            "operateur": operateur,
                        },
                        lambda rep: (
                            f"✅ Nouvel article ajouté : {rep['code']} - {designation.strip()} "
                            f"({int(qte)} en stock)"
                        ),
                    )

# ---------------------------------------------------------------------------
# CONSULTER
# ---------------------------------------------------------------------------
else:
    recherche = st.text_input("🔍 Rechercher un article, un code ou un modèle :")
    seulement_bas = st.checkbox("Afficher uniquement le stock bas")

    df_affiche = filtrer(df_zone, recherche)
    if seulement_bas:
        df_affiche = df_affiche[df_affiche[COL_STATUT].str.contains("Bas", case=False, na=False)]

    colonnes = [c for c in [COL_CODE, COL_ARTICLE, COL_CAT, COL_MODELE, COL_QTE, COL_STATUT] if c in df.columns]
    st.dataframe(df_affiche[colonnes], use_container_width=True, hide_index=True)
    st.caption(f"{len(df_affiche)} article(s) affiché(s)")
