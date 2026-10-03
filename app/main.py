"""FastAPI backend + static dashboard.

NOTE: this is a single-user, local tool. It has NO authentication, so it binds to 127.0.0.1 by
default. Don't expose it to a network without putting auth in front of it."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Callable, Literal, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .feed import refresh
from .pipeline import PipelineBusy, ensure_profile, rescore
from .resume import ResumeError, profile_from_file
from .store import VALID_STATUSES, Store

Level = Literal["intern", "entry", "mid", "senior", "unknown"]
Status = Literal["new", "saved", "applied", "dismissed"]
Loc = Literal["india", "remote"]
MAX_UPLOAD = 5 * 1024 * 1024


class StatusBody(BaseModel):
    status: Status


class ProfileUpdate(BaseModel):
    skills: Optional[list[str]] = Field(default=None, max_length=80)
    target_roles: Optional[list[str]] = Field(default=None, max_length=60)
    exclude_keywords: Optional[list[str]] = Field(default=None, max_length=40)
    locations: Optional[list[Loc]] = None
    level: Optional[Literal["entry", "mid", "senior"]] = None
    wants_internships: Optional[bool] = None


def _clean_list(values: list[str]) -> list[str]:
    out, seen = [], set()
    for v in values:
        v = " ".join(str(v).split())[:60].strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _csv(value: Optional[str]) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def create_app(store: Optional[Store] = None, crawler: Optional[Callable[[Store], dict]] = None) -> FastAPI:
    """`crawler` lets tests swap the network crawl for a fake."""
    store = store or Store()
    crawler = crawler or (lambda s: refresh(s))
    app = FastAPI(title="Jobs Pipeline", version="1.0")
    state = {"thread": None, "lock": threading.Lock(), "error": None}

    # ------------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    def list_jobs(
        q: str = Query("", max_length=200),
        min_score: int = Query(0, ge=0, le=100),
        level: Optional[str] = None,
        location: Optional[str] = None,
        source: Optional[str] = None,
        status: Optional[str] = None,
        company: str = Query("", max_length=100),
        days: Optional[int] = Query(None, ge=1, le=365),
        min_lpa: Optional[float] = Query(None, ge=0, le=1000, description="Advertised salary range must reach this many LPA"),
        has_salary: bool = False,
        sort: Literal["score", "newest", "company", "salary"] = "score",
        limit: int = Query(30, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        levels, locs, statuses = _csv(level), _csv(location), _csv(status)
        if any(l not in ("intern", "entry", "mid", "senior", "unknown") for l in levels):
            raise HTTPException(422, "invalid level")
        if any(l not in ("india", "remote") for l in locs):
            raise HTTPException(422, "invalid location")
        if any(s not in VALID_STATUSES for s in statuses):
            raise HTTPException(422, "invalid status")
        return store.query_jobs(
            q=q, min_score=min_score, levels=levels, locations=locs, sources=_csv(source),
            statuses=statuses, company=company, posted_within_days=days, min_lpa=min_lpa, has_salary=has_salary, sort=sort, limit=limit, offset=offset,
        )

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int):
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        job.pop("fingerprint", None)
        return job

    @app.post("/api/jobs/{job_id}/status")
    def set_status(job_id: int, body: StatusBody):
        if not store.set_status(job_id, body.status):
            raise HTTPException(404, "job not found")
        return {"id": job_id, "status": body.status}

    @app.get("/api/stats")
    def stats():
        s = store.stats()
        s["crawling"] = bool(store.running_run())
        return s

    # ---------------------------------------------------------------- profile
    @app.get("/api/profile")
    def get_profile():
        profile = store.load_profile()
        if not profile:
            raise HTTPException(404, "no profile yet - upload a resume")
        return profile

    @app.put("/api/profile")
    def update_profile(body: ProfileUpdate):
        profile = store.load_profile()
        if not profile:
            raise HTTPException(404, "no profile yet - upload a resume")
        if body.skills is not None:
            skills = _clean_list(body.skills)
            weights = profile.get("skill_weights", {})
            profile["skills"] = [s.lower() for s in skills]
            profile["skill_weights"] = {s: weights.get(s, 1.0) for s in profile["skills"]}
        if body.target_roles is not None:
            profile["target_roles"] = [r.lower() for r in _clean_list(body.target_roles)]
        if body.exclude_keywords is not None:
            profile["exclude_keywords"] = [r.lower() for r in _clean_list(body.exclude_keywords)]
        if body.locations is not None:
            profile["locations"] = list(dict.fromkeys(body.locations))
        if body.level is not None:
            profile["level"] = body.level
        if body.wants_internships is not None:
            profile["wants_internships"] = body.wants_internships
        store.save_profile(profile)
        return {"profile": profile, "rescored": rescore(store, profile)}

    @app.post("/api/profile/resume")
    async def upload_resume(file: UploadFile = File(...)):
        name = (file.filename or "").lower()
        if not name.endswith((".pdf", ".txt", ".md")):
            raise HTTPException(415, "Upload a PDF, TXT or MD resume")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Resume larger than 5 MB")
        suffix = Path(name).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            try:
                profile = profile_from_file(tmp.name)
            except ResumeError as e:
                raise HTTPException(422, str(e)) from e
        profile["source_file"] = Path(file.filename or "upload").name
        store.save_profile(profile)
        return {"profile": profile, "rescored": rescore(store, profile)}

    # ------------------------------------------------------------------ crawl
    def _crawl_worker():
        try:
            crawler(store)
            state["error"] = None
        except PipelineBusy:
            pass
        except Exception as e:  # noqa: BLE001 - surface to the UI, never crash the thread silently
            state["error"] = f"{type(e).__name__}: {e}"

    @app.post("/api/crawl", status_code=202)
    def start_crawl():
        with state["lock"]:
            t = state["thread"]
            if (t and t.is_alive()) or store.running_run():
                raise HTTPException(409, "A crawl is already running")
            if not store.load_profile():
                try:
                    ensure_profile(store)
                except ResumeError as e:
                    raise HTTPException(422, f"Upload a resume first ({e})") from e
            state["error"] = None
            t = threading.Thread(target=_crawl_worker, daemon=True, name="crawl")
            state["thread"] = t
            t.start()
        return {"started": True}

    @app.get("/api/crawl/status")
    def crawl_status():
        t = state["thread"]
        running = bool((t and t.is_alive()) or store.running_run())
        runs = store.recent_runs(1)
        return {"running": running, "error": state["error"], "last_run": runs[0] if runs else None}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ----------------------------------------------------------------- static
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    return app


def __getattr__(name: str):
    """Lazy module-level `app` so `uvicorn app.main:app` works without side effects on plain import."""
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(name)
