// What the signed-in person may see. The API decides this from the role (api/app/permissions.py) and sends the
// list as `permissions` on /auth/me; nothing here knows about roles, so a new role needs no change in the web app.
// The API enforces the same permissions on every endpoint; hiding things here is only so people aren't shown what they can't use.

export type Permission = string;

export const can = (permissions: readonly Permission[] | undefined, p: Permission) => !!permissions?.includes(p);

export type ExamTab = "overview" | "papers" | "rubric" | "import" | "advanced";

/** Tabs of an exam page, in display order, each with the permission that shows it. */
export const EXAM_TABS: { key: ExamTab; label: string; permission: Permission }[] = [
  { key: "overview", label: "Overview", permission: "tab.overview" },
  { key: "papers", label: "Papers", permission: "tab.papers" },
  { key: "rubric", label: "Rubric", permission: "tab.rubric" },
  { key: "import", label: "Upload", permission: "tab.upload" },
  { key: "advanced", label: "Advanced", permission: "tab.advanced" },
];

export const visibleTabs = (permissions: readonly Permission[] | undefined) =>
  EXAM_TABS.filter((t) => can(permissions, t.permission));

/** The tab to open: the one named in the URL hash if the person may see it, otherwise the first they may. */
export function pickTab(hash: string, permissions: readonly Permission[] | undefined): ExamTab | null {
  const tabs = visibleTabs(permissions);
  return (tabs.find((t) => t.key === hash.replace(/^#/, "")) ?? tabs[0])?.key ?? null;
}

/** Page access, checked by the middleware. /admin/exams is the Exams section; the rest of /admin (people) is admins only. */
export function pageAllowed(path: string, user: { role: string; permissions?: readonly Permission[] }): boolean {
  if (path === "/admin/exams" || path.startsWith("/admin/exams/")) return can(user.permissions, "exams.view");
  if (path === "/admin" || path.startsWith("/admin/")) return user.role === "admin";
  return true;
}
