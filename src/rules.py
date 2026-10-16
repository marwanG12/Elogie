"""Blocs 5-6 — Règles déterministes et arbre de décision (CLAUDE.md §6).

L'IA lit, les règles décident (CLAUDE.md §2). En cas de doute → « douteux ».

Chaque règle produit un contrôle {"regle", "resultat", "motif"} avec
resultat ∈ ok / douteux / non_conforme / sans_effet. Principe commun sur le statut du champ :
- « trouve »  → on applique la règle ;
- « ambigu »  → douteux (on ne valide jamais sur une lecture incertaine) ;
- « absent »  → selon le champ (le document a passé la porte d'exploitabilité : absence avérée).

Arbre : 1. non exploitable → douteux ; 2. autre type de document → non_conforme ;
3-4. sinon tous les contrôles sont évalués et le pire l'emporte (non_conforme > douteux > ok).
Tous les motifs sont conservés pour la revue humaine.

RGPD : les motifs ne contiennent ni nom ni adresse ni n° de police, seulement des scores et des dates.
"""
import re
from datetime import date

from rapidfuzz import fuzz

from src.extract import normaliser_pour_comparaison

VERDICTS = ("conforme", "non_conforme", "douteux")
OK, DOUTEUX, NON_CONFORME, SANS_EFFET = "ok", "douteux", "non_conforme", "sans_effet"
GRAVITE = {SANS_EFFET: 0, OK: 0, DOUTEUX: 1, NON_CONFORME: 2}


def _controle(regle: str, resultat: str, motif: str) -> dict:
    return {"regle": regle, "resultat": resultat, "motif": motif}


def _date(valeur) -> date | None:
    """Date ISO (texte) ou date → date ; None sinon."""
    if isinstance(valeur, date):
        return valeur
    try:
        return date.fromisoformat(str(valeur))
    except ValueError:
        return None


def determiner_date_reference(parametres: dict, date_depot: date | None, aujourd_hui: date | None = None) -> date:
    """PAV-3 : date à laquelle l'attestation doit être valable (settings.yaml → validite.date_reference).
    « depot » → date de dépôt (à défaut, date du jour) ; « jour » → date du jour ; sinon date ISO fixe."""
    aujourd_hui = aujourd_hui or date.today()
    choix = parametres["validite"]["date_reference"]
    if choix in (None, "jour"):
        return aujourd_hui
    if choix == "depot":
        return date_depot or aujourd_hui
    return date.fromisoformat(str(choix))


# --------------------------------------------------------------------------- bloc A : document


def controler_type(champs: dict) -> dict:
    """A.1 — Nature du document."""
    champ = champs["type_document"]
    if champ["statut"] == "trouve" and champ["valeur_normalisee"] == "attestation_habitation":
        return _controle("A.1", OK, "attestation d'assurance habitation")
    if champ["statut"] == "trouve" and champ["valeur_normalisee"] == "autre":
        return _controle("A.1", NON_CONFORME, "le document n'est pas une attestation d'assurance")
    return _controle("A.1", DOUTEUX, "nature du document incertaine")


def controler_validite(champs: dict, date_reference: date, parametres: dict) -> list[dict]:
    """A.4 — La période couvre la date de référence. Date de fin obligatoire, début facultatif."""
    config = parametres["validite"]
    controles = []
    fin, debut = champs["date_fin"], champs["date_debut"]

    if fin["statut"] == "ambigu" or (fin["statut"] == "trouve" and not _date(fin["valeur_normalisee"])):
        controles.append(_controle("A.4", DOUTEUX, "date de fin de garantie incertaine"))
    elif fin["statut"] == "absent":
        controles.append(_controle("A.4", config["date_fin_absente"], "date de fin de garantie absente"))
    elif _date(fin["valeur_normalisee"]) < date_reference:
        controles.append(_controle("A.4", NON_CONFORME,
                                   f"attestation expirée (fin {fin['valeur_normalisee']} < {date_reference.isoformat()})"))
    else:
        controles.append(_controle("A.4", OK, f"valable jusqu'au {fin['valeur_normalisee']}"))

    if debut["statut"] == "ambigu":
        controles.append(_controle("A.4", DOUTEUX, "date de début de garantie incertaine"))
    elif debut["statut"] == "trouve" and _date(debut["valeur_normalisee"]) \
            and _date(debut["valeur_normalisee"]) > date_reference:
        controles.append(_controle("A.4", config["date_debut_future"],
                                   f"garantie pas encore commencée (début {debut['valeur_normalisee']} "
                                   f"> {date_reference.isoformat()})"))
    return controles


