"""HTTP front for the Claude CLI, run inside the dedicated `claude` container.

The container owns the Claude login (a persistent home volume). Callers on the internal network
send pages + prompt; the CLI flags are fixed here (grader.claude_cli.build_command), never taken
from the request, so a leaked token can't be used to run Claude with shell access.
"""

import hmac
import json
import re
import threading

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.grader.base import GraderError
from app.grader.claude_cli import ClaudeExecutor, ExecTimeout, LocalExecutor

FILE_NAME = re.compile(r"^(page-\d{1,4}\.png|sheet-\d{1,4}\.png|rubric\.json)$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,100}$")
MAX_PROMPT_CHARS = 50_000
MAX_FILES = 400


def create_app(
    executor: ClaudeExecutor | None = None,
    token: str | None = None,
    concurrency: int | None = None,
    max_request_mb: int | None = None,
) -> FastAPI:
    s = get_settings()
    token = s.claude_runner_token if token is None else token
    if not token:
        raise RuntimeError("CLAUDE_RUNNER_TOKEN must be set; refusing to start an unauthenticated runner")
    executor = executor or LocalExecutor(s.claude_bin, s.claude_bare)
    slots = threading.BoundedSemaphore(concurrency or s.claude_runner_concurrency)
    max_bytes = (max_request_mb or s.claude_runner_max_request_mb) * 1024 * 1024
    bearer = HTTPBearer(auto_error=False)

    def authed(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if creds is None or not hmac.compare_digest(creds.credentials.encode(), token.encode()):
            raise HTTPException(401, "Invalid or missing token")

    app = FastAPI(title="Claude runner", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}  # liveness only; says nothing about the login

    @app.get("/status", dependencies=[Depends(authed)])
    def status():
        return executor.status()

    @app.post("/usage", dependencies=[Depends(authed)])
    def usage():
        """One tiny Haiku request that reports the plan's (or API key's) rate limits. Not queued behind grading."""
        try:
            return executor.usage()
        except GraderError as e:
            return JSONResponse({"detail": str(e)}, status_code=502)

    @app.post("/run", dependencies=[Depends(authed)])
    def run(
        model: str = Form(...),
        prompt: str = Form(...),
        timeout: int = Form(600, ge=1, le=3600),
        json_schema: str | None = Form(None),
        inline_images: bool = Form(False),
        files: list[UploadFile] = File(...),
    ):
        if not MODEL_NAME.fullmatch(model):
            raise HTTPException(422, "Invalid model name")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise HTTPException(413, "Prompt too long")
        parsed_schema: dict | None = None
        if json_schema is not None:
            if len(json_schema) > MAX_PROMPT_CHARS:
                raise HTTPException(413, "JSON schema too long")
            try:
                parsed_schema = json.loads(json_schema)
            except json.JSONDecodeError:
                raise HTTPException(422, "Invalid json_schema")
            if not isinstance(parsed_schema, dict):
                raise HTTPException(422, "json_schema must be a JSON object")
        if len(files) > MAX_FILES:
            raise HTTPException(413, "Too many files")
        contents: dict[str, bytes] = {}
        total = 0
        for f in files:
            name = f.filename or ""
            if not FILE_NAME.fullmatch(name) or name in contents:
                raise HTTPException(422, f"Unexpected file name {name!r}")
            data = f.file.read()
            total += len(data)
            if total > max_bytes:
                raise HTTPException(413, "Request too large")
            contents[name] = data

        with slots:
            try:
                result = executor.run(model, prompt, contents, timeout, json_schema=parsed_schema, inline_images=inline_images)
            except ExecTimeout as e:
                return JSONResponse({"detail": str(e)}, status_code=504)
            except GraderError as e:
                return JSONResponse({"detail": str(e)}, status_code=502)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    return app
