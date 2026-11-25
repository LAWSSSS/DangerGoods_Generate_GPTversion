#!/usr/bin/env python3
"""本地操作界面。只在点击生成时调用 OpenAI。"""

from __future__ import annotations

import json
import mimetypes
import os
import random
# import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import yaml

import generate_openai as engine
from openai_image_models import COMPLEXITY_MODELS, QUALITY_BY_MODEL

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
UI = ROOT / "ui"
INPUT = ROOT / "input"
OUTPUT = PROJECT / "output"
CONFIG = ROOT / "config_openai.yaml"
SAVE_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES: list[subprocess.Popen] = []
HOST = "127.0.0.1"
PORT = 8765
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def catalog_rows() -> tuple[list[dict], list[dict]]:
    data = json.loads((ROOT / "profiles" / "object_profiles.json").read_text(encoding="utf-8"))
    objects = []
    for item in data["objects"]:
        policy = item["size_policy"]
        objects.append(
            {
                "id": item["id"],
                "name": item["name"],
                "category": item["category"],
                "reference": item["reference_filename"],
                "original": item.get("original_filename") or "",
                "reference_axis": policy["reference_axis"],
                "cross_name": policy["cross_name"],
                "length_is_overall": policy.get("length_is_overall", True),
                "dimensions": item.get("dimensions_mm") or {},
                "parts": item.get("parts_mm") or {},
                "render_scale": policy["render_scale"],
                "prompt_text": item.get("prompt_text") or "",
                "prompt_default": engine.prompt_body({**item, "prompt_text": ""}),
            }
        )
    backgrounds = []
    for path in sorted((INPUT / "backgrounds").iterdir()):
        if path.suffix.lower() in IMAGE_SUFFIXES:
            backgrounds.append({"name": path.name})
    return objects, backgrounds


def catalog_payload() -> dict:
    objects, backgrounds = catalog_rows()
    return {
        "objects": objects,
        "backgrounds": backgrounds,
        "models": [
            {"id": model, "qualities": list(qualities), "complexity": model in COMPLEXITY_MODELS}
            for model, qualities in QUALITY_BY_MODEL.items()
        ],
    }


def index_objects() -> dict[str, str]:
    objects, _backgrounds = catalog_rows()
    found: dict[str, str] = {}
    for item in objects:
        found[item["id"]] = item["id"]
        found[item["reference"]] = item["id"]
        if item["original"]:
            found[item["original"]] = item["id"]
    return found


def register_process(proc: subprocess.Popen) -> None:
    with PROCESS_LOCK:
        ACTIVE_PROCESSES.append(proc)


def forget_process(proc: subprocess.Popen) -> None:
    with PROCESS_LOCK:
        if proc in ACTIVE_PROCESSES:
            ACTIVE_PROCESSES.remove(proc)


def terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=3)
            return
        except (subprocess.TimeoutExpired, OSError):
            proc.terminate()
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def stop_processes() -> None:
    with PROCESS_LOCK:
        running = list(ACTIVE_PROCESSES)
    for proc in running:
        terminate_process(proc)


def popen_command(command: list[str]) -> subprocess.Popen:
    kwargs: dict = {"cwd": ROOT, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **kwargs)
    register_process(proc)
    return proc


def open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def safe_file(root: Path, relative: str) -> Path | None:
    path = (root / unquote(relative)).resolve()
    if root not in path.parents and path != root:
        return None
    if not path.is_file():
        return None
    return path


def reveal(kind: str, folder: str = "") -> None:
    if kind == "backgrounds":
        target = INPUT / "backgrounds"
    elif kind == "objects":
        target = INPUT / "objects" / "six_views"
    elif kind == "output":
        target = OUTPUT / "results" / folder if folder else OUTPUT / "results"
        target = target.resolve()
        root = (OUTPUT / "results").resolve()
        if target != root and root not in target.parents:
            raise ValueError("输出目录无效")
    else:
        raise ValueError("未知文件夹")
    if not target.exists():
        raise ValueError("文件夹不存在")
    open_folder(target)