def controler_mention(champs: dict, voie_lecture: str, parametres: dict) -> dict:
    """A.5 — Mention de garantie (famille de formulations de config/mentions.yaml)."""
    champ = champs["mention_garantie_locative"]
    valeur = champ["valeur_normalisee"]
    if valeur == "presente" and champ["statut"] == "trouve":
        return _controle("A.5", OK, f"mention trouvée ({champ['valeur_brute']})")
    if champ["statut"] == "absent" or valeur == "absente":
        # Distinction « absent » / « mal lu » (§6) : une lecture OCR a pu manquer la ligne
        lu_par_ocr = voie_lecture in ("ocr", "mixte")
        config = parametres["mention"]
        resultat = config["absente_si_ocr"] if lu_par_ocr else config["absente_si_texte"]
        return _controle("A.5", resultat, "mention de garantie locative absente"
                         + (" (document lu par OCR)" if lu_par_ocr else ""))
    return _controle("A.5", DOUTEUX, "mention de garantie locative incertaine")


def _mots_nom(nom: str, mots_ignores: set[str]) -> str:
    return " ".join(m for m in normaliser_pour_comparaison(nom).split() if m not in mots_ignores)


def variantes_nom(nom: str, mots_ignores: set[str], mots_alliance: set[str]) -> list[str]:
    """Variantes d'un nom portant un mot d'alliance (épouse, née, veuve…), pour reconnaître la personne
    sous son nom de naissance comme sous son nom d'usage. Ex. « Marie MARTIN épouse DUPONT » →
    « marie martin », « dupont martin », « marie dupont ». Sans mot d'alliance : le nom seul.
    L'ordre prénom/nom étant inconnu, chaque mot avant l'alliance est tour à tour remplacé par la suite ;
    le prénom doit donc toujours correspondre."""
    mots = _mots_nom(nom, mots_ignores).split()
    position = next((i for i, mot in enumerate(mots) if mot in mots_alliance), None)
    if position is None:
        return [" ".join(mots)]
    avant, apres = mots[:position], [m for m in mots[position + 1:] if m not in mots_alliance]
    variantes = [" ".join(avant)] if avant else []
    if apres:
        variantes += [" ".join(avant[:i] + apres + avant[i + 1:]) for i in range(len(avant))] or [" ".join(apres)]
    return variantes


def score_nom(noms_document: list[str], titulaires: list[str], mots_ignores: set[str],
              mots_alliance: set[str] = frozenset()) -> float:
    """Meilleure correspondance entre un assuré du document (ou une de ses variantes épouse/née/veuve)
    et un titulaire du bail (ordre prénom/nom indifférent ; civilités ignorées). Un nom de famille seul ne suffit pas."""
    scores = [fuzz.token_sort_ratio(variante, _mots_nom(ref, mots_ignores))
              for doc in noms_document for variante in variantes_nom(doc, mots_ignores, mots_alliance)
              for ref in titulaires]
    return max(scores, default=0.0)


def controler_nom(champs: dict, reference: dict | None, parametres: dict) -> dict:
    """A.2 — L'assuré correspond à un titulaire (données de référence via le n° de locataire)."""
    if not reference or not reference.get("titulaires"):
        return _controle("A.2", DOUTEUX, "référence locataire indisponible : nom non vérifiable")
    champ = champs["assure_nom"]
    if champ["statut"] == "absent":
        return _controle("A.2", DOUTEUX, "nom de l'assuré absent du document")
    if champ["statut"] == "ambigu" or not champ["valeur_normalisee"]:
        return _controle("A.2", DOUTEUX, "nom de l'assuré incertain")
    seuils = parametres["correspondance"]["nom"]
    noms = champ["valeur_normalisee"]
    score = score_nom(noms if isinstance(noms, list) else [noms], reference["titulaires"],
                      set(seuils["mots_ignores"]), set(seuils["mots_alliance"]))
    if score >= seuils["seuil_net"]:
        return _controle("A.2", OK, f"nom concordant (score {score:.0f})")
    if score >= seuils["seuil_douteux"]:
        return _controle("A.2", DOUTEUX, f"nom approchant (score {score:.0f})")
    return _controle("A.2", NON_CONFORME, f"nom différent du titulaire (score {score:.0f})")


