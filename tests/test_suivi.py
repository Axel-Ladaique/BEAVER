"""Journal de génération : durées d'étapes, signal périodique, booléen dans un processus à part."""

import os
import time

import pytest
from build123d import Box, Cylinder, Pos

from beaver import suivi
from beaver.pipeline import ISOLATE_MIN_TOOLS, cut_tools


def test_duration_text():
    assert suivi.duration(4.24) == "4,2 s"
    assert suivi.duration(185) == "3 min 05 s"


def test_step_reports_duration_and_heartbeat(monkeypatch):
    monkeypatch.setattr(suivi, "REPORT_MIN", 0.0)
    msgs = []
    with suivi.step(msgs.append, "W : découpe", period=0.05):
        time.sleep(0.18)
    beats = [m for m in msgs if "en cours depuis" in m]
    assert len(beats) >= 2 and all(m.startswith("W : découpe : en cours depuis ") for m in beats)
    assert msgs[-1].startswith("W : découpe : ") and msgs[-1].endswith(" s")
    assert "mémoire" in beats[0] and beats[0].endswith((" Go", " Mo"))  # psutil présent dans l'environnement


def test_short_step_is_silent():
    msgs = []
    with suivi.step(msgs.append, "rapide"):
        pass
    assert msgs == []


def _pid(x):
    return os.getpid(), x * 2


def test_isolated_runs_in_another_process():
    pid, value = suivi.isolated(_pid, 21)
    assert value == 42 and pid != os.getpid()


def test_isolated_can_be_disabled(monkeypatch):
    monkeypatch.setenv("BEAVER_ISOLER", "0")
    assert suivi.isolated(_pid, 1) == (os.getpid(), 2)


def test_isolated_cut_matches_in_process_cut():
    box = Box(40, 40, 10)
    tools = [Pos(-18 + 0.36 * i, 0, 0) * Cylinder(0.1, 20) for i in range(ISOLATE_MIN_TOOLS)]
    isolated = cut_tools(box, tools)
    local = box.cut(*tools)
    assert len(isolated.solids()) == 1 and isolated.is_valid
    assert abs(isolated.volume - local.volume) < 1e-6
    assert len(isolated.faces()) == len(local.faces())


def test_piece_workers_bounded_by_memory(monkeypatch):
    from beaver import pipeline

    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(pipeline, "available_memory", lambda: 8e9)
    assert pipeline.piece_workers([10, 20, 30]) == 3  # petits morceaux : un par morceau
    assert pipeline.piece_workers([60, 115, 95]) == 2  # 1 couche : ≈ 2,5 Go pour 115 outils rognés, 2 dans 5,6 Go
    # 2 couches (grande aile delta) : ≈ 9,4 Go pour le plus gros morceau (244 outils rognés) → 1 à la fois dans 70 % de 8 Go,
    # 2 dans 32 Go
    assert pipeline.piece_workers([244, 240, 192]) == 1
    monkeypatch.setattr(pipeline, "available_memory", lambda: 32e9)
    assert pipeline.piece_workers([244, 240, 192]) == 2
    monkeypatch.setattr(pipeline, "available_memory", lambda: 1e9)
    assert pipeline.piece_workers([180, 150]) == 1  # jamais moins d'un
    monkeypatch.setattr(pipeline, "available_memory", lambda: None)  # sans psutil : un par cœur
    assert pipeline.piece_workers([180] * 12) == 8


def test_piece_memory_cap(monkeypatch):
    from beaver import pipeline

    monkeypatch.delenv("BEAVER_MEMOIRE_MORCEAU_GO", raising=False)
    monkeypatch.delenv("AERORIB_MEMOIRE_MORCEAU_GO", raising=False)
    assert pipeline.piece_memory_cap(2, 40e9) == 0.85 * 20e9  # mémoire disponible partagée entre les morceaux
    assert pipeline.piece_memory_cap(1, None) is None  # sans psutil : pas de plafond
    monkeypatch.setenv("BEAVER_MEMOIRE_MORCEAU_GO", "12,5")
    assert pipeline.piece_memory_cap(3, 40e9) == 12.5e9
    monkeypatch.setenv("BEAVER_MEMOIRE_MORCEAU_GO", "0")
    assert pipeline.piece_memory_cap(1, 40e9) is None


