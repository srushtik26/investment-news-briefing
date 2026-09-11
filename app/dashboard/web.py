"""
FastAPI web application for Plutus Wealth Management LLP - Investment Committee Briefing Dashboard.
Read-only interface strictly decoupled from the pipeline runner.
"""

from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.dashboard.repository import DashboardRepository


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def create_app(db_path: Optional[Path] = None) -> FastAPI:
    """Application factory for dashboard web server."""
    app = FastAPI(
        title="Plutus Wealth Management LLP - Investment Committee News Briefing",
        description="Read-only institutional intelligence dashboard.",
        docs_url=None,  # Disabled in production for security
        redoc_url=None,
    )

    # Mount static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    repo = DashboardRepository(db_path=db_path)

    @app.get("/health", response_class=JSONResponse)
    def health_check():
        """Health check endpoint."""
        latest = repo.get_latest_briefing()
        return {
            "status": "ok",
            "backend": repo.backend,
            "app": "plutus-briefing-dashboard",
            "database": "connected",
            "latest_briefing_date": latest.briefing_date.isoformat() if latest else None,
            "total_briefings_count": len(repo.get_archive_dates()),
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """Display the latest successful investment committee briefing."""
        latest = repo.get_latest_briefing()
        return templates.TemplateResponse(
            request=request,
            name="briefing.html",
            context={
                "briefing": latest,
                "is_latest": True,
                "title": "Latest Briefing",
            },
        )

    @app.get("/archive", response_class=HTMLResponse)
    def archive(request: Request):
        """Display list of all archived briefing dates."""
        archives = repo.get_archive_dates()
        return templates.TemplateResponse(
            request=request,
            name="archive.html",
            context={
                "archives": archives,
                "title": "Briefing Archive",
            },
        )

    @app.get("/briefing/{briefing_date}", response_class=HTMLResponse)
    def get_briefing(request: Request, briefing_date: str):
        """Display briefing for a specific historical date (YYYY-MM-DD)."""
        try:
            target_d = date.fromisoformat(briefing_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

        briefing = repo.get_briefing_by_date(target_d)
        if not briefing:
            raise HTTPException(status_code=404, detail=f"No briefing found for {briefing_date}.")

        latest = repo.get_latest_briefing()
        is_latest = latest is not None and latest.briefing_date == briefing.briefing_date

        return templates.TemplateResponse(
            request=request,
            name="briefing.html",
            context={
                "briefing": briefing,
                "is_latest": is_latest,
                "title": f"Briefing - {briefing.briefing_date.strftime('%d %B %Y')}",
            },
        )

    return app


# Default app instance
app = create_app()
