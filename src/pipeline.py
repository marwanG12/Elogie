"""Orchestration bout en bout : corpus → résultats (JSON + CSV) dans data/output/.

Simule l'écriture dans l'ERP. Journalise sans données personnelles (numéro de locataire seulement).
"""


def executer(chemin_settings: str = "config/settings.yaml") -> None:
    """Traite tous les PDF du corpus et écrit les résultats."""
    raise NotImplementedError


if __name__ == "__main__":
    executer()
