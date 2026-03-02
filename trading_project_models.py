"""
Trading Project Models for PM Integration
Extends PM system with trading-specific project types
"""

from datetime import datetime
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class TradingPerformance(BaseModel):
    """Trading performance metrics"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_usd: float = 0.0
    avg_pnl_per_trade: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    
    # Time-based metrics
    daily_pnl: List[Dict[str, Any]] = []
    weekly_pnl: List[Dict[str, Any]] = []
    monthly_pnl: List[Dict[str, Any]] = []


class BacktestResult(BaseModel):
    """Backtest results for a strategy"""
    start_date: str
    end_date: str
    symbol: str
    strategy_version: str
    
    # Performance
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    
    # Trade stats
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_trade_duration: str
    
    # Data
    equity_curve: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    
    # Status
    status: Literal["passed", "failed", "warning"]
    notes: str = ""


class RiskParameters(BaseModel):
    """Risk management parameters"""
    max_position_size_usd: float = 100.0
    max_daily_loss_usd: float = 50.0
    max_trades_per_day: int = 10
    stop_loss_percent: float = 2.0
    take_profit_percent: float = 4.0
    
    # Pair trading specific
    z_score_entry: float = -2.0
    z_score_exit: float = 0.0
    

class StrategyMilestone(BaseModel):
    """Milestone in a trading project"""
    milestone_id: str
    name: str
    description: str
    status: Literal["pending", "in_progress", "completed", "failed"]
    due_date: Optional[str] = None
    completed_date: Optional[str] = None
    
    # Milestone types
    type: Literal[
        "backtest",
        "paper_trading",
        "live_trading",
        "optimization",
        "review"
    ]
    
    # Results
    results: Optional[BacktestResult] = None
    notes: str = ""


class TradingProject(BaseModel):
    """
    A trading strategy as a PM project
    
    This model bridges the trading system and PM system,
    treating strategies as projects with milestones,
    tasks, and ROI tracking.
    """
    
    # PM System fields
    project_id: str
    name: str
    description: str
    
    # Project type
    project_type: Literal["trading_strategy"] = "trading_strategy"
    
    # Trading-specific fields
    strategy_name: str
    strategy_version: str = "1.0"
    strategy_logic: str = ""  # Pine Script or Python code
    
    # Symbols
    symbols: List[str] = []
    symbol_pairs: List[str] = []  # For pair trading
    
    # Strategy type
    strategy_type: Literal[
        "pair_trading",
        "momentum",
        "mean_reversion",
        "breakout",
        "custom"
    ] = "custom"
    
    # Risk parameters
    risk_parameters: RiskParameters = Field(default_factory=RiskParameters)
    
    # Status
    status: Literal[
        "research",
        "backtesting",
        "paper_trading",
        "live",
        "paused",
        "archived"
    ] = "research"
    
    # Milestones
    milestones: List[StrategyMilestone] = []
    
    # Performance
    backtest_results: List[BacktestResult] = []
    live_performance: TradingPerformance = Field(default_factory=TradingPerformance)
    
    # Development tracking
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    deployed_at: Optional[str] = None
    
    # Team
    created_by: str = "system"
    assigned_to: List[str] = []
    
    # Links
    linked_repo: Optional[str] = None  # GitHub repo
    linked_docs: Optional[str] = None  # Documentation
    
    # Metadata
    tags: List[str] = []
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    
    class Config:
        json_schema_extra = {
            "example": {
                "project_id": "proj-ethbtc-v3",
                "name": "ETHBTC Pair Trading Strategy v3",
                "description": "Mean reversion strategy for ETHBTC pair",
                "strategy_name": "PairTrading_ETHBTC",
                "symbols": ["ETH", "BTC"],
                "symbol_pairs": ["ETHBTC"],
                "strategy_type": "pair_trading",
                "status": "live"
            }
        }


class TradeTask(BaseModel):
    """A trade execution as a PM task"""
    task_id: str
    project_id: str  # Links to TradingProject
    
    # Trade details
    trade_id: str
    proposal_id: str
    symbol: str
    side: str
    amount_usd: float
    
    # Status
    status: Literal["pending", "executed", "failed", "cancelled"]
    
    # Execution
    execution_price: Optional[float] = None
    execution_time: Optional[str] = None
    pnl_usd: Optional[float] = None
    
    # Error tracking
    error_message: Optional[str] = None
    retry_count: int = 0
    
    # Timestamps
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    executed_at: Optional[str] = None


# API Request/Response models
class CreateTradingProjectRequest(BaseModel):
    """Request to create a new trading project"""
    name: str
    description: str
    strategy_name: str
    symbols: List[str]
    symbol_pairs: List[str] = []
    strategy_type: str = "custom"
    risk_parameters: Optional[RiskParameters] = None
    priority: str = "medium"
    tags: List[str] = []


class UpdateTradingProjectRequest(BaseModel):
    """Request to update a trading project"""
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    risk_parameters: Optional[RiskParameters] = None
    priority: Optional[str] = None
    tags: Optional[List[str]] = None


class TradingProjectSummary(BaseModel):
    """Summary view of trading project"""
    project_id: str
    name: str
    status: str
    strategy_type: str
    symbols: List[str]
    total_pnl: float
    win_rate: float
    total_trades: int
    last_updated: str
