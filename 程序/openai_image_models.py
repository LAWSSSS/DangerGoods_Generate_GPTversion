"""现行五个图像模型各自的编辑接口。每个调用函数只发送该模型接受的字段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import requests

QUALITY_THROUGH_HIGH = ("auto", "low", "medium", "high")
QUALITY_THROUGH_MAX = ("auto", "low", "medium", "high", "xhigh", "max")
COMPLEXITY_LEVELS = ("low", "medium", "high")
BACKGROUNDS = ("auto", "opaque", "transparent")
OUTPUT_FORMATS = ("png", "jpeg", "webp")
MODERATIONS = ("auto", "low")

# 2.6 接受 complexity，省略时接口自己默认 high。2 和 2.5 会拒绝这个字段。
COMPLEXITY_MODELS = frozenset({"gpt-image-2.6-flare", "gpt-image-2.6-sunburst"})
QUALITY_BY_MODEL = {
    "gpt-image-2": QUALITY_THROUGH_HIGH,
    "gpt-image-2.5-flare": QUALITY_THROUGH_MAX,
    "gpt-image-2.5-sunburst": QUALITY_THROUGH_MAX,
    "gpt-image-2.6-flare": QUALITY_THROUGH_MAX,
    "gpt-image-2.6-sunburst": QUALITY_THROUGH_MAX,
}


@dataclass(frozen=True)
class ImageEditCall:
    prompt: str
    images: list[str]
    quality: str
    size: str
    background: str
    output_format: str
    output_compression: int | None
    moderation: str
    count: int
    complexity: str | None = None


def _reject(model: str, reason: str) -> None:
    raise ValueError(f"{model}：{reason}")


def _shared_body(model: str, call: ImageEditCall) -> dict[str, Any]:
    if call.quality not in QUALITY_BY_MODEL[model]:
        allowed = "、".join(QUALITY_BY_MODEL[model])
        _reject(model, f"画质只能是 {allowed}")
    if call.background not in BACKGROUNDS:
        _reject(model, "背景只能是 auto、opaque、transparent")
    if call.output_format not in OUTPUT_FORMATS:
        _reject(model, "编辑输出只能是 png、jpeg、webp")
    if call.moderation not in MODERATIONS:
        _reject(model, "moderation 只能是 auto 或 low")
    if not 1 <= call.count <= 10:
        _reject(model, "n 必须在 1 到 10")
    if call.output_format == "png" and call.output_compression is not None:
        _reject(model, "png 不能带 output_compression")
    if call.output_compression is not None and not 0 <= call.output_compression <= 100:
        _reject(model, "output_compression 必须在 0 到 100")
    body: dict[str, Any] = {
        "model": model,
        "prompt": call.prompt,
        "images": [{"image_url": item} for item in call.images],
        "quality": call.quality,
        "size": call.size,
        "n": call.count,
        "output_format": call.output_format,
        "background": call.background,
        "moderation": call.moderation,
    }
    if call.output_format in ("jpeg", "webp") and call.output_compression is not None:
        body["output_compression"] = call.output_compression
    return body


def _without_complexity(model: str, call: ImageEditCall) -> dict[str, Any]:
    if call.complexity:
        _reject(model, "不接受 complexity，本次不会发送该字段")
    return _shared_body(model, call)


def _with_optional_complexity(model: str, call: ImageEditCall) -> dict[str, Any]:
    body = _shared_body(model, call)
    if not call.complexity:
        return body
    if call.complexity not in COMPLEXITY_LEVELS:
        _reject(model, "complexity 只能是 low、medium、high")
    body["complexity"] = call.complexity
    return body


def body_gpt_image_2(call: ImageEditCall) -> dict[str, Any]:
    """gpt-image-2：画质到 high，不发送 complexity、input_fidelity 或 markup。"""
    return _without_complexity("gpt-image-2", call)


def body_gpt_image_2_5_flare(call: ImageEditCall) -> dict[str, Any]:
    """gpt-image-2.5-flare：画质可到 max，不发送 complexity 或 input_fidelity。"""
    return _without_complexity("gpt-image-2.5-flare", call)


def body_gpt_image_2_5_sunburst(call: ImageEditCall) -> dict[str, Any]:
    """gpt-image-2.5-sunburst：画质可到 max，不发送 complexity 或 input_fidelity。"""
    return _without_complexity("gpt-image-2.5-sunburst", call)


def body_gpt_image_2_6_flare(call: ImageEditCall) -> dict[str, Any]:
    """gpt-image-2.6-flare：可发送 low/medium/high。不传时由接口默认 high。"""
    return _with_optional_complexity("gpt-image-2.6-flare", call)


def body_gpt_image_2_6_sunburst(call: ImageEditCall) -> dict[str, Any]:
    """gpt-image-2.6-sunburst：可发送 low/medium/high。不传时由接口默认 high。"""
    return _with_optional_complexity("gpt-image-2.6-sunburst", call)


def _post(
    session: requests.Session,
    endpoint: str,
    api_key: str,
    body: dict[str, Any],
    timeout: float,
) -> requests.Response:
    return session.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )


def call_gpt_image_2(
    session: requests.Session,
    endpoint: str,
    api_key: str,
    call: ImageEditCall,
    timeout: float = 240,
) -> tuple[requests.Response, dict[str, Any]]:
    body = body_gpt_image_2(call)
    return _post(session, endpoint, api_key, body, timeout), body


def call_gpt_image_2_5_flare(
    session: requests.Session,
    endpoint: str,
    api_key: str,
    call: ImageEditCall,
    timeout: float = 240,
) -> tuple[requests.Response, dict[str, Any]]:
    body = body_gpt_image_2_5_flare(call)
    return _post(session, endpoint, api_key, body, timeout), body


def call_gpt_image_2_5_sunburst(
    session: requests.Session,
    endpoint: str,
    api_key: str,
    call: ImageEditCall,
    timeout: float = 240,
) -> tuple[requests.Response, dict[str, Any]]:
    body = body_gpt_image_2_5_sunburst(call)
    return _post(session, endpoint, api_key, body, timeout), body


def call_gpt_image_2_6_flare(
    session: requests.Session,
    endpoint: str,
    api_key: str,
    call: ImageEditCall,
    timeout: float = 240,
) -> tuple[requests.Response, dict[str, Any]]:
    body = body_gpt_image_2_6_flare(call)
    return _post(session, endpoint, api_key, body, timeout), body


def call_gpt_image_2_6_sunburst(
    session: requests.Session,
    endpoint: str,
    api_key: str,
    call: ImageEditCall,
    timeout: float = 240,
) -> tuple[requests.Response, dict[str, Any]]:
    body = body_gpt_image_2_6_sunburst(call)
    return _post(session, endpoint, api_key, body, timeout), body


IMAGE_EDIT_CALLS: dict[str, Callable[..., tuple[requests.Response, dict[str, Any]]]] = {
    "gpt-image-2": call_gpt_image_2,
    "gpt-image-2.5-flare": call_gpt_image_2_5_flare,
    "gpt-image-2.5-sunburst": call_gpt_image_2_5_sunburst,
    "gpt-image-2.6-flare": call_gpt_image_2_6_flare,
    "gpt-image-2.6-sunburst": call_gpt_image_2_6_sunburst,
}


def call_image_edit(
    model: str,
    session: requests.Session,
    endpoint: str,
    api_key: str,
    call: ImageEditCall,
    timeout: float = 240,
) -> tuple[requests.Response, dict[str, Any]]:
    caller = IMAGE_EDIT_CALLS.get(model)
    if caller is None:
        raise ValueError(f"{model} 没有单独的现行调用函数，已拒绝发送")
    return caller(session, endpoint, api_key, call, timeout)
