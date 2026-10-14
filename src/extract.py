"""Bloc 4 — Extraction + normalisation (CLAUDE.md §5 et §7).

L'IA LIT, ELLE NE DÉCIDE PAS : le modèle local (Ollama) repère les 9 champs dans le texte et
recopie ce qu'il voit. Tout le reste est fait ici par du code déterministe, qui se méfie du modèle :
- dates re-normalisées par dateparser à partir du texte brut (jamais une date inventée) ;
- mention de garantie cherchée DIRECTEMENT dans le texte avec la famille de config/mentions.yaml ;
- toute valeur « trouvée » doit réellement figurer dans le document, sinon → « ambigu ».
Chaque champ : {"valeur_normalisee", "valeur_brute", "statut": "trouve" | "ambigu" | "absent"}.
"""
import json
import logging
import re
import time
import unicodedata
from datetime import date

import dateparser
import requests
from rapidfuzz import fuzz

journal = logging.getLogger(__name__)

CHAMPS = [
    "type_document",
    "assureur",
    "assure_nom",
    "adresse_risque",
    "date_debut",
    "date_fin",
    "mention_garantie_locative",
    "numero_police",
    "date_emission",
]
CHAMPS_DATE = ("date_debut", "date_fin", "date_emission")
STATUTS = ("trouve", "ambigu", "absent")
TYPES_DOCUMENT = ("attestation_habitation", "autre", "illisible")

# --------------------------------------------------------------------------- prompt et appel Ollama

PROMPT_SYSTEME = """Tu es un outil d'extraction d'informations. Tu lis le texte d'un document \
(attestation d'assurance habitation, ou autre document) et tu recopies des informations. \
Tu ne juges PAS si le document est conforme. Tu n'inventes RIEN : si une information n'est pas \
dans le texte, son statut est "absent" et ses valeurs sont null.

Pour chaque champ, renvoie :
- "valeur_brute" : le passage EXACT du texte, recopié tel quel (ou null) ;
- "valeur_normalisee" : la valeur nettoyée (ou null) ;
- "statut" : "trouve" (clairement présent), "ambigu" (présent mais incertain, plusieurs candidats \
possibles, mal lisible) ou "absent".

Champs :
- type_document : "attestation_habitation" si le document atteste qu'une personne est assurée \
pour un logement ; "autre" pour tout autre document (avis d'échéance, appel de cotisation, \
facture, conditions générales, courrier...) ; "illisible" si le texte est incompréhensible. \
Si le PDF contient plusieurs documents, réponds "attestation_habitation" dès qu'une attestation \
s'y trouve, et extrais les autres champs depuis l'attestation.
- assureur : nom de la compagnie d'assurance (ex. MACIF, MAAF, AXA, GMF, Abeille, SMACL).
- assure_nom : nom(s) de la ou des personnes assurées ; valeur_normalisee = liste de noms.
- adresse_risque : adresse du logement assuré (pas l'adresse de l'assureur ni de l'agence).
- date_debut : début de la période de garantie ; valeur_normalisee au format AAAA-MM-JJ. \
S'il n'y a qu'une date de fin (ex. "valable jusqu'au ..."), date_debut est "absent".
- date_fin : fin de la période de garantie (AAAA-MM-JJ).
- mention_garantie_locative : la phrase qui indique la garantie habitation / responsabilité \
locative / risques locatifs ; valeur_normalisee = "presente", "absente" ou "indeterminee".
- numero_police : numéro de contrat / de police / de sociétaire.
- date_emission : date à laquelle le document a été établi ("fait le", "édité le") ; ce n'est PAS \
une date de la période de garantie.

Réponds UNIQUEMENT par l'objet JSON, sans texte autour."""

_VALEUR = {"type": ["string", "null"]}
_SCHEMA_CHAMP = {
    "type": "object",
    "properties": {"valeur_brute": _VALEUR, "valeur_normalisee": _VALEUR,
                   "statut": {"type": "string", "enum": list(STATUTS)}},
    "required": ["valeur_brute", "valeur_normalisee", "statut"],
}
SCHEMA_REPONSE = {
    "type": "object",
    "properties": {
        champ: (_SCHEMA_CHAMP if champ != "assure_nom" else
                {**_SCHEMA_CHAMP, "properties": {**_SCHEMA_CHAMP["properties"],
                 "valeur_normalisee": {"type": ["array", "null"], "items": {"type": "string"}}}})
        for champ in CHAMPS
    },
    "required": CHAMPS,
}


