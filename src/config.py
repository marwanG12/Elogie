"""Chargement de la configuration externalisée (config/*.yaml)."""
from pathlib import Path

import yaml

RACINE_PROJET = Path(__file__).resolve().parent.parent


def charger_yaml(chemin_relatif: str) -> dict:
    """Lit un fichier YAML relatif à la racine du projet."""
    chemin = RACINE_PROJET / chemin_relatif
    return yaml.safe_load(chemin.read_text(encoding="utf-8"))


def charger_parametres(chemin_relatif: str = "config/settings.yaml") -> dict:
    return charger_yaml(chemin_relatif)
