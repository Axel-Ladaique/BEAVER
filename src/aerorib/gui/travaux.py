"""Générations complètes en arrière-plan (un processus séparé à la fois) et historique des essais.

Chaque génération crée `projets/<nom>/essais/<AAAAMMJJ-HHMMSS>[-n]/` : `config.yaml` (configuration résolue
utilisée), `resume.json` (statut, chiffres, erreurs/avertissements, fichiers) et les sorties (`<nom>.step`,
`.stl`, `.3mf`, `<nom>-rapport.html`). Le processus fils (`_run_job`, fonction de module : compatible spawn)
pousse dans une queue des dicts `{"type": "progression", "message": …}` puis `{"type": "fin", "resume": …}`
ou `{"type": "erreur", "message": …}` ; le serveur les garde en mémoire et les relaie en SSE. Une annulation
ajoute `{"type": "annule", "message": …}`.

Crochets de test (lus dans le fils, car le monkeypatch ne franchit pas spawn) :
- `AERORIB_TEST_DELAI` : secondes d'attente avant le calcul (travail lent, pour l'annulation) ;
- `AERORIB_TEST_PLANTAGE` : le fils meurt aussitôt sans message final ;
- `AERORIB_TEST_DELAI_VALIDATION` (lu par `aerorib.validate`) : attente par couche validée (pool occupé).
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import queue as queue_mod
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from aerorib.gui.projects import CONFIG_NAME, ProjectError, ProjectStore, _flatten, scrub_paths

HEARTBEAT = 15.0  # s entre deux commentaires de maintien du flux SSE
POLL = 0.1  # s, scrutation des messages (flux SSE et queue du fils)
TERMINATE_TIMEOUT = 5.0  # s avant de passer de SIGTERM (terminate) à SIGKILL (kill)
ESSAIS = "essais"
RESUME = "resume.json"
EN_COURS = "en cours"
_ESSAI_RE = re.compile(r"[0-9]{8}-[0-9]{6}(?:-[0-9]{1,3})?")  # toujours avec fullmatch
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".stl": "model/stl",
    ".3mf": "model/3mf",
    ".step": "model/step",
    ".stp": "model/step",
    ".json": "application/json",
    ".yaml": "application/yaml; charset=utf-8",
    ".yml": "application/yaml; charset=utf-8",
}


def _maintenant() -> datetime:
    return datetime.now()


def _write_json(path: Path, data: dict) -> None:
    """Écriture atomique (fichier temporaire puis remplacement)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".resume.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        for essai in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows : fichier ouvert en lecture par le serveur au même instant
                if essai == 9:
                    raise
                time.sleep(0.05)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- processus fils ----------------------------------------------------------------------------------
def _issues(report) -> tuple[list[str], list[str]]:
    from aerorib.report import LABELS

    def grouped(issues, warn: bool) -> list[str]:
        by: dict[str, list] = {}
        for i in issues:
            by.setdefault(i.check, []).append(i)
        out = []
        for check, items in by.items():
            zs = sorted({i.z for i in items})
            texte = f"{LABELS.get(check, check)} : {len(zs)} couche(s), z = {zs[0]:.1f} – {zs[-1]:.1f} mm"
            out.append(texte if warn else f"{texte}, ex. : {items[0].message}")
        return out

    return grouped(report.errors, False), grouped(report.warnings, True)