class ErreurExtraction(Exception):
    """Le modèle n'a pas renvoyé de JSON exploitable après tous les essais."""


def _preparer_texte(texte: str, max_caracteres: int) -> str:
    """Rend les sauts de page visibles pour le modèle et tronque si le texte est trop long."""
    pages = texte.split("\f")
    texte_balise = "\n".join(f"--- page {i} ---\n{p.strip()}" for i, p in enumerate(pages, start=1))
    if len(texte_balise) > max_caracteres:
        journal.warning("Texte tronqué pour le modèle (%d > %d caractères)", len(texte_balise), max_caracteres)
        texte_balise = texte_balise[:max_caracteres]
    return texte_balise


def _parser_json(contenu: str) -> dict | None:
    """Lit la réponse du modèle en JSON ; tolère du texte ou des balises ``` autour de l'objet."""
    for candidat in (contenu, contenu[contenu.find("{"): contenu.rfind("}") + 1]):
        try:
            objet = json.loads(candidat)
            if isinstance(objet, dict):
                return objet
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def appeler_modele(texte: str, parametres: dict) -> tuple[dict, int]:
    """Envoie le texte au modèle local et renvoie (réponse JSON, nombre d'essais).
    Si la réponse n'est pas du JSON valide, on la renvoie au modèle en lui demandant de corriger."""
    config = parametres["ollama"]
    messages = [{"role": "system", "content": PROMPT_SYSTEME},
                {"role": "user", "content": "Texte du document :\n\n" + texte}]
    for essai in range(1, config["nb_essais"] + 1):
        reponse = requests.post(
            f"{config['url']}/api/chat",
            json={"model": config["modele"], "messages": messages, "stream": False,
                  "format": SCHEMA_REPONSE, "keep_alive": config["keep_alive"],
                  "options": {"temperature": config["temperature"], "num_ctx": config["num_ctx"]}},
            timeout=config["timeout_secondes"],
        )
        reponse.raise_for_status()
        contenu = reponse.json()["message"]["content"]
        objet = _parser_json(contenu)
        if objet is not None:
            return objet, essai
        journal.warning("Réponse non JSON (essai %d/%d)", essai, config["nb_essais"])
        messages += [{"role": "assistant", "content": contenu},
                     {"role": "user", "content": "Ta réponse n'est pas un JSON valide. "
                                                 "Renvoie uniquement l'objet JSON demandé."}]
    raise ErreurExtraction(f"JSON invalide après {config['nb_essais']} essais")

# --------------------------------------------------------------------------- normalisation


def normaliser_pour_comparaison(texte: str) -> str:
    """Minuscules, sans accents ni ponctuation, espaces réduits (pour comparer des textes :
    « 15 Rue de la Nativité, Paris » et « 15 RUE DE LA NATIVITE PARIS » deviennent identiques)."""
    sans_accents = "".join(c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c))
    sans_ponctuation = re.sub(r"[^\w]+", " ", sans_accents.lower())
    return re.sub(r"\s+", " ", sans_ponctuation).strip()


def _nettoyer(texte: str | None) -> str | None:
    """Enlève les espaces superflus ; None si vide."""
    if texte is None:
        return None
    propre = re.sub(r"\s+", " ", str(texte)).strip(" ,;:-")
    return propre or None


MOIS = (r"janvier|janv|f[ée]vrier|f[ée]vr|mars|avril|avr|mai|juin|juillet|juil|ao[uû]t|"
        r"septembre|sept|octobre|oct|novembre|nov|d[ée]cembre|d[ée]c")
MOTIF_DATE = re.compile(
    r"\b\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*(?:\d{4}|\d{2})\b"                    # 01/01/2023, 01.01.22
    rf"|\b(?:1er|\d{{1,2}})\s+(?:{MOIS})\.?\s+\d{{4}}\b",                          # 1er avril 2023
    re.IGNORECASE,
)


