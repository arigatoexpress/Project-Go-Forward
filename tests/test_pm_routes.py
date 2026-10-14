"""Tests for the AI PM routes (pm_routes.py) — projects / tasks / cycles /
activity feed / views / dashboard / GitHub issue import.

load_app() deliberately stubs pm_routes with an empty router (main.py import
cost), so these tests call the async route functions directly — the pattern
used by tests/test_mira_routes.py for the same reason.

The admin gate itself (Depends(require_admin) at the main.py mount) is
main.py wiring, covered by the critical-routes tests.

Run: python -m pytest tests/test_pm_routes.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pm_routes  # noqa: E402
from database.models import (  # noqa: E402
    Cycle,
    Project,
    ProjectType,
    Task,
    TaskPriority,
    View,
    WorkflowState,
)

STORES = (
    pm_routes.projects_db,
    pm_routes.tasks_db,
    pm_routes.cycles_db,
    pm_routes.activities_db,
    pm_routes.views_db,
)


@pytest.fixture(autouse=True)
def clean_stores():
    for store in STORES:
        store.clear()
    yield
    for store in STORES:
        store.clear()


def _project(name="Launch Hardening", **overrides) -> Project:
    fields = {"name": name, "project_type": "business", "status": "active"}
    fields.update(overrides)
    return Project(**fields)


def _task(project_id, title="Fix the thing", **overrides) -> Task:
    fields = {"title": title, "project_id": project_id}
    fields.update(overrides)
    return Task(**fields)


class TestProjects:
    @pytest.mark.asyncio
    async def test_create_and_get_project(self):
        created = await pm_routes.create_project(_project(description="THO go-live"))
        assert created.name == "Launch Hardening"
        assert created.id in pm_routes.projects_db

        fetched = await pm_routes.get_project(created.id)
        assert fetched.name == "Launch Hardening"

    @pytest.mark.asyncio
    async def test_create_logs_activity(self):
        created = await pm_routes.create_project(_project())
        events = [
            a for a in pm_routes.activities_db.values() if a.project_id == created.id
        ]
        assert any(a.activity_type == "project_created" for a in events)

    @pytest.mark.asyncio
    async def test_list_projects_with_filters(self):
        await pm_routes.create_project(_project(name="Repo A", project_type=ProjectType.SOFTWARE))
        await pm_routes.create_project(_project(name="Biz B", project_type=ProjectType.BUSINESS))
        await pm_routes.create_project(_project(name="Paused C", status="paused"))

        assert (await pm_routes.list_projects())["total"] == 3

        software = await pm_routes.list_projects(project_type=ProjectType.SOFTWARE)
        assert software["total"] == 1
        assert software["projects"][0].name == "Repo A"

        paused = await pm_routes.list_projects(status="paused")
        assert paused["total"] == 1
        assert paused["projects"][0].name == "Paused C"

    @pytest.mark.asyncio
    async def test_get_missing_project_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await pm_routes.get_project("nope")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_project(self):
        created = await pm_routes.create_project(_project())
        before = created.updated_at

        updated = await pm_routes.update_project(
            created.id, {"status": "archived", "bogus_field": "ignored"}
        )
        assert updated.status == "archived"
        assert not hasattr(updated, "bogus_field")
        assert updated.updated_at >= before

    @pytest.mark.asyncio
    async def test_update_missing_project_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await pm_routes.update_project("nope", {"status": "x"})
        assert exc_info.value.status_code == 404


class TestTasks:
    @pytest.mark.asyncio
    async def test_create_task_updates_project_stats(self):
        project = await pm_routes.create_project(_project())
        task = await pm_routes.create_task(
            _task(project.id, priority=3, labels=["bug"])
        )

        assert task.project_id == project.id
        assert pm_routes.projects_db[project.id].total_tasks == 1
        assert any(
            a.activity_type == "task_created" and a.task_id == task.id
            for a in pm_routes.activities_db.values()
        )

    @pytest.mark.asyncio
    async def test_create_task_without_project_keeps_stats(self):
        task = await pm_routes.create_task(_task("ghost-project"))
        assert task.id in pm_routes.tasks_db

    @pytest.mark.asyncio
    async def test_list_tasks_filters_and_sort(self):
        project_a = (await pm_routes.create_project(_project(name="A"))).id
        project_b = (await pm_routes.create_project(_project(name="B"))).id
        low = await pm_routes.create_task(_task(project_a, title="Low", priority=1))
        urgent = await pm_routes.create_task(
            _task(project_a, title="Urgent", priority=4, labels=["bug"])
        )
        await pm_routes.create_task(_task(project_b, title="Other project"))

        assert (await pm_routes.list_tasks(limit=50))["total"] == 3

        by_project = await pm_routes.list_tasks(project_id=project_a, limit=50)
        assert by_project["total"] == 2
        # Priority desc ordering: urgent first.
        assert by_project["tasks"][0].id == urgent.id
        assert by_project["tasks"][-1].id == low.id

        by_state = await pm_routes.list_tasks(state=WorkflowState.BACKLOG, limit=50)
        assert by_state["total"] == 3

        by_label = await pm_routes.list_tasks(label="bug", limit=50)
        assert by_label["total"] == 1
        assert by_label["tasks"][0].id == urgent.id

        by_priority = await pm_routes.list_tasks(priority=TaskPriority.LOW, limit=50)
        assert by_priority["total"] == 1

        limited = await pm_routes.list_tasks(limit=2)
        assert limited["count"] == 2

    @pytest.mark.asyncio
    async def test_get_missing_task_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await pm_routes.get_task("nope")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_task_state_records_audit_trail(self):
        project = await pm_routes.create_project(_project())
        task = await pm_routes.create_task(_task(project.id))

        updated = await pm_routes.update_task(
            task.id, {"state": "in_progress"}, actor="ari"
        )
        assert updated.state == WorkflowState.IN_PROGRESS
        assert updated.started_at is not None
        assert updated.state_history[-1]["from"] == "backlog"
        assert updated.state_history[-1]["to"] == "in_progress"
        assert updated.state_history[-1]["by"] == "ari"

        assert any(
            a.activity_type == "state_changed"
            for a in pm_routes.activities_db.values()
        )

    @pytest.mark.asyncio
    async def test_update_task_other_fields_no_state_event(self):
        project = await pm_routes.create_project(_project())
        task = await pm_routes.create_task(_task(project.id))
        updated = await pm_routes.update_task(task.id, {"title": "Renamed"})
        assert updated.title == "Renamed"
        assert updated.state == WorkflowState.BACKLOG  # unchanged
        assert not any(
            a.activity_type == "state_changed" for a in pm_routes.activities_db.values()
        )

    @pytest.mark.asyncio
    async def test_transition_task_done_increments_completed(self):
        project = await pm_routes.create_project(_project())
        task = await pm_routes.create_task(_task(project.id))

        result = await pm_routes.transition_task(
            task.id, WorkflowState.DONE, actor="ari"
        )
        assert result["success"] is True
        done_task = result["task"]
        assert done_task.state == WorkflowState.DONE
        assert done_task.completed_at is not None
        assert pm_routes.projects_db[project.id].completed_tasks == 1

    @pytest.mark.asyncio
    async def test_transition_missing_task_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await pm_routes.transition_task("nope", WorkflowState.DONE)
        assert exc_info.value.status_code == 404


class TestCycles:
    def _cycle(self, name="Week 1", **overrides) -> Cycle:
        fields = {
            "name": name,
            "start_date": datetime.utcnow() - timedelta(days=1),
            "end_date": datetime.utcnow() + timedelta(days=6),
            "status": "active",
        }
        fields.update(overrides)
        return Cycle(**fields)

    @pytest.mark.asyncio
    async def test_create_and_list_cycles(self):
        await pm_routes.create_cycle(self._cycle())
        await pm_routes.create_cycle(self._cycle(name="Week 2", status="planning"))

        assert (await pm_routes.list_cycles())["total"] == 2

        planning = await pm_routes.list_cycles(status="planning")
        assert planning["total"] == 1
        assert planning["cycles"][0].name == "Week 2"

    @pytest.mark.asyncio
    async def test_current_cycle(self):
        await pm_routes.create_cycle(self._cycle(name="Active"))
        await pm_routes.create_cycle(
            self._cycle(name="Future", start_date=datetime(2999, 1, 1))
        )

        current = await pm_routes.get_current_cycle()
        assert current is not None
        assert current.name == "Active"

    @pytest.mark.asyncio
    async def test_current_cycle_none(self):
        assert await pm_routes.get_current_cycle() is None


class TestActivityFeed:
    @pytest.mark.asyncio
    async def test_feed_filters_and_limit(self):
        project_a = (await pm_routes.create_project(_project(name="A"))).id
        project_b = (await pm_routes.create_project(_project(name="B"))).id
        task_a = await pm_routes.create_task(_task(project_a, title="Task A"))
        await pm_routes.create_task(_task(project_b, title="Task B"))

        feed = await pm_routes.get_activity_feed(project_id=project_a, limit=20)
        assert feed["count"] >= 2  # project_created + task_created
        assert all(a.project_id == project_a for a in feed["activities"])

        by_task = await pm_routes.get_activity_feed(task_id=task_a.id, limit=20)
        assert by_task["count"] == 1
        assert by_task["activities"][0].activity_type == "task_created"

        limited = await pm_routes.get_activity_feed(limit=1)
        assert limited["count"] == 1


class TestViews:
    @pytest.mark.asyncio
    async def test_create_and_list_views(self):
        await pm_routes.create_view(
            View(name="My bugs", owner_id="ari", filter_labels=["bug"])
        )
        await pm_routes.create_view(
            View(name="Shared board", owner_id="sam", is_shared=True)
        )

        assert (await pm_routes.list_views())["total"] == 2

        ari_views = await pm_routes.list_views(owner_id="ari")
        names = {v.name for v in ari_views["views"]}
        # Own view plus shared views from others.
        assert names == {"My bugs", "Shared board"}


class TestDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_aggregates(self):
        project = await pm_routes.create_project(_project())
        task = await pm_routes.create_task(_task(project.id))
        await pm_routes.transition_task(task.id, WorkflowState.DONE)

        body = await pm_routes.get_dashboard()
        assert body["stats"]["total_projects"] == 1
        assert body["stats"]["total_tasks"] == 1
        assert body["stats"]["tasks_by_state"] == {"done": 1}
        assert body["stats"]["completed_this_week"] == 1
        assert body["active_cycle"] is None
        assert len(body["recent_activity"]) > 0


class TestGithubImport:
    @pytest.mark.asyncio
    async def test_import_maps_states_and_assignees(self):
        project = await pm_routes.create_project(_project())

        result = await pm_routes.import_github_issues(
            project.id,
            "owner/repo",
            [
                {
                    "title": "Open bug",
                    "body": "details",
                    "state": "open",
                    "number": 42,
                    "labels": ["bug"],
                    "assignee": {"login": "ari"},
                },
                {"title": "Closed feature", "state": "closed", "number": 43},
                {"title": "Weird state", "state": "unknown", "number": 44},
            ],
        )
        assert result["imported"] == 3

        by_title = {t.title: t for t in result["tasks"]}
        assert by_title["Open bug"].state == WorkflowState.TODO
        assert by_title["Open bug"].assignee == "ari"
        assert by_title["Open bug"].related_github_issue == 42
        assert by_title["Closed feature"].state == WorkflowState.DONE
        assert by_title["Weird state"].state == WorkflowState.BACKLOG
        assert all(t.project_id == project.id for t in result["tasks"])

    @pytest.mark.asyncio
    async def test_import_unknown_project_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await pm_routes.import_github_issues(
                "nope", "owner/repo", [{"title": "x", "state": "open"}]
            )
        assert exc_info.value.status_code == 404


class TestGithubStateMapping:
    def test_maps_known_and_unknown_states(self):
        assert pm_routes._map_github_state("open") == WorkflowState.TODO
        assert pm_routes._map_github_state("closed") == WorkflowState.DONE
        assert pm_routes._map_github_state("something_else") == WorkflowState.BACKLOG
