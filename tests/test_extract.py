"""Tests du bloc 4 (extraction + normalisation). Ollama est SIMULÉ : on contrôle la réponse du
modèle pour vérifier que la normalisation corrige / se méfie de lui. Rapides, sans réseau."""
import json

import pytest

from src import extract
from src.config import charger_parametres, charger_yaml

TEXTE = (
    "ATTESTATION D'ASSURANCE\nMACIF certifie que M. Jean DUPONT est assuré au titre du contrat "
    "multirisque habitation n° 123456789 garantissant la responsabilité civile de locataire pour "
    "le logement situé 12 rue des Lilas 75019 Paris, pour la période du 01.01.22 AU 31.12.22.\n"
    "Fait à Niort, le 1er avril 2022."
)


@pytest.fixture(scope="module")
def parametres():
    return charger_parametres()


@pytest.fixture(scope="module")
def formulations():
    return charger_yaml("config/mentions.yaml")["formulations_garantie"]


def _champ(brute, normalisee, statut="trouve"):
    return {"valeur_brute": brute, "valeur_normalisee": normalisee, "statut": statut}


def _reponse(**surcharges):
    """Réponse type d'un modèle qui a bien lu TEXTE ; on surcharge les champs à tester."""
    reponse = {
        "type_document": _champ("ATTESTATION D'ASSURANCE", "attestation_habitation"),
        "assureur": _champ("MACIF", "MACIF"),
        "assure_nom": _champ("M. Jean DUPONT", ["Jean DUPONT"]),
        "adresse_risque": _champ("12 rue des Lilas 75019 Paris", "12 rue des Lilas 75019 Paris"),
        "date_debut": _champ("du 01.01.22 AU 31.12.22", "2022-01-01"),
        "date_fin": _champ("du 01.01.22 AU 31.12.22", "2022-12-31"),
        "mention_garantie_locative": _champ("responsabilité civile de locataire", "presente"),
        "numero_police": _champ("n° 123456789", "123456789"),
        "date_emission": _champ("le 1er avril 2022", "2022-04-01"),
    }
    reponse.update(surcharges)
    return reponse


def _normaliser(reponse, parametres, formulations, texte=TEXTE):
    return extract.normaliser_reponse(reponse, texte, parametres, formulations)


class FauxOllama:
    """Remplace requests.post : renvoie successivement les contenus donnés."""

    def __init__(self, contenus):
        self.contenus, self.appels = list(contenus), 0

    def __call__(self, *args, **kwargs):
        self.appels += 1
        contenu = self.contenus.pop(0)

        class Reponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": contenu}}
        return Reponse()


# --- Appel du modèle et JSON mal formé ---------------------------------------------------------

def test_json_entoure_de_texte_est_relu():
    assert extract._parser_json('Voici :\n```json\n{"a": 1}\n```') == {"a": 1}


def test_json_mal_forme_puis_correct_reessai(monkeypatch, parametres, formulations):
    faux = FauxOllama(["pas du json {", json.dumps(_reponse())])
    monkeypatch.setattr(extract.requests, "post", faux)
    resultat = extract.extraire_champs(TEXTE, parametres, formulations)
    assert faux.appels == 2 and resultat["meta"]["essais"] == 2 and resultat["meta"]["erreur"] is None
    assert resultat["champs"]["assureur"]["valeur_normalisee"] == "MACIF"


def test_json_toujours_mal_forme_tout_ambigu(monkeypatch, parametres, formulations):
    monkeypatch.setattr(extract.requests, "post", FauxOllama(["n'importe quoi"] * 10))
    resultat = extract.extraire_champs(TEXTE, parametres, formulations)
    assert resultat["meta"]["erreur"]
    assert all(c["statut"] == "ambigu" for c in resultat["champs"].values())


# --- Cas nominal ---------------------------------------------------------------------------------

def test_cas_nominal_9_champs(parametres, formulations):
    champs = _normaliser(_reponse(), parametres, formulations)
    assert list(champs) == extract.CHAMPS
    assert all(set(c) == {"valeur_normalisee", "valeur_brute", "statut"} for c in champs.values())
    assert champs["date_debut"]["valeur_normalisee"] == "2022-01-01"
    assert champs["date_fin"]["valeur_normalisee"] == "2022-12-31"
    assert champs["date_emission"]["valeur_normalisee"] == "2022-04-01"
    assert champs["assure_nom"]["valeur_normalisee"] == ["Jean DUPONT"]
    assert all(c["statut"] == "trouve" for c in champs.values())


# --- Dates -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("brut, attendu", [
    ("1er avril 2023", "2023-04-01"),
    ("01/01/2023 au 31/12/2023", "2023-01-01"),
    ("01.01.22 AU 31.12.22", "2022-01-01"),
])
def test_formats_de_dates(brut, attendu):
    assert extract.extraire_dates(brut, "fr")[0].isoformat() == attendu


