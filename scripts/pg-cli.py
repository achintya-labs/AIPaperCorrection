#!/usr/bin/env python3
"""Local-only CLI for the Paper Grader API (default: http://127.0.0.1:8000)."""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_COOKIE_PATH = Path.home() / ".config" / "papergrader" / "cookies.txt"


def validate_local_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in LOCAL_HOSTS:
        raise ValueError(
            f"Refusing non-local URL {url!r}: this CLI can only run against localhost / 127.0.0.1 / ::1."
        )
    return url.rstrip("/")


class ApiClient:
    def __init__(self, base_url: str, cookie_path: Path = DEFAULT_COOKIE_PATH):
        self.base_url = validate_local_url(base_url)
        self.cookie_path = cookie_path
        self.cookie_path.parent.mkdir(parents=True, exist_ok=True)
        self.jar = http.cookiejar.MozillaCookieJar(str(self.cookie_path))
        if self.cookie_path.exists():
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _save_cookies(self) -> None:
        self.jar.save(ignore_discard=True, ignore_expires=True)
        try:
            os.chmod(self.cookie_path, 0o600)
        except OSError:
            pass

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        hdrs = dict(headers or {})
        if json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method, headers=hdrs)
        try:
            with self.opener.open(req) as resp:
                body = resp.read()
                self._save_cookies()
                return resp.status, dict(resp.headers), body
        except urllib.error.HTTPError as e:
            body = e.read()
            msg = body.decode(errors="replace")
            try:
                detail = json.loads(msg).get("detail", msg)
            except Exception:
                detail = msg
            print(f"Error {e.code}: {detail}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"Connection error ({self.base_url}): {e.reason}", file=sys.stderr)
            sys.exit(1)

    def get_json(self, path: str):
        _, _, body = self.request("GET", path)
        return json.loads(body)

    def post_json(self, path: str, payload: object | None = None):
        _, _, body = self.request("POST", path, json_body=payload)
        return json.loads(body) if body else None

    def put_json(self, path: str, payload: object):
        _, _, body = self.request("PUT", path, json_body=payload)
        return json.loads(body) if body else None

    def delete(self, path: str) -> None:
        self.request("DELETE", path)

    def post_multipart(
        self,
        path: str,
        fields: list[tuple[str, str]],
        files: list[tuple[str, str, bytes, str]],
    ):
        boundary = f"----pgcli-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields:
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
            )
        for field_name, filename, content, ctype in files:
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()
                + content
                + b"\r\n"
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        _, _, resp = self.request(
            "POST",
            path,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return json.loads(resp) if resp else None


def _emit(data: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2))
        return
    if isinstance(data, list):
        if not data:
            print("(none)")
            return
        for item in data:
            print(json.dumps(item) if isinstance(item, dict) else item)
    elif isinstance(data, dict):
        print(json.dumps(data, indent=2))
    elif data is not None:
        print(data)


def cmd_login(client: ApiClient, args: argparse.Namespace) -> None:
    email = args.email or os.environ.get("PAPERGRADER_EMAIL") or input("Email: ")
    password = args.password or os.environ.get("PAPERGRADER_PASSWORD") or getpass.getpass("Password: ")
    user = client.post_json("/auth/login", {"email": email, "password": password})
    if args.json:
        _emit(user, True)
    else:
        print(f"Signed in as {user['email']} ({user['role']})")


def cmd_logout(client: ApiClient, args: argparse.Namespace) -> None:
    client.post_json("/auth/logout", {})
    if client.cookie_path.exists():
        client.cookie_path.unlink()
    print("Signed out.")


def cmd_whoami(client: ApiClient, args: argparse.Namespace) -> None:
    _emit(client.get_json("/auth/me"), args.json)


def cmd_status(client: ApiClient, args: argparse.Namespace) -> None:
    health = client.get_json("/health")
    grader = client.get_json("/grader/status")
    _emit({"health": health, "grader": grader}, args.json)


def cmd_exams_list(client: ApiClient, args: argparse.Namespace) -> None:
    exams = client.get_json("/exams")
    if args.json:
        _emit(exams, True)
    elif not exams:
        print("(no exams)")
    else:
        for e in exams:
            model = e.get("model_override") or "default"
            print(f"{e['id']:>4}  {e['name']}  (model: {model})")


def cmd_exams_create(client: ApiClient, args: argparse.Namespace) -> None:
    _emit(client.post_json("/exams", {"name": args.name, "model_override": args.model}), args.json)


def cmd_exams_rubric(client: ApiClient, args: argparse.Namespace) -> None:
    _emit(client.get_json(f"/exams/{args.exam_id}/rubric"), args.json)


def cmd_exams_queue(client: ApiClient, args: argparse.Namespace) -> None:
    _emit(client.post_json(f"/exams/{args.exam_id}/queue", {}), args.json)


