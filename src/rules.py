"""Blocs 5-6 — Règles déterministes et arbre de décision.

L'IA lit, les règles décident (CLAUDE.md §2). En cas de doute → « douteux ».
"""

VERDICTS = ("conforme", "non_conforme", "douteux")


def decider(exploitabilite: dict, champs: dict, reference: dict, declaratif: dict,
            parametres: dict) -> dict:
    """Applique la grille (blocs A et B) et l'arbre → {"verdict": ..., "motif": ...}."""
    raise NotImplementedError
