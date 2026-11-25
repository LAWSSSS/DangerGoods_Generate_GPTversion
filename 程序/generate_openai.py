#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI Images Playground 参数的废钢异物编辑。支持现行图像模型和 complexity。"""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
import yaml
from PIL import Image, ImageOps

from openai_image_models import (
    COMPLEXITY_MODELS,
    IMAGE_EDIT_CALLS,
    QUALITY_BY_MODEL,
    ImageEditCall,
    call_image_edit,
)

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DEFAULT_OBJECTS = ("S01", "S20", "S26")
DEFAULT_BACKGROUNDS = (
    "2026_08_06_36_1_zhongfei1_95_zhongfei2_5_19.jpg",
    "2026_08_06_36_6_jianliao1_50_houjian_18#33_zhongfei3_15_zhongfei2_8#33_jianliao2_8#33_10.jpg",
)
# Playground 当前可选的别名。日期快照和已停用的 gpt-image-1 / 1-mini / 1.5 不在列表里。
PLAYGROUND_MODELS = (
    "gpt-image-2",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.6-flare",
    "gpt-image-2.6-sunburst",
)
QUALITIES = ("auto", "low", "medium", "high", "xhigh", "max")
COMPLEXITIES = ("low", "medium", "high")
BACKGROUNDS = ("auto", "opaque", "transparent")
OUTPUT_FORMATS = ("png", "jpeg", "webp")
MODERATIONS = ("auto", "low")
# 每百万 token：文本输入、文本输出、图像输入、图像输出。2/2.5/2.6 用同一档。
TOKEN_RATES_PER_MILLION = {
    "gpt-image-1-mini": (2.0, 0.0, 2.5, 8.0),
    "gpt-image-1.5": (5.0, 10.0, 8.0, 32.0),
    "chatgpt-image-latest": (5.0, 10.0, 8.0, 32.0),
    "gpt-image-1": (5.0, 0.0, 10.0, 40.0),
}
# 横图 1536×1024 官方输出费从低到高的前九个组合。gpt-image-1 已停用，仍保留在名单里以便记录拒绝结果。
COST_RANK = (
    ("gpt-image-2", "low"),
    ("gpt-image-1-mini", "low"),
    ("gpt-image-1.5", "low"),
    ("gpt-image-1-mini", "medium"),
    ("gpt-image-1", "low"),
    ("gpt-image-2", "medium"),
    ("gpt-image-1.5", "medium"),
    ("gpt-image-1-mini", "high"),
    ("gpt-image-1", "medium"),
)
CALL_RESERVE_USD = 0.40
HOPPER_WIDTH_MM = 2500


def explain_error(text: str | None) -> str:
    """把接口或网络报错收成可写入 metadata 的原因。"""
    raw = str(text or "").strip()
    lowered = raw.lower()
    if "safety" in lowered or "moderation_blocked" in lowered:
        if "violence" in lowered:
            return "安全系统拒绝：判定为暴力内容，这一张没有生成。"
        return "安全系统拒绝了这一张，没有生成图片。"
    if "ssl" in lowered or "eof occurred" in lowered or "max retries" in lowered:
        return "连接 OpenAI 中断。"
    if "timeout" in lowered or "timed out" in lowered:
        return "等待 OpenAI 超时。"
    if not raw:
        return "生成失败"
    return raw.splitlines()[-1][:300]


def reserve_for(quality: str) -> float:
    """low 画质实测约 0.02 美元，预留 0.05；更高画质仍按原档预留。"""
    if quality == "low":
        return 0.05
    if quality == "medium":
        return 0.20
    return CALL_RESERVE_USD


def load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit("config_openai.yaml 必须是键值映射")
    return raw


def resolve_api_key(settings: dict[str, Any]) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip() or str(settings.get("api_key") or "").strip()
    if not key:
        raise SystemExit(
            "未找到 OpenAI API Key。请导出 OPENAI_API_KEY，或把密钥写入已忽略的 config_openai.yaml 的 api_key。"
        )
    return key


_STOP_REQUESTED = False


def request_stop(signum: int, _frame: Any) -> None:
    """第一次中断留出写记录的时间，第二次直接退出。"""
    global _STOP_REQUESTED
    if _STOP_REQUESTED:
        os._exit(128 + signum)
    _STOP_REQUESTED = True
    raise KeyboardInterrupt