def _run_job(step: str, config_path: str, out_dir: str, queue) -> None:
    """Génération complète (comme `aerorib run --3mf`) dans le processus fils ; écrit `resume.json`."""
    # POSIX : le fils devient chef de son propre groupe de processus ; ses descendants (pool de validation,
    # tracker de ressources) en héritent, et l'annulation tue tout le groupe (`_tuer_arbre`). Ctrl+C ne
    # l'atteint plus ; on ignore quand même SIGINT : seul le serveur décide de l'arrêt (son extinction
    # annule le travail proprement), le fils ne doit pas mourir d'un KeyboardInterrupt.
    if hasattr(os, "setpgid"):
        try:
            os.setpgid(0, 0)
        except OSError:
            pass
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    t0 = time.monotonic()
    out = Path(out_dir)
    stem = out.parents[1].name  # projets/<nom>/essais/<essai>
    resume: dict[str, Any] = {"statut": EN_COURS, "debut": _maintenant().isoformat(timespec="seconds")}

    def progress(message: str) -> None:
        queue.put({"type": "progression", "message": scrub_paths(str(message))})

    def finish(statut: str, **extra) -> dict:
        resume.update(statut=statut, duree=round(time.monotonic() - t0, 2), **extra)
        resume["fichiers"] = sorted(p.name for p in out.iterdir() if p.is_file() and not p.name.startswith("."))
        _write_json(out / RESUME, resume)
        return resume

    try:
        if os.environ.get("AERORIB_TEST_PLANTAGE"):
            os._exit(3)
        progress("démarrage de la génération…")
        delai = float(os.environ.get("AERORIB_TEST_DELAI") or 0)
        if delai > 0:
            time.sleep(delai)

        from aerorib.bambu import BambuError, read_template, vase_warning
        from aerorib.classify import ClassifyError, classify
        from aerorib.report import format_bays, format_bores, piece_rows
        from aerorib.config import ConfigError, load_config, material_label, resolve_template
        from aerorib.export import export_results
        from aerorib.io_step import StepReadError, read_step
        from aerorib.pipeline import PipelineError, run_model

        avertissements: list[str] = []
        try:
            cfg = load_config(Path(config_path))
            formats = ["step", "stl", "3mf"]
            template = None
            try:
                template = read_template(resolve_template(cfg))
                if (warn := vase_warning(template)) is not None:
                    avertissements.append(scrub_paths(warn))
            except (ConfigError, BambuError) as exc:
                formats.remove("3mf")
                avertissements.append(
                    f"projet Bambu (3MF) non produit pour {material_label(cfg.material)} : {scrub_paths(str(exc))}")
            model = classify(read_step(Path(step)), cfg)
            result = run_model(model, cfg, progress=progress)
            progress("écriture des fichiers…")
            export_results(result, out, stem, formats, template)
        except (ConfigError, StepReadError, ClassifyError, PipelineError, BambuError, FileNotFoundError) as exc:
            message = scrub_paths(str(exc))
            finish("erreur", erreurs=[message], avertissements=avertissements)
            queue.put({"type": "erreur", "message": message})
            return
        erreurs: list[str] = []
        corps = []
        plateau = 1  # un morceau par plateau du projet 3MF, dans l'ordre des corps
        for b in result.bodies:
            errs, warns = _issues(b.report)
            erreurs += [f"{b.name} : {e}" for e in errs]
            avertissements += [f"{b.name} : {w}" for w in warns] + [f"{b.name} : {w}" for w in b.warnings]
            s = b.report.stats
            corps.append({
                "nom": b.name, "ok": b.report.ok, "nervures": b.plates,
                "troncons": len(b.z_heights) or 1,  # tronçons en Z
                "morceaux": max(len(b.segments), 1),  # pièces imprimées (coupes dans la corde comprises)
                "masse": round(s["mass_g"], 2), "trajet": round(s["path_length_m"], 2),
                "couches": int(s["layers"]), "hauteur": round(s["height_mm"], 2),
                "trous": format_bores(b.bores, b.filled, b.tubes).strip().removeprefix("trous : "),
                "baies": format_bays(b.bays, b.z_heights).strip().removeprefix("baies : "),
                # morceaux imprimés : nom, étendue en Z, emprise, rotation sur le plateau, plateau, validation
                "liste_morceaux": piece_rows(b, plateau),
            })
            plateau += max(len(b.segments), 1)
        rapports = sorted(p.name for p in out.glob("*-rapport.html"))
        finish(
            "ok" if result.ok else "non imprimable",
            nervures=sum(c["nervures"] for c in corps),
            troncons=sum(c["troncons"] for c in corps),
            morceaux=sum(c["morceaux"] for c in corps),
            masse=round(sum(c["masse"] for c in corps), 2),
            trajet=round(sum(c["trajet"] for c in corps), 2),
            erreurs=erreurs,
            avertissements=avertissements,
            notices=[f"{b.name} : {n}" for b in result.bodies for n in b.notices],
            corps=corps,
            orientation=result.orientation.source,
            rapport=rapports[0] if rapports else None,
        )
        queue.put({"type": "fin", "resume": resume})
    except Exception as exc:  # dernier recours : jamais de trace Python pour l'utilisateur
        message = scrub_paths(f"erreur interne : {type(exc).__name__}: {exc}")
        try:
            finish("erreur", erreurs=[message])
        except OSError:
            pass
        queue.put({"type": "erreur", "message": message})


