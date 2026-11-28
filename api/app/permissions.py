"""What each role may do. The one place to change when a role gains or loses access.

A permission is a plain string the web app also sees (`permissions` on `GET /auth/me`), so the API and the UI
are controlled from this table. Per-exam tabs are `tab.*` permissions: a role that has `tab.x` sees that tab,
and the API endpoints that exist for that tab require the same permission.

To add a role: add it to `models.Role`, then give it an entry in `ROLE_PERMISSIONS`. A role with no entry has
no permissions. To open a tab to a role, add its `tab.*` permission to that role's set.
"""

from enum import Enum

from app.models import Role


class Permission(str, Enum):
    # Exams page
    exams_view = "exams.view"  # see the Exams page; the list is always scoped to the exams the person is assigned to
    exams_create = "exams.create"
    exams_delete = "exams.delete"
    # Tabs inside an exam: visible to the role, and required by the endpoints behind them
    tab_papers = "tab.papers"  # see the papers list: marks, status, assigned TAs, and the people list that names them
    tab_rubric = "tab.rubric"  # read the rubric
    tab_upload = "tab.upload"  # import papers, auto-split
    tab_overview = "tab.overview"  # statistics and plots
    tab_advanced = "tab.advanced"  # rename, grading model
    # Actions that are not a tab
    overview_export = "overview.export"  # download the overview statistics
    papers_manage = "papers.manage"  # grade, regrade, assign TAs, delete papers, edit students, export CSV/ZIP of papers
    rubric_edit = "rubric.edit"  # write rubric versions, print the rubric PDF


ALL = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.admin: ALL,
    Role.ta: frozenset(  # read-only: the exam's Papers, Rubric and Overview tabs
        {
            Permission.exams_view,
            Permission.tab_papers,
            Permission.tab_rubric,
            Permission.tab_overview,
            Permission.overview_export,
        }
    ),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())
