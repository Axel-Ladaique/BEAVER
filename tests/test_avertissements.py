import io
import warnings

from beaver import avertissements


def test_avertissement_build123d_traduit_une_fois(monkeypatch):
    monkeypatch.setattr(avertissements, "_vus", set())
    monkeypatch.setattr(avertissements, "_origine", None)
    monkeypatch.setattr(warnings, "showwarning", warnings.showwarning)
    sortie = io.StringIO()
    with warnings.catch_warnings():
        avertissements.installer()
        for _ in range(3):
            avertissements._afficher(
                UserWarning("Boolean operation unable to clean"), UserWarning, "x.py", 1, file=sortie)
    texte = sortie.getvalue()
    assert texte.count("\n") == 1  # une seule fois par processus
    assert "faces non fusionnées" in texte and "unable to clean" not in texte


def test_autres_avertissements_inchanges(monkeypatch):
    vus = []
    monkeypatch.setattr(avertissements, "_origine", lambda *a: vus.append(str(a[0])))
    avertissements._afficher(UserWarning("autre chose"), UserWarning, "x.py", 1)
    assert vus == ["autre chose"]


def test_avertissement_reel_passe_par_la_traduction(monkeypatch, capsys):
    monkeypatch.setattr(avertissements, "_vus", set())
    monkeypatch.setattr(avertissements, "_origine", None)
    with warnings.catch_warnings():
        warnings.simplefilter("default")
        avertissements.installer()
        warnings.warn("Boolean operation unable to clean", stacklevel=1)
    err = capsys.readouterr().err
    assert "faces non fusionnées" in err and "UserWarning" not in err
