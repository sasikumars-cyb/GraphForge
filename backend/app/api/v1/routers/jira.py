"""Jira search — the structured entry point NewWorkflowPage's Jira picker
uses to let a user browse/select a real issue, replacing the old
"paste a key and hope extract_issue_key finds it" flow (see
app.tools.implementations.jira_tool.extract_issue_key, still used as the
Planning Agent's own enrichment trigger once an issue key/URL is actually
in the objective text — this endpoint is what puts it there deliberately).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.v1.dependencies import get_current_user
from app.models.user import User
from app.tools import get_tool_registry
from app.tools.implementations.jira_tool import JiraTool

router = APIRouter(prefix="/jira", tags=["jira"])


class JiraIssueResult(BaseModel):
    key: str
    summary: str
    status: str
    issue_type: str
    url: str


@router.get("/search", response_model=list[JiraIssueResult])
async def search_jira_issues(
    q: str = Query(..., min_length=2, max_length=200),
    _: User = Depends(get_current_user),
) -> list[JiraIssueResult]:
    """Search Jira issues by free text. Returns [] (not an error) when
    Jira isn't configured — see JiraTool.search_issues's docstring for why
    an empty picker result is the right degrade here, not a 400/503."""
    tool = get_tool_registry().get_tool("jira")
    if not isinstance(tool, JiraTool):
        return []
    results = await tool.search_issues(q)
    return [JiraIssueResult(**r) for r in results]
