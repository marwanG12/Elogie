"""Tests des blocs 5-6 (règles + arbre) sur des champs synthétiques. Aucune donnée réelle."""
from datetime import date

import pytest

from src import rules
from src.config import charger_parametres, charger_yaml
from src.ingest import charger_declaratif, extraire_date_depot

DATE_REF = date(2026, 10, 14)


@pytest.fixture(scope="module")
def parametres():
    return charger_parametres()


@pytest.fixture(scope="module")
def assureurs():
    return charger_yaml("config/assureurs.yaml")


def _champ(normalisee, statut="trouve", brute=None):
    return {"valeur_normalisee": normalisee if statut != "absent" else None,
            "valeur_brute": brute or normalisee, "statut": statut}


def _champs(**surcharges):
    """Attestation conforme type ; on surcharge les champs à tester."""
    champs = {
        "type_document": _champ("attestation_habitation"),
        "assureur": _champ("MACIF"),
        "assure_nom": _champ(["Jean DUPONT"]),
        "adresse_risque": _champ("12 rue des Lilas 75019 Paris"),
        "date_debut": _champ("2026-01-01"),
        "date_fin": _champ("2026-12-31"),
        "mention_garantie_locative": _champ("presente", brute="responsabilité locative"),
        "numero_police": _champ("123456789"),
        "date_emission": _champ("2026-01-02"),
    }
    champs.update(surcharges)
    return champs


EXPLOITABLE = {"statut": "exploitable", "motif": "ok"}
REFERENCE = {"titulaires": ["DUPONT Jean", "MARTIN Marie"], "adresse": "12 R. DES LILAS 75019 PARIS"}
DECLARATIF = {"assureur": "MACIF", "police": "123456789",
              "date_debut": date(2026, 1, 1), "date_fin": date(2026, 12, 31)}


def _decider(parametres, assureurs, champs=None, reference=REFERENCE, declaratif=DECLARATIF,
             exploitabilite=EXPLOITABLE, voie="texte"):
    return rules.decider(exploitabilite, champs or _champs(), reference, declaratif, parametres,
                         assureurs, DATE_REF, voie)


def _resultat(decision, regle):
    return [c["resultat"] for c in decision["controles"] if c["regle"] == regle]


# --------------------------------------------------------------------------- arbre


def test_attestation_conforme(parametres, assureurs):
    decision = _decider(parametres, assureurs)
    assert decision["verdict"] == "conforme", decision["motif"]


def test_non_exploitable_douteux_avant_tout(parametres, assureurs):
    decision = _decider(parametres, assureurs, champs=_champs(type_document=_champ("autre")),
                        exploitabilite={"statut": "non_exploitable", "motif": "page blanche"})
    assert decision["verdict"] == "douteux"
    assert len(decision["controles"]) == 1


def test_avis_echeance_non_conforme(parametres, assureurs):
    decision = _decider(parametres, assureurs, champs=_champs(type_document=_champ("autre")))
    assert decision["verdict"] == "non_conforme"


def test_type_incertain_douteux(parametres, assureurs):
    decision = _decider(parametres, assureurs, champs=_champs(type_document=_champ(None, "ambigu")))
    assert decision["verdict"] == "douteux"


def test_non_conforme_l_emporte_sur_douteux(parametres, assureurs):
    champs = _champs(date_fin=_champ("2026-06-30"), assure_nom=_champ(None, "ambigu"))
    decision = _decider(parametres, assureurs, champs=champs)
    assert decision["verdict"] == "non_conforme"
    assert decision["motif"].startswith("[A.4]")  # le plus grave d'abord, tous les motifs conservés
    assert "[A.2]" in decision["motif"]


def test_motifs_sans_donnees_personnelles(parametres, assureurs):
    champs = _champs(assure_nom=_champ(["Paul DURAND"]), adresse_risque=_champ("3 avenue Foch 75016 Paris"),
                     numero_police=_champ("999999999"))
    motif = _decider(parametres, assureurs, champs=champs)["motif"].lower()
    for donnee in ("durand", "dupont", "foch", "lilas", "999999999", "123456789"):
        assert donnee not in motif