MOTIF_CODE_POSTAL = re.compile(r"\b(\d{5})\b")
MOTIF_NUMERO_VOIE = re.compile(r"^(\d{1,4})\s*(bis|ter|quater|[a-d])?\b")


def decomposer_adresse(adresse: str, config: dict) -> dict:
    """Adresse → {"numero" (ex. « 12bis »), "code_postal", "voie" (reste normalisé)}.
    Abréviations développées, compléments (bât., esc., appt…) retirés avec le mot qui suit."""
    texte = normaliser_pour_comparaison(adresse)
    code_postal = MOTIF_CODE_POSTAL.search(texte)
    if code_postal:
        texte = (texte[:code_postal.start()] + " " + texte[code_postal.end():]).strip()
    mots, a_sauter = [], False
    for mot in texte.split():
        if a_sauter:
            a_sauter = False
            continue
        if mot in config["complements"]:
            a_sauter = True
            continue
        mots.append(config["abreviations"].get(mot, mot))
    texte = " ".join(mots)
    numero = MOTIF_NUMERO_VOIE.match(texte)
    if numero:
        texte = texte[numero.end():].strip()
    return {"numero": "".join(g for g in numero.groups() if g) if numero else None,
            "code_postal": code_postal.group(1) if code_postal else None,
            "voie": texte}


def controler_adresse(champs: dict, reference: dict | None, parametres: dict) -> dict:
    """A.3 — L'adresse assurée correspond au logement : n° de rue et code postal stricts, voie floue."""
    if not reference or not reference.get("adresse"):
        return _controle("A.3", DOUTEUX, "référence logement indisponible : adresse non vérifiable")
    champ = champs["adresse_risque"]
    if champ["statut"] == "absent":
        return _controle("A.3", DOUTEUX, "adresse du risque absente du document")
    if champ["statut"] == "ambigu" or not champ["valeur_normalisee"]:
        return _controle("A.3", DOUTEUX, "adresse du risque incertaine")
    config = parametres["correspondance"]["adresse"]
    doc = decomposer_adresse(champ["valeur_normalisee"], config)
    ref = decomposer_adresse(reference["adresse"], config)
    score = fuzz.token_set_ratio(doc["voie"], ref["voie"])

    ecarts = [libelle for libelle, cle in (("n° de rue", "numero"), ("code postal", "code_postal"))
              if doc[cle] != ref[cle]]
    if score < config["seuil_douteux"]:
        return _controle("A.3", NON_CONFORME, f"adresse différente du logement (score voie {score:.0f})")
    if score < config["seuil_net"]:
        return _controle("A.3", DOUTEUX, f"adresse approchante (score voie {score:.0f})")
    if ecarts:
        return _controle("A.3", DOUTEUX, f"voie concordante mais {' et '.join(ecarts)} différent(s) ou manquant(s)")
    return _controle("A.3", OK, f"adresse concordante (score voie {score:.0f})")


# --------------------------------------------------------------------------- bloc B : déclaratif


def groupe_assureur(nom: str, table: dict) -> str | None:
    """Rattache un nom d'assureur (ou de banque, de courtier) à un groupe de config/assureurs.yaml.
    Renvoie le groupe, « non_comparable », ou None si inconnu. Alias reconnus comme mots entiers ;
    le plus long alias trouvé l'emporte (« la banque postale » avant « lbp »)."""
    texte = f" {normaliser_pour_comparaison(nom)} "
    for alias in table.get("non_comparables", []):
        if texte.strip() == normaliser_pour_comparaison(alias):
            return "non_comparable"
    candidats = [(len(alias), groupe) for groupe, alias_groupe in table["groupes"].items()
                 for alias in alias_groupe if f" {normaliser_pour_comparaison(alias)} " in texte]
    return max(candidats)[1] if candidats else None


