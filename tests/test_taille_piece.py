"""Contrôle en amont : pièce sans commune mesure avec le volume d'impression."""


def test_piece_demesuree_refusee_avant_tout_calcul():
    """Solide de 1,9 × 3 m (≈ 400 L) : refusé tout de suite, message clair, sans orientation ni génération."""
    import pytest
    from build123d import Box

    from beaver.classify import classify
    from beaver.config import Config
    from beaver.io_step import NamedBody
    from beaver.pipeline import MAX_BUILD_VOLUMES, PipelineError, check_printable_size, run_model

    cfg = Config()
    geant = Box(1900, 3000, 72)
    model = classify([NamedBody(("WING_1",), geant)], cfg)
    etapes = []
    with pytest.raises(PipelineError, match=r"WING_1 : pièce sans commune mesure .* 410 L .*unité d'export"):
        run_model(model, cfg, progress=etapes.append)
    assert etapes == []  # refusée avant l'orientation
    # une grande aile (≈ 4,5 volumes de P1S) passe, et le seuil est bien au-dessus
    check_printable_size("WING_1", Box(4500, 400, 38), cfg)
    assert MAX_BUILD_VOLUMES >= 5