# --------------------------------------------------------------------------- A.4 validité


def test_expiree_non_conforme(parametres, assureurs):
    decision = _decider(parametres, assureurs, champs=_champs(date_fin=_champ("2026-10-13")))
    assert decision["verdict"] == "non_conforme"


def test_fin_le_jour_meme_valable(parametres):
    assert rules.controler_validite(_champs(date_fin=_champ("2026-10-14")), DATE_REF, parametres)[0]["resultat"] == "ok"


@pytest.mark.parametrize("statut", ["absent", "ambigu"])
def test_fin_absente_ou_incertaine_douteux(parametres, statut):
    controles = rules.controler_validite(_champs(date_fin=_champ(None, statut)), DATE_REF, parametres)
    assert controles[0]["resultat"] == "douteux"


def test_fin_seule_acceptee(parametres):
    controles = rules.controler_validite(_champs(date_debut=_champ(None, "absent")), DATE_REF, parametres)
    assert [c["resultat"] for c in controles] == ["ok"]


def test_debut_futur_douteux(parametres):
    controles = rules.controler_validite(_champs(date_debut=_champ("2026-11-01"), date_fin=_champ("2027-10-31")),
                                         DATE_REF, parametres)
    assert [c["resultat"] for c in controles] == ["ok", "douteux"]


def test_date_reference(parametres):
    jour = date(2027, 1, 1)
    assert rules.determiner_date_reference(parametres, DATE_REF, jour) == DATE_REF  # défaut : dépôt
    assert rules.determiner_date_reference(parametres, None, jour) == jour
    autre = {**parametres, "validite": {**parametres["validite"], "date_reference": "2026-05-01"}}
    assert rules.determiner_date_reference(autre, DATE_REF, jour) == date(2026, 5, 1)


# --------------------------------------------------------------------------- A.5 mention


def test_mention_absente_texte_natif_non_conforme(parametres):
    champs = _champs(mention_garantie_locative={"valeur_normalisee": "absente", "valeur_brute": None,
                                                "statut": "absent"})
    assert rules.controler_mention(champs, "texte", parametres)["resultat"] == "non_conforme"
    assert rules.controler_mention(champs, "ocr", parametres)["resultat"] == "douteux"
    assert rules.controler_mention(champs, "mixte", parametres)["resultat"] == "douteux"


def test_mention_indeterminee_douteux(parametres):
    champs = _champs(mention_garantie_locative=_champ("indeterminee", "ambigu"))
    assert rules.controler_mention(champs, "texte", parametres)["resultat"] == "douteux"


# --------------------------------------------------------------------------- A.2 nom


@pytest.mark.parametrize("noms, attendu", [
    (["Jean DUPONT"], "ok"),
    (["M. DUPONT JEAN"], "ok"),            # civilité et ordre prénom/nom ignorés
    (["Marie MARTIN", "Paul X"], "ok"),    # un co-titulaire suffit
    (["Jean DUPOND"], "douteux"),          # faute d'une lettre : pas de validation automatique
    (["DUPONT"], "douteux"),               # nom de famille seul
    (["Paul DURAND"], "non_conforme"),
    (["Mme Marie MARTIN épouse DUPONT"], "ok"),   # nom de naissance + nom d'usage
    (["Marie DUPONT née MARTIN"], "ok"),
    (["MARTIN Marie ép. DUPONT"], "ok"),         # ordre inversé, abréviation
    (["Paul DURAND épouse DUPONT"], "douteux"),  # même nom d'usage, prénom différent : jamais validé
])
def test_nom(parametres, noms, attendu):
    assert rules.controler_nom(_champs(assure_nom=_champ(noms)), REFERENCE, parametres)["resultat"] == attendu


