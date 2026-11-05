import os
from urllib.parse import quote

import qrcode

# Lien de votre application Streamlit
URL_APPLICATION = "https://gestion-inventaire-ado-dwt4jg34bot6hrqgfwjqcb.streamlit.app/"

# Dossier où seront sauvegardées les images des QR Codes
DOSSIER_SORTIE = "qrcodes_url_application"
os.makedirs(DOSSIER_SORTIE, exist_ok=True)

print("==================================================")
print("   GÉNÉRATEUR DE QR CODES POUR APPLICATION WEB      ")
print("==================================================")

# Les noms doivent correspondre EXACTEMENT à la colonne « Catégorie » du Google Sheets.
# Ajoutez une ligne ici pour chaque nouveau rack / bac.
zones = ["Plomberie", "Électricité", "Quincaillerie & Outillage", "Divers"]

base = URL_APPLICATION.rstrip("/")

for zone in zones:
    # Le paramètre ?zone= ouvre directement l'application sur la bonne zone
    lien_complet = f"{base}/?zone={quote(zone, safe='')}"

    img = qrcode.make(lien_complet)

    nom_fichier = os.path.join(
        DOSSIER_SORTIE, f"QR_{zone.replace('&', 'et').replace(' ', '_')}.png"
    )
    img.save(nom_fichier)

    print(f"✅ QR Code généré pour la zone '{zone}' -> {nom_fichier}")
    print(f"   Lien : {lien_complet}")

# QR Code général (toutes les zones)
img = qrcode.make(f"{base}/")
nom_general = os.path.join(DOSSIER_SORTIE, "QR_Toutes_les_zones.png")
img.save(nom_general)
print(f"✅ QR Code général -> {nom_general}")

print(f"\n🎉 Les QR Codes prêts à être imprimés sont dans le dossier '{DOSSIER_SORTIE}' !")
