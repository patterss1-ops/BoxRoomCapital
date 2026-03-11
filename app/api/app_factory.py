"""Shared FastAPI app factory for the control plane."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


def create_app(
    *,
    init_db: Any,
    run_preflight_checks: Any,
    config_module: Any,
    control_obj: Any,
    project_root: Path,
    ledger_router: Any,
    advisory_router: Any,
    broker_router: Any,
    webhooks_router: Any,
    research_router: Any,
    fragments_router: Any,
    system_router: Any,
    logger_name: str,
) -> FastAPI:
    @asynccontextmanager
    async def app_lifespan(app: FastAPI):
        logger = logging.getLogger(logger_name)
        init_db()

        preflight = run_preflight_checks(logger)
        app.state.preflight = preflight
        config_warnings = config_module.validate_critical_config()
        app.state.config_warnings = list(config_warnings)

        if preflight["ig_broker"] == "missing":
            logger.warning(
                "IG credentials not configured for the active broker mode. Set IG_DEMO_* or IG_LIVE_* "
                "(legacy IG_* remains supported) to enable broker connection from the control plane."
            )
        for warning in config_warnings:
            logger.warning("Config validation: %s", warning)

        if config_module.ORCHESTRATOR_ENABLED:
            try:
                result = control_obj.start_scheduler()
                logger.info("Auto-start scheduler: %s", result.get("status"))
            except Exception as exc:
                logger.error("Failed to auto-start scheduler: %s", exc)

        if config_module.DISPATCHER_ENABLED:
            try:
                result = control_obj.start_dispatcher()
                logger.info("Auto-start dispatcher: %s", result.get("status"))
            except Exception as exc:
                logger.error("Failed to auto-start dispatcher: %s", exc)

        if config_module.INTRADAY_ENABLED:
            try:
                result = control_obj.start_intraday()
                logger.info("Auto-start intraday loop: %s", result.get("status"))
            except Exception as exc:
                logger.error("Failed to auto-start intraday loop: %s", exc)

        supervisor_stop = asyncio.Event()

        async def supervisor_loop():
            while not supervisor_stop.is_set():
                try:
                    await asyncio.sleep(60)
                    restarted = control_obj.check_and_restart()
                    if restarted:
                        logger.warning("Supervisor restarted: %s", restarted)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug("Supervisor tick error: %s", exc)

        supervisor_task = asyncio.create_task(supervisor_loop())

        yield

        supervisor_stop.set()
        supervisor_task.cancel()

        logger.info("Shutting down background services...")
        try:
            control_obj.stop_scheduler()
        except Exception:
            pass
        try:
            control_obj.stop_dispatcher()
        except Exception:
            pass
        try:
            control_obj.stop_intraday()
        except Exception:
            pass

    app = FastAPI(
        title="Trading Bot Control Plane",
        version="1.0.0",
        lifespan=app_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"https://([a-z0-9-]+\.)?seekingalpha\.com",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(ledger_router)
    app.include_router(advisory_router)
    app.include_router(broker_router)
    app.include_router(webhooks_router)
    app.include_router(research_router)
    app.include_router(fragments_router)
    app.include_router(system_router)
    app.mount(
        "/static",
        StaticFiles(directory=str(project_root / "app" / "web" / "static")),
        name="static",
    )
    return app
