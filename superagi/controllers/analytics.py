from fastapi import APIRouter, Depends, HTTPException, Query
from superagi.helper.auth import check_auth, get_user_organisation
from superagi.apm.analytics_helper import AnalyticsHelper
from superagi.apm.event_handler import EventHandler
from superagi.apm.tools_handler import ToolsHandler
from superagi.apm.knowledge_handler import KnowledgeHandler
from fastapi_jwt_auth import AuthJWT
from fastapi_sqlalchemy import db
from superagi.monitoring.soundcloud_engagement import runtime_from_env
import logging

router = APIRouter()

# XuniHub's SoundCloud monitor is intentionally read-only. It starts only when
# SOUNDCLOUD_ACCESS_TOKEN is configured; otherwise its API reports UNCONFIGURED
# instead of inventing engagement values.
_soundcloud_runtime = runtime_from_env()
_soundcloud_runtime.start()


@router.get("/metrics", status_code=200)
def get_metrics(organisation=Depends(get_user_organisation)):
    """
    Get the total tokens, total calls, and the number of run completed.

    Returns:
        metrics: dictionary containing total tokens, total calls, and the number of runs completed.

    """
    try:
        return AnalyticsHelper(session=db.session, organisation_id=organisation.id).calculate_run_completed_metrics()
    except Exception as e:
        logging.error(f"Error while calculating metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/agents/all", status_code=200)
def get_agents(organisation=Depends(get_user_organisation)):
    try:
        return AnalyticsHelper(session=db.session, organisation_id=organisation.id).fetch_agent_data()
    except Exception as e:
        logging.error(f"Error while fetching agent data: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/agents/{agent_id}", status_code=200)
def get_agent_runs(agent_id: int, organisation=Depends(get_user_organisation)):
    try:
        return AnalyticsHelper(session=db.session, organisation_id=organisation.id).fetch_agent_runs(agent_id)
    except Exception as e:
        logging.error(f"Error while fetching agent data: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/runs/active", status_code=200)
def get_active_runs(organisation=Depends(get_user_organisation)):
    try:
        return AnalyticsHelper(session=db.session, organisation_id=organisation.id).get_active_runs()
    except Exception as e:
        logging.error(f"Error while getting active runs: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/tools/used", status_code=200)
def get_tools_used(organisation=Depends(get_user_organisation)):
    try:
        return ToolsHandler(session=db.session, organisation_id=organisation.id).calculate_tool_usage()
    except Exception as e:
        logging.error(f"Error while calculating tool usage: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/tools/{tool_name}/usage", status_code=200)
def get_tool_usage(tool_name: str, organisation=Depends(get_user_organisation)):
    try:
        return ToolsHandler(session=db.session, organisation_id=organisation.id).get_tool_usage_by_name(tool_name)
    except Exception as e:
        if hasattr(e, 'status_code'):
            raise HTTPException(status_code=e.status_code, detail=e.detail)
        else:
            raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/knowledge/{knowledge_name}/usage", status_code=200)
def get_knowledge_usage(knowledge_name: str, organisation=Depends(get_user_organisation)):
    try:
        return KnowledgeHandler(session=db.session, organisation_id=organisation.id).get_knowledge_usage_by_name(knowledge_name)
    except Exception as e:
        if hasattr(e, 'status_code'):
            raise HTTPException(status_code=e.status_code, detail=e.detail)
        else:
            raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/tools/{tool_name}/logs", status_code=200)
def get_tool_logs(tool_name: str, organisation=Depends(get_user_organisation)):
    try:
        return ToolsHandler(session=db.session, organisation_id=organisation.id).get_tool_events_by_name(tool_name)
    except Exception as e:
        logging.error(f"Error while getting tool event details: {str(e)}")
        if hasattr(e, 'status_code'):
            raise HTTPException(status_code=e.status_code, detail=e.detail)
        else:
            raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/knowledge/{knowledge_name}/logs", status_code=200)
def get_knowledge_logs(knowledge_name: str, organisation=Depends(get_user_organisation)):
    try:
        return KnowledgeHandler(session=db.session, organisation_id=organisation.id).get_knowledge_usage_by_name(knowledge_name)
    except Exception as e:
        logging.error(f"Error while getting knowledge event details: {str(e)}")
        if hasattr(e, 'status_code'):
            raise HTTPException(status_code=e.status_code, detail=e.detail)
        else:
            raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/soundcloud/health", status_code=200)
def get_soundcloud_monitor_health(organisation=Depends(get_user_organisation)):
    """Truthful monitor state: CONNECTED, DEGRADED, ERROR, STOPPED, or UNCONFIGURED."""
    return _soundcloud_runtime.health()


@router.get("/soundcloud/latest", status_code=200)
def get_soundcloud_latest(organisation=Depends(get_user_organisation)):
    if _soundcloud_runtime.store is None:
        return {
            "status": "UNCONFIGURED",
            "snapshot": None,
            "message": "Set SOUNDCLOUD_ACCESS_TOKEN to enable official API monitoring.",
        }
    return {
        "status": _soundcloud_runtime.health()["status"],
        "snapshot": _soundcloud_runtime.store.latest(),
    }


@router.get("/soundcloud/delta", status_code=200)
def get_soundcloud_delta(organisation=Depends(get_user_organisation)):
    if _soundcloud_runtime.store is None:
        return {"status": "UNCONFIGURED", "delta": None}
    return {
        "status": _soundcloud_runtime.health()["status"],
        "delta": _soundcloud_runtime.store.delta(),
    }


@router.get("/soundcloud/history", status_code=200)
def get_soundcloud_history(
    limit: int = Query(default=60, ge=1, le=1440),
    organisation=Depends(get_user_organisation),
):
    if _soundcloud_runtime.store is None:
        return {"status": "UNCONFIGURED", "snapshots": []}
    return {
        "status": _soundcloud_runtime.health()["status"],
        "snapshots": _soundcloud_runtime.store.history(limit=limit),
    }


@router.post("/soundcloud/poll-now", status_code=200)
def poll_soundcloud_now(organisation=Depends(get_user_organisation)):
    if _soundcloud_runtime.monitor is None:
        raise HTTPException(
            status_code=503,
            detail="SoundCloud monitor is unconfigured; set SOUNDCLOUD_ACCESS_TOKEN.",
        )
    try:
        snapshot = _soundcloud_runtime.poll_once()
    except Exception as e:
        logging.error(f"SoundCloud monitoring poll failed: {str(e)}")
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "status": _soundcloud_runtime.health()["status"],
        "captured_at": snapshot.captured_at if snapshot else None,
        "delta": _soundcloud_runtime.store.delta() if _soundcloud_runtime.store else None,
    }