# --- côté serveur ------------------------------------------------------------------------------------
def _killpg(pgid: int, sig: int) -> bool:
    """Signal au groupe `pgid` ; False s'il n'existe pas (fils pas encore chef de groupe, ou déjà parti).

    Sûr : un identifiant de groupe est le PID de son chef, qui ne peut être réattribué tant que le groupe vit.
    """
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _taskkill(pid: int) -> None:
    """Windows : termine le processus et tous ses descendants (/T), de force (/F)."""
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)


def _tuer_arbre(proc) -> None:
    """Termine le fils de génération et tous ses descendants (pool de validation), puis l'attend.

    `terminate()` seul laisserait les processus du pool orphelins (bloqués sur leur queue, pour toujours).
    """
    if sys.platform == "win32":
        _taskkill(proc.pid)
        proc.join(TERMINATE_TIMEOUT)
    else:
        if not _killpg(proc.pid, signal.SIGTERM):  # fils pas encore chef de groupe : pas encore de pool
            proc.terminate()
        proc.join(TERMINATE_TIMEOUT)
        _killpg(proc.pid, signal.SIGKILL)  # retardataires (le tracker de ressources ignore SIGTERM)
    if proc.is_alive():
        proc.kill()
    proc.join()


class Job:
    def __init__(self, job_id: str, nom: str, essai: str, dossier: Path) -> None:
        self.id = job_id
        self.nom = nom
        self.essai = essai
        self.dossier = dossier
        self.debut = time.monotonic()
        self.statut = EN_COURS
        self.resume: dict | None = None
        self.messages: list[dict] = []
        self.lock = threading.Lock()
        self.annule = False
        self.process = None
        self.queue = None
        self.pompe: threading.Thread | None = None
        self.cloture = threading.Lock()  # une seule clôture sans message final (pompe ou annulation)

    @property
    def termine(self) -> bool:
        return self.statut != EN_COURS

    def public(self) -> dict:
        with self.lock:
            return {
                "id": self.id, "projet": self.nom, "essai": self.essai, "statut": self.statut,
                "messages": [m["message"] for m in self.messages if m["type"] == "progression"],
                "resume": self.resume,
            }