def draw_random(background_count: int, object_count: int, seed: str) -> dict:
    objects, backgrounds = catalog_rows()
    object_ids = [item["id"] for item in objects]
    names = [item["name"] for item in backgrounds]
    if background_count < 1 or object_count < 1:
        raise ValueError("背景数量和物品数量都要至少为 1")
    if background_count > len(names):
        raise ValueError(f"背景库只有 {len(names)} 张")
    if object_count > len(object_ids):
        raise ValueError(f"可生成物品只有 {len(object_ids)} 个")
    rng = random.SystemRandom() if seed == "" else random.Random(seed)
    picked_backgrounds = rng.sample(names, background_count)
    picked_objects = rng.sample(object_ids, object_count)
    pairs = [
        {"background": background, "object": object_id}
        for background in picked_backgrounds
        for object_id in picked_objects
    ]
    return {
        "backgrounds": picked_backgrounds,
        "objects": picked_objects,
        "pairs": pairs,
        "count": len(pairs),
        "seeded": seed != "",
    }


def parse_request(handler: BaseHTTPRequestHandler) -> tuple[dict, list[tuple[str, str, bytes]]]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length)
    ctype = handler.headers.get("Content-Type", "")
    if ctype.startswith("application/json"):
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("请求格式不对")
        return body, []
    if "multipart/form-data" not in ctype or "boundary=" not in ctype:
        raise ValueError("请求格式不对")
    marker = ctype.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    boundary = ("--" + marker).encode()
    fields: dict[str, list[str]] = {}
    files: list[tuple[str, str, bytes]] = []
    for part in raw.split(boundary):
        if part in {b"", b"--", b"--\r\n"} or not part.startswith(b"\r\n"):
            continue
        chunk = part[2:]
        if chunk.endswith(b"--\r\n"):
            chunk = chunk[:-4]
        elif chunk.endswith(b"--"):
            chunk = chunk[:-2]
        if b"\r\n\r\n" not in chunk:
            continue
        head, content = chunk.split(b"\r\n\r\n", 1)
        if content.endswith(b"\r\n"):
            content = content[:-2]
        header = head.decode("utf-8", "replace")
        disposition = next((line for line in header.split("\r\n") if line.lower().startswith("content-disposition")), "")
        name = _disposition_value(disposition, "name")
        filename = _disposition_value(disposition, "filename")
        if not name:
            continue
        if filename:
            files.append((name, Path(filename).name, content))
        else:
            fields.setdefault(name, []).append(content.decode("utf-8"))
    body = {key: values if len(values) > 1 else values[0] for key, values in fields.items()}
    return body, files


def _disposition_value(header: str, key: str) -> str:
    token = key + "="
    for piece in header.split(";"):
        piece = piece.strip()
        if piece.startswith(token):
            return piece[len(token) :].strip().strip('"')
    return ""


def as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    text = str(value).strip()
    return [text] if text else []


DEFAULT_BUDGET_USD = 5.0
DEFAULT_PER_IMAGE_USD = 0.05


def money_field(raw, default: float, label: str) -> float:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{label}必须是数字") from None
    if value <= 0:
        raise ValueError(f"{label}必须大于 0")
    return value


def resolve_job(body: dict, files: list[tuple[str, str, bytes]]) -> tuple[list[str], list[str], float, float | None]:
    mode = str(body.get("mode") or "pick")
    budget = money_field(body.get("budget_usd"), DEFAULT_BUDGET_USD, "总预算")
    per_image = money_field(body.get("per_image_usd"), DEFAULT_PER_IMAGE_USD, "单张上限")
    if per_image > budget:
        raise ValueError("单张上限不能超过总预算")
    if mode == "random":
        picked = draw_random(int(body.get("background_count")), int(body.get("object_count")), str(body.get("seed") or ""))
        return picked["backgrounds"], picked["objects"], budget, per_image
    if mode != "pick":
        raise ValueError("未知生成方式")
    known = index_objects()
    backgrounds = as_list(body.get("backgrounds"))
    objects = []
    for name in as_list(body.get("objects")):
        objects.append(known.get(name, name))
    for field, filename, content in files:
        if field == "background_file":
            if filename not in backgrounds:
                backgrounds.append(filename)
        elif field == "object_file":
            if filename not in known:
                raise ValueError(f"无法按尺寸目录识别物品文件：{filename}")
            if known[filename] not in objects:
                objects.append(known[filename])
        else:
            continue
        if field == "background_file" and not (INPUT / "backgrounds" / filename).is_file():
            if Path(filename).suffix.lower() not in IMAGE_SUFFIXES or not content:
                raise ValueError(f"背景文件无效：{filename}")
    if not backgrounds or not objects:
        raise ValueError("请至少选择一张背景和一个物品")
    return backgrounds, objects, budget, per_image