def test_piece_over_memory_cap_is_stopped_with_its_name(monkeypatch):
    """Un booléen de morceau qui dépasse le plafond est arrêté : erreur claire (nom du morceau, outils), annoncé avant."""
    from build123d.persistence import serialize_shape

    from beaver import pipeline

    piece = serialize_shape(Box(40, 40, 40).wrapped)
    tools = [serialize_shape((Pos(-15 + 3 * i, 15, 0) * Box(0.4, 20, 60)).wrapped) for i in range(10)]
    msgs = []
    with pytest.raises(pipeline.PieceMemoryError, match="T1-B : le booléen de 10 outils dépasse"):
        pipeline._run_piece_jobs([(piece, 0.0, tools, [])], ["T1-B"], 1, 1e6, lambda k, r: None, msgs.append, "")
    assert msgs[0] == "T1-B : découpe de 10 outils…"


def test_piece_jobs_run_in_child_processes():
    from build123d.persistence import serialize_shape

    from beaver import pipeline

    piece = serialize_shape(Box(40, 40, 40).wrapped)
    tools = [serialize_shape((Pos(-15 + 3 * i, 15, 0) * Box(0.4, 20, 60)).wrapped) for i in range(4)]
    out = {}
    pipeline._run_piece_jobs([(piece, 0.0, tools, []), (piece, 0.0, tools[:2], [])], ["A", "B"], 2, None,
                             out.__setitem__, lambda m: None, "")
    assert sorted(out) == [0, 1] and out[0][1] == 0 and out[0][2] == []


def test_clip_tools_keeps_the_cut_solid():
    """Outils rognés à la boîte du morceau : même solide que la découpe par les outils entiers ; ceux qui ne touchent
    que la boîte voisine sont écartés."""
    from build123d import Rot

    from beaver import pipeline

    piece = Box(40, 20, 40)
    tools = [Pos(-12 + 6 * i, 5, 0) * Rot(0, 45, 0) * Box(0.4, 20, 300) for i in range(5)]
    tools.append(Pos(0, 5, 200) * Box(0.4, 20, 30))  # boîte au-delà du morceau
    clipped = pipeline.clip_tools(piece, tools)
    assert len(clipped) == 5
    assert all(c.bounding_box().size.Z < 45 for c in clipped)
    whole, cut = piece.cut(*tools), piece.cut(*clipped)
    assert cut.volume == pytest.approx(whole.volume, rel=1e-9)
    assert len(cut.faces()) == len(whole.faces())


def test_piece_over_shared_cap_is_retried_alone(monkeypatch):
    """Plafond partagé dépassé à plusieurs morceaux à la fois : le morceau est repris seul à la fin, au plafond d'un
    morceau seul ; il ne s'arrête en erreur que s'il le dépasse aussi seul."""
    from build123d.persistence import serialize_shape

    from beaver import pipeline

    piece = serialize_shape(Box(40, 40, 40).wrapped)
    tools = [serialize_shape((Pos(-15 + 3 * i, 15, 0) * Box(0.4, 20, 60)).wrapped) for i in range(4)]
    jobs = [(piece, 0.0, tools, []), (piece, 0.0, tools[:2], [])]
    monkeypatch.delenv("BEAVER_MEMOIRE_MORCEAU_GO", raising=False)
    monkeypatch.delenv("AERORIB_MEMOIRE_MORCEAU_GO", raising=False)
    msgs, out = [], {}
    pipeline._run_piece_jobs(jobs, ["A", "B"], 2, 1e6, out.__setitem__, msgs.append, "")
    assert sorted(out) == [0, 1]
    assert any("A : 0,0 Go dépassés à plusieurs morceaux à la fois — repris seul" in m for m in msgs), msgs
    assert any(m.startswith("reprise seul de ") for m in msgs), msgs
    monkeypatch.setenv("BEAVER_MEMOIRE_MORCEAU_GO", "0.001")  # seul aussi : arrêt
    with pytest.raises(pipeline.PieceMemoryError, match="dépasse 0,0 Go"):
        pipeline._run_piece_jobs(jobs, ["A", "B"], 2, 1e6, lambda k, r: None, lambda m: None, "")