class JobManager:
    """Un seul travail à la fois pour tout le serveur."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._courant: Job | None = None
        self._arret = False  # extinction du serveur en cours : un fils qui meurt est « annulé »

    # --- démarrage ---------------------------------------------------------------------------
    def _nouvel_essai(self, projet: Path) -> tuple[str, Path]:
        base = _maintenant().strftime("%Y%m%d-%H%M%S")
        racine = projet / ESSAIS
        racine.mkdir(exist_ok=True)
        for n in range(1, 1000):
            essai = base if n == 1 else f"{base}-{n}"
            try:
                (racine / essai).mkdir()
            except FileExistsError:
                continue
            return essai, racine / essai
        raise ProjectError(409, "trop d'essais lancés dans la même seconde")

    def start(self, nom: str) -> Job:
        step = self.store.step_path(nom)
        projet = step.parent
        cfg = self.store.load_config(nom)
        with self._lock:
            if self._courant is not None and not self._courant.termine:
                raise ProjectError(409, "une génération est déjà en cours")
            essai, dossier = self._nouvel_essai(projet)
            data = cfg.model_dump(mode="json")
            data["output"] = {**data.get("output", {}), "dir": "."}  # sorties forcées dans le dossier de l'essai
            data["bambu"] = {**data.get("bambu", {}), "open": False}
            (dossier / CONFIG_NAME).write_text(
                "# configuration résolue utilisée pour cet essai (sorties écrites dans ce dossier)\n"
                + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            job = Job(uuid.uuid4().hex, projet.name, essai, dossier)
            _write_json(dossier / RESUME, {"statut": EN_COURS, "debut": _maintenant().isoformat(timespec="seconds")})
            ctx = multiprocessing.get_context("spawn")
            job.queue = ctx.Queue()
            job.process = ctx.Process(
                target=_run_job, args=(str(step), str(dossier / CONFIG_NAME), str(dossier), job.queue),
                name=f"aerorib-generation-{essai}",
                daemon=False)  # non démon : la validation ouvre un pool ; shutdown()/cancel() le terminent explicitement
            job.process.start()
            job.pompe = threading.Thread(target=self._pomper, args=(job,), name="aerorib-pompe", daemon=True)
            self._jobs[job.id] = job
            self._courant = job
            job.pompe.start()
        return job

    # --- suivi -------------------------------------------------------------------------------
    def _recevoir(self, job: Job, msg: Any) -> bool:
        """Enregistre un message du fils ; True si c'est le message final."""
        if not isinstance(msg, dict) or msg.get("type") not in ("progression", "fin", "erreur"):
            return False
        with job.cloture, job.lock:
            if job.termine:
                return True
            job.messages.append(msg)
            if msg["type"] == "fin":
                job.resume = msg.get("resume") or {}
                job.statut = job.resume.get("statut", "ok")
                return True
            if msg["type"] == "erreur":
                job.resume = _read_json(job.dossier / RESUME) or {"statut": "erreur", "erreurs": [msg.get("message")]}
                job.statut = "erreur"
                return True
        return False

    def _pomper(self, job: Job) -> None:
        proc, q = job.process, job.queue
        final = False
        while not final:
            try:
                final = self._recevoir(job, q.get(timeout=POLL))
                continue
            except queue_mod.Empty:
                pass
            except Exception:  # queue corrompue par un arrêt brutal : pas de boucle active
                time.sleep(POLL)
            if not proc.is_alive():
                while not final:  # derniers messages encore en transit
                    try:
                        final = self._recevoir(job, q.get(timeout=0.5))
                    except Exception:
                        break
                break
        if final:
            proc.join(TERMINATE_TIMEOUT)
        else:
            proc.join()
            self._clore_sans_final(job, proc.exitcode)
        q.close()

    def _clore_sans_final(self, job: Job, exitcode) -> None:
        with job.cloture:
            if not job.termine:
                self._clore(job, exitcode)

    def _clore(self, job: Job, exitcode) -> None:
        duree = round(time.monotonic() - job.debut, 2)
        resume = _read_json(job.dossier / RESUME) or {}
        signaux = {-signal.SIGINT, -signal.SIGTERM}
        if job.annule or self._arret or exitcode in signaux:
            message = "génération annulée" if job.annule or self._arret else "génération interrompue par un signal d'arrêt"
            resume.update(statut="annulé", duree=duree, erreurs=[], message=message)
            final = {"type": "annule", "message": message}
        else:
            message = (f"le processus de génération s'est arrêté sans résultat (code {exitcode}) : "
                       "mémoire insuffisante ou plantage du noyau géométrique ?")
            resume.update(statut="erreur", duree=duree, erreurs=[message])
            final = {"type": "erreur", "message": message}
        resume.setdefault("avertissements", [])
        try:
            resume["fichiers"] = sorted(p.name for p in job.dossier.iterdir()
                                        if p.is_file() and not p.name.startswith("."))
            _write_json(job.dossier / RESUME, resume)
        except OSError:
            pass
        with job.lock:
            job.resume = resume
            job.statut = resume["statut"]
            job.messages.append(final)

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise ProjectError(404, "génération introuvable")
        return job

    def status(self, job_id: str) -> dict:
        return self.get(job_id).public()

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        with job.lock:
            deja = job.termine
            if not deja:
                job.annule = True
        if deja:
            return job.public()
        proc = job.process
        _tuer_arbre(proc)
        if job.pompe is not None:
            job.pompe.join(TERMINATE_TIMEOUT)
        self._clore_sans_final(job, proc.exitcode)  # sans effet si la pompe a déjà clos (message final ou mort)
        return job.public()

    def live(self, nom: str, essai: str) -> Job | None:
        job = self._courant
        if job is not None and not job.termine and job.nom == nom and job.essai == essai:
            return job
        return None

    async def events(self, job_id: str):
        """Messages passés puis en direct (format SSE), battement de cœur, fin après le message final."""
        job = self.get(job_id)
        sent = 0
        dernier = time.monotonic()
        while True:
            with job.lock:
                nouveaux = job.messages[sent:]
                fini = job.termine
            for msg in nouveaux:
                sent += 1
                yield f"id: {sent}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                dernier = time.monotonic()
            if fini and not nouveaux:
                with job.lock:
                    if len(job.messages) == sent:
                        return
                continue
            if time.monotonic() - dernier >= HEARTBEAT:
                yield ": maintien\n\n"
                dernier = time.monotonic()
            await asyncio.sleep(POLL)

    def shutdown(self) -> None:
        """Annule le travail en cours (arrêt du serveur, fin des tests)."""
        self._arret = True
        job = self._courant
        if job is not None and not job.termine:
            self.cancel(job.id)
        for job in list(self._jobs.values()):  # travaux finis : le fils se termine juste après son message final
            if job.pompe is not None:
                job.pompe.join(TERMINATE_TIMEOUT)
            proc = job.process
            if proc is not None and proc.is_alive():
                proc.join(TERMINATE_TIMEOUT)
                if proc.is_alive():
                    _tuer_arbre(proc)


