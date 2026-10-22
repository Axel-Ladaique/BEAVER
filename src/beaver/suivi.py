"""Journal de génération : durée de chaque étape et signal périodique (temps écoulé, mémoire du processus) pendant
les étapes longues ; mêmes lignes dans la console (`beaver run`) et le journal de la GUI (`progress`).

OpenCascade garde le GIL pendant un booléen : un fil Python ne peut rien dire tant qu'il dure. Les booléens longs
sont donc calculés dans un processus à part (`isolated`) pendant que le processus principal, libre, envoie le
signal périodique."""

from __future__ import annotations

import functools
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager

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


@functools.lru_cache(maxsize=1)
def _libproc():
    import ctypes

    try:
        return ctypes.CDLL("/usr/lib/libproc.dylib")
    except OSError:
        return None


def _footprint(pid: int) -> int | None:
    """macOS : empreinte physique (`phys_footprint`, celle du Moniteur d'activité). Le RSS n'y compte pas la mémoire
    compressée : un booléen de plusieurs Go sous pression mémoire y paraît peser 50 Mo."""
    import ctypes

    lib = _libproc()
    if lib is None:
        return None
    info = (ctypes.c_uint64 * 40)()  # rusage_info_v2 : uuid (2 mots), 6 compteurs, resident_size, phys_footprint…
    if lib.proc_pid_rusage(int(pid), 2, ctypes.byref(info)) != 0:
        return None
    return int(info[9])


def _process_memory(proc) -> int:
    if sys.platform == "darwin" and (fp := _footprint(proc.pid)) is not None:
        return fp
    return proc.memory_info().rss


def memory_bytes() -> int | None:
    """Mémoire du processus et de ses fils (booléen isolé, pool de validation) ; None si inconnue (sans psutil)."""
    try:
        import psutil
    except ImportError:
        return _footprint(os.getpid()) if sys.platform == "darwin" else None
    try:
        me = psutil.Process()
        total = _process_memory(me)
        for child in me.children(recursive=True):
            try:
                total += _process_memory(child)
            except psutil.Error:
                pass
        return total
    except psutil.Error:
        return None


def memory_text() -> str:
    """« mémoire 3,2 Go » (« 450 Mo » sous 1 Go), vide si inconnue."""
    mem = memory_bytes()
    if mem is None:
        return ""
    return f"mémoire {mem / 1e9:.1f} Go".replace(".", ",") if mem >= 1e9 else f"mémoire {mem / 1e6:.0f} Mo"


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


def can_isolate() -> bool:
    """Calcul dans un processus à part possible : pas désactivé (BEAVER_ISOLER=0), pas dans un processus démon (qui
    ne peut pas avoir de fils)."""
    return lire_env(ISOLATE_ENV) not in ("0", "non", "false") and not multiprocessing.current_process().daemon


def isolated(fn: Callable, *args):
    """`fn(*args)` dans un processus à part (spawn) : le processus principal garde le GIL libre pour le signal
    périodique. Arguments et résultat doivent être picklables (formes OCP : `build123d.persistence`). Processus
    indisponible (parent démon, ressources) : calcul sur place, même résultat."""
    if not can_isolate():
        return fn(*args)
    try:
        with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
            return pool.submit(fn, *args).result()
    except (BrokenProcessPool, OSError):
        return fn(*args)