def extraire_dates(texte_brut: str, langue: str) -> list[date]:
    """Trouve toutes les dates COMPLÈTES (jour, mois, année) d'un passage, dans l'ordre.
    Chaque date est isolée par un motif puis convertie par dateparser : une plage
    « 01.01.22 AU 31.12.22 » donne deux dates, et une année seule (« 2023 ») n'en donne aucune."""
    dates = []
    for correspondance in MOTIF_DATE.finditer(texte_brut or ""):
        lue = dateparser.parse(correspondance.group(0), languages=[langue],
                               settings={"DATE_ORDER": "DMY"})
        if lue:
            dates.append(lue.date())
    return dates


def _champ(valeur_normalisee=None, valeur_brute=None, statut="absent") -> dict:
    if statut == "absent":
        valeur_normalisee = None
    return {"valeur_normalisee": valeur_normalisee, "valeur_brute": valeur_brute, "statut": statut}


def _lire_champ_modele(reponse: dict, nom: str) -> dict:
    """Récupère un champ de la réponse du modèle ; un champ manquant ou mal formé devient « ambigu »."""
    brut = reponse.get(nom)
    if not isinstance(brut, dict):
        return _champ(statut="ambigu")
    statut = brut.get("statut") if brut.get("statut") in STATUTS else "ambigu"
    return {"valeur_normalisee": brut.get("valeur_normalisee"),
            "valeur_brute": _nettoyer(brut.get("valeur_brute")), "statut": statut}


def _verifier_presence(champ: dict, texte_normalise: str, seuil: int, verifier_normalisee: bool = True) -> dict:
    """Anti-hallucination : un champ « trouve » passe en « ambigu » si
    - son passage brut n'est pas dans le document (comparaison floue ≥ seuil, tolère l'OCR) ;
    - OU un mot de sa valeur normalisée n'existe pas tel quel dans le document : le modèle peut
      reformater (majuscules, accents, virgules) mais pas changer un mot (ex. DUPONT → DUPOND).
    Pour une liste (plusieurs assurés), chaque élément est vérifié."""
    if champ["statut"] != "trouve":
        return champ
    brut = champ["valeur_brute"]
    brut_present = bool(brut) and fuzz.partial_ratio(normaliser_pour_comparaison(brut), texte_normalise) >= seuil
    normalisee_presente = True
    if verifier_normalisee:
        mots_du_texte = set(texte_normalise.split())
        normalisee = champ["valeur_normalisee"]
        for valeur in (normalisee if isinstance(normalisee, list) else [normalisee]):
            mots = normaliser_pour_comparaison(str(valeur or "")).split()
            normalisee_presente &= bool(mots) and all(mot in mots_du_texte for mot in mots)
    if not (brut_present and normalisee_presente):
        return {**champ, "statut": "ambigu"}
    return champ


def normaliser_date(champ: dict, nom: str, langue: str) -> dict:
    """La date est relue par dateparser dans le passage brut (pas celle proposée par le modèle).
    Début → 1re date du passage ; fin et émission → dernière / 1re. Désaccord avec le modèle → « ambigu »."""
    if champ["statut"] == "absent":
        return _champ(valeur_brute=champ["valeur_brute"])
    dates = extraire_dates(champ["valeur_brute"], langue)
    try:
        date_modele = date.fromisoformat(str(champ["valeur_normalisee"]))
    except ValueError:
        date_modele = None
    if not dates:
        # Le passage ne contient pas de date complète : on ne complète jamais une date partielle
        return _champ(date_modele.isoformat() if date_modele else None, champ["valeur_brute"], "ambigu")
    date_lue = dates[-1] if nom == "date_fin" else dates[0]
    statut = champ["statut"]
    if date_modele and date_modele != date_lue:
        statut = "ambigu"
    return _champ(date_lue.isoformat(), champ["valeur_brute"], statut)


def detecter_mention(texte: str, reponse_modele: dict, formulations: list[str], seuils: dict) -> dict:
    """Mention de garantie : cherchée directement dans le texte du document (pas confiée au modèle).
    - une formulation de mentions.yaml est présente (comparaison floue ≥ seuil_presente) → presente ;
    - presque présente (OCR abîmé) ou le modèle cite une phrase hors liste → indeterminee (revue humaine) ;
    - sinon → absente."""
    texte_normalise = normaliser_pour_comparaison(texte)
    meilleure, meilleur_score = None, 0.0
    for formulation in formulations:
        score = fuzz.partial_ratio(normaliser_pour_comparaison(formulation), texte_normalise)
        if score > meilleur_score:
            meilleure, meilleur_score = formulation, score

    if meilleur_score >= seuils["seuil_presente"]:
        return _champ("presente", meilleure, "trouve")
    if meilleur_score >= seuils["seuil_indeterminee"]:
        return _champ("indeterminee", meilleure, "ambigu")
    phrase_modele = _nettoyer((reponse_modele.get("mention_garantie_locative") or {}).get("valeur_brute"))
    if phrase_modele:
        return _champ("indeterminee", phrase_modele, "ambigu")
    # Document lu, aucune formulation trouvée : mention confirmée absente
    return {"valeur_normalisee": "absente", "valeur_brute": None, "statut": "absent"}


