"""Веб-приложение: сбор тестов для Moodle."""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from . import moodle_xml
from .auth import Accounts, LoginThrottle
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
MINSK = timezone(timedelta(hours=3))  # Беларусь, без перехода на летнее время
MAX_QUESTIONS = 2000
STATIC_VERSION = "3"


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
    db = Database(settings.db_path)
    accounts = Accounts(db)
    accounts.ensure("editor", settings.editor_login, settings.editor_password)
    accounts.ensure("admin", settings.admin_login, settings.admin_password)
    throttle = LoginThrottle(settings.login_max_failures, settings.login_window_seconds, settings.login_block_seconds)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.accounts = accounts
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.resolved_secret_key(),
        session_cookie="mt_session",
        max_age=14 * 24 * 3600,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
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
            return render(
                request, "login.html", next=target, login=login,
                error=f"Слишком много неудачных попыток. Попробуйте через {minutes} мин.",
            )
        role = accounts.authenticate(login, password)
        if role is None:
            throttle.failure(key)
            return render(request, "login.html", next=target, login=login, error="Неверный логин или пароль")
        throttle.success(key)
        request.session.clear()
        request.session.update({"role": role, "cv": accounts.credential_version(role)})
        return RedirectResponse(target, status_code=303)

    @app.post("/logout")
    def logout(request: Request):
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
            return render(request, "new_test.html", role, form=form, nav="tests",
                          error="Укажите дисциплину и преподавателя")
        if not confirm:
            key = form["discipline"].casefold()
            duplicate = next((t for t in db.list_tests() if t["discipline"].casefold() == key), None)
            if duplicate:
                return render(request, "new_test.html", role, form=form, nav="tests", duplicate=duplicate)
        test = db.create_test(form["discipline"], form["teacher"], form["department"], default_settings())
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
            return JSONResponse(
                {"detail": "Тест изменили в другой вкладке или другой человек. Обновите страницу.",
                 "conflict": True},
                status_code=409,
            )
        return {"version": saved["version"], "updated_at": _fmt_dt(saved["updated_at"]),
                "status": saved["status"], "issues": validate_test(saved)}

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
            raise HTTPException(400, "Сначала исправьте ошибки в тесте")
        saved = db.set_status(test_id, "ready" if ready else "draft")
        return {"status": saved["status"], "version": saved["version"],
                "updated_at": _fmt_dt(saved["updated_at"])}

    @app.delete("/api/tests/{test_id}")
    def api_delete_test(test_id: int, role: str = Depends(api_role)):
        get_test_or_404(test_id)
        db.soft_delete(test_id)
        return {"ok": True}

    @app.post("/api/tests/{test_id}/images")
    async def api_upload_image(test_id: int, file: UploadFile = File(...), role: str = Depends(api_role)):
        get_test_or_404(test_id)
        data = await file.read(settings.max_image_bytes + 1)
        if len(data) > settings.max_image_bytes:
            raise HTTPException(400, "Картинка больше 5 МБ — уменьшите её")
        mime = _sniff_image(data)
        if mime is None:
            raise HTTPException(400, "Поддерживаются картинки PNG, JPEG, GIF и WebP")
        image_id = db.add_image(test_id, _clean(file.filename, 200) or "image", mime, data)
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
        return parse_text(text)

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
        return render(request, "admin.html", role, tests=tests, deleted=deleted, totals=totals,
                      editor_login=accounts.login_name("editor"), admin_login=accounts.login_name("admin"),
                      msg=msg, nav="admin")

    @app.get("/admin/export/{test_id}.xml")
    def admin_export_one(test_id: int, role: str = Depends(page_admin)):
        test = get_test_or_404(test_id)
        data = moodle_xml.build_test_xml(test, image_getter(test_id))
        name = moodle_xml.xml_filename(test)
        return Response(data, media_type="application/xml",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/admin/export.zip")
    def admin_export_all(role: str = Depends(page_admin), ready_only: int = 0):
        tests = db.list_tests()
        if ready_only:
            tests = [t for t in tests if test_summary(t)["state"] == "ready"]

        data = moodle_xml.export_zip(tests, image_getter)
        stamp = datetime.now(MINSK).strftime("%Y-%m-%d_%H-%M")
        name = f"moodle-tests_{stamp}.zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/admin/backup.db")
    def admin_backup(role: str = Depends(page_admin)):
        tmp = Path(tempfile.mkdtemp()) / "tests.db"
        db.backup_to(tmp)
        stamp = datetime.now(MINSK).strftime("%Y-%m-%d_%H-%M")

        def cleanup():
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()

        return FileResponse(tmp, filename=f"backup_{stamp}.db", media_type="application/octet-stream",
                            background=BackgroundTask(cleanup))

    @app.post("/admin/tests/{test_id}/restore")
    def admin_restore(test_id: int, role: str = Depends(page_admin)):
        db.restore(test_id)
        return RedirectResponse("/admin?msg=restored", status_code=303)

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
            return RedirectResponse("/admin?msg=weak#accounts", status_code=303)
        accounts.set(account, login, password)
        if account == "admin":
            request.session.update({"role": "admin", "cv": accounts.credential_version("admin")})
        return RedirectResponse(f"/admin?msg=password_{account}#accounts", status_code=303)

    return app


def build_app() -> FastAPI:
    """Точка входа для uvicorn: `uvicorn --factory app.main:build_app`."""
    from .config import load_dotenv

    load_dotenv()
    return create_app(Settings())
