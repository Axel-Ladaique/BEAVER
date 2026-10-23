"""Journal de génération : durées d'étapes, signal périodique, booléen dans un processus à part."""

import os
import time

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
    assert pipeline.piece_workers([60, 115, 95, 80, 85]) == 3  # 1 couche : ≈ 1,4 Go pour 115 outils
    # 2 couches : ≈ 6,4 Go pour le plus gros morceau (290 outils) → 1 à la fois dans 70 % de 8 Go, 2 dans 20 Go
    assert pipeline.piece_workers([290, 250, 240, 180]) == 1
    monkeypatch.setattr(pipeline, "available_memory", lambda: 20e9)
    assert pipeline.piece_workers([290, 250, 240, 180]) == 2
    monkeypatch.setattr(pipeline, "available_memory", lambda: 1e9)
    assert pipeline.piece_workers([180, 150]) == 1  # jamais moins d'un
    monkeypatch.setattr(pipeline, "available_memory", lambda: None)  # sans psutil : un par cœur
    assert pipeline.piece_workers([180] * 12) == 8