def cmd_papers_list(client: ApiClient, args: argparse.Namespace) -> None:
    params = {}
    if args.exam is not None:
        params["exam_id"] = str(args.exam)
    if args.status:
        params["status"] = args.status
    qs = f"?{urllib.parse.urlencode(params)}" if params else ""
    subs = client.get_json(f"/submissions{qs}")
    if args.json:
        _emit(subs, True)
    elif not subs:
        print("(no papers)")
    else:
        for s in subs:
            st = s["student"]
            prog = f"{s['grades_reviewed']}/{s['grades_total']}" if s["grades_total"] else "-"
            conf = f"{s['min_confidence']:.2f}" if s["min_confidence"] is not None else "-"
            print(f"{s['id']:>4}  exam={s['exam_id']}  {st['external_id']:<12}  {s['status']:<16}  reviewed={prog:<6}  min_conf={conf}")


def cmd_papers_pending(client: ApiClient, args: argparse.Namespace) -> None:
    args.status = "ready_for_review"
    cmd_papers_list(client, args)


def cmd_papers_show(client: ApiClient, args: argparse.Namespace) -> None:
    _emit(client.get_json(f"/submissions/{args.submission_id}/detail"), args.json)


def cmd_papers_assign(client: ApiClient, args: argparse.Namespace) -> None:
    _emit(client.put_json(f"/submissions/{args.submission_id}/assignment", {"ta_ids": args.ta_id}), args.json)


def cmd_papers_regrade(client: ApiClient, args: argparse.Namespace) -> None:
    _emit(client.post_json(f"/submissions/{args.submission_id}/regrade", {"force": args.force}), args.json)


def cmd_grades_show(client: ApiClient, args: argparse.Namespace) -> None:
    detail = client.get_json(f"/submissions/{args.submission_id}/detail")
    if args.json:
        _emit(detail["grades"], True)
        return
    st = detail["student"]
    print(f"Submission {detail['id']} — {st['external_id']} ({st['name'] or 'no name'}) [{detail['status']}]")
    for g in detail["grades"]:
        score = g["final_score"] if g["final_score"] is not None else g["ai_score"]
        score_s = "-" if score is None else f"{score:g}"
        fb = g["final_feedback"] if g["final_feedback"] is not None else g["ai_feedback"]
        print(f"  Q{g['question_number']}: {score_s}/{g['max_points']:g} ({g['status']}) — {fb}")


def _download(client: ApiClient, path: str, output: Path) -> None:
    _, _, body = client.request("GET", path)
    output.write_bytes(body)
    print(f"Saved {output} ({len(body)} bytes)")


def cmd_grades_export_csv(client: ApiClient, args: argparse.Namespace) -> None:
    _download(client, f"/exams/{args.exam_id}/export.csv", Path(args.output))


def cmd_grades_export_pdf(client: ApiClient, args: argparse.Namespace) -> None:
    _download(client, f"/submissions/{args.submission_id}/export.pdf", Path(args.output))


def cmd_grades_export_zip(client: ApiClient, args: argparse.Namespace) -> None:
    _download(client, f"/exams/{args.exam_id}/export.zip", Path(args.output))


def cmd_import_files(client: ApiClient, args: argparse.Namespace) -> None:
    files: list[tuple[str, str, bytes, str]] = []
    for p in args.pdfs:
        path = Path(p)
        files.append(("files", path.name, path.read_bytes(), "application/pdf"))
    if args.csv:
        cpath = Path(args.csv)
        files.append(("mapping_csv", cpath.name, cpath.read_bytes(), "text/csv"))
    fields = [("dry_run", "true" if args.dry_run else "false"), ("reviewers_per_paper", str(args.reviewers))]
    if args.assign:
        fields.append(("assign", args.assign))
    _emit(client.post_multipart(f"/exams/{args.exam_id}/imports", fields, files), args.json)


def cmd_import_auto_split(client: ApiClient, args: argparse.Namespace) -> None:
    path = Path(args.pdf)
    res = client.post_multipart(
        f"/exams/{args.exam_id}/imports/auto-split",
        [],
        [("file", path.name, path.read_bytes(), "application/pdf")],
    )
    if args.wait:
        token = res["split_token"]
        while res["status"] in ("queued", "processing"):
            time.sleep(2)
            res = client.get_json(f"/exams/{args.exam_id}/imports/auto-split/{token}")
    _emit(res, args.json)


def cmd_import_auto_split_status(client: ApiClient, args: argparse.Namespace) -> None:
    if args.token:
        res = client.get_json(f"/exams/{args.exam_id}/imports/auto-split/{args.token}")
    else:
        res = client.get_json(f"/exams/{args.exam_id}/imports/auto-split")
    _emit(res, args.json)


def cmd_import_auto_split_commit(client: ApiClient, args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.spec).read_text())
    if isinstance(payload, list):
        payload = {"items": payload}
    _emit(client.post_json(f"/exams/{args.exam_id}/imports/auto-split/{args.token}/commit", payload), args.json)


