"""Blocs 1-2 — Entrée et acquisition du texte.

- Charge un PDF depuis data/corpus/, extrait le numéro de locataire du nom de fichier.
- Extraction directe (PyMuPDF) page par page ; si une page renvoie trop peu de texte,
  elle est rasterisée (PyMuPDF, sans Poppler) puis passée à l'OCR (pytesseract, fra).
  La décision est prise PAR PAGE : un même PDF peut mêler une page texte et une page scannée.
"""
import io
import logging
import re
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image

journal = logging.getLogger(__name__)

MOTIF_NUMERO = re.compile(r"^(\d+)_")


def extraire_numero_locataire(chemin_pdf: Path, parametres: dict) -> dict:
    """Étape 1 : récupère le numéro de locataire au début du nom du fichier (chiffres avant le 1er « _ »)
    et le complète par des zéros à gauche pour obtenir le format entrepôt (11 caractères).
    Erreur si le nom ne commence pas par un numéro (on ne devine pas).

    Ex. « 1234_xxx.pdf » → {"numero_fichier": "1234", "numero_entrepot": "00000001234"}.
    """
    correspondance = MOTIF_NUMERO.match(Path(chemin_pdf).name)
    if not correspondance:
        raise ValueError(f"Nom de fichier sans numéro de locataire en préfixe : {Path(chemin_pdf).name}")
    numero_fichier = correspondance.group(1)
    config_numero = parametres["numero_locataire"]
    numero_entrepot = numero_fichier.rjust(config_numero["longueur_entrepot"],
                                           config_numero["caractere_remplissage"])
    return {"numero_fichier": numero_fichier, "numero_entrepot": numero_entrepot}


def compter_caracteres_utiles(texte: str) -> int:
    """Compte les caractères d'un texte sans les espaces ni les retours à la ligne
    (une page remplie d'espaces compte donc 0)."""
    return len(re.sub(r"\s", "", texte))


def _ocr_page(page: pymupdf.Page, config_ocr: dict) -> tuple[str, float | None]:
    """Lit une page scannée : la transforme en image (rasterisation), la fait lire par Tesseract,
    et renvoie (texte lu, confiance moyenne de la page sur 100 = moyenne des confiances de chaque mot)."""
    image_png = page.get_pixmap(dpi=config_ocr["dpi"]).tobytes("png")
    image = Image.open(io.BytesIO(image_png))
    donnees = pytesseract.image_to_data(image, lang=config_ocr["langue"],
                                        output_type=pytesseract.Output.DICT)
    confiances = [float(c) for c, mot in zip(donnees["conf"], donnees["text"])
                  if float(c) >= 0 and mot.strip()]
    confiance_moyenne = sum(confiances) / len(confiances) if confiances else None
    texte = pytesseract.image_to_string(image, lang=config_ocr["langue"])
    return texte, confiance_moyenne


def lire_pdf(chemin_pdf: Path, parametres: dict) -> dict:
    """Étapes 1-2 : lit un PDF page par page.
    Pour chaque page : lecture directe ; si moins de N caractères (settings.yaml), on tente l'OCR
    et on garde celui des deux qui a trouvé le plus de texte. Puis on range la fiche de la page.
    À la fin : voie du document (texte / ocr / mixte) et texte complet.

    Renvoie :
    {
      "fichier": nom du fichier,
      "numero_fichier", "numero_entrepot",
      "pages": [{"numero_page", "voie" ("texte"|"ocr"), "texte", "nb_caracteres", "confiance_ocr"}],
      "voie_lecture": "texte" | "ocr" | "mixte",
      "texte": texte complet (pages séparées par un saut de page \\f),
    }
    """
    chemin_pdf = Path(chemin_pdf)
    config_ocr = parametres["ocr"]
    if config_ocr.get("tesseract_cmd"):
        pytesseract.pytesseract.tesseract_cmd = config_ocr["tesseract_cmd"]
    seuil_bascule = config_ocr["min_caracteres_par_page"]

    resultat = {"fichier": chemin_pdf.name, **extraire_numero_locataire(chemin_pdf, parametres)}
    pages = []
    with pymupdf.open(chemin_pdf) as document:
        for index, page in enumerate(document, start=1):
            texte = page.get_text()
            voie, confiance = "texte", None
            if compter_caracteres_utiles(texte) < seuil_bascule:
                texte_ocr, confiance = _ocr_page(page, config_ocr)
                # On garde l'OCR sauf s'il fait pire que le peu de texte natif
                if compter_caracteres_utiles(texte_ocr) >= compter_caracteres_utiles(texte):
                    texte, voie = texte_ocr, "ocr"
                else:
                    confiance = None
            pages.append({
                "numero_page": index,
                "voie": voie,
                "texte": texte,
                "nb_caracteres": compter_caracteres_utiles(texte),
                "confiance_ocr": confiance,
            })

    voies = {p["voie"] for p in pages}
    resultat["voie_lecture"] = voies.pop() if len(voies) == 1 else ("mixte" if voies else "texte")
    resultat["pages"] = pages
    resultat["texte"] = "\f".join(p["texte"] for p in pages)
    # Journal sans donnée personnelle : numéro (pseudonyme), voie, volumes
    journal.info("Lu %s : %d page(s), voie=%s", resultat["numero_fichier"], len(pages),
                 resultat["voie_lecture"])
    return resultat
