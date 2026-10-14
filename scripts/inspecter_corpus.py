"""Inspection rapide des blocs 1-3 sur un dossier de PDF.

Affiche, pour chaque PDF : numéro de locataire, nb de pages, voie de lecture (texte/OCR),
longueur de texte, confiance OCR, exploitabilité. N'affiche aucun contenu du document.

Usage : python -m scripts.inspecter_corpus [dossier]   (défaut : data/corpus)
"""
import sys
import time
from pathlib import Path

from src.config import RACINE_PROJET, charger_parametres
from src.exploitability import evaluer_exploitabilite
from src.ingest import lire_pdf


def main() -> None:
    """Fait passer chaque PDF du dossier par les étapes 1-3 (lire_pdf puis evaluer_exploitabilite)
    et affiche une ligne de résumé par fichier. N'affiche jamais le texte des documents (RGPD).
    Dossier : celui donné dans la commande, sinon data/corpus (settings.yaml)."""
    # Préparation : réglages chargés une fois, choix du dossier, liste des PDF
    parametres = charger_parametres()
    dossier = Path(sys.argv[1]) if len(sys.argv) > 1 else RACINE_PROJET / parametres["chemins"]["corpus"]
    fichiers = sorted(dossier.glob("*.pdf"), key=lambda f: f.name.lower())
    if not fichiers:
        print(f"Aucun PDF dans {dossier}")
        return

    # En-tête du tableau
    entete = f"{'N° fichier':<10} {'N° entrepôt':<12} {'Pages':>5} {'Voie':<6} {'Car.':>6} {'Conf.OCR':>8} {'Score':>6}  {'Statut':<16} Motif"
    print(entete)
    print("-" * len(entete))
    # Une ligne par PDF : lecture (étapes 1-2) → lisibilité (étape 3) → affichage
    for chemin in fichiers:
        debut = time.perf_counter()
        try:
            lecture = lire_pdf(chemin, parametres)
            verdict = evaluer_exploitabilite(lecture, parametres)
        except Exception as erreur:  # un fichier en erreur ne bloque pas l'inspection
            print(f"{chemin.name[:10]:<10} ERREUR : {type(erreur).__name__}: {erreur}")
            continue
        # Mise en forme : confiance OCR (« - » si pas d'OCR) et détail par page « p1:voie/caractères/score »
        confiance = f"{verdict['confiance_ocr']:.0f}" if verdict["confiance_ocr"] is not None else "-"
        detail = " | ".join(
            f"p{p['numero_page']}:{p['voie']}/{p['nb_caracteres']}c/{s['score']}"
            for p, s in zip(lecture["pages"], verdict["pages"])
        )
        print(f"{lecture['numero_fichier']:<10} {lecture['numero_entrepot']:<12} {len(lecture['pages']):>5} "
              f"{lecture['voie_lecture']:<6} {verdict['nb_caracteres']:>6} {confiance:>8} "
              f"{verdict['score']:>6}  {verdict['statut']:<16} {verdict['motif']}  [{detail}]  ({time.perf_counter() - debut:.1f}s)")


if __name__ == "__main__":
    main()