def materialize_backgrounds(names: list[str], files: list[tuple[str, str, bytes]], directory: Path) -> list[str]:
    uploaded = {filename: content for field, filename, content in files if field == "background_file"}
    ready = []
    for name in names:
        content = uploaded.get(Path(name).name)
        if content is not None:
            target = directory / Path(name).name
            target.write_bytes(content)
            ready.append(str(target))
            continue
        if (INPUT / "backgrounds" / name).is_file():
            ready.append(name)
            continue
        raise ValueError(f"找不到背景：{name}")
    return ready


def run_generation(body: dict, files: list[tuple[str, str, bytes]]) -> dict:
    model = str(body.get("model") or "gpt-image-2")
    quality = str(body.get("quality") or "low")
    if model not in QUALITY_BY_MODEL or quality not in QUALITY_BY_MODEL[model]:
        raise ValueError("这个模型不接受所选画质")
    backgrounds, objects, budget, per_image = resolve_job(body, files)
    planned = len(backgrounds) * len(objects)
    before = set((OUTPUT / "results").glob("*")) if (OUTPUT / "results").exists() else set()
    with tempfile.TemporaryDirectory(prefix="openai-ui-") as temporary:
        background_args = materialize_backgrounds(backgrounds, files, Path(temporary))
        command = [
            sys.executable,
            str(ROOT / "generate_openai.py"),
            "--model",
            model,
            "--quality",
            quality,
            "--budget-usd",
            str(budget),
            "--per-image-usd",
            str(per_image),
            "--objects",
            *objects,
            "--backgrounds",
            *background_args,
        ]
        complexity = body.get("complexity")
        if complexity and model in COMPLEXITY_MODELS:
            command.extend(["--complexity", str(complexity)])
        timeout = max(300, 260 * planned)
        engine.write_progress(
            {
                "running": True,
                "folder": "",
                "total": planned,
                "done": 0,
                "ok": 0,
                "failed": 0,
                "spent_usd": 0,
                "budget_usd": budget,
                "current": None,
            }
        )
        proc = popen_command(command)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process(proc)
            raise
        finally:
            forget_process(proc)
            settle_progress()
    try:
        record = newest_run(before)
    except RuntimeError:
        if proc.returncode != 0:
            detail = stderr or stdout or "生成失败"
            raise RuntimeError(engine.explain_error(detail))
        raise
    if proc.returncode != 0 and not any(item.get("status") == "ok" for item in record["items"]):
        record["error"] = next(
            (item.get("error_reason") or item.get("error") for item in record["items"] if item.get("status") != "ok"),
            "生成失败",
        )
    record["selection"] = {"backgrounds": backgrounds, "objects": objects, "count": planned}
    return record


def settle_progress() -> None:
    path = OUTPUT / "progress.json"
    data: dict = {"running": False, "total": 0, "done": 0, "ok": 0, "failed": 0, "spent_usd": 0, "current": None}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    data["running"] = False
    data["current"] = None
    engine.write_progress(data)


def progress_status() -> dict:
    path = OUTPUT / "progress.json"
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    with PROCESS_LOCK:
        running = any(proc.poll() is None for proc in ACTIVE_PROCESSES)
    if not running:
        data["running"] = False
        data["current"] = None
    current = data.get("current") if data.get("running") else None
    return {
        "running": bool(data.get("running")),
        "folder": data.get("folder") or "",
        "total": int(data.get("total") or 0),
        "done": int(data.get("done") or 0),
        "ok": int(data.get("ok") or 0),
        "failed": int(data.get("failed") or 0),
        "spent_usd": data.get("spent_usd") or 0,
        "budget_usd": data.get("budget_usd"),
        "current": current if isinstance(current, dict) else None,
    }


