# POC — Vérification automatisée des attestations d'assurance

Preuve de concept (mémoire M2 IA & Big Data, Elogie-Siemp) : lire des attestations d'assurance
habitation (PDF), vérifier leur conformité par des **règles déterministes**, rendre un verdict
`conforme` / `non_conforme` / `douteux`, et mesurer la fiabilité. **Traitement 100 % local.**

Le contexte complet et les règles du projet sont dans [`CLAUDE.md`](CLAUDE.md).

## Prérequis
- Python 3.12
- [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) avec la langue `fra`
- [Ollama](https://ollama.com) + un modèle : `ollama pull qwen2.5:7b-instruct`

## Installation (Windows / PowerShell)
```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest tests/test_environnement.py -v   # vérifie l'environnement
```

## Données (non versionnées)
- `data/corpus/` : PDF nommés par numéro de locataire
- `data/reference/reference.csv` : export numéro → nom, adresse
- `data/ground_truth.csv` : vérité terrain remplie à la main

## Utilisation
```powershell
python -m scripts.inspecter_corpus   # blocs 1-3 : voie de lecture + exploitabilité par PDF
python -m pytest tests -v            # tests (environnement + blocs 1-3)
```

À venir :
```powershell
python -m src.pipeline    # traite le corpus → data/output/
python -m src.evaluate    # métriques + data/output/rapport.md
```