# --- historique ----------------------------------------------------------------------------------
def check_essai(essai: str) -> str:
    if not isinstance(essai, str) or not _ESSAI_RE.fullmatch(essai):
        raise ProjectError(422, "identifiant d'essai invalide (attendu : AAAAMMJJ-HHMMSS)")
    return essai


def ouvrir_dossier(path: Path) -> None:
    """Ouvre un dossier dans le gestionnaire de fichiers du système, sans bloquer."""
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


class History:
    def __init__(self, store: ProjectStore, manager: JobManager) -> None:
        self.store = store
        self.manager = manager

    def _essai_dir(self, nom: str, essai: str) -> Path:
        check_essai(essai)
        projet = self.store.require(nom)
        racine = (projet / ESSAIS).resolve()
        d = (racine / essai).resolve()
        if d.parent != racine or not d.is_dir():
            raise ProjectError(404, f"essai introuvable : {essai}")
        return d

    def _resume(self, nom: str, d: Path) -> dict:
        resume = _read_json(d / RESUME) or {}
        job = self.manager.live(nom, d.name)
        if resume.get("statut", EN_COURS) == EN_COURS and job is None:
            resume = {**resume, "statut": "interrompu"}
        resume.setdefault("statut", EN_COURS)
        return resume

    def list(self, nom: str) -> list[dict]:
        projet = self.store.require(nom)
        racine = projet / ESSAIS
        if not racine.is_dir():
            return []
        out = []
        for d in racine.iterdir():
            if not d.is_dir() or d.is_symlink() or not _ESSAI_RE.fullmatch(d.name):
                continue
            r = self._resume(projet.name, d)
            job = self.manager.live(projet.name, d.name)
            out.append({
                "essai": d.name,
                "date": r.get("debut") or _date_essai(d.name),
                "statut": r["statut"],
                "id": job.id if job else None,
                "duree": r.get("duree"),
                "nervures": r.get("nervures"),
                "troncons": r.get("troncons"),
                "morceaux": r.get("morceaux"),
                "masse": r.get("masse"),
                "trajet": r.get("trajet"),
                "nb_erreurs": len(r.get("erreurs") or []),
                "nb_avertissements": len(r.get("avertissements") or []),
                "rapport": r.get("rapport"),
            })
        return sorted(out, key=lambda e: _cle_essai(e["essai"]), reverse=True)

    def config(self, nom: str, essai: str) -> dict:
        d = self._essai_dir(nom, essai)
        try:
            data = yaml.safe_load((d / CONFIG_NAME).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError, UnicodeDecodeError):
            raise ProjectError(422, f"configuration de l'essai {essai} illisible") from None
        return data if isinstance(data, dict) else {}

    def get(self, nom: str, essai: str) -> dict:
        d = self._essai_dir(nom, essai)
        projet = self.store.require(nom).name
        job = self.manager.live(projet, essai)
        try:
            config = self.config(nom, essai)
        except ProjectError:
            config = None
        return {"essai": essai, "id": job.id if job else None, "resume": self._resume(projet, d), "config": config}

    def diff(self, nom: str, a: str, b: str) -> list[dict]:
        ca, cb = _flatten(self.config(nom, a)), _flatten(self.config(nom, b))
        out = []
        for chemin in sorted(set(ca) | set(cb)):
            va, vb = ca.get(chemin), cb.get(chemin)
            if va != vb:
                out.append({"chemin": list(chemin), "a": va, "b": vb})
        return out

    def fichier(self, nom: str, essai: str, chemin: str) -> Path:
        d = self._essai_dir(nom, essai)
        refus = ProjectError(404, "fichier introuvable dans cet essai")
        if not chemin or "\\" in chemin or "\x00" in chemin or chemin.startswith("/") or re.match(r"^[A-Za-z]:", chemin):
            raise refus
        parts = chemin.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise refus
        cible = d.joinpath(*parts)
        try:
            reel = cible.resolve(strict=True)
        except (OSError, RuntimeError):
            raise refus from None
        if not reel.is_relative_to(d) or not reel.is_file():
            raise refus
        # aucun lien symbolique sur le chemin (même interne) : seuls les fichiers écrits par la génération
        p = cible
        while p != d:
            if p.is_symlink():
                raise refus
            p = p.parent
        return reel

    def projet_3mf(self, nom: str, essai: str) -> Path:
        d = self._essai_dir(nom, essai)
        projets = sorted(p for p in d.glob("*.3mf") if p.is_file() and not p.is_symlink())
        if not projets:
            raise ProjectError(404, "aucun projet Bambu (.3mf) dans cet essai")
        return projets[0]