def jpeg_data_url(path: Path, max_side: int, quality: int = 90) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    scale = min(1.0, max_side / max(image.size))
    if scale < 1:
        width = max(16, int(image.width * scale) // 16 * 16)
        height = max(16, int(image.height * scale) // 16 * 16)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def rates_for(model: str) -> tuple[float, float, float, float]:
    text_in, text_out, image_in, image_out = TOKEN_RATES_PER_MILLION.get(model, (5.0, 0.0, 8.0, 30.0))
    million = 1_000_000
    return text_in / million, text_out / million, image_in / million, image_out / million


def usage_cost_usd(usage: dict[str, Any] | None, model: str) -> float | None:
    if not usage:
        return None
    text_rate, text_out_rate, image_rate, image_out_rate = rates_for(model)
    details = usage.get("input_tokens_details") or {}
    image_in = details.get("image_tokens")
    text_in = details.get("text_tokens")
    if image_in is None and text_in is None:
        text_in = int(usage.get("input_tokens") or 0)
        image_in = 0
    out_details = usage.get("output_tokens_details") or {}
    if out_details.get("image_tokens") is not None:
        image_out = int(out_details["image_tokens"])
        text_out = int(out_details.get("text_tokens") or 0)
    else:
        image_out = int(usage.get("output_tokens") or 0)
        text_out = 0
    return (
        int(text_in or 0) * text_rate
        + int(text_out) * text_out_rate
        + int(image_in or 0) * image_rate
        + image_out * image_out_rate
    )


def _column_contrast(image: Image.Image) -> list[float]:
    """每列与邻列的色差。斗壁通常更平整，废钢堆的色差更高。"""
    width, height = image.size
    pixels = image.load()
    top = int(height * 0.12)
    bottom = max(top + 1, int(height * 0.88))
    contrast = []
    for x in range(width):
        previous = max(0, x - 1)
        total = 0.0
        for y in range(top, bottom):
            red, green, blue = pixels[x, y]
            old_red, old_green, old_blue = pixels[previous, y]
            total += abs(red - old_red) + abs(green - old_green) + abs(blue - old_blue)
        contrast.append(total / (bottom - top))
    smoothed = []
    for index, _value in enumerate(contrast):
        window = contrast[max(0, index - 2) : index + 3]
        smoothed.append(sum(window) / len(window))
    return smoothed


def _inner_wall(contrast: list[float], start: int, step: int, limit: int, threshold: float) -> int | None:
    """从画面边缘向内走，色差持续升高处视为斗壁内沿。找不到则返回 None。"""
    position = start
    high_run = 0
    last_wall = start
    moved = 0
    while 0 <= position < len(contrast) and moved < limit:
        if contrast[position] >= threshold:
            high_run += 1
            if high_run >= 8:
                return last_wall
        else:
            high_run = 0
            last_wall = position
        position += step
        moved += 1
    return None


def hopper_span(path: Path) -> dict[str, Any]:
    """估计斗内区域占画面宽度的比例。两侧斗壁贴边时为 1。"""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    width = 480
    height = max(1, round(image.height * width / image.width))
    image = image.resize((width, height), Image.Resampling.BOX)
    contrast = _column_contrast(image)
    center = contrast[width // 4 : width * 3 // 4]
    scrap_level = sorted(center)[len(center) // 2]
    threshold = scrap_level * 0.55
    search = int(width * 0.45)
    left = _inner_wall(contrast, 0, 1, search, threshold)
    right = _inner_wall(contrast, width - 1, -1, search, threshold)
    left_x = 0 if left is None else left
    right_x = width - 1 if right is None else right
    if right_x <= left_x:
        left_x, right_x = 0, width - 1
    aligned = left_x <= width * 0.04 and width - 1 - right_x <= width * 0.04
    span = 1.0 if aligned else (right_x - left_x) / width
    span = min(1.0, max(0.5, span))
    return {
        "hopper_width_mm": HOPPER_WIDTH_MM,
        "hopper_span": round(span, 4),
        "left_fraction": round(left_x / width, 4),
        "right_fraction": round((right_x + 1) / width, 4),
        "aligned": aligned,
    }


def frame_scale(item: dict[str, Any], background: Path) -> dict[str, Any]:
    """对准时画面宽度等于 2500 mm；斗壁内收时物品占整幅画面的比例按跨度缩小。"""
    scale = hopper_span(background)
    size = object_size_mm(item)
    span = scale["hopper_span"]
    length = size.get("reference_length_mm")
    cross = size.get("cross_mm")
    render_scale = float((item.get("size_policy") or {}).get("render_scale", 1) or 1)
    scale["render_scale"] = render_scale
    scale["length_axis"] = size.get("reference_axis")
    scale["length_mm"] = length
    length_fraction = None if length is None else length / HOPPER_WIDTH_MM * span
    cross_fraction = None if cross is None else cross / HOPPER_WIDTH_MM * span
    scale["length_fraction_before_scale"] = None if length_fraction is None else round(length_fraction, 4)
    scale["cross_fraction_before_scale"] = None if cross_fraction is None else round(cross_fraction, 4)
    scale["length_fraction"] = None if length_fraction is None else round(length_fraction * render_scale, 4)
    scale["cross_name"] = size.get("cross_name")
    scale["cross_mm"] = cross
    scale["cross_fraction"] = None if cross_fraction is None else round(cross_fraction * render_scale, 4)
    return scale


def object_size_mm(item: dict[str, Any]) -> dict[str, Any]:
    """物品自身的毫米尺寸。画面占比由 frame_scale 另行计算。"""
    policy = item.get("size_policy") or {}
    return {
        "id": item["id"],
        "name": item["name"],
        "dimensions_mm": item.get("dimensions_mm") or {},
        "parts_mm": item.get("parts_mm") or {},
        "dimension_ranges_mm": item.get("dimension_ranges_mm") or {},
        "reference_axis": policy.get("reference_axis"),
        "reference_length_mm": policy.get("reference_length_mm"),
        "length_is_overall": policy.get("length_is_overall", True),
        "cross_name": policy.get("cross_name"),
        "cross_mm": policy.get("cross_mm"),
    }


def _percent(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _mm(value: Any) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


DIM_EN = {
    "上安装套宽": "upper eye width",
    "上安装孔直径": "upper eye bore",
    "下安装套宽": "lower eye width",
    "下安装孔直径": "lower eye bore",
    "两耳中心距": "eye-center distance",
    "主气室外径": "main air-sleeve OD",
    "产品宽": "width",
    "产品长": "length",
    "产品高": "height",
    "侧手柄长": "side handle length",
    "六视图标称通径": "marked bore",
    "单瓶标称高": "nominal bottle height",
    "压力表直径": "gauge diameter",
    "参考图示高度": "shown height",
    "可见标称通径": "marked bore",
    "后凸台直径": "rear boss diameter",
    "后凸台突出": "rear boss projection",
    "含上下阀总高": "height with both valves",
    "含喷盖总高": "height with cap",
    "含手轮总高": "height with handwheel",
    "含手轮支脚总高": "height with handwheel and feet",
    "含盖总高": "height with lid",
    "含盖成品高": "height with cap",
    "含阀总高": "height with valve",
    "含阀整机总高": "overall height with valve",
    "图示状态总高": "shown overall height",
    "外部直径": "outside diameter",
    "外露杆直径": "exposed rod diameter",
    "外露短杆直径": "exposed short-rod diameter",
    "安装中心距": "mounting-center distance",
    "安装套宽": "eye width",
    "安装孔中心距": "eye-center distance",
    "安装孔直径": "eye bore",
    "安装孔距": "mounting-hole span",
    "底座宽": "base width",
    "底座总长": "base length",
    "底座长": "base length",
    "底脚厚": "foot thickness",
    "座体轴向宽": "housing axial width",
    "弹簧外径": "spring OD",
    "总高": "overall height",
    "成品单瓶高": "finished bottle height",
    "成品外径": "finished OD",
    "成品总高": "finished height",
    "手轮左缘至侧法兰端面": "handwheel edge to side flange",
    "手轮直径": "handwheel diameter",
    "托盘最低高": "lowest pad height",
    "托盘直径": "pad diameter",
    "接口法兰外径": "port flange OD",
    "接口端面跨度": "port-face span",
    "推算总长": "derived length",
    "握把宽": "handle width",
    "支脚高": "foot height",
    "无载安装孔中心距": "unloaded eye-center distance",
    "最大外径": "max OD",
    "最小安装孔中心距": "minimum eye-center distance",
    "本体图示高": "shown body height",
    "杆端耳厚": "rod-eye thickness",
    "杆端耳外径": "rod-eye OD",
    "标称高度": "nominal height",
    "气室外径": "air-sleeve OD",
    "气瓶标称高": "nominal cylinder height",
    "法兰外径": "flange OD",
    "法兰端面跨度": "flange-face span",
    "活塞杆直径": "piston-rod diameter",
    "瓶体外径": "body OD",
    "瓶帽高": "cap height",
    "瓶肩宽": "shoulder width",
    "瓶身外径": "body OD",
    "端到端长": "end-to-end length",
    "端盖宽": "end-cap width",
    "端盖高": "end-cap height",
    "筒体外径": "shell OD",
    "筒体直径": "shell diameter",
    "筒体高度": "shell height",
    "管路中心线至手柄顶": "centerline to handle top",
    "缩回主体高": "retracted body height",
    "缩回含两耳总长": "retracted length over eyes",
    "缩回含杆端总长": "retracted length over rod end",
    "缩回安装中心距": "retracted mounting-center distance",
    "缩回总高": "retracted height",
    "缸头外径": "head OD",
    "缸底耳厚": "base-eye thickness",
    "缸筒外径": "barrel OD",
    "罐体外径": "can OD",
    "耳厚": "eye thickness",
    "耳孔直径": "eye bore",
    "耳环孔径": "eye bore",
    "螺旋顶杆直径": "screw diameter",
    "软管展开长": "hose length",
    "轴孔直径": "bore diameter",
    "轴心高": "shaft-center height",
    "轴承宽": "bearing width",
    "轴线高": "axis height",
    "钢瓶外径": "cylinder OD",
    "阀体端面总长": "body face-to-face length",
    "阀杆直径": "stem diameter",
    "阀杆轴线至手柄远端": "stem axis to handle tip",
    "面对面长": "face-to-face length",
    "顶盖法兰外径": "top-flange OD",
}


def _label(name: Any) -> str:
    text = str(name or "")
    return DIM_EN.get(text, text)


def _pairs(mapping: dict[str, Any]) -> str:
    return ", ".join(f"{_label(name)} {_mm(value)}" for name, value in mapping.items())


def prompt_body(item: dict[str, Any]) -> str:
    """短英文身份。填写了 prompt_text 时用用户的文本。"""
    custom = str(item.get("prompt_text") or "").strip()
    if custom:
        return custom
    shape = str(item.get("shape_en") or "").strip() or ", ".join(item.get("identity_features") or [])
    note = str(item.get("prompt_note") or "").strip()
    body = f"Do: {shape}. Soil it like the scrap around it. Don't: a clean, glossy, new part."
    return f"{body} {note}" if note else body


def build_prompt(item: dict[str, Any], scale: dict[str, Any]) -> str:
    size = object_size_mm(item)
    custom = str(item.get("prompt_text") or "").strip()
    shape = str(item.get("shape_en") or "").strip() or ", ".join(item.get("identity_features") or [])
    identity = custom or shape
    note = str(item.get("prompt_note") or "").strip()
    axis_name = str(size.get("reference_axis") or "")
    axis = _label(axis_name)
    length = size.get("reference_length_mm")
    cross_name = str(size.get("cross_name") or "")
    cross_mm = size.get("cross_mm")
    opening = f"{_percent(float(length) / HOPPER_WIDTH_MM)} of the {HOPPER_WIDTH_MM} mm opening"
    length_cap = _percent(scale["length_fraction"])
    if size.get("length_is_overall", True):
        axis_rule = f"{axis} {_mm(length)} mm is the full length."
        axis_ban = f"Don't draw {axis} longer than {_mm(length)} mm."
    else:
        axis_rule = (
            f"{axis} {_mm(length)} mm is a hard cap on that axis, not permission to make the whole part longer."
        )
        axis_ban = "Don't use that partial axis as a reason to grow the rest of the part."
    cross_cap = ""
    cross_bit = ""
    if cross_name and cross_mm is not None and scale.get("cross_fraction") is not None:
        cross_label = _label(cross_name)
        cross_cap = (
            f"{cross_label} is {_mm(cross_mm)} mm, {_percent(float(cross_mm) / HOPPER_WIDTH_MM)} of the opening, "
            f"and at most {_percent(scale['cross_fraction'])} of the image width. "
        )
        cross_bit = f" {cross_label} {_mm(cross_mm)} mm."
    extra_dims = [
        f"{_label(name)} {_mm(value)}"
        for name, value in size["dimensions_mm"].items()
        if name not in {axis_name, cross_name}
    ]
    extra_bit = f" Also {', '.join(extra_dims)}." if extra_dims else ""
    parts = _pairs(size["parts_mm"])
    part_bit = f" Parts, in mm: {parts}." if parts else ""
    render_scale = float(scale.get("render_scale") or 1)
    scale_note = (
        f"Scale {render_scale:g} is already inside both caps; multiplying it back is a size error. "
        if render_scale != 1
        else ""
    )
    extra = f"\nNote — {note}" if note and note not in identity else ""
    return (
        f"Add 1 scrap part to photo 1 only: {item['id']}.\n"
        "Do: keep every other pixel, the timestamp, and the camera text.\n"
        "Don't: change the background or add a second object.\n"
        "Photo 2 — Do: copy only its silhouette.\n"
        "Photo 2 — Don't: copy its backdrop, grid, text, arrows, or new paint.\n"
        f"Shape — Do: {identity}. Solid, opaque, closed outline.\n"
        "Shape — Don't: change the type, blur, ghost, melt, or omit a part from photo 2.\n"
        f"Size — Do: The hopper opening is {HOPPER_WIDTH_MM} mm and covers {_percent(scale['hopper_span'])} of this image width. "
        f"Hard cap: {axis} is {_mm(length)} mm, {opening}, and at most {length_cap} of the image width. "
        f"{cross_cap}{scale_note}Draw one whole part that meets {'both caps' if cross_cap else 'this cap'} and keeps photo-2 proportions. "
        "If either axis covers more of the frame than its cap, shrink the part until it does not.\n"
        "Size — Don't: pass either image-width cap. Don't copy photo 2's apparent size; it is a close-up and is not a ruler. "
        f"Don't enlarge the part to fill the pile, and don't shrink it to a speck. Hiding an edge does not allow a larger body. {axis_ban}\n"
        f"mm — Do: {axis_rule}{cross_bit}{extra_bit}{part_bit}\n"
        "mm — Don't: exceed any listed millimeter, enlarge a part, or invent a larger overall size.\n"
        "Wear — Do: soil the whole surface to the dirt level of the scrap it touches: brown rust on steel, grey dust packed into seams and coils, black oil smears, paint only as small dull chips.\n"
        "Wear — Don't: leave a clean, bright, glossy, or showroom patch. Don't blur the outline or bury the identifying body. Don't rust plastic. Don't turn stainless into solid red rust.\n"
        "Place — Do: nest it so the body and its mass rest on scrap.\n"
        "Place — Don't: float, interpenetrate, bridge a gap, or balance on a tip."
        f"{extra}"
    )


def write_progress(payload: dict[str, Any]) -> None:
    """把当前进度写到 output/progress.json。写失败不影响这一次生成。"""
    try:
        path = PROJECT / "output" / "progress.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def publish_progress(
    folder: str,
    total: int,
    records: list[dict[str, Any]],
    spent: float,
    budget: float,
    current: dict[str, str] | None,
    running: bool,
) -> None:
    ok = sum(1 for item in records if (item.get("output") or {}).get("status") == "ok")
    failed = sum(1 for item in records if (item.get("output") or {}).get("status") == "error")
    write_progress(
        {
            "running": running,
            "folder": folder,
            "total": total,
            "done": ok + failed,
            "ok": ok,
            "failed": failed,
            "spent_usd": round(spent, 6),
            "budget_usd": budget,
            "current": current,
        }
    )


def run_directory(output_root: Path | None) -> Path:
    """每次执行一个文件夹。默认用本地日期；同一天再次执行时追加 _2、_3。"""
    if output_root is not None:
        path = output_root.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    parent = PROJECT / "output" / "results"
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    candidate = parent / day
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{day}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def decode_generated_images(payload: dict[str, Any]) -> list[bytes]:
    images = []
    for item in payload.get("data") or []:
        encoded = (item or {}).get("b64_json")
        if isinstance(encoded, str) and encoded:
            images.append(base64.b64decode(encoded))
    if not images:
        raise RuntimeError("响应没有 b64_json")
    return images


def cost_shares(total: float, count: int) -> list[float]:
    """把一次调用的费用分到每张图上，四舍五入后的合计仍等于本次调用费用。"""
    if count < 1:
        raise ValueError("count 必须大于 0")
    shares = [round(total / count, 6) for _ in range(count)]
    shares[-1] = round(total - sum(shares[:-1]), 6)
    return shares


def main() -> int:
    parser = argparse.ArgumentParser(description="用 OpenAI 图像编辑试生成废钢异物，并限制美元花费")
    parser.add_argument("--objects", nargs="+", default=list(DEFAULT_OBJECTS))
    parser.add_argument("--backgrounds", nargs="+", default=list(DEFAULT_BACKGROUNDS))
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--per-image-usd", type=float, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--all-models", action="store_true", help="依次测试 Playground 中的全部现行图像模型")
    parser.add_argument("--cost-rank", action="store_true", help="按横图输出费从低到高测试前九个模型与画质组合")
    parser.add_argument("--quality", default=None, choices=QUALITIES)
    parser.add_argument("--complexity", default=None, choices=COMPLEXITIES, help="思考/规划强度，最低为 low")
    parser.add_argument("--size", default=None)
    parser.add_argument("--background-mode", default=None, choices=BACKGROUNDS)
    parser.add_argument("--output-format", default=None, choices=OUTPUT_FORMATS)
    parser.add_argument("--output-compression", type=int, default=None)
    parser.add_argument("--moderation", default=None, choices=MODERATIONS)
    parser.add_argument("--n", type=int, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=ROOT / "config_openai.yaml")
    args, _unknown = parser.parse_known_args()

    settings = load_settings(args.config)
    api_key = resolve_api_key(settings)
    if args.cost_rank and (args.all_models or args.model):
        raise SystemExit("--cost-rank 不再接受 --model 或 --all-models")
    if args.all_models and args.model:
        raise SystemExit("--all-models 与 --model 只能使用一个")
    allowed_models = set(PLAYGROUND_MODELS) | {name for name, _quality in COST_RANK}
    models = list(PLAYGROUND_MODELS) if args.all_models else [
        args.model or str(settings.get("model") or "gpt-image-2.6-flare")
    ]
    if not args.cost_rank:
        unknown_models = [name for name in models if name not in allowed_models]
        if unknown_models:
            raise SystemExit(f"不在可测试的图像模型中: {', '.join(unknown_models)}")
    if args.cost_rank:
        if tuple(args.objects) == DEFAULT_OBJECTS:
            args.objects = ["S01"]
        if tuple(args.backgrounds) == DEFAULT_BACKGROUNDS:
            args.backgrounds = [DEFAULT_BACKGROUNDS[0]]
        if len(args.objects) != 1 or len(args.backgrounds) != 1:
            raise SystemExit("成本排行测试只接受一个物品和一个背景")
    quality = args.quality or str(settings.get("quality") or "low")
    complexity = None if args.cost_rank else (
        args.complexity if args.complexity is not None else settings.get("complexity")
    )
    if complexity is not None and complexity not in COMPLEXITIES:
        raise SystemExit(f"complexity 只能是 {', '.join(COMPLEXITIES)}")
    size = args.size or ("1536x1024" if args.cost_rank else str(settings.get("size") or "1536x864"))
    background_mode = args.background_mode or str(settings.get("background") or "opaque")
    output_format = args.output_format or str(settings.get("output_format") or "png")
    moderation = args.moderation or str(settings.get("moderation") or "low")
    count = int(args.n if args.n is not None else settings.get("n") or 1)
    compression = args.output_compression
    if compression is None and settings.get("output_compression") is not None:
        compression = int(settings["output_compression"])
    if quality not in QUALITIES or background_mode not in BACKGROUNDS or output_format not in OUTPUT_FORMATS:
        raise SystemExit("quality、background 或 output_format 不在 Playground 可选项中")
    if not args.cost_rank:
        rejected = [
            name for name in models
            if name in QUALITY_BY_MODEL and quality not in QUALITY_BY_MODEL[name]
        ]
        if rejected:
            raise SystemExit(
                f"{', '.join(rejected)} 不接受画质 {quality}。gpt-image-2 只有 auto、low、medium、high"
            )
    if output_format == "png":
        compression = None
    elif compression is None:
        compression = 80
    if compression is not None and not 0 <= compression <= 100:
        raise SystemExit("output_compression 必须在 0 到 100")
    if not 1 <= count <= 10:
        raise SystemExit("n 必须在 1 到 10")
    budget = float(args.budget_usd if args.budget_usd is not None else settings.get("budget_usd", 5.0))
    if budget <= 0:
        raise SystemExit("budget-usd 必须大于 0")
    per_image = None if args.per_image_usd is None else float(args.per_image_usd)
    if per_image is not None and per_image <= 0:
        raise SystemExit("单张上限必须大于 0")
    base = str(settings.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("OpenAI base_url 必须是 HTTPS")

    catalog = json.loads((ROOT / "profiles" / "object_profiles.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in catalog["objects"]}
    missing = [item_id for item_id in args.objects if item_id not in by_id]
    if missing:
        raise SystemExit(f"未知物品 ID: {', '.join(missing)}")
    backgrounds = []
    for name in args.backgrounds:
        path = Path(name)
        if not path.is_file():
            path = ROOT / "input" / "backgrounds" / name
        if not path.is_file():
            raise SystemExit(f"找不到背景: {name}")
        backgrounds.append(path)

    if args.cost_rank:
        item = by_id[args.objects[0]]
        background = backgrounds[0]
        pairs = [
            (rank, model_name, rank_quality, item, background)
            for rank, (model_name, rank_quality) in enumerate(COST_RANK, start=1)
        ]
    else:
        pairs = [
            (0, model_name, quality, by_id[item_id], background)
            for model_name in models
            for background in backgrounds
            for item_id in args.objects
        ]
    if per_image is not None and per_image > budget + 1e-9:
        raise SystemExit("单张上限不能超过总预算")

    out_dir = run_directory(args.output_root.resolve() if args.output_root else None)
    generated_dir = out_dir / "生成图"
    generated_dir.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)

    session = requests.Session()
    spent = 0.0
    records: list[dict[str, Any]] = []
    next_index = 1
    interrupted = False
    endpoint = f"{base}/images/edits"
    suffix = "png" if output_format == "png" else output_format
    prepared = []
    for _rank, model_name, rank_quality, item, background in pairs:
        scale = frame_scale(item, background)
        prompt = build_prompt(item, scale)
        print(
            f"[scale] {background.name} 跨度 {scale['hopper_span']:.3f} "
            f"{item['id']} {scale['length_axis']} {_percent(scale['length_fraction'])} "
            f"{scale['cross_name'] or '-'} "
            f"{_percent(scale['cross_fraction']) if scale['cross_fraction'] is not None else '-'}"
        )
        prepared.append((model_name, rank_quality, item, background, scale, prompt))
    total = len(prepared)
    publish_progress(out_dir.name, total, records, spent, budget, None, True)
    try:
        for model_name, rank_quality, item, background, scale, prompt in prepared:
            reference_name = item["reference_filename"]
            base_input = {
                "object_id": item["id"],
                "object_name": item["name"],
                "object_size_mm": object_size_mm(item),
                "frame_scale": scale,
                "background_source": background.name,
                "six_view": reference_name,
                "model": model_name,
                "quality": rank_quality,
                "complexity": complexity,
                "complexity_sent": complexity if model_name in COMPLEXITY_MODELS else None,
                "size": size,
                "background_mode": background_mode,
                "output_format": output_format,
                "output_compression": compression,
                "moderation": moderation,
                "n": count,
                "prompt": prompt,
            }
            hold = reserve_for(rank_quality)
            if per_image is not None:
                hold = min(hold, per_image)
            current = {
                "object_id": item["id"],
                "object_name": item["name"],
                "background": background.name,
            }
            publish_progress(out_dir.name, total, records, spent, budget, current, True)
            if spent > 0 and spent + hold > budget + 1e-9:
                records.append(
                    {
                        "index": None,
                        "input": base_input,
                        "output": {
                            "status": "stopped_budget",
                            "cost_usd": None,
                            "error_reason": "剩余预算不够再生成一张。",
                        },
                    }
                )
                break
            reference = ROOT / "input" / "objects" / "six_views" / reference_name
            images = [jpeg_data_url(background, 1536, 90), jpeg_data_url(reference, 1280, 92)]
            sent_complexity = complexity if model_name in COMPLEXITY_MODELS else None
            edit_call = ImageEditCall(
                prompt=prompt,
                images=images,
                quality=rank_quality,
                size=size,
                background=background_mode,
                output_format=output_format,
                output_compression=compression,
                moderation=moderation,
                count=count,
                complexity=str(sent_complexity) if sent_complexity else None,
            )
            complexity_applied = False
            try:
                if model_name not in IMAGE_EDIT_CALLS:
                    raise ValueError(f"{model_name} 没有单独的现行调用函数，已拒绝发送")
                response = None
                sent_body = None
                current = session
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        response, sent_body = call_image_edit(
                            model_name, current, endpoint, api_key, edit_call, 240
                        )
                        break
                    except requests.RequestException as exc:
                        last_error = exc
                        print(
                            f"[retry] {model_name} {item['id']} 第 {attempt + 1} 次 {exc.__class__.__name__}",
                            file=sys.stderr,
                        )
                        if attempt == 2:
                            raise
                        time.sleep(1.5)
                        current = requests.Session()
                if response is None or sent_body is None:
                    raise last_error or RuntimeError("没有收到接口响应")
                complexity_applied = "complexity" in sent_body
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
                payload = response.json()
                generated = decode_generated_images(payload)
                actual = usage_cost_usd(payload.get("usage"), model_name)
                if actual is None:
                    raise RuntimeError("响应没有 usage，已停止以免无法记账")
                usage = payload.get("usage") or {}
                details = usage.get("input_tokens_details") or {}
                written: list[Path] = []
                new_records: list[dict[str, Any]] = []
                try:
                    for offset, blob in enumerate(generated):
                        index = next_index + offset
                        image_path = generated_dir / f"{index:04d}.{suffix}"
                        written.append(image_path)
                        image_path.write_bytes(blob)
                        new_records.append(
                            {
                                "index": index,
                                "input": base_input,
                                "output": {
                                    "status": "ok",
                                    "generated_image": f"生成图/{image_path.name}",
                                    "cost_usd": cost_shares(actual, len(generated))[offset],
                                    "request_cost_usd": round(actual, 6),
                                    "quality": payload.get("quality"),
                                    "size": payload.get("size"),
                                    "complexity_applied": complexity_applied,
                                    "input_tokens": usage.get("input_tokens"),
                                    "text_tokens": details.get("text_tokens"),
                                    "image_tokens": details.get("image_tokens"),
                                    "output_tokens": usage.get("output_tokens"),
                                },
                            }
                        )
                except (Exception, KeyboardInterrupt):
                    for path in written:
                        path.unlink(missing_ok=True)
                    raise
                spent += actual
                records.extend(new_records)
                next_index += len(generated)
                publish_progress(out_dir.name, total, records, spent, budget, None, True)
                print(f"[ok] {model_name} {rank_quality} ${actual:.4f} 累计 ${spent:.4f}")
                if per_image is not None and actual > per_image + 1e-9:
                    print(f"[stop] 这一张 ${actual:.4f} 超过单张上限 ${per_image:.2f}")
                    break
            except Exception as exc:
                message = str(exc)[:500]
                records.append(
                    {
                        "index": None,
                        "input": base_input,
                        "output": {
                            "status": "error",
                            "cost_usd": None,
                            "complexity_applied": complexity_applied,
                            "error": message,
                            "error_reason": explain_error(message),
                        },
                    }
                )
                publish_progress(out_dir.name, total, records, spent, budget, None, True)
                print(f"[fail] {model_name}: {records[-1]['output']['error']}", file=sys.stderr)
            if spent > budget:
                print(f"[stop] 累计 ${spent:.4f} 已超过预算 ${budget:.2f}")
                break
    except KeyboardInterrupt:
        interrupted = True
    publish_progress(out_dir.name, total, records, spent, budget, None, False)

    metadata = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "date": datetime.now().astimezone().strftime("%Y-%m-%d"),
        "budget_usd": budget,
        "per_image_usd": per_image,
        "spent_usd": round(spent, 6),
        "items": records,
    }
    if interrupted:
        metadata["interrupted"] = True
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if interrupted:
        print(f"[stop] 已中断 {out_dir} 花费 ${spent:.4f} / ${budget:.2f}", flush=True)
        return 130
    print(f"[done] {out_dir} 花费 ${spent:.4f} / ${budget:.2f}")
    return 0 if any(item["output"].get("status") == "ok" for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
