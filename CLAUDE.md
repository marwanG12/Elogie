# POC — Vérification automatisée des attestations d'assurance

> Contexte : preuve de concept (POC) réalisée dans le cadre d'un mémoire de M2 (IA & Big Data).
> Terrain : Elogie-Siemp, bailleur social. Ce fichier est le contexte permanent du projet :
> lis-le en entier avant toute tâche, et respecte-le.

---

## 1. Objectif

Automatiser le contrôle annuel des attestations d'assurance habitation des locataires :
à partir d'un document déposé (PDF), **lire** le document, **vérifier** sa conformité et sa
cohérence avec les informations déclarées, puis rendre un **verdict** : `conforme`,
`non_conforme` ou `douteux`.

Le POC traite un dossier local d'attestations et produit un fichier de résultats + des mesures
de performance. Ce n'est pas un produit : il démontre la faisabilité et mesure la fiabilité.

## 2. Principe directeur (à ne jamais enfreindre)

**L'IA lit, les règles décident.**
- L'IA (OCR + modèle de langage local) sert uniquement à **extraire et normaliser** les
  informations du document.
- La **décision de conformité** est prise par des **règles déterministes et auditables**
  (voir §6), jamais par l'IA.
- En cas de doute, le système **ne devine pas** : il classe en `douteux` et laisse la main à un humain.

## 3. Périmètre et non-objectifs

Dans le périmètre :
- Lecture PDF (texte natif OU image/scan via OCR), extraction, normalisation, règles, verdict, mesure.
- Traitement **100 % local**. Aucune donnée ne sort de la machine. Aucun appel à un service externe.

Hors périmètre (ne pas implémenter) :
- Intégration réelle à l'extranet ou à l'ERP (TEGIA) → **simulée** (entrée = dossier local, sortie = fichier).
- Interface utilisateur, temps réel, mise en production, authentification.

## 4. Contraintes RGPD (strictes)

- Traitement exclusivement local. Interdiction d'appeler une API externe ou d'envoyer des données sur le réseau.
- Le **numéro de locataire** (présent dans le nom de fichier) sert à la fois de **clé de jointure**
  vers les données de référence ET de **pseudonyme** : le pipeline travaille sur ce numéro, et ne
  charge le nom/adresse réels qu'au moment de la comparaison.
- Les données de référence (numéro → nom, adresse) viennent d'un export interne (CSV), fourni à part.
- Ne jamais logguer de données personnelles en clair dans les fichiers de sortie destinés au mémoire.

## 5. Architecture — pipeline en 7 blocs (modulaires, testables séparément)

1. **Entrée** — charger un fichier PDF depuis le dossier `data/corpus/`. Le numéro de locataire est extrait du nom de fichier.
2. **Acquisition du texte** — deux voies :
   - PDF avec couche texte → extraction directe (`pdfplumber` ou `PyMuPDF`).
   - PDF image/scan (peu ou pas de texte) → OCR (`pytesseract` + Tesseract, langue `fra`).
   - Détection automatique : si l'extraction directe renvoie trop peu de texte, basculer en OCR.
3. **Exploitabilité** — décider si le document est lisible/exploitable (cf. §6, porte préalable).
4. **Extraction + normalisation** — un modèle de langage **local via Ollama** lit le texte et
   renvoie les 9 champs structurés (§7), chacun avec valeur normalisée, valeur brute et statut.
   Les dates sont re-normalisées via `dateparser` ; la mention via la liste de formulations (§8).
5. **Règles** — appliquer la grille de conformité (§6) sur les champs normalisés.
6. **Décision** — l'arbre agrège → verdict (`conforme` / `non_conforme` / `douteux`) + motif.
7. **Sortie** — écrire un fichier de résultats (JSON + CSV) dans `data/output/`. Simule l'écriture dans l'ERP.

## 6. Grille de conformité et arbre de décision

### Distinction fondamentale : « champ absent » vs « document non lu »
- **Champ confirmé absent** : le document a été lu, et le champ n'y figure pas → peut mener à `non_conforme`.
- **Document non exploitable** : la lecture a échoué (illisible, charabia, page blanche) → `douteux`.
  Ne jamais confondre « non trouvé car illisible » avec « absent ». D'où la porte d'exploitabilité (bloc 3),
  qui s'exécute AVANT les règles sur les champs.