def newest_run(started_dirs: set[Path]) -> dict:
    candidates = [path for path in (OUTPUT / "results").glob("*/metadata.json") if path.parent not in started_dirs]
    if not candidates:
        raise RuntimeError("没有找到这次的生成记录")
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    metadata = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for item in metadata.get("items", []):
        output = item.get("output") or {}
        source = item.get("input") or {}
        items.append(
            {
                "object_id": source.get("object_id"),
                "object_name": source.get("object_name"),
                "background": source.get("background_source"),
                "status": output.get("status"),
                "cost_usd": output.get("cost_usd"),
                "error": output.get("error"),
                "error_reason": recorded_reason(output),
                "file": output.get("generated_image"),
                "image_url": media_url(path.parent.name, output.get("generated_image")),
            }
        )
    return {
        "folder": path.parent.name,
        "spent_usd": metadata.get("spent_usd"),
        "budget_usd": metadata.get("budget_usd"),
        "per_image_usd": metadata.get("per_image_usd"),
        "items": items,
    }


def recorded_reason(output: dict) -> str:
    if output.get("status") == "ok":
        return ""
    if output.get("error_reason"):
        return str(output["error_reason"])
    if output.get("status") == "stopped_budget":
        return "剩余预算不够再生成一张。"
    if output.get("error"):
        return engine.explain_error(output.get("error"))
    return ""


def media_url(folder: str, relative: str | None) -> str:
    if not relative:
        return ""
    return "/media/output/" + quote(folder) + "/" + quote(relative, safe="/")


def preview_url(folder: str, items: list) -> str:
    for item in items:
        output = item.get("output") or {}
        relative = output.get("generated_image")
        if output.get("status") == "ok" and relative:
            return media_url(folder, relative)
    return ""


