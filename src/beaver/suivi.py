"""Journal de génération : durée de chaque étape et signal périodique (temps écoulé, mémoire du processus) pendant
les étapes longues ; mêmes lignes dans la console (`beaver run`) et le journal de la GUI (`progress`).

OpenCascade garde le GIL pendant un booléen : un fil Python ne peut rien dire tant qu'il dure. Les booléens longs
sont donc calculés dans un processus à part (`isolated`) pendant que le processus principal, libre, envoie le
signal périodique."""

from __future__ import annotations

import functools
import os
import resource
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
import multiprocessing

from beaver.env import lire_env

HEARTBEAT = 15.0  # s entre deux signaux « en cours » d'une étape longue
REPORT_MIN = 1.0  # s : durée d'étape annoncée à partir de ce seuil
HEAVY_PLATES = 600  # plaques au-delà desquelles la découpe est annoncée comme un calcul lourd
ISOLATE_ENV = "ISOLER"  # BEAVER_ISOLER=0 : booléens dans le processus principal (pas de signal pendant le calcul)


def duration(seconds: float) -> str:
    """« 4,2 s », « 3 min 05 s »."""
    if seconds < 60:
        return f"{seconds:.1f} s".replace(".", ",")
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s:02d} s"


def memory_bytes() -> int | None:
    """Mémoire résidente du processus et de ses fils (booléen isolé, pool de validation) ; None si inconnue."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        me = psutil.Process()
        total = me.memory_info().rss
        for child in me.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                pass
        return total
    except psutil.Error:
        return None


def peak_memory_bytes() -> int:
    """Pic de mémoire résidente du processus (sans ses fils)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # octets sous macOS, Kio sous Linux


def memory_text() -> str:
    mem = memory_bytes()
    return f"mémoire {mem / 1e9:.1f} Go".replace(".", ",") if mem is not None else ""


@contextmanager
def step(progress: Callable[[str], None], label: str, period: float = HEARTBEAT) -> Iterator[None]:
    """Étape chronométrée : « label : en cours depuis 45 s, mémoire 3,2 Go » toutes les `period` secondes, puis
    « label : 12,3 s » à la fin (si ≥ REPORT_MIN). Le signal vient d'un fil : il ne parle que quand le GIL est libre
    (Python, attente d'un pool ou d'un booléen isolé)."""
    t0 = time.monotonic()
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(period):
            mem = memory_text()
            progress(f"{label} : en cours depuis {duration(time.monotonic() - t0)}" + (f", {mem}" if mem else ""))

    thread = threading.Thread(target=beat, name="beaver-signal", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
    elapsed = time.monotonic() - t0
    if elapsed >= REPORT_MIN:
        progress(f"{label} : {duration(elapsed)}")


def _isolation_enabled() -> bool:
    return lire_env(ISOLATE_ENV) not in ("0", "non", "false")


def isolated(fn: Callable, *args):
    """`fn(*args)` dans un processus à part (spawn) : le processus principal garde le GIL libre pour le signal
    périodique. Arguments et résultat doivent être picklables (formes OCP : `build123d.persistence`). Processus
    indisponible (parent démon, ressources) : calcul sur place, même résultat."""
    if not _isolation_enabled() or multiprocessing.current_process().daemon:
        return fn(*args)
    try:
        with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
            return pool.submit(fn, *args).result()
    except (BrokenProcessPool, OSError):
        return fn(*args)


@functools.lru_cache(maxsize=1)
def cpu_count() -> int:
    return os.cpu_count() or 1