def test_date_de_fin_seule(parametres, formulations):
    texte = TEXTE.replace("pour la période du 01.01.22 AU 31.12.22", "valable jusqu'au 15/04/2023")
    champs = _normaliser(_reponse(date_debut=_champ(None, None, "absent"),
                                  date_fin=_champ("valable jusqu'au 15/04/2023", "2023-04-15")),
                         parametres, formulations, texte)
    assert champs["date_debut"] == {"valeur_normalisee": None, "valeur_brute": None, "statut": "absent"}
    assert champs["date_fin"]["valeur_normalisee"] == "2023-04-15"
    assert champs["date_fin"]["statut"] == "trouve"


def test_date_du_modele_contredite_par_le_texte(parametres, formulations):
    # Le modèle a mal converti (inversion jour/mois) : on garde la date relue, statut ambigu
    champs = _normaliser(_reponse(date_fin=_champ("du 01.01.22 AU 31.12.22", "2022-12-13")),
                         parametres, formulations)
    assert champs["date_fin"]["valeur_normalisee"] == "2022-12-31"
    assert champs["date_fin"]["statut"] == "ambigu"


def test_annee_seule_jamais_completee():
    assert extract.extraire_dates("exercice 2023", "fr") == []


def test_periode_inversee_ambigue(parametres, formulations):
    texte = TEXTE.replace("du 01.01.22 AU 31.12.22", "du 31.12.22 AU 01.01.22")
    champs = _normaliser(_reponse(date_debut=_champ("31.12.22", "2022-12-31"),
                                  date_fin=_champ("01.01.22", "2022-01-01")),
                         parametres, formulations, texte)
    assert champs["date_debut"]["statut"] == champs["date_fin"]["statut"] == "ambigu"


# --- Anti-hallucination ------------------------------------------------------------------------------

def test_valeur_absente_du_texte_devient_ambigue(parametres, formulations):
    champs = _normaliser(_reponse(assureur=_champ("AXA", "AXA")), parametres, formulations)
    assert champs["assureur"]["statut"] == "ambigu"


def test_nom_modifie_par_le_modele_devient_ambigu(parametres, formulations):
    # Cas vu sur le corpus : brut correct, mais le modèle a « corrigé » le nom en le normalisant
    champs = _normaliser(_reponse(assure_nom=_champ("M. Jean DUPONT", ["Jean DUPOND"])),
                         parametres, formulations)
    assert champs["assure_nom"]["statut"] == "ambigu"


def test_reformatage_autorise_reste_trouve(parametres, formulations):
    # Majuscules / virgule ajoutées par le modèle : mêmes mots → reste « trouve »
    champs = _normaliser(_reponse(adresse_risque=_champ("12 rue des Lilas 75019 Paris",
                                                        "12 Rue des Lilas, 75019 PARIS")),
                         parametres, formulations)
    assert champs["adresse_risque"]["statut"] == "trouve"


def test_type_document_hors_liste_ambigu(parametres, formulations):
    champs = _normaliser(_reponse(type_document=_champ("x", "facture")), parametres, formulations)
    assert champs["type_document"]["statut"] == "ambigu"
    assert champs["type_document"]["valeur_normalisee"] is None


# --- Mention de garantie (décidée par le texte, pas par le modèle) ---------------------------------

def test_mention_trouvee_meme_si_le_modele_dit_absente(parametres, formulations):
    champs = _normaliser(_reponse(mention_garantie_locative=_champ(None, "absente", "absent")),
                         parametres, formulations)
    assert champs["mention_garantie_locative"]["valeur_normalisee"] == "presente"


def test_mention_absente(parametres, formulations):
    texte = TEXTE.replace("multirisque habitation", "contrat").replace(
        "la responsabilité civile de locataire", "la garantie vol")
    champs = _normaliser(_reponse(mention_garantie_locative=_champ(None, None, "absent")),
                         parametres, formulations, texte)
    assert champs["mention_garantie_locative"] == {"valeur_normalisee": "absente", "valeur_brute": None,
                                                   "statut": "absent"}


def test_mention_abimee_par_ocr_reconnue(parametres, formulations):
    texte = "Le contrat couvre la responsabilite locatlve du locataire."
    mention = extract.detecter_mention(texte, {}, formulations, parametres["extraction"]["mention"])
    assert mention["valeur_normalisee"] == "presente"


def test_mention_hors_liste_citee_par_le_modele_indeterminee(parametres, formulations):
    texte = "Le contrat Habitat Plus couvre les dommages au logement occupé."
    mention = extract.detecter_mention(
        texte, {"mention_garantie_locative": _champ("couvre les dommages au logement", "presente")},
        formulations, parametres["extraction"]["mention"])
    assert mention["valeur_normalisee"] == "indeterminee" and mention["statut"] == "ambigu"
