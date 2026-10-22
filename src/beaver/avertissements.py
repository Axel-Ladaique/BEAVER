"""Avertissements bruts des bibliothèques (build123d, OpenCascade) traduits en français, une fois par processus."""

from __future__ import annotations

import sys
import threading
import warnings

# message d'origine → texte affiché. « unable to clean » : après un booléen, build123d tente de fusionner les faces
# coplanaires (ShapeUpgrade_UnifySameDomain) ; en cas d'échec il garde le résultat non fusionné, valide mais avec
# plus de faces. Rien à faire côté utilisateur : la validation de la pièce contrôle le résultat.
TRADUCTIONS = {
    "Boolean operation unable to clean": (
        "BEAVER ⚠ opération booléenne : faces non fusionnées après la découpe (simplification OpenCascade "
        "impossible) — résultat conservé tel quel, en général sans effet ; la validation de la pièce le contrôle"
    ),
}

_vus: set[str] = set()
_lock = threading.Lock()
_origine = None


def _afficher(message, category, filename, lineno, file=None, line=None):
    texte = TRADUCTIONS.get(str(message))
    if texte is None:
        return _origine(message, category, filename, lineno, file, line)
    with _lock:
        if texte in _vus:
            return None
        _vus.add(texte)
    flux = file if file is not None else sys.stderr
    try:
        flux.write(texte + "\n")
        flux.flush()
    except (OSError, ValueError, AttributeError):
        pass
    return None


def installer() -> None:
    """Remplace l'affichage des avertissements connus (idempotent) ; les autres gardent l'affichage Python."""
    global _origine
    if _origine is not None:
        return
    _origine = warnings.showwarning
    warnings.showwarning = _afficher
    for brut in TRADUCTIONS:
        # toujours transmis à `_afficher` (qui dédoublonne lui-même, quel que soit l'appelant)
        warnings.filterwarnings("always", message=rf"^{brut}$", category=UserWarning)
