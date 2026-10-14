"""Test réel du bloc 4 : lecture (1-3) puis extraction par le modèle local, sur quelques PDF.

Affiche le JSON extrait (9 champs) et l'enregistre dans data/output/extractions_test.json.
⚠️ Le JSON contient des données personnelles (noms, adresses) : affichage local uniquement,
le dossier data/output/ n'est pas versionné.

Usage :
  python -m scripts.tester_extraction                → les 3 premiers PDF du corpus
  python -m scripts.tester_extraction 1234 5678      → les PDF de ces numéros de locataire
  python -m scripts.tester_extraction tous           → tout le corpus
"""
import json
import sys

from src.config import RACINE_PROJET, charger_parametres, charger_yaml
from src.exploitability import evaluer_exploitabilite
from src.extract import extraire_champs
from src.ingest import lire_pdf


def choisir_fichiers(dossier, arguments):
    """Sélectionne les PDF : 3 premiers par défaut, « tous », ou ceux des numéros donnés."""
    fichiers = sorted(dossier.glob("*.pdf"), key=lambda f: f.name.lower())
    if not arguments:
        return fichiers[:3]
    if arguments == ["tous"]:
        return fichiers
    return [f for f in fichiers if f.name.split("_")[0] in arguments]


def main() -> None:
    """Pour chaque PDF choisi : lecture → lisibilité → extraction (si lisible) → affichage du JSON."""
    parametres = charger_parametres()
    formulations = charger_yaml("config/mentions.yaml")["formulations_garantie"]
    dossier = RACINE_PROJET / parametres["chemins"]["corpus"]
    fichiers = choisir_fichiers(dossier, sys.argv[1:])
    if not fichiers:
        print("Aucun PDF correspondant.")
        return

    resultats = []
    for chemin in fichiers:
        lecture = lire_pdf(chemin, parametres)
        lisibilite = evaluer_exploitabilite(lecture, parametres)
        resultat = {"fichier": lecture["fichier"], "numero_entrepot": lecture["numero_entrepot"],
                    "voie_lecture": lecture["voie_lecture"], "exploitabilite": lisibilite["statut"]}
        # Document illisible : pas d'extraction (il ira en « douteux » à l'étape des règles)
        if lisibilite["statut"] == "exploitable":
            print(f"\n⏳ {lecture['numero_fichier']} : extraction en cours…", flush=True)
            resultat.update(extraire_champs(lecture["texte"], parametres, formulations))
        else:
            resultat["motif"] = lisibilite["motif"]
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        resultats.append(resultat)

    sortie = RACINE_PROJET / parametres["chemins"]["sortie"] / "extractions_test.json"
    sortie.write_text(json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ {len(resultats)} document(s) — résultats enregistrés dans {sortie.relative_to(RACINE_PROJET)}")


if __name__ == "__main__":
    main()
