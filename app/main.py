"""Веб-приложение: сбор тестов для Moodle."""
import json
import logging
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import activity, moodle_xml
from .activity import MINSK, event
from .auth import Accounts, LoginThrottle
from .guard import Guard
from .config import Settings
from .db import Database
from .paste_parser import parse_text
from .questions import (
    QUESTION_TYPES,
    default_settings,
    has_errors,
    normalize_question,
    normalize_settings,
    validate_test,
)

BASE_DIR = Path(__file__).parent
MAX_QUESTIONS = 2000
STATIC_VERSION = "15"
WARNING = logging.WARNING
# Запросы, которые не пишем в журнал, если они прошли успешно: статика и картинки.
QUIET_PREFIXES = ("/static/", "/favicon", "/images/")
# Открыто даже для заблокированного адреса: преподаватель из той же сети должен войти.
ALWAYS_OPEN = ("/login", "/static/", "/favicon", "/robots.txt")
BLOCKED_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Доступ ограничен</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body class="login-body"><main><div class="login sheet">
<h1>Доступ временно ограничен</h1>
<p>С вашего адреса было много подозрительных запросов, поэтому доступ без входа ограничен.</p>
<p>Если вы преподаватель, войдите на сайт: после входа всё работает как обычно.</p>
<a class="btn btn-primary btn-big" href="/login">Войти</a>
</div></main></body></html>"""
CLIENT_LOG_PER_MINUTE = 30


def _fmt_dt(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value).astimezone(MINSK)
    except ValueError:
        return value
    return dt.strftime("%d.%m.%Y %H:%M")


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_next(value: str | None) -> str:
    """Только локальный путь: «//evil» и «/\\evil» браузер считает чужим сайтом."""
    value = value or "/"
    if not value.startswith("/") or value[1:2] in ("/", "\\") or "\\" in value or any(c < " " for c in value):
        return "/"
    return value


def _clean(value: str | None, limit: int = 300) -> str:
    return " ".join((value or "").split())[:limit]