def list_runs() -> list[dict]:
    root = OUTPUT / "results"
    if not root.is_dir():
        return []
    runs = []
    for path in root.glob("*/metadata.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        folder = path.parent.name
        items = data.get("items") or []
        runs.append(
            {
                "folder": folder,
                "date": str(data.get("date") or folder[:10]),
                "spent_usd": data.get("spent_usd"),
                "budget_usd": data.get("budget_usd"),
                "per_image_usd": data.get("per_image_usd"),
                "count": len(items),
                "ok": sum(1 for item in items if (item.get("output") or {}).get("status") == "ok"),
                "error_reason": next((recorded_reason(item.get("output") or {}) for item in items if recorded_reason(item.get("output") or {})), ""),
                "preview": preview_url(folder, items),
                "mtime": path.stat().st_mtime,
            }
        )
    runs.sort(key=lambda item: item["mtime"], reverse=True)
    return runs


def run_detail(folder: str) -> dict:
    if not folder or folder in {".", ".."} or "/" in folder or "\\" in folder:
        raise ValueError("结果不存在")
    path = (OUTPUT / "results" / folder / "metadata.json").resolve()
    root = (OUTPUT / "results").resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("结果不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("items") or []:
        output = item.get("output") or {}
        reason = recorded_reason(output)
        if reason:
            output["error_reason"] = reason
            item["output"] = output
        output.pop("object_position", None)
        output.pop("annotated_image", None)
        item["output"] = output
        relative = output.get("generated_image")
        if relative:
            item["image_url"] = media_url(folder, relative)
    data["folder"] = folder
    return data


# EXPORT_DIR_NAME = "导出生成图"
# 
# 
# def export_state_path() -> Path:
#     return OUTPUT / "export_state.json"
# 
# 
# def load_export_state() -> dict:
#     path = export_state_path()
#     if not path.is_file():
#         return {"destination": EXPORT_DIR_NAME, "copied": {}}
#     try:
#         data = json.loads(path.read_text(encoding="utf-8"))
#     except (OSError, json.JSONDecodeError):
#         return {"destination": EXPORT_DIR_NAME, "copied": {}}
#     destination = str(data.get("destination") or EXPORT_DIR_NAME)
#     if not destination or destination in {".", "..", "results"} or "/" in destination or "\\" in destination:
#         destination = EXPORT_DIR_NAME
#     copied = data.get("copied") if isinstance(data.get("copied"), dict) else {}
#     return {"destination": destination, "copied": {str(key): str(value) for key, value in copied.items()}}
# 
# 
# def export_status() -> dict:
#     state = load_export_state()
#     folder = OUTPUT / state["destination"]
#     present = sum(1 for name in state["copied"].values() if (folder / Path(name).name).is_file())
#     return {"destination": f"output/{state['destination']}", "count": present, "recorded": bool(export_state_path().is_file())}
# 
# 
# def export_generated() -> dict:
#     state = load_export_state()
#     destination = state["destination"]
#     folder = OUTPUT / destination
#     folder.mkdir(parents=True, exist_ok=True)
#     copied = dict(state["copied"])
#     added = 0
#     skipped = 0
#     root = OUTPUT / "results"
#     if root.is_dir():
#         for meta_path in sorted(root.glob("*/metadata.json")):
#             try:
#                 data = json.loads(meta_path.read_text(encoding="utf-8"))
#             except (OSError, json.JSONDecodeError):
#                 continue
#             run_dir = meta_path.parent.resolve()
#             run_name = meta_path.parent.name
#             for item in data.get("items") or []:
#                 output = item.get("output") or {}
#                 relative = output.get("generated_image")
#                 if output.get("status") != "ok" or not relative:
#                     continue
#                 source = (meta_path.parent / relative).resolve()
#                 if run_dir not in source.parents or not source.is_file():
#                     continue
#                 key = f"{run_name}/{relative}"
#                 filename = Path(str(copied.get(key) or f"{run_name}_{source.name}")).name
#                 target = folder / filename
#                 if key in copied and target.is_file():
#                     skipped += 1
#                     continue
#                 if target.exists():
#                     stem, suffix = target.stem, target.suffix
#                     number = 2
#                     while (folder / f"{stem}_{number}{suffix}").exists():
#                         number += 1
#                     filename = f"{stem}_{number}{suffix}"
#                     target = folder / filename
#                 shutil.copy2(source, target)
#                 copied[key] = filename
#                 added += 1
#     export_state_path().write_text(
#         json.dumps({"destination": destination, "copied": copied}, ensure_ascii=False, indent=2) + "\n",
#         encoding="utf-8",
#     )
#     return {"destination": f"output/{destination}", "added": added, "skipped": skipped, "count": len(copied)}


def output_image(relative: str) -> Path | None:
    folder, separator, rest = unquote(relative).partition("/")
    if not separator or folder in {".", ".."} or "/" in folder:
        return None
    base = (OUTPUT / "results" / folder).resolve()
    root = (OUTPUT / "results").resolve()
    if root not in base.parents:
        return None
    return safe_file(base, rest)


def config_public() -> dict:
    settings = engine.load_settings(CONFIG) if CONFIG.is_file() else {}
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    file_key = str(settings.get("api_key") or "").strip()
    active = env_key or file_key
    hint = ""
    if len(active) >= 4:
        hint = "…" + active[-4:]
    elif active:
        hint = "已保存"
    return {
        "has_key": bool(active),
        "key_hint": hint,
        "from_environment": bool(env_key),
        "base_url": str(settings.get("base_url") or "https://api.openai.com/v1"),
    }


def save_config(body: dict) -> dict:
    if CONFIG.is_file():
        settings = engine.load_settings(CONFIG)
    else:
        settings = engine.load_settings(ROOT / "config_openai.yaml.example")
    base = str(body.get("base_url") or "").strip() or str(settings.get("base_url") or "https://api.openai.com/v1")
    if not base.startswith("https://") or any(ch.isspace() for ch in base):
        raise ValueError("API 地址必须是 HTTPS")
    key = str(body.get("api_key") or "").strip()
    if any(ch in key for ch in "\r\n"):
        raise ValueError("API Key 无效")
    if key:
        settings["api_key"] = key
    elif not str(settings.get("api_key") or "").strip():
        raise ValueError("请填写 API Key")
    settings["base_url"] = base.rstrip("/")
    CONFIG.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        CONFIG.chmod(0o600)
    except OSError:
        pass
    return config_public()


def _positive(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label}必须是大于 0 的数")
    number = float(value)
    return int(number) if number.is_integer() else number


def update_object(object_id: str, body: dict) -> dict:
    seeds_path = ROOT / "profiles" / "object_profile_seeds.json"
    with SAVE_LOCK:
        seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
        seed = next((item for item in seeds["objects"] if item["id"] == object_id), None)
        if seed is None:
            raise ValueError("物品不存在")
        dims = body.get("dimensions")
        if not isinstance(dims, dict) or set(dims) != set(seed["dimensions"]):
            raise ValueError("尺寸名称需要和目录一致")
        seed["dimensions"] = {key: _positive(value, key) for key, value in dims.items()}
        parts = body.get("parts", seed.get("parts") or {})
        if not isinstance(parts, dict) or set(parts) != set(seed.get("parts") or {}):
            raise ValueError("零件名称需要和目录一致")
        seed["parts"] = {key: _positive(value, key) for key, value in parts.items()}
        seed["render_scale"] = _positive(body.get("render_scale", seed.get("render_scale", 1)), "缩放指数")
        axis = seed["reference_axis"]
        seed["primary_range"] = [seed["dimensions"][axis], seed["dimensions"][axis]]
        profiles = json.loads((ROOT / "profiles" / "object_profiles.json").read_text(encoding="utf-8"))
        current = next(item for item in profiles["objects"] if item["id"] == object_id)
        default_prompt = engine.prompt_body({**current, "prompt_text": ""})
        text = str(body.get("prompt_text") or "").strip()
        if text and text != default_prompt:
            seed["prompt_text"] = text
        else:
            seed.pop("prompt_text", None)
        seeds_path.write_text(json.dumps(seeds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        proc = popen_command([sys.executable, str(ROOT / "tools" / "build_object_profiles.py")])
        try:
            stdout, stderr = proc.communicate()
        finally:
            forget_process(proc)
        if proc.returncode != 0:
            detail = (stderr or stdout or "目录没有写上").strip().splitlines()
            raise RuntimeError(detail[-1][:500] if detail else "目录没有写上")
    updated = next(item for item in catalog_rows()[0] if item["id"] == object_id)
    return updated


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("请求格式不对")
        return body

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.send_file(UI / "index.html")
            return
        if parsed.path.startswith("/ui/"):
            target = safe_file(UI, parsed.path.removeprefix("/ui/"))
            if target:
                self.send_file(target)
                return
        if parsed.path == "/api/catalog":
            self.send_json(catalog_payload())
            return
        if parsed.path == "/api/config":
            self.send_json(config_public())
            return
        if parsed.path == "/api/progress":
            self.send_json(progress_status())
            return
#         if parsed.path == "/api/export":
#             self.send_json(export_status())
#             return
        if parsed.path == "/api/runs":
            self.send_json({"runs": list_runs()})
            return
        if parsed.path.startswith("/api/runs/"):
            try:
                self.send_json(run_detail(unquote(parsed.path.removeprefix("/api/runs/"))))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 404)
            return
        if parsed.path.startswith("/media/output/"):
            target = output_image(parsed.path.removeprefix("/media/output/"))
            if target:
                self.send_file(target)
                return
        self.send_json({"error": "找不到页面"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
#             if path == "/api/export":
#                 self.send_json(export_generated())
#                 return
            if path == "/api/reveal":
                body = self.read_json()
                reveal(str(body.get("kind") or ""), str(body.get("folder") or ""))
                self.send_json({"ok": True})
                return
            if path == "/api/plan":
                body = self.read_json()
                self.send_json(draw_random(int(body.get("background_count")), int(body.get("object_count")), str(body.get("seed") or "")))
                return
            if path == "/api/generate":
                body, files = parse_request(self)
                self.send_json(run_generation(body, files))
                return
            if path == "/api/config":
                self.send_json(save_config(self.read_json()))
                return
            if path.startswith("/api/objects/"):
                self.send_json(update_object(unquote(path.removeprefix("/api/objects/")), self.read_json()))
                return
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.send_json({"error": str(exc) or "请求无效"}, 400)
            return
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            self.send_json({"error": str(exc)[:500]}, 502)
            return
        self.send_json({"error": "找不到页面"}, 404)


def main() -> None:
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    exit_code = 0

    def stop(signum: int, _frame) -> None:
        nonlocal exit_code
        if exit_code:
            os._exit(128 + signum)
        exit_code = 128 + signum
        print("正在退出", flush=True)
        stop_processes()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"界面：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        stop_processes()
        server.server_close()
    os._exit(exit_code)


if __name__ == "__main__":
    main()