def test_nom_sans_reference_douteux(parametres):
    assert rules.controler_nom(_champs(), None, parametres)["resultat"] == "douteux"


# --------------------------------------------------------------------------- A.3 adresse


@pytest.mark.parametrize("adresse, attendu", [
    ("12 rue des Lilas 75019 Paris", "ok"),
    ("12, R. des Lilas, bât. B, appt 34, 75019 PARIS", "ok"),   # abréviations et compléments
    ("14 rue des Lilas 75019 Paris", "douteux"),                # numéro différent : immeuble voisin
    ("12 rue des Lilas 75020 Paris", "douteux"),                # code postal différent
    ("12 rue des Lilas Paris", "douteux"),                      # code postal manquant
    ("3 avenue Foch 75016 Paris", "non_conforme"),
])
def test_adresse(parametres, adresse, attendu):
    controle = rules.controler_adresse(_champs(adresse_risque=_champ(adresse)), REFERENCE, parametres)
    assert controle["resultat"] == attendu, controle["motif"]


def test_decomposer_adresse(parametres):
    morceaux = rules.decomposer_adresse("12 bis bd de la Villette, esc. 3, 75019 Paris",
                                        parametres["correspondance"]["adresse"])
    assert morceaux == {"numero": "12bis", "code_postal": "75019", "voie": "boulevard de la villette paris"}


# --------------------------------------------------------------------------- bloc B


@pytest.mark.parametrize("declare, document, attendu", [
    ("MACIF", "MACIF", "ok"),
    ("LA BANQUE POSTALE", "La Banque Postale Assurances IARD", "ok"),
    ("LCL", "PACIFICA", "ok"),
    ("CIC ASSURANCES", "ACM IARD SA", "ok"),
    ("DIRECT ASSURANCE", "AVANSSUR", "ok"),
    ("AUTRES ASSURANCES", "MAIF", "sans_effet"),
    ("ASSU 2000", "MMA IARD", "sans_effet"),
    ("MATMUT", "MACIF", "douteux"),
])
def test_assureur(parametres, assureurs, declare, document, attendu):
    controle = rules.controler_assureur(_champs(assureur=_champ(document)), {"assureur": declare},
                                        assureurs, parametres)
    assert controle["resultat"] == attendu, controle["motif"]


@pytest.mark.parametrize("declare, document, attendu", [
    ("750 3090 09937 W", "750309009937W", True),          # espaces saisis
    ("HORMA TI0006383497 MRH00175220", "TI0006383497", True),  # préfixes ajoutés
    ("AQ000362 / ACA936900301", "ACA936900301", True),    # deux numéros saisis
    ("12345678", "I2345678", True),                       # confusion OCR 1/I
    ("80 résidence principale", "W19600200067M", False),
    ("123", "123456789", False),                          # trop court pour l'inclusion
])
def test_polices_concordent(declare, document, attendu):
    assert rules.polices_concordent(declare, document, 6) is attendu


def test_police_absente_sans_effet(parametres):
    controle = rules.controler_police(_champs(numero_police=_champ(None, "absent")), DECLARATIF, parametres)
    assert controle["resultat"] == "sans_effet"


def test_dates_declarees(parametres):
    declaratif = {**DECLARATIF, "date_debut": date(2026, 10, 14), "date_fin": date(2027, 12, 31)}
    controles = rules.controler_dates_declarees(_champs(), declaratif, parametres)
    # début saisi = date de dépôt : douteux au plus ; fin décalée d'un an : non conforme
    assert [c["resultat"] for c in controles] == ["douteux", "non_conforme"]
    declaratif["date_fin"] = date(2027, 1, 15)
    assert rules.controler_dates_declarees(_champs(), declaratif, parametres)[1]["resultat"] == "douteux"


def test_sans_declaratif_douteux(parametres, assureurs):
    assert _decider(parametres, assureurs, declaratif=None)["verdict"] == "douteux"


# --------------------------------------------------------------------------- entrée (ingest)