def cmd_users_list(client: ApiClient, args: argparse.Namespace) -> None:
    users = client.get_json("/auth/users")
    if args.json:
        _emit(users, True)
    else:
        for u in users:
            state = "active" if u["active"] else "inactive"
            print(f"{u['id']:>4}  {u['email']:<28}  {u['role']:<5}  {state:<8}  papers={u['assigned_papers']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pg-cli", description="Local-only CLI for Paper Grader.")
    p.add_argument("--url", default=os.environ.get("PAPERGRADER_URL", DEFAULT_URL), help="Local API URL (default: http://127.0.0.1:8000)")
    p.add_argument("--cookies", default=str(DEFAULT_COOKIE_PATH), help="Path to session cookie file")
    p.add_argument("--json", action="store_true", help="Output raw JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="Sign in and store session cookie locally")
    s.add_argument("--email")
    s.add_argument("--password")
    s.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="Sign out and remove local cookie").set_defaults(func=cmd_logout)
    sub.add_parser("whoami", help="Show current signed-in user").set_defaults(func=cmd_whoami)
    sub.add_parser("status", help="Show API health and Claude grader readiness").set_defaults(func=cmd_status)

    ex = sub.add_parser("exams", help="Manage exams").add_subparsers(dest="subcmd", required=True)
    ex.add_parser("list", help="List visible exams").set_defaults(func=cmd_exams_list)
    s = ex.add_parser("create", help="Create an exam")
    s.add_argument("name")
    s.add_argument("--model", default=None)
    s.set_defaults(func=cmd_exams_create)
    s = ex.add_parser("rubric", help="Show latest rubric for an exam")
    s.add_argument("exam_id", type=int)
    s.set_defaults(func=cmd_exams_rubric)
    s = ex.add_parser("queue", help="Queue waiting & failed papers for an exam")
    s.add_argument("exam_id", type=int)
    s.set_defaults(func=cmd_exams_queue)

    pa = sub.add_parser("papers", help="Query and manage submissions").add_subparsers(dest="subcmd", required=True)
    for name, fn, help_txt in [
        ("list", cmd_papers_list, "List submissions"),
        ("pending", cmd_papers_pending, "List papers ready for review"),
    ]:
        s = pa.add_parser(name, help=help_txt)
        s.add_argument("--exam", type=int, default=None)
        if name == "list":
            s.add_argument("--status", default=None)
        s.set_defaults(func=fn)
    s = pa.add_parser("show", help="Show submission details")
    s.add_argument("submission_id", type=int)
    s.set_defaults(func=cmd_papers_show)
    s = pa.add_parser("assign", help="Assign TAs to a submission")
    s.add_argument("submission_id", type=int)
    s.add_argument("--ta-id", type=int, action="append", default=[])
    s.set_defaults(func=cmd_papers_assign)
    s = pa.add_parser("regrade", help="Requeue a submission for grading")
    s.add_argument("submission_id", type=int)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_papers_regrade)

    gr = sub.add_parser("grades", help="Inspect and export grades").add_subparsers(dest="subcmd", required=True)
    s = gr.add_parser("show", help="Show grade results for a submission")
    s.add_argument("submission_id", type=int)
    s.set_defaults(func=cmd_grades_show)
    s = gr.add_parser("export-csv", help="Download exam grades CSV")
    s.add_argument("exam_id", type=int)
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_grades_export_csv)
    s = gr.add_parser("export-pdf", help="Download annotated PDF for one submission")
    s.add_argument("submission_id", type=int)
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_grades_export_pdf)
    s = gr.add_parser("export-zip", help="Download ZIP of all annotated PDFs for an exam")
    s.add_argument("exam_id", type=int)
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_grades_export_zip)

    im = sub.add_parser("import", help="Import answer sheets").add_subparsers(dest="subcmd", required=True)
    s = im.add_parser("files", help="Import per-student PDFs or combined PDF + CSV")
    s.add_argument("exam_id", type=int)
    s.add_argument("pdfs", nargs="+")
    s.add_argument("--csv", default=None)
    s.add_argument("--assign", choices=["csv", "round_robin", "none"], default=None)
    s.add_argument("--reviewers", type=int, default=1)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_import_files)
    s = im.add_parser("auto-split", help="Upload combined PDF for background Claude auto-split")
    s.add_argument("exam_id", type=int)
    s.add_argument("pdf")
    s.add_argument("--wait", action="store_true", help="Poll until the split job finishes")
    s.set_defaults(func=cmd_import_auto_split)
    s = im.add_parser("auto-split-status", help="List or poll auto-split jobs for an exam")
    s.add_argument("exam_id", type=int)
    s.add_argument("token", nargs="?", default=None)
    s.set_defaults(func=cmd_import_auto_split_status)
    s = im.add_parser("auto-split-commit", help="Commit a ready auto-split job from a JSON spec file")
    s.add_argument("exam_id", type=int)
    s.add_argument("token")
    s.add_argument("--spec", required=True, help="JSON file with items list or {items: [...]}")
    s.set_defaults(func=cmd_import_auto_split_commit)

    us = sub.add_parser("users", help="Manage users").add_subparsers(dest="subcmd", required=True)
    us.add_parser("list", help="List users").set_defaults(func=cmd_users_list)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        client = ApiClient(args.url, Path(args.cookies))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    args.func(client, args)


if __name__ == "__main__":
    main()
