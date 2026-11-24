"""HTTP layer: a handful of JSON routes plus static files.

No framework. The route table is a list of (method, regex, handler) tuples,
which is enough for an app this size and keeps startup instant.
"""

from __future__ import annotations

import json
import mimetypes
import re
import time
import uuid
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import foods as food_db
from . import health
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 2 * 1024 * 1024

MIME_OVERRIDES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


def _repair(text: str) -> str:
    """Undo the latin-1 reinterpretation http.server applies to the request line.

    Browsers percent-encode UTF-8 in the query string, but tools like curl can
    send raw bytes. Both end up as a latin-1 str here, so re-decode as UTF-8
    and fall back to the original when that is not valid.
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _decode_component(text: str) -> str:
    return _repair(unquote(text))


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _iso_date(value, fallback: str | None = None) -> str:
    if value in (None, ""):
        return fallback or date.today().isoformat()
    text = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ApiError("日期格式应为 YYYY-MM-DD")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ApiError(f"无效日期：{text}") from None
    return text


def _number(value, name: str, low: float, high: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ApiError(f"{name} 必须是数字") from None
    if out != out:
        raise ApiError(f"{name} 必须是数字")
    if not low <= out <= high:
        raise ApiError(f"{name} 应在 {low:g} 到 {high:g} 之间")
    return round(out, 1)


class App:
    """Business logic, independent of the HTTP plumbing."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- read -----------------------------------------------------------
    def recent_foods(self, entries: list[dict], limit: int = 8) -> list[dict]:
        """Foods logged most often in the last two weeks.

        Dieting means eating a lot of the same things, so one tap should be
        enough for the usual breakfast.
        """
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        counts: dict[str, int] = {}
        for entry in entries:
            food_id = entry.get("food_id")
            if food_id and str(entry.get("date", "")) >= cutoff:
                counts[food_id] = counts.get(food_id, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return [food_db.BY_ID[fid] for fid, _ in ranked if fid in food_db.BY_ID]

    def state(self, day: str) -> dict:
        data = self.store.snapshot()
        profile = health.normalise_profile(data["profile"])
        day_targets = health.targets(profile)
        day_entries = [e for e in data["entries"] if e.get("date") == day]
        return {
            "date": day,
            "today": date.today().isoformat(),
            "profile": profile,
            "targets": day_targets,
            "day": health.day_summary(day, day_entries, day_targets),
            "week": health.week_report(data["entries"], day_targets, day),
            "weight": health.weight_summary(data["weights"]),
            "recent": self.recent_foods(data["entries"]),
            "has_data": bool(data["entries"] or data["weights"] or data["profile"]),
            "food_stats": food_db.stats(),
            "meals": [{"key": k, "label": v} for k, v in health.MEALS],
            "categories": food_db.CATEGORIES,
            "activity_options": [
                {"key": k, "label": v["label"]} for k, v in health.ACTIVITY.items()
            ],
            "goal_options": [{"key": k, "label": v["label"]} for k, v in health.GOALS.items()],
        }

    # -- write ----------------------------------------------------------
    def save_profile(self, payload: dict) -> dict:
        profile = health.normalise_profile(payload)
        day = _iso_date(payload.get("date"))

        def apply(data: dict):
            data["profile"] = profile
            # Keep the weight log in sync with the profile weight so the
            # trend chart has a point even if the user never logged one.
            if profile["weight_kg"] > 0:
                rows = [w for w in data["weights"] if w.get("date") != day]
                rows.append({"date": day, "kg": profile["weight_kg"]})
                data["weights"] = rows

        self.store.mutate(apply)
        return self.state(day)

    def add_entry(self, payload: dict) -> dict:
        day = _iso_date(payload.get("date"))
        meal = payload.get("meal") or "snack"
        if meal not in health.MEAL_LABELS:
            raise ApiError(f"未知餐次：{meal}")

        entry = {"id": uuid.uuid4().hex[:12], "date": day, "meal": meal, "ts": time.time()}

        if payload.get("custom"):
            custom = payload["custom"]
            name = str(custom.get("name") or "").strip()[:40]
            if not name:
                raise ApiError("自定义食物需要填写名称")
            entry.update({
                "name": name,
                "category": str(custom.get("category") or "自定义")[:12],
                "food_id": None,
                "grams": _number(custom.get("grams") or 100, "份量", 1, 5000),
                "kcal": _number(custom.get("kcal") or 0, "热量", 0, 5000),
                "protein": _number(custom.get("protein") or 0, "蛋白质", 0, 300),
                "carb": _number(custom.get("carb") or 0, "碳水", 0, 500),
                "fat": _number(custom.get("fat") or 0, "脂肪", 0, 300),
            })
        else:
            food_id = str(payload.get("food_id") or "")
            food = food_db.BY_ID.get(food_id)
            if food is None:
                raise ApiError("找不到这个食物", HTTPStatus.NOT_FOUND)
            grams = _number(payload.get("grams") or 100, "份量", 1, 5000)
            macro = food_db.scale(food, grams)
            unit_label = payload.get("unit_label")
            entry.update({
                "name": food["name"],
                "category": food["category"],
                "food_id": food_id,
                "grams": grams,
                "unit_label": unit_label,
                **macro,
            })
            # Remember what one "bowl" weighs so the UI can show "1 碗" later.
            for unit in food["units"]:
                if unit_label and unit["label"] == unit_label:
                    entry["unit_grams"] = unit["grams"]
                    break

        self.store.mutate(lambda data: data["entries"].append(entry))
        return {"entry": entry, "state": self.state(day)}

    def delete_entry(self, entry_id: str) -> dict:
        removed: dict = {}

        def apply(data: dict):
            keep = []
            for item in data["entries"]:
                if item.get("id") == entry_id:
                    removed.update(item)
                else:
                    keep.append(item)
            data["entries"] = keep

        self.store.mutate(apply)
        if not removed:
            raise ApiError("这条记录不存在", HTTPStatus.NOT_FOUND)
        return self.state(removed.get("date") or date.today().isoformat())

    def save_weight(self, payload: dict) -> dict:
        day = _iso_date(payload.get("date"))
        kg = _number(payload.get("kg"), "体重", 30, 300)

        def apply(data: dict):
            data["weights"] = [w for w in data["weights"] if w.get("date") != day]
            data["weights"].append({"date": day, "kg": kg})
            data["weights"].sort(key=lambda w: w["date"])
            # Today's weight becomes the profile weight, which drives targets.
            if day == date.today().isoformat() or day >= max(
                (w["date"] for w in data["weights"]), default=day
            ):
                profile = health.normalise_profile(data["profile"])
                profile["weight_kg"] = kg
                data["profile"] = profile

        self.store.mutate(apply)
        return self.state(day)

    def delete_weight(self, day: str) -> dict:
        self.store.mutate(
            lambda data: data.update(
                {"weights": [w for w in data["weights"] if w.get("date") != day]}
            )
        )
        return self.state(day)

    def export_data(self) -> dict:
        data = self.store.snapshot()
        data["exported_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["app"] = "chizhang"
        return data

    def import_data(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or "entries" not in payload:
            raise ApiError("这不是 chizhang 的备份文件")
        entries = [e for e in payload.get("entries", []) if isinstance(e, dict)]
        weights = [w for w in payload.get("weights", []) if isinstance(w, dict)]
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        self.store.replace_all({
            "version": 1,
            "profile": profile,
            "entries": entries,
            "weights": weights,
        })
        return self.state(date.today().isoformat())

    def clear_day(self, day: str) -> dict:
        self.store.mutate(
            lambda data: data.update(
                {"entries": [e for e in data["entries"] if e.get("date") != day]}
            )
        )
        return self.state(day)


ROUTES: list[tuple[str, re.Pattern, str]] = [
    ("GET", re.compile(r"^/api/state$"), "state"),
    ("GET", re.compile(r"^/api/foods$"), "foods"),
    ("GET", re.compile(r"^/api/report$"), "report"),
    ("GET", re.compile(r"^/api/export$"), "export"),
    ("POST", re.compile(r"^/api/profile$"), "profile"),
    ("POST", re.compile(r"^/api/entries$"), "add_entry"),
    ("DELETE", re.compile(r"^/api/entries/(?P<id>[0-9a-f]+)$"), "delete_entry"),
    ("POST", re.compile(r"^/api/weights$"), "weight"),
    ("DELETE", re.compile(r"^/api/weights/(?P<day>\d{4}-\d{2}-\d{2})$"), "delete_weight"),
    ("POST", re.compile(r"^/api/import$"), "import"),
    ("DELETE", re.compile(r"^/api/day/(?P<day>\d{4}-\d{2}-\d{2})$"), "clear_day"),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "chizhang"
    app: App  # injected on the server instance
    quiet = False

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover
        if not self.quiet:
            super().log_message(fmt, *args)

    # -- helpers --------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("Content-Length 无效") from None
        if length > MAX_BODY:
            raise ApiError("请求太大", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError("请求体不是合法的 JSON") from None
        if not isinstance(payload, dict):
            raise ApiError("请求体应为 JSON 对象")
        return payload

    @property
    def app_ref(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    # -- verbs ----------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = _decode_component(parsed.path)
        query = {
            _decode_component(k): [_decode_component(v) for v in values]
            for k, values in parse_qs(parsed.query, keep_blank_values=True).items()
        }

        if not path.startswith("/api/"):
            self._serve_static(path)
            return

        for route_method, pattern, name in ROUTES:
            match = pattern.match(path)
            if not match:
                continue
            if route_method != method and not (method == "GET" and route_method == "GET"):
                continue
            try:
                self._json(self._invoke(name, match, query, method))
            except ApiError as err:
                self._json({"error": err.message}, err.status)
            except Exception as err:  # pragma: no cover - defensive
                self._json({"error": f"服务器内部错误：{err}"}, 500)
            return

        self._json({"error": f"没有这个接口：{path}"}, HTTPStatus.NOT_FOUND)

    def _invoke(self, name: str, match: re.Match, query: dict, method: str):
        app = self.app_ref
        groups = match.groupdict()

        if name == "state":
            return app.state(_iso_date(query.get("date", [None])[0]))
        if name == "foods":
            items = food_db.search(
                query.get("q", [""])[0],
                query.get("category", [""])[0],
                limit=max(1, min(100, int(query.get("limit", ["24"])[0] or 24))),
            )
            return {"items": items, "count": len(items)}
        if name == "report":
            day = _iso_date(query.get("date", [None])[0])
            days = max(1, min(90, int(query.get("days", ["7"])[0] or 7)))
            snapshot = app.state(day)
            return health.week_report(
                [e for e in app.store.snapshot()["entries"]], snapshot["targets"], day, days
            )
        if name == "export":
            return app.export_data()
        if name == "profile":
            return app.save_profile(self._read_json())
        if name == "add_entry":
            return app.add_entry(self._read_json())
        if name == "delete_entry":
            return app.delete_entry(groups["id"])
        if name == "weight":
            return app.save_weight(self._read_json())
        if name == "delete_weight":
            return app.delete_weight(groups["day"])
        if name == "import":
            return app.import_data(self._read_json())
        if name == "clear_day":
            return app.clear_day(groups["day"])
        raise ApiError("未实现的接口", HTTPStatus.NOT_IMPLEMENTED)

    # -- static ---------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            target = STATIC_DIR / "index.html"
        elif path.startswith("/assets/"):
            target = STATIC_DIR / path[len("/assets/"):]
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            resolved = target.resolve()
            resolved.relative_to(STATIC_DIR.resolve())
        except (ValueError, OSError):
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        if not resolved.is_file():
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        content_type = MIME_OVERRIDES.get(
            resolved.suffix.lower(),
            mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
        )
        body = resolved.read_bytes()
        self._send(200, body, content_type, cache="no-cache")


def make_server(host: str, port: int, store: Store, quiet: bool = True) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"quiet": quiet})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.app = App(store)  # type: ignore[attr-defined]
    return server
