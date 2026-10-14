"""Variables d'environnement : `BEAVER_*` d'abord, puis repli sur l'ancien nom `AERORIB_*`."""
from __future__ import annotations

import os


def lire_env(nom: str) -> str | None:
    """Valeur de `BEAVER_<nom>`, sinon de `AERORIB_<nom>` (ancien nom conservé), sinon None."""
    for prefixe in ("BEAVER_", "AERORIB_"):
        valeur = os.environ.get(prefixe + nom)
        if valeur is not None:
            return valeur
    return None
