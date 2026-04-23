"""
AI PM Manager Routes - Linear-Inspired Project Management API
Manages tasks, projects, and cycles across all repos
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime, timedelta
import uuid

from database.models import (
    Project, Task, Cycle, Activity, View,
    WorkflowState, TaskPriority, ProjectType
)

router = APIRouter(prefix="/api/pm", tags=["pm"])

# In-memory stores (replace with Firestore)
projects_db = {}
tasks_db = {}
cycles_db = {}
activities_db = {}
views_db = {}


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/projects", response_model=Project)
async def create_project(project: Project):
    """Create a new project (software repo, business initiative, etc.)"""
    project.id = str(uuid.uuid4())
    project.created_at = datetime.utcnow()
    projects_db[project.id] = project
    
    # Log activity
    _log_activity(
        activity_type="project_created",
        description=f"Created project: {project.name}",
        project_id=project.id
    )
    
    return project


@router.get("/projects", response_model=dict)
async def list_projects(
    project_type: Optional[ProjectType] = None,
    status: Optional[str] = None
):
    """List all projects, optionally filtered"""
    projects = list(projects_db.values())
    
    if project_type:
        projects = [p for p in projects if p.project_type == project_type]
    if status:
        projects = [p for p in projects if p.status == status]
    
    return {
        "projects": projects,
        "total": len(projects)
    }


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(project_id: str):
    """Get project details"""
    if project_id not in projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    return projects_db[project_id]


@router.patch("/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, updates: dict):
    """Update project fields"""
    if project_id not in projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = projects_db[project_id]
    for key, value in updates.items():
        if hasattr(project, key):
            setattr(project, key, value)
    
    project.updated_at = datetime.utcnow()
    projects_db[project_id] = project
    return project


# ═══════════════════════════════════════════════════════════════════════════════
# TASKS (Like Linear Issues)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/tasks", response_model=Task)
async def create_task(task: Task):
    """Create a new task"""
    task.id = str(uuid.uuid4())
    task.created_at = datetime.utcnow()
    tasks_db[task.id] = task
    
    # Update project stats
    if task.project_id in projects_db:
        project = projects_db[task.project_id]
        project.total_tasks += 1
        project.updated_at = datetime.utcnow()
    
    # Log activity
    _log_activity(
        activity_type="task_created",
        description=f"Created task: {task.title}",
        project_id=task.project_id,
        task_id=task.id
    )
    
    return task


@router.get("/tasks", response_model=dict)
async def list_tasks(
    project_id: Optional[str] = None,
    state: Optional[WorkflowState] = None,
    assignee: Optional[str] = None,
    priority: Optional[TaskPriority] = None,
    label: Optional[str] = None,
    limit: int = Query(50, le=100)
):
    """List tasks with filters"""
    tasks = list(tasks_db.values())
    
    if project_id:
        tasks = [t for t in tasks if t.project_id == project_id]
    if state:
        tasks = [t for t in tasks if t.state == state]
    if assignee:
        tasks = [t for t in tasks if t.assignee == assignee]
    if priority:
        tasks = [t for t in tasks if t.priority == priority]
    if label:
        tasks = [t for t in tasks if label in t.labels]
    
    # Sort by priority desc, then created_at desc
    tasks.sort(key=lambda t: (t.priority.value, t.created_at), reverse=True)
    
    return {
        "tasks": tasks[:limit],
        "total": len(tasks),
        "count": len(tasks[:limit])
    }


@router.get("/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str):
    """Get task details"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks_db[task_id]


@router.patch("/tasks/{task_id}", response_model=Task)
async def update_task(task_id: str, updates: dict, actor: Optional[str] = None):
    """Update task fields"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks_db[task_id]
    old_state = task.state
    
    for key, value in updates.items():
        if key == "state" and value != old_state:
            # Handle state transition with audit
            task.transition_to(WorkflowState(value), changed_by=actor)
        elif hasattr(task, key):
            setattr(task, key, value)
    
    task.updated_at = datetime.utcnow()
    tasks_db[task_id] = task
    
    # Log state change if applicable
    if "state" in updates and updates["state"] != old_state:
        _log_activity(
            activity_type="state_changed",
            description=f"Moved to {updates['state']}",
            project_id=task.project_id,
            task_id=task.id,
            metadata={"from": old_state, "to": updates["state"]}
        )
    
    return task


@router.post("/tasks/{task_id}/transition")
async def transition_task(
    task_id: str,
    new_state: WorkflowState,
    actor: Optional[str] = None
):
    """Transition task to new state"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks_db[task_id]
    old_state = task.state
    task.transition_to(new_state, changed_by=actor)
    tasks_db[task_id] = task
    
    # Update project stats if done
    if new_state == WorkflowState.DONE and old_state != WorkflowState.DONE:
        if task.project_id in projects_db:
            project = projects_db[task.project_id]
            project.completed_tasks += 1
    
    _log_activity(
        activity_type="state_changed",
        description=f"Moved from {old_state} to {new_state}",
        project_id=task.project_id,
        task_id=task.id,
        actor=actor,
        metadata={"from": old_state, "to": new_state}
    )
    
    return {"success": True, "task": task}