def controler_assureur(champs: dict, declaratif: dict, table_assureurs: dict, parametres: dict) -> dict:
    """B — Assureur déclaré ↔ assureur de l'attestation (via la table d'alias). Écart → douteux."""
    declare = declaratif.get("assureur") or ""
    groupe_declare = groupe_assureur(declare, table_assureurs)
    if not declare or groupe_declare == "non_comparable":
        return _controle("B.assureur", SANS_EFFET, f"assureur déclaré non comparable ({declare or 'vide'})")
    champ = champs["assureur"]
    if champ["statut"] == "absent":
        return _controle("B.assureur", SANS_EFFET, "assureur absent du document")
    if champ["statut"] == "ambigu" or not champ["valeur_normalisee"]:
        return _controle("B.assureur", DOUTEUX, "assureur du document incertain")
    groupe_document = groupe_assureur(champ["valeur_normalisee"], table_assureurs)
    if groupe_declare and groupe_declare == groupe_document:
        return _controle("B.assureur", OK, f"assureur concordant ({groupe_declare})")
    score = fuzz.token_set_ratio(normaliser_pour_comparaison(declare),
                                 normaliser_pour_comparaison(champ["valeur_normalisee"]))
    if score >= parametres["correspondance"]["declaratif"]["seuil_net"]:
        return _controle("B.assureur", OK, f"assureur concordant (score {score:.0f})")
    return _controle("B.assureur", DOUTEUX,
                     f"assureur déclaré ({declare}) ≠ document ({champ['valeur_normalisee']})")


def normaliser_police(texte: str) -> str:
    """N° de police comparable : alphanumérique majuscule, confusions OCR courantes ramenées (O→0, I/L→1)."""
    return re.sub(r"[^0-9A-Z]", "", texte.upper()).translate(str.maketrans("OIL", "011"))


def polices_concordent(declare: str, document: str, longueur_min_inclusion: int) -> bool:
    """Égalité après normalisation, ou l'un contenu dans l'autre (préfixe/suffixe ajouté par le locataire).
    Plusieurs n° saisis séparés par « / », « ; » ou « , » : un seul suffit."""
    police_document = normaliser_police(document)
    for candidat in re.split(r"[/;,]", declare):
        candidat = normaliser_police(candidat)
        if not candidat or not police_document:
            continue
        if candidat == police_document:
            return True
        courte, longue = sorted((candidat, police_document), key=len)
        if len(courte) >= longueur_min_inclusion and courte in longue:
            return True
    return False


def controler_police(champs: dict, declaratif: dict, parametres: dict) -> dict:
    """B — N° de police déclaré ↔ document. Pas un critère de conformité : absent → sans effet, écart → douteux."""
    champ = champs["numero_police"]
    if not declaratif.get("police"):
        return _controle("B.police", SANS_EFFET, "n° de police non déclaré")
    if champ["statut"] == "absent":
        return _controle("B.police", SANS_EFFET, "n° de police absent du document")
    if champ["statut"] == "ambigu" or not champ["valeur_normalisee"]:
        return _controle("B.police", DOUTEUX, "n° de police du document incertain")
    longueur_min = parametres["correspondance"]["declaratif"]["police_longueur_min_inclusion"]
    if polices_concordent(declaratif["police"], champ["valeur_normalisee"], longueur_min):
        return _controle("B.police", OK, "n° de police concordant")
    return _controle("B.police", DOUTEUX, "n° de police déclaré différent du document")