def test_summary(test: dict) -> dict:
    issues = validate_test(test)
    errors = sum(1 for i in issues if i["level"] == "error")
    if test["status"] == "ready" and not errors:
        state = "ready"
    elif errors:
        state = "errors"
    else:
        state = "draft"
    return {
        "id": test["id"],
        "discipline": test["discipline"],
        "teacher": test["teacher"],
        "department": test["department"],
        "questions": len(test["questions"]),
        "errors": errors,
        "state": state,
        "updated": _fmt_dt(test["updated_at"]),
        "deleted": _fmt_dt(test["deleted_at"]),
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    log_path = activity.setup(settings.data_dir / "logs")
    activity.log.info("%-18s data_dir=%s", "app.start", settings.data_dir)
    db = Database(settings.db_path)
    accounts = Accounts(db)
    accounts.ensure("editor", settings.editor_login, settings.editor_password)
    accounts.ensure("admin", settings.admin_login, settings.admin_password)
    throttle = LoginThrottle(settings.login_max_failures, settings.login_window_seconds, settings.login_block_seconds)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.accounts = accounts
    app.state.log_path = log_path
    guard = Guard()
    app.state.guard = guard

    # Защита от сканеров. Добавлена раньше SessionMiddleware, значит работает внутри неё
    # и уже знает, вошёл ли человек: вошедших блокировки не касаются.
    async def protect(request: Request, call_next):
        session = request.scope.get("session") or {}
        if session.get("role") in ("editor", "admin"):
            return await call_next(request)
        ip = client_key(request)
        path = request.url.path
        full = f"{path}?{request.url.query}" if request.url.query else path
        ban = guard.banned(ip)
        if ban and not path.startswith(ALWAYS_OPEN):
            ban.blocked += 1
            request.state.silent = True  # о блокировке уже есть строка security.ban
            return HTMLResponse(BLOCKED_PAGE, status_code=403)
        if not ban and guard.is_trap(full):
            guard.ban(ip, "ловушка", full)
            event(request, "security.ban", WARNING, reason="ловушка", path=full, hours=guard.ban_seconds // 3600)
            request.state.silent = True
            return PlainTextResponse("Not Found", status_code=404)  # не подсказываем, что его заметили
        if not ban and not path.startswith(("/static/", "/favicon")) and guard.too_fast(ip):
            guard.ban(ip, "слишком часто", full)
            event(request, "security.ban", WARNING, reason="слишком часто", path=full,
                  hours=guard.ban_seconds // 3600)
            request.state.silent = True
            return PlainTextResponse("Too Many Requests", status_code=429)
        response = await call_next(request)
        if response.status_code == 404 and not ban and guard.not_found(ip):
            guard.ban(ip, "много 404", full)
            event(request, "security.ban", WARNING, reason="много 404", path=full, hours=guard.ban_seconds // 3600)
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=protect)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.resolved_secret_key(),
        session_cookie="mt_session",
        max_age=14 * 24 * 3600,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    # Журнал запросов. Объявлен раньше security_headers, значит работает внутри неё:
    # заголовки безопасности попадают и на ответ 500, который собирается здесь.
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        request.state.rid = activity.new_id()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            activity.log.exception("%-18s %s", "server.error", activity.fields_text(
                {**activity.context(request), "method": request.method, "path": request.url.path}))
            message = f"Ошибка на сервере. Сообщите администратору код {request.state.rid}"
            if request.url.path.startswith("/api/"):
                response = JSONResponse({"detail": message}, status_code=500)
            else:
                response = PlainTextResponse(message, status_code=500)
        status = response.status_code
        path = request.url.path
        quiet = path.startswith(QUIET_PREFIXES) or getattr(request.state, "logged", False)
        silent = getattr(request.state, "silent", False)
        if not silent and (status >= 400 or not quiet):
            level = logging.ERROR if status >= 500 else WARNING if status >= 400 else logging.INFO
            guest = (request.scope.get("session") or {}).get("role") is None
            if guest and status in (404, 405):
                level = logging.INFO  # боты без входа перебирают адреса — это не проблема сайта
            query = request.url.query
            activity.log.log(level, "%-18s %s", "http", activity.fields_text({
                **activity.context(request),
                "method": request.method,
                "path": f"{path}?{query}" if query else path,
                "status": status,
                "ms": round((time.perf_counter() - started) * 1000),
                "detail": getattr(request.state, "error_detail", None),
            }))
        response.headers["X-Request-ID"] = request.state.rid
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        request.state.error_detail = exc.detail  # попадёт в строку журнала о запросе
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        request.state.error_detail = str(exc.errors())[:300]
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; "
            "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        if request.url.path.startswith(("/api/", "/admin", "/tests", "/images")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.globals["QUESTION_TYPES"] = QUESTION_TYPES
    templates.env.globals["static_version"] = STATIC_VERSION

    # --- авторизация ------------------------------------------------------
    def session_role(request: Request) -> str | None:
        role = request.session.get("role")
        if role not in ("editor", "admin"):
            return None
        if request.session.get("cv") != accounts.credential_version(role):
            request.session.clear()
            return None
        return role

    class NeedLogin(Exception):
        pass

    @app.exception_handler(NeedLogin)
    async def need_login_handler(request: Request, exc: NeedLogin):
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)

    def page_role(request: Request) -> str:
        role = session_role(request)
        if role is None:
            raise NeedLogin()
        return role

    def page_admin(request: Request) -> str:
        role = page_role(request)
        if role != "admin":
            raise HTTPException(403, "Только для администратора")
        return role

    def api_role(request: Request) -> str:
        role = session_role(request)
        if role is None:
            raise HTTPException(401, "Сессия истекла — войдите заново")
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "fetch":
            raise HTTPException(403, "Запрос отклонён")
        return role

    def client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def render(request: Request, name: str, role: str | None = None, **ctx):
        return templates.TemplateResponse(request, name, {"role": role, **ctx})

    def get_test_or_404(test_id: int) -> dict:
        test = db.get_test(test_id)
        if test is None:
            raise HTTPException(404, "Тест не найден")
        return test

    def image_getter(test_id: int):
        def get(image_id: str):
            img = db.get_image(image_id)
            return img if img and img["test_id"] == test_id else None
        return get

    # --- вход -------------------------------------------------------------
    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nDisallow: /\n"

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if session_role(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html", next=safe_next(next))

    @app.post("/login")
    def login(request: Request, login: str = Form(""), password: str = Form(""), next: str = Form("/")):
        key = client_key(request)
        target = safe_next(next)
        left = throttle.blocked_for(key)
        if left:
            minutes = max(1, (left + 59) // 60)
            event(request, "auth.blocked", WARNING, login=login, minutes=minutes)
            return render(
                request, "login.html", next=target, login=login,
                error=f"Слишком много неудачных попыток. Попробуйте через {minutes} мин.",
            )
        role = accounts.authenticate(login, password)
        if role is None:
            throttle.failure(key)
            event(request, "auth.fail", WARNING, login=login)  # пароль в журнал не пишем
            return render(request, "login.html", next=target, login=login, error="Неверный логин или пароль")
        throttle.success(key)
        request.session.clear()
        # sid — метка сеанса браузера: у преподавателей общий логин, по ней видно действия одного человека.
        request.session.update({"role": role, "cv": accounts.credential_version(role), "sid": activity.new_id(6)})
        event(request, "auth.login", login=login, ua=request.headers.get("user-agent", "")[:150])
        return RedirectResponse(target, status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        event(request, "auth.logout")
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # --- страницы ---------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, role: str = Depends(page_role)):
        tests = [test_summary(t) for t in db.list_tests()]
        return render(request, "index.html", role, tests=tests, nav="tests")

    @app.get("/tests/new", response_class=HTMLResponse)
    def new_test_page(request: Request, role: str = Depends(page_role)):
        return render(request, "new_test.html", role, form={}, nav="tests")

    @app.post("/tests/new")
    def new_test(
        request: Request,
        discipline: str = Form(""),
        teacher: str = Form(""),
        department: str = Form(""),
        confirm: str = Form(""),
        role: str = Depends(page_role),
    ):
        form = {"discipline": _clean(discipline), "teacher": _clean(teacher), "department": _clean(department)}
        if not form["discipline"] or not form["teacher"]:
            event(request, "test.create_empty", WARNING, **form)
            return render(request, "new_test.html", role, form=form, nav="tests",
                          error="Укажите дисциплину и преподавателя")
        if not confirm:
            key = form["discipline"].casefold()
            duplicate = next((t for t in db.list_tests() if t["discipline"].casefold() == key), None)
            if duplicate:
                event(request, "test.duplicate", existing=duplicate["id"], **form)
                return render(request, "new_test.html", role, form=form, nav="tests", duplicate=duplicate)
        test = db.create_test(form["discipline"], form["teacher"], form["department"], default_settings())
        event(request, "test.create", test=test["id"], confirmed_duplicate=bool(confirm) or None, **form)
        return RedirectResponse(f"/tests/{test['id']}", status_code=303)

    @app.get("/tests/{test_id}", response_class=HTMLResponse)
    def editor_page(request: Request, test_id: int, role: str = Depends(page_role)):
        test = get_test_or_404(test_id)
        payload = {**test_payload(test), "updated_label": _fmt_dt(test["updated_at"])}
        return render(request, "editor.html", role, test=test, payload=payload,
                      types=QUESTION_TYPES, nav="tests")

    @app.get("/tests/{test_id}/preview", response_class=HTMLResponse)
    def preview_page(request: Request, test_id: int, answers: int = 0, role: str = Depends(page_role)):
        test = get_test_or_404(test_id)
        return render(request, "preview.html", role, test=test, show_answers=bool(answers),
                      issues=validate_test(test), nav="tests")

    # --- API тестов -------------------------------------------------------
    def test_payload(test: dict) -> dict:
        return {**test, "issues": validate_test(test)}

    @app.get("/api/tests/{test_id}")
    def api_get_test(test_id: int, role: str = Depends(api_role)):
        return test_payload(get_test_or_404(test_id))

    @app.put("/api/tests/{test_id}")
    async def api_save_test(test_id: int, request: Request, role: str = Depends(api_role)):
        current = get_test_or_404(test_id)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Некорректные данные")
        if not isinstance(body, dict):
            raise HTTPException(400, "Некорректные данные")
        raw_questions = body.get("questions") or []
        if not isinstance(raw_questions, list) or len(raw_questions) > MAX_QUESTIONS:
            raise HTTPException(400, f"Слишком много вопросов (максимум {MAX_QUESTIONS})")
        questions = [normalize_question(q) for q in raw_questions]
        seen = set()
        for q in questions:  # id вопросов должны быть уникальны
            if q["id"] in seen:
                q["id"] = normalize_question({})["id"]
            seen.add(q["id"])
        fields = {
            "discipline": _clean(body.get("discipline")),
            "teacher": _clean(body.get("teacher")),
            "department": _clean(body.get("department")),
            "settings": normalize_settings(body.get("settings")),
            "questions": questions,
            "status": current["status"],
        }
        draft = {**current, **fields}
        if fields["status"] == "ready" and has_errors(validate_test(draft)):
            fields["status"] = "draft"
        try:
            version = int(body.get("version"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Не указана версия теста")
        saved = db.update_test(test_id, version, fields)
        if saved is None:
            latest = db.get_test(test_id) or current
            request.state.error_detail = "конфликт версий"
            event(request, "test.conflict", WARNING, test=test_id, client_version=version,
                  server_version=latest["version"])
            return JSONResponse(
                {"detail": "Тест изменили в другой вкладке или другой человек. Обновите страницу.",
                 "conflict": True},
                status_code=409,
            )
        issues = validate_test(saved)
        event(request, "test.save", test=test_id, version=f"{version}->{saved['version']}",
              questions=len(questions), errors=sum(1 for i in issues if i["level"] == "error"),
              status=saved["status"],
              reopened=(current["status"] == "ready" and saved["status"] != "ready") or None)
        return {"version": saved["version"], "updated_at": _fmt_dt(saved["updated_at"]),
                "status": saved["status"], "issues": issues}

    @app.post("/api/tests/{test_id}/status")
    async def api_set_status(test_id: int, request: Request, role: str = Depends(api_role)):
        test = get_test_or_404(test_id)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "Некорректные данные")
        ready = bool(body.get("ready"))
        issues = validate_test(test)
        if ready and has_errors(issues):
            event(request, "test.ready_refused", WARNING, test=test_id,
                  errors=sum(1 for i in issues if i["level"] == "error"))
            raise HTTPException(400, "Сначала исправьте ошибки в тесте")
        saved = db.set_status(test_id, "ready" if ready else "draft")
        event(request, "test.status", test=test_id, status=saved["status"], questions=len(test["questions"]))
        return {"status": saved["status"], "version": saved["version"],
                "updated_at": _fmt_dt(saved["updated_at"])}

    @app.delete("/api/tests/{test_id}")
    def api_delete_test(test_id: int, request: Request, role: str = Depends(api_role)):
        test = get_test_or_404(test_id)
        db.soft_delete(test_id)
        event(request, "test.delete", test=test_id, discipline=test["discipline"], teacher=test["teacher"],
              questions=len(test["questions"]))
        return {"ok": True}

    @app.post("/api/tests/{test_id}/images")
    async def api_upload_image(test_id: int, request: Request, file: UploadFile = File(...),
                               role: str = Depends(api_role)):
        get_test_or_404(test_id)
        data = await file.read(settings.max_image_bytes + 1)
        if len(data) > settings.max_image_bytes:
            event(request, "image.rejected", WARNING, test=test_id, reason="too_big", name=file.filename)
            raise HTTPException(400, "Картинка больше 5 МБ — уменьшите её")
        mime = _sniff_image(data)
        if mime is None:
            event(request, "image.rejected", WARNING, test=test_id, reason="format", name=file.filename,
                  content_type=file.content_type)
            raise HTTPException(400, "Поддерживаются картинки PNG, JPEG, GIF и WebP")
        image_id = db.add_image(test_id, _clean(file.filename, 200) or "image", mime, data)
        event(request, "image.upload", test=test_id, image=image_id, mime=mime, kb=round(len(data) / 1024))
        return {"id": image_id}

    @app.get("/images/{image_id}")
    def get_image(image_id: str, request: Request):
        if session_role(request) is None:
            raise HTTPException(401)
        img = db.get_image(image_id)
        if img is None:
            raise HTTPException(404)
        return Response(img["data"], media_type=img["mime"], headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/api/parse")
    async def api_parse(request: Request, role: str = Depends(api_role)):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "Некорректные данные")
        text = str(body.get("text") or "")[:500_000]
        result = parse_text(text)
        # Сам текст теста не пишем — только размеры, чтобы видеть, что разбор не потерял вопросы.
        event(request, "word.parse", chars=len(text), questions=len(result["questions"]),
              no_answer=sum(1 for q in result["questions"]
                            if q.get("type") in ("single", "multiple")
                            and not any(o.get("correct") for o in q.get("options", []))),
              warnings=len(result["warnings"]))
        return result

    # --- ошибки из браузера -----------------------------------------------
    client_log_hits: dict[str, list[float]] = {}

    @app.post("/api/client-log")
    async def api_client_log(request: Request):
        """Браузер сообщает о своих ошибках: JS-исключения, сбои сохранения из-за связи."""
        if request.headers.get("x-requested-with") != "fetch":
            raise HTTPException(403, "Запрос отклонён")
        key, now = client_key(request), time.monotonic()
        if len(client_log_hits) > 5000:
            client_log_hits.clear()
        hits = [t for t in client_log_hits.get(key, []) if now - t < 60]
        if len(hits) >= CLIENT_LOG_PER_MINUTE:
            client_log_hits[key] = hits
            request.state.logged = True  # не засоряем журнал, если браузер шлёт ошибки потоком
            return Response(status_code=204)
        client_log_hits[key] = [*hits, now]
        raw = await request.body()
        if len(raw) > 16_000:
            raise HTTPException(413, "Слишком большой отчёт")
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "Некорректные данные")
        kind = re.sub(r"[^a-z_]", "", str(body.get("kind") or ""))[:30] or "error"
        limits = {"message": 500, "page": 200, "source": 200, "line": 10, "col": 10, "test": 10,
                  "details": 500, "stack": 1500}
        fields = {name: str(body.get(name))[:limit] for name, limit in limits.items() if body.get(name)}
        event(request, f"client.{kind}", WARNING, **fields, ua=request.headers.get("user-agent", "")[:150])
        return Response(status_code=204)

    # --- админка ----------------------------------------------------------
    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request, role: str = Depends(page_admin), msg: str = ""):
        tests = [test_summary(t) for t in db.list_tests()]
        deleted = [test_summary(t) for t in db.list_tests(deleted=True)]
        totals = {
            "tests": len(tests),
            "ready": sum(1 for t in tests if t["state"] == "ready"),
            "errors": sum(1 for t in tests if t["state"] == "errors"),
            "questions": sum(t["questions"] for t in tests),
        }
        bans = [{"ip": ip, "reason": b.reason, "path": b.path, "blocked": b.blocked,
                 "since": datetime.fromtimestamp(b.since, MINSK).strftime("%d.%m %H:%M"),
                 "until": datetime.fromtimestamp(b.until, MINSK).strftime("%d.%m %H:%M")}
                for ip, b in guard.active()]
        return render(request, "admin.html", role, tests=tests, deleted=deleted, totals=totals,
                      editor_login=accounts.login_name("editor"), admin_login=accounts.login_name("admin"),
                      bans=bans, msg=msg, nav="admin")

    @app.post("/admin/security/unban")
    def admin_unban(request: Request, ip: str = Form(...), role: str = Depends(page_admin)):
        if guard.unban(ip):
            event(request, "security.unban", target=ip)
        return RedirectResponse("/admin?msg=unbanned#security", status_code=303)

    @app.get("/admin/export/{test_id}.xml")
    def admin_export_one(test_id: int, request: Request, role: str = Depends(page_admin)):
        test = get_test_or_404(test_id)
        data = moodle_xml.build_test_xml(test, image_getter(test_id))
        name = moodle_xml.xml_filename(test)
        event(request, "admin.export_xml", test=test_id, questions=len(test["questions"]), kb=round(len(data) / 1024))
        return Response(data, media_type="application/xml",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/admin/export.zip")
    def admin_export_all(request: Request, role: str = Depends(page_admin), ready_only: int = 0):
        tests = db.list_tests()
        if ready_only:
            tests = [t for t in tests if test_summary(t)["state"] == "ready"]

        data = moodle_xml.export_zip(tests, image_getter)
        event(request, "admin.export_zip", tests=len(tests), ready_only=bool(ready_only), kb=round(len(data) / 1024))
        stamp = datetime.now(MINSK).strftime("%Y-%m-%d_%H-%M")
        name = f"moodle-tests_{stamp}.zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/admin/backup.db")
    def admin_backup(request: Request, role: str = Depends(page_admin)):
        tmp = Path(tempfile.mkdtemp()) / "tests.db"
        db.backup_to(tmp)
        event(request, "admin.backup", kb=round(tmp.stat().st_size / 1024))
        stamp = datetime.now(MINSK).strftime("%Y-%m-%d_%H-%M")

        def cleanup():
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()

        return FileResponse(tmp, filename=f"backup_{stamp}.db", media_type="application/octet-stream",
                            background=BackgroundTask(cleanup))

    @app.post("/admin/tests/{test_id}/restore")
    def admin_restore(test_id: int, request: Request, role: str = Depends(page_admin)):
        db.restore(test_id)
        event(request, "admin.restore", test=test_id)
        return RedirectResponse("/admin?msg=restored", status_code=303)

    @app.get("/admin/api/logs")
    def admin_api_logs(request: Request, role: str = Depends(page_admin), q: str = "", level: str = "",
                       before: str = "", skip: int = 0, after: str | None = None, after_skip: int = 0):
        """Журнал для окна в админке.

        Без курсора — последние 200 событий; before — следующая порция в прошлое;
        after — новые события для автообновления. Поиск и фильтр действуют на весь журнал.
        """
        request.state.logged = True  # просмотр журнала в сам журнал не пишем
        query = q[:200]
        if after is not None:
            return activity.newer(log_path, after=after or None, after_skip=max(0, after_skip),
                                  query=query, level=level)
        return activity.search(log_path, query=query, level=level, before=before or None, skip=max(0, skip))

    @app.get("/admin/logs")
    def admin_logs(request: Request, role: str = Depends(page_admin), lines: int = 3000, download: int = 0):
        request.state.logged = True  # просмотр журнала в сам журнал не пишем
        if download:
            stamp = datetime.now(MINSK).strftime("%Y-%m-%d_%H-%M")
            if not log_path.exists():
                return PlainTextResponse("")
            return FileResponse(log_path, filename=f"journal_{stamp}.log", media_type="text/plain; charset=utf-8")
        return PlainTextResponse(activity.tail(log_path, max(1, min(lines, 50_000))))

    @app.post("/admin/accounts")
    def admin_accounts(
        request: Request,
        account: str = Form(...),
        login: str = Form(""),
        password: str = Form(""),
        role: str = Depends(page_admin),
    ):
        if account not in ("editor", "admin"):
            raise HTTPException(400)
        login = login.strip()
        if not login or len(password) < 8:
            event(request, "admin.password_weak", WARNING, account=account)
            return RedirectResponse("/admin?msg=weak#accounts", status_code=303)
        accounts.set(account, login, password)
        event(request, "admin.password", account=account, login=login)  # сам пароль не пишем
        if account == "admin":
            request.session.update({"role": "admin", "cv": accounts.credential_version("admin")})
        return RedirectResponse(f"/admin?msg=password_{account}#accounts", status_code=303)

    return app


def build_app() -> FastAPI:
    """Точка входа для uvicorn: `uvicorn --factory app.main:build_app`."""
    from .config import load_dotenv

    load_dotenv()
    return create_app(Settings())