# ═══════════════════════════════════════════════════════════════════════════════
# CYCLES (Like Linear Sprints)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/cycles", response_model=Cycle)
async def create_cycle(cycle: Cycle):
    """Create a new cycle"""
    cycle.id = str(uuid.uuid4())
    cycles_db[cycle.id] = cycle
    
    _log_activity(
        activity_type="cycle_created",
        description=f"Created cycle: {cycle.name}",
        project_id=cycle.project_ids[0] if cycle.project_ids else None
    )
    
    return cycle


@router.get("/cycles", response_model=dict)
async def list_cycles(status: Optional[str] = None):
    """List cycles"""
    cycles = list(cycles_db.values())
    
    if status:
        cycles = [c for c in cycles if c.status == status]
    
    return {"cycles": cycles, "total": len(cycles)}


@router.get("/cycles/current")
async def get_current_cycle():
    """Get currently active cycle"""
    for cycle in cycles_db.values():
        if cycle.is_active():
            return cycle
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITY FEED
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/activity", response_model=dict)
async def get_activity_feed(
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    limit: int = Query(20, le=50)
):
    """Get recent activity across all projects"""
    activities = sorted(
        activities_db.values(),
        key=lambda a: a.created_at,
        reverse=True
    )
    
    if project_id:
        activities = [a for a in activities if a.project_id == project_id]
    if task_id:
        activities = [a for a in activities if a.task_id == task_id]
    
    return {
        "activities": activities[:limit],
        "count": len(activities[:limit])
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VIEWS (Like Linear Saved Views)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/views", response_model=View)
async def create_view(view: View):
    """Create a saved view"""
    view.id = str(uuid.uuid4())
    views_db[view.id] = view
    return view


@router.get("/views", response_model=dict)
async def list_views(owner_id: Optional[str] = None):
    """List saved views"""
    views = list(views_db.values())
    
    if owner_id:
        views = [v for v in views if v.owner_id == owner_id or v.is_shared]
    
    return {"views": views, "total": len(views)}


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD / STATS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def get_dashboard():
    """Get PM dashboard overview"""
    total_projects = len(projects_db)
    total_tasks = len(tasks_db)
    
    # Task counts by state
    tasks_by_state = {}
    for state in WorkflowState:
        count = len([t for t in tasks_db.values() if t.state == state])
        if count > 0:
            tasks_by_state[state.value] = count
    
    # Active cycle
    active_cycle = None
    for cycle in cycles_db.values():
        if cycle.is_active():
            active_cycle = cycle
            break
    
    # Recent activity
    recent_activity = sorted(
        activities_db.values(),
        key=lambda a: a.created_at,
        reverse=True
    )[:10]
    
    return {
        "stats": {
            "total_projects": total_projects,
            "total_tasks": total_tasks,
            "tasks_by_state": tasks_by_state,
            "completed_this_week": len([
                t for t in tasks_db.values()
                if t.completed_at and t.completed_at > datetime.utcnow() - timedelta(days=7)
            ])
        },
        "active_cycle": active_cycle,
        "recent_activity": recent_activity
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AI PM INTEGRATION - Import tasks from external systems
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/import/github-issues")
async def import_github_issues(
    project_id: str,
    repo: str,
    issues: List[dict]
):
    """Import GitHub issues as tasks"""
    if project_id not in projects_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    imported = []
    for issue in issues:
        task = Task(
            title=issue.get("title"),
            description=issue.get("body"),
            project_id=project_id,
            state=_map_github_state(issue.get("state")),
            related_github_issue=issue.get("number"),
            labels=issue.get("labels", []),
            assignee=issue.get("assignee", {}).get("login") if issue.get("assignee") else None
        )
        tasks_db[task.id] = task
        imported.append(task)
    
    return {
        "imported": len(imported),
        "tasks": imported
    }


def _map_github_state(github_state: str) -> WorkflowState:
    """Map GitHub issue state to workflow state"""
    mapping = {
        "open": WorkflowState.TODO,
        "closed": WorkflowState.DONE
    }
    return mapping.get(github_state, WorkflowState.BACKLOG)


def _log_activity(
    activity_type: str,
    description: str,
    project_id: str,
    task_id: Optional[str] = None,
    actor: Optional[str] = None,
    metadata: Optional[dict] = None
):
    """Helper to log activity"""
    activity = Activity(
        id=str(uuid.uuid4()),
        activity_type=activity_type,
        description=description,
        project_id=project_id,
        task_id=task_id,
        actor=actor or "AI PM",
        metadata=metadata or {}
    )
    activities_db[activity.id] = activity