def controler_dates_declarees(champs: dict, declaratif: dict, parametres: dict) -> list[dict]:
    """B — Dates déclarées ↔ dates lues. Écart sur la fin au-delà du seuil → non_conforme ;
    écart sur le début → douteux au plus (saisie souvent = date de dépôt ou de souscription)."""
    config = parametres["correspondance"]["declaratif"]
    controles = []
    for nom, libelle in (("date_debut", "début"), ("date_fin", "fin")):
        regle = f"B.{nom}"
        declaree, champ = declaratif.get(nom), champs[nom]
        lue = _date(champ["valeur_normalisee"]) if champ["statut"] == "trouve" else None
        if not declaree or champ["statut"] == "absent":
            controles.append(_controle(regle, SANS_EFFET, f"date de {libelle} non comparable"))
            continue
        if champ["statut"] == "ambigu" or not lue:
            controles.append(_controle(regle, DOUTEUX, f"date de {libelle} du document incertaine"))
            continue
        ecart = abs((lue - declaree).days)
        if ecart <= config["ecart_dates_jours_douteux"]:
            controles.append(_controle(regle, OK, f"date de {libelle} concordante"))
        elif nom == "date_fin" and ecart > config["ecart_dates_jours_non_conforme"]:
            controles.append(_controle(regle, NON_CONFORME,
                                       f"date de {libelle} déclarée {declaree.isoformat()} ≠ document "
                                       f"{lue.isoformat()} ({ecart} j)"))
        else:
            controles.append(_controle(regle, DOUTEUX, f"date de {libelle} déclarée {declaree.isoformat()} "
                                                       f"≠ document {lue.isoformat()} ({ecart} j)"))
    return controles


# --------------------------------------------------------------------------- arbre de décision


def _verdict(resultat: str, motif: str, controles: list[dict]) -> dict:
    return {"verdict": resultat, "motif": motif, "controles": controles}


def decider(exploitabilite: dict, champs: dict, reference: dict | None, declaratif: dict | None,
            parametres: dict, table_assureurs: dict, date_reference: date, voie_lecture: str = "texte") -> dict:
    """Applique la grille (blocs A et B) et l'arbre → {"verdict", "motif", "controles": [...]}.

    - exploitabilite : sortie de exploitability.evaluer_exploitabilite ;
    - champs : sortie de extract (9 champs normalisés) ;
    - reference : {"titulaires": [noms], "adresse": str} depuis l'entrepôt, ou None ;
    - declaratif : ligne du CSV extranet (ingest.charger_declaratif), ou None ;
    - date_reference : voir determiner_date_reference ; voie_lecture : texte / ocr / mixte.
    """
    # 1. Porte d'exploitabilité : document non lu ≠ champ absent
    if exploitabilite["statut"] != "exploitable":
        controle = _controle("exploitabilite", DOUTEUX, f"document non exploitable : {exploitabilite['motif']}")
        return _verdict("douteux", controle["motif"], [controle])

    # 2. Nature du document
    controle_type = controler_type(champs)
    if controle_type["resultat"] == NON_CONFORME:
        return _verdict("non_conforme", controle_type["motif"], [controle_type])

    # 3-4. Règles éliminatoires, de correspondance et de cohérence déclarative
    controles = [controle_type,
                 *controler_validite(champs, date_reference, parametres),
                 controler_mention(champs, voie_lecture, parametres),
                 controler_nom(champs, reference, parametres),
                 controler_adresse(champs, reference, parametres)]
    if declaratif:
        controles += [controler_assureur(champs, declaratif, table_assureurs, parametres),
                      controler_police(champs, declaratif, parametres),
                      *controler_dates_declarees(champs, declaratif, parametres)]
    else:
        controles.append(_controle("B", DOUTEUX, "déclaratif extranet introuvable pour ce dépôt"))

    # 5. Le pire résultat l'emporte ; motifs = tous les contrôles défavorables, du plus grave au moins grave
    pire = max(GRAVITE[c["resultat"]] for c in controles)
    verdict = {0: "conforme", 1: "douteux", 2: "non_conforme"}[pire]
    defavorables = sorted((c for c in controles if GRAVITE[c["resultat"]] > 0),
                          key=lambda c: -GRAVITE[c["resultat"]])
    motif = " ; ".join(f"[{c['regle']}] {c['motif']}" for c in defavorables) or "toutes les règles sont vérifiées"
    return _verdict(verdict, motif, controles)