def _date_essai(essai: str) -> str | None:
    try:
        return datetime.strptime(essai[:15], "%Y%m%d-%H%M%S").isoformat()
    except ValueError:  # dossier au nom plausible mais date impossible (créé à la main)
        return None


def _cle_essai(essai: str) -> tuple[str, int]:
    base, _, n = essai[:15], essai[15:16], essai[16:]
    return base, int(n) if n.isdigit() else 1


# --- routes ------------------------------------------------------------------------------------------
def build_router(store: ProjectStore, manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api")
    historique = History(store, manager)

    @router.post("/projets/{nom}/generations", status_code=202)
    def lancer(nom: str) -> dict:
        return manager.start(nom).public()

    @router.get("/generations/{job_id}")
    def statut(job_id: str) -> dict:
        return manager.status(job_id)

    @router.post("/generations/{job_id}/annuler")
    def annuler(job_id: str) -> dict:
        return manager.cancel(job_id)

    @router.get("/generations/{job_id}/flux")
    def flux(job_id: str) -> StreamingResponse:
        manager.get(job_id)  # 404 avant d'ouvrir le flux
        return StreamingResponse(manager.events(job_id), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/projets/{nom}/essais")
    def essais(nom: str) -> list[dict]:
        return historique.list(nom)

    @router.get("/projets/{nom}/essais/diff")
    def diff(nom: str, a: str = Query(...), b: str = Query(...)) -> list[dict]:
        return historique.diff(nom, a, b)

    @router.get("/projets/{nom}/essais/{essai}")
    def essai(nom: str, essai: str) -> dict:
        return historique.get(nom, essai)

    @router.get("/projets/{nom}/essais/{essai}/fichiers/{chemin:path}")
    def fichier(nom: str, essai: str, chemin: str) -> FileResponse:
        p = historique.fichier(nom, essai, chemin)
        return FileResponse(p, media_type=_TYPES.get(p.suffix.lower(), "application/octet-stream"))

    @router.post("/projets/{nom}/essais/{essai}/ouvrir-bambu")
    def ouvrir_bambu(nom: str, essai: str) -> dict:
        from aerorib import bambu

        p = historique.projet_3mf(nom, essai)
        try:
            bambu.open_in_bambu(p)
        except bambu.BambuError as exc:
            raise ProjectError(422, scrub_paths(str(exc))) from exc
        return {"ok": True, "fichier": p.name}

    @router.post("/projets/{nom}/essais/{essai}/ouvrir-dossier")
    def ouvrir(nom: str, essai: str) -> dict:
        d = historique._essai_dir(nom, essai)
        try:
            ouvrir_dossier(d)
        except OSError as exc:
            raise ProjectError(422, f"ouverture du dossier impossible : {exc.strerror or exc}") from exc
        return {"ok": True}

    return router