def test_extraire_date_depot():
    assert extraire_date_depot("18314_01-06-4013-2-47_14-10-26-12-12-46_8.pdf") == date(2026, 10, 14)
    assert extraire_date_depot("1234_attestation.pdf") is None


def test_charger_declaratif(tmp_path):
    (tmp_path / "AttestationAssurances20261014.csv").write_text(
        "01-01-1-1-1;1234;MACIF;AB 12;01/01/2026;31/12/2026;1234_x.pdf\n"
        "ligne;mal;formee\n"
        "01-01-1-1-2;5678;AXA;CD;99/99/2026;;5678_y.pdf\n", encoding="utf-8")
    declaratif = charger_declaratif("AttestationAssurances*.csv", tmp_path)
    assert set(declaratif) == {"1234_x.pdf", "5678_y.pdf"}
    assert declaratif["1234_x.pdf"]["date_fin"] == date(2026, 12, 31)
    assert declaratif["5678_y.pdf"]["date_debut"] is None and declaratif["5678_y.pdf"]["date_fin"] is None


def test_charger_reference(tmp_path, parametres):
    entete = ("CCOMPTE;ID_LOT;ID_TERME;OCC;CIVILITE;NOM;PRENOM;LIB_RUE;COMPL_RUE;COD_POSTAL;LIB_COM;"
              "D_ENT_QUIT;D_SOR_QUIT;NOM_COMPAG_ASS;D_EFFET_ASS;D_ECHEANCE_ASS\n")
    lignes = [
        "00000001234;01-01-1-1-1;202610;1;M;DUPONT;Jean;12 RUE DES LILAS-ESC 4 ETA 02;ESC 4 ETA 02;75019;PARIS 19;2020-01-01;NULL;MACIF;NULL;NULL",
        "00000001234;01-01-1-1-1;202610;2;MME;MARTIN;Marie;12 RUE DES LILAS-ESC 4 ETA 02;ESC 4 ETA 02;75019;PARIS 19;2020-01-01;NULL;MACIF;NULL;NULL",
        "00000001234;01-01-1-1-1;202610;3;M;ANCIEN;Paul;12 RUE DES LILAS-ESC 4 ETA 02;ESC 4 ETA 02;75019;PARIS 19;2015-01-01;2019-12-31;NULL;NULL;NULL",
        "00000001234;01-01-1-9-9;202610;1;M;DUPONT;Jean;12 RUE DES LILAS-PARKING 9;PARKING 9;75019;PARIS 19;2020-01-01;NULL;NULL;NULL;NULL",
    ]
    chemin = tmp_path / "reference.csv"
    chemin.write_text(entete + "\n".join(lignes) + "\n", encoding="utf-8-sig")
    from src.ingest import charger_reference
    reference = charger_reference(chemin, parametres)
    logement = reference[("00000001234", "01-01-1-1-1")]
    assert logement == {"titulaires": ["Jean DUPONT", "Marie MARTIN"],      # occupant sorti écarté
                        "adresse": "12 RUE DES LILAS 75019 PARIS 19"}       # complément retiré
    assert ("00000001234", "01-01-1-9-9") in reference                     # autre lot : clé distincte
    assert charger_reference(tmp_path / "absent.csv", parametres) == {}
    # l'adresse de référence ainsi construite concorde avec celle d'une attestation
    controle = rules.controler_adresse(_champs(adresse_risque=_champ("12, rue des Lilas 75019 Paris")),
                                       logement, parametres)
    assert controle["resultat"] == "ok", controle["motif"]


def test_variantes_nom(parametres):
    config = parametres["correspondance"]["nom"]
    variantes = rules.variantes_nom("Mme Marie MARTIN épouse DUPONT", set(config["mots_ignores"]),
                                    set(config["mots_alliance"]))
    assert variantes == ["marie martin", "dupont martin", "marie dupont"]
    assert rules.variantes_nom("Jean DUPONT", set(), set(config["mots_alliance"])) == ["jean dupont"]
