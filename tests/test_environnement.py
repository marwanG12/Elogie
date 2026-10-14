"""Vérifie que l'environnement du POC est opérationnel (dépendances, Tesseract fra, Ollama, config)."""
from pathlib import Path

import pytest
import requests
import yaml

RACINE = Path(__file__).resolve().parent.parent


def test_imports_dependances():
    import dateparser, pymupdf, pandas, pdf2image, pdfplumber, pytesseract, rapidfuzz  # noqa: F401


def test_configs_lisibles():
    settings = yaml.safe_load((RACINE / "config/settings.yaml").read_text(encoding="utf-8"))
    mentions = yaml.safe_load((RACINE / "config/mentions.yaml").read_text(encoding="utf-8"))
    assert settings["ollama"]["modele"]
    assert len(mentions["formulations_garantie"]) == 7


def test_tesseract_francais():
    import pytesseract
    assert "fra" in pytesseract.get_languages(config="")


def test_dateparser_francais():
    import dateparser
    date = dateparser.parse("1er avril 2023", languages=["fr"])
    assert date is not None and date.strftime("%Y-%m-%d") == "2023-04-01"


def test_ollama_local_et_modele():
    settings = yaml.safe_load((RACINE / "config/settings.yaml").read_text(encoding="utf-8"))
    url, modele = settings["ollama"]["url"], settings["ollama"]["modele"]
    try:
        reponse = requests.get(f"{url}/api/tags", timeout=5)
    except requests.ConnectionError:
        pytest.fail(f"Ollama ne répond pas sur {url} (lancer : ollama serve)")
    modeles = [m["name"] for m in reponse.json().get("models", [])]
    assert any(m.startswith(modele) for m in modeles), f"Modèle absent (ollama pull {modele}) ; présents : {modeles}"
