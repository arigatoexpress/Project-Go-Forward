"""
Trading Project API Routes for PM Hub
Extends PM system with trading-specific functionality
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime
import uuid

from trading_project_models import (
    TradingProject,
    CreateTradingProjectRequest,
    UpdateTradingProjectRequest,
    TradingProjectSummary,
    StrategyMilestone,
    BacktestResult,
    TradeTask,
    TradingPerformance
)

router = APIRouter(prefix="/api/trading-projects", tags=["trading-projects"])

# In-memory store (replace with Firestore in production)
trading_projects_db = {}


@router.post("", response_model=TradingProject)
async def create_trading_project(request: CreateTradingProjectRequest):
    """Create a new trading project"""
    project_id = f"trading-{uuid.uuid4().hex[:8]}"
    
    project = TradingProject(
        project_id=project_id,
        name=request.name,
        description=request.description,
        strategy_name=request.strategy_name,
        symbols=request.symbols,
        symbol_pairs=request.symbol_pairs,
        strategy_type=request.strategy_type,
        risk_parameters=request.risk_parameters or RiskParameters(),
        priority=request.priority,
        tags=request.tags,
        status="research",
        created_at=datetime.utcnow().isoformat()
    )
    
    trading_projects_db[project_id] = project
    return project


@router.get("", response_model=dict)
async def list_trading_projects(
    status: Optional[str] = None,
    strategy_type: Optional[str] = None,
    limit: int = Query(50, le=100)
):
    """List all trading projects"""
    projects = list(trading_projects_db.values())
    
    if status:
        projects = [p for p in projects if p.status == status]
    if strategy_type:
        projects = [p for p in projects if p.strategy_type == strategy_type]
    
    return {
        "projects": projects[:limit],
        "total": len(projects),
        "count": len(projects[:limit])
    }


@router.get("/{project_id}", response_model=TradingProject)
async def get_trading_project(project_id: str):
    """Get a trading project by ID"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    return trading_projects_db[project_id]


@router.patch("/{project_id}", response_model=TradingProject)
async def update_trading_project(project_id: str, request: UpdateTradingProjectRequest):
    """Update a trading project"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = trading_projects_db[project_id]
    
    # Update fields
    if request.name:
        project.name = request.name
    if request.description:
        project.description = request.description
    if request.status:
        project.status = request.status
    if request.risk_parameters:
        project.risk_parameters = request.risk_parameters
    if request.priority:
        project.priority = request.priority
    if request.tags:
        project.tags = request.tags
    
    project.updated_at = datetime.utcnow().isoformat()
    trading_projects_db[project_id] = project
    
    return project


@router.post("/{project_id}/milestones")
async def add_milestone(project_id: str, milestone: StrategyMilestone):
    """Add a milestone to a trading project"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = trading_projects_db[project_id]
    project.milestones.append(milestone)
    trading_projects_db[project_id] = project
    
    return {"success": True, "milestone_id": milestone.milestone_id}


@router.patch("/{project_id}/milestones/{milestone_id}")
async def update_milestone(project_id: str, milestone_id: str, status: str, results: Optional[dict] = None):
    """Update milestone status"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = trading_projects_db[project_id]
    
    for milestone in project.milestones:
        if milestone.milestone_id == milestone_id:
            milestone.status = status
            if results:
                milestone.results = BacktestResult(**results)
            if status == "completed":
                milestone.completed_date = datetime.utcnow().isoformat()
            break
    
    trading_projects_db[project_id] = project
    return {"success": True}


@router.post("/{project_id}/backtests")
async def add_backtest(project_id: str, backtest: BacktestResult):
    """Add a backtest result to a project"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = trading_projects_db[project_id]
    project.backtest_results.append(backtest)
    trading_projects_db[project_id] = project
    
    return {"success": True, "backtest_id": f"bt-{len(project.backtest_results)}"}


@router.post("/{project_id}/performance")
async def update_performance(project_id: str, performance: dict):
    """Update live trading performance"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = trading_projects_db[project_id]
    
    # Update performance fields
    for key, value in performance.items():
        if hasattr(project.live_performance, key):
            setattr(project.live_performance, key, value)
    
    project.updated_at = datetime.utcnow().isoformat()
    trading_projects_db[project_id] = project
    
    return {"success": True}


@router.post("/{project_id}/trades")
async def create_trade_task(project_id: str, trade_task: TradeTask):
    """Create a trade task for a project"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # In a real implementation, this would create a proper task in the PM system
    # For now, we just acknowledge receipt
    return {
        "success": True,
        "task_id": trade_task.task_id,
        "message": "Trade task created"
    }


@router.get("/{project_id}/roi")
async def get_project_roi(project_id: str):
    """Calculate ROI for a trading project"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = trading_projects_db[project_id]
    perf = project.live_performance
    
    return {
        "project_id": project_id,
        "strategy_name": project.strategy_name,
        "total_pnl_usd": perf.total_pnl_usd,
        "win_rate": perf.win_rate,
        "total_trades": perf.total_trades,
        "sharpe_ratio": perf.sharpe_ratio,
        "status": project.status,
        "days_active": 0  # Calculate from deployed_at
    }


@router.get("/{project_id}/summary", response_model=TradingProjectSummary)
async def get_project_summary(project_id: str):
    """Get summary of a trading project"""
    if project_id not in trading_projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = trading_projects_db[project_id]
    perf = project.live_performance
    
    return TradingProjectSummary(
        project_id=project_id,
        name=project.name,
        status=project.status,
        strategy_type=project.strategy_type,
        symbols=project.symbols,
        total_pnl=perf.total_pnl_usd,
        win_rate=perf.win_rate,
        total_trades=perf.total_trades,
        last_updated=project.updated_at
    )