def normaliser_reponse(reponse: dict, texte: str, parametres: dict, formulations: list[str]) -> dict:
    """Transforme la réponse brute du modèle en 9 champs normalisés et vérifiés."""
    config = parametres["extraction"]
    texte_normalise = normaliser_pour_comparaison(texte)
    champs = {}

    # type_document : valeur imposée parmi la liste ; sinon ambigu
    type_doc = _lire_champ_modele(reponse, "type_document")
    if type_doc["valeur_normalisee"] not in TYPES_DOCUMENT:
        type_doc = _champ(None, type_doc["valeur_brute"], "ambigu")
    elif type_doc["statut"] == "absent":
        type_doc["statut"] = "ambigu"  # le type ne peut pas être « absent » : le modèle hésite
    champs["type_document"] = type_doc

    # Champs texte : nettoyage + vérification de présence dans le document
    for nom in ("assureur", "adresse_risque", "numero_police"):
        champ = _lire_champ_modele(reponse, nom)
        champ["valeur_normalisee"] = _nettoyer(champ["valeur_normalisee"])
        champs[nom] = _verifier_presence(_champ(**champ), texte_normalise, config["seuil_presence_dans_texte"])

    # assure_nom : toujours une liste de noms
    noms = _lire_champ_modele(reponse, "assure_nom")
    valeur = noms["valeur_normalisee"]
    liste = [_nettoyer(n) for n in (valeur if isinstance(valeur, list) else [valeur]) if _nettoyer(n)]
    champs["assure_nom"] = _verifier_presence(_champ(liste or None, noms["valeur_brute"], noms["statut"]),
                                              texte_normalise, config["seuil_presence_dans_texte"])

    # Dates : relues par dateparser dans le passage brut
    for nom in CHAMPS_DATE:
        # (la date ISO n'apparaît pas telle quelle dans le texte : on ne vérifie que le passage brut)
        champ = _verifier_presence(_lire_champ_modele(reponse, nom), texte_normalise,
                                   config["seuil_presence_dans_texte"], verifier_normalisee=False)
        champs[nom] = normaliser_date(champ, nom, config["langue_dates"])

    # Période incohérente (début après fin) → les deux dates passent en ambigu
    debut, fin = champs["date_debut"]["valeur_normalisee"], champs["date_fin"]["valeur_normalisee"]
    if debut and fin and debut > fin:
        champs["date_debut"]["statut"] = champs["date_fin"]["statut"] = "ambigu"

    champs["mention_garantie_locative"] = detecter_mention(texte, reponse, formulations, config["mention"])
    return {nom: champs[nom] for nom in CHAMPS}


def extraire_champs(texte: str, parametres: dict, formulations: list[str]) -> dict:
    """Étape 4 : texte du document → 9 champs normalisés.
    Renvoie {"champs": {...}, "meta": {"essais", "duree_secondes", "erreur"}}.
    Si le modèle échoue, tous les champs sont « ambigu » (le document ira en revue humaine)."""
    debut = time.perf_counter()
    texte_modele = _preparer_texte(texte, parametres["extraction"]["max_caracteres_texte"])
    try:
        reponse, essais = appeler_modele(texte_modele, parametres)
        champs, erreur = normaliser_reponse(reponse, texte, parametres, formulations), None
    except ErreurExtraction as exc:
        champs = {nom: _champ(statut="ambigu") for nom in CHAMPS}
        essais, erreur = parametres["ollama"]["nb_essais"], str(exc)
    duree = round(time.perf_counter() - debut, 1)
    journal.info("Extraction : %d essai(s), %.1fs, erreur=%s", essais, duree, erreur)
    return {"champs": champs, "meta": {"essais": essais, "duree_secondes": duree, "erreur": erreur}}