### Ordre d'évaluation (arbre)
1. Document exploitable ? Non (illisible / page blanche / charabia) → `douteux` (motif : non exploitable).
2. Est-ce bien une attestation d'assurance ? Non (autre type : avis d'échéance, facture…) → `non_conforme`.
3. Règles éliminatoires satisfaites ? (validité non expirée ; mention de garantie présente)
   Échec net → `non_conforme`.
4. Règles de correspondance (nom, adresse, cohérence déclaratif) : nette → continuer ;
   approximative/incertaine → `douteux` ; contradiction manifeste → `non_conforme`.
5. Tout est vérifié → `conforme`.

### Bloc A — conformité du document
- **A.1 Nature** : c'est une attestation d'assurance habitation (pas un avis d'échéance, une facture, etc.).
- **A.2 Identité** : le nom de l'assuré correspond au titulaire (données de référence, via numéro de locataire).
- **A.3 Localisation** : l'adresse assurée correspond au logement (données de référence).
- **A.4 Validité** : la période couvre la date de référence (cf. PAV-3 ci-dessous : à confirmer — par défaut, « valable au jour du contrôle »).
- **A.5 Mention de garantie** : une formulation de la famille « garantie locative » est présente (§8).

### Bloc B — cohérence déclaratif ↔ document
Comparer les 4 champs saisis par le locataire (`Assurance`, `Police`, `Date_Debut`, `Date_Fin`)
aux valeurs extraites du document. Un écart mineur (faute de frappe) → `douteux` ; un écart
substantiel (dates qui ne correspondent pas) → `douteux` ou `non_conforme` selon l'ampleur.

### Arbitrage (règle du jeu en cas de doute)
**Un faux positif (valider une attestation non conforme) est plus grave qu'un faux négatif
(alerter à tort).** Donc en cas de doute, ne jamais valider automatiquement : router vers `douteux`.
Le seuil de correspondance (nom, adresse) est réglé de façon exigeante.

## 7. Format générique des champs (sortie de l'extraction)

Chaque champ porte : `valeur_normalisee`, `valeur_brute`, `statut` (`trouve` / `ambigu` / `absent`).

| Champ                        | Forme normalisée                                   |
|------------------------------|----------------------------------------------------|
| `type_document`              | enum : `attestation_habitation` / `autre` / `illisible` |
| `assureur`                   | texte nettoyé (ex. MACIF, MAAF, AXA, GMF…)         |
| `assure_nom`                 | texte nettoyé (liste si plusieurs assurés)         |
| `adresse_risque`             | texte nettoyé                                      |
| `date_debut`                 | date ISO `AAAA-MM-JJ` (peut être absente)          |
| `date_fin`                   | date ISO `AAAA-MM-JJ`                              |
| `mention_garantie_locative`  | enum : `presente` / `absente` / `indeterminee`     |
| `numero_police`              | texte (format variable)                            |
| `date_emission`              | date ISO — champ de service (anti-confusion de dates), absence sans conséquence |

## 8. Spécificités du réel (observées sur un échantillon d'attestations)

- **Assureurs vus** : MACIF, MAAF, Abeille, SMACL, AXA, GMF. Mises en page et logos très variés.
- **La mention de garantie ne contient PAS toujours le mot « habitation ».** Beaucoup d'attestations
  valides disent « responsabilité locative » ou « responsabilité civile de locataire ». La détection
  doit accepter une **famille de formulations**, à externaliser dans `config/mentions.yaml` :
  `assurance habitation`, `multirisque habitation`, `responsabilité locative`,
  `responsabilité civile locative`, `responsabilité civile de locataire`, `risques locatifs`,
  `garantie des risques locatifs`. (⚠️ À revalider avec le métier — écart avec la consigne initiale.)
- **Formats de dates** très variés : `1er avril 2023`, `01.01.22 AU 31.12.22`, `01/01/2023 au 31/12/2023`,
  `valable jusqu'au 15/04/2023` (fin seule, pas de début). `dateparser` (langue fr) gère le gros ;
  prévoir le cas « date de fin seule ».
- **Documents multi-pages / multi-documents** : un même PDF peut contenir l'attestation + un avis
  d'échéance. Traiter toutes les pages ; chercher l'attestation.
- **Cas « autre type »** : un avis d'échéance de cotisation n'est PAS une attestation → `non_conforme`.
- **Cas « non exploitable »** : page blanche / scan illisible → `douteux`.

## 9. Stack technique

- **Python 3.12**, environnement virtuel `venv`.
- Lecture PDF : `pdfplumber` et/ou `PyMuPDF` (fitz) ; rasterisation si besoin via `pdf2image`/`PyMuPDF`.
- OCR : `pytesseract` (binaire Tesseract installé, langue `fra`).
- Extraction IA locale : **Ollama** (HTTP local `http://localhost:11434`), modèle au choix —
  démarrer avec `qwen2.5:7b-instruct` ou `llama3.1:8b` (CPU, lent mais OK pour un petit lot).
  Le modèle doit renvoyer du **JSON structuré** (format du §7).
- Dates : `dateparser`. Correspondance floue nom/adresse : `rapidfuzz`.
- Config externalisée en `config/*.yaml` (mentions, seuils, modèle Ollama, dossiers).

## 10. Mesure (vérité terrain + métriques)

- **Vérité terrain** : un CSV rempli à la main (`data/ground_truth.csv`), une ligne par attestation :
  les vrais champs attendus + le verdict attendu. Établi AVANT de regarder les sorties du pipeline.
- **Mesure de lecture** : taux d'extraction correcte, **par champ**. Critère : une extraction est
  correcte si l'écart avec la vérité terrain **ne change pas le verdict** (strict sur dates et mention,
  souple sur le texte nom/assureur/adresse tant que l'info reste reconnaissable).
- **Mesure de décision** : matrice de confusion (verdict pipeline × verdict attendu). Compter
  **faux positifs** (le grave : `conforme` à tort) et **faux négatifs** (alertes à tort). Objectif :
  ≈ zéro faux positif, faux négatifs tolérés.
- **Restitution** : un rapport (`data/output/rapport.md` + tableaux CSV) avec taux par champ,
  matrice de confusion, et une courte interprétation.

## 11. Structure de projet attendue

```
poc-attestations/
  data/
    corpus/            # PDF d'entrée (nommés par numéro de locataire) — NON versionné
    reference/         # export interne numéro → nom, adresse (CSV) — NON versionné
    ground_truth.csv   # vérité terrain remplie à la main — NON versionné
    output/            # résultats + rapport
  config/
    mentions.yaml      # familles de formulations de garantie
    settings.yaml      # modèle Ollama, seuils, chemins
  src/
    ingest.py          # bloc 1-2 : lecture + OCR
    exploitability.py  # bloc 3
    extract.py         # bloc 4 : appel Ollama + normalisation
    rules.py           # bloc 5-6 : grille + arbre + arbitrage
    pipeline.py        # orchestration bout en bout
    evaluate.py        # vérité terrain + métriques + rapport
  tests/               # tests sur quelques cas (y compris page blanche, avis d'échéance)
  .gitignore           # exclut data/corpus, data/reference, ground_truth.csv
  requirements.txt
  README.md
```

## 12. Conventions

- Code et commentaires en français ; noms de variables explicites.
- Tout paramètre réglable (seuils, modèle, listes) va dans `config/`, jamais en dur.
- Chaque bloc est une fonction/module testable indépendamment, avec des entrées/sorties claires.
- `.gitignore` exclut toute donnée réelle (corpus, référence, vérité terrain). Le code est versionnable, les données non.
- Journaliser les étapes (quel fichier, quelle voie de lecture, quel verdict) sans exposer de données personnelles.

## 13. Points à confirmer (PAV) — ne pas bloquer, prendre une valeur par défaut raisonnable et la signaler

- **PAV-1 (mention exigée)** : le métier a dit « mention assurance habitation » ; le réel montre une
  famille plus large (§8). Par défaut : accepter la famille complète. À revalider.
- **PAV-3 (date de référence pour la validité)** : par défaut, « l'attestation couvre le jour du contrôle ».
- **Modèle Ollama** : démarrer avec un modèle 7-8B instruct ; ajustable dans `config/settings.yaml`.
