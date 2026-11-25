"""从种子重建运行时物品目录。不调用生图接口，不写旧版长提示词。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

WEATHERING = {
    "painted_steel": "明确呈现与当前废钢堆相称的报废钢件：可见钢主体主要为深褐锈、灰褐尘土和旧油泥，原漆仅作约10–20%的暗淡碎片残留；此为合成外观目标，不是实测腐蚀面积。裸钢有不规则锈层、点蚀和接缝积锈，大片剥漆，不保留完整红/蓝/绿大色块或亮漆高光。不能只加几个锈点。锈纹沿材质、磕碰和水路分布，不均匀喷成橙色。塑料旋钮/标牌不生铁锈。",
    "painted_castiron": "报废铸铁件的可见外壳明显氧化、漆膜剥落，深褐锈层与铸造粗糙度相互叠加，凹槽积尘油泥，底脚、孔边及螺栓有锈蚀；原色只留下暗淡残漆。受力面可局部擦亮但不能形成干净的新品反光，禁止仅在角落添几处轻微磨痕。",
    "stainless_steel": "呈现长期弃置的不锈钢旧件：镜面高光被灰尘、油膜、水垢和不均匀灰褐污层压低，表面有密集细擦伤、暗斑，焊缝/钢制紧固件可有局部锈迹或铁污染。主体仍为不锈钢，不把全部表面变成碳钢红锈；禁止均匀厚红锈。绝不保留干净银白新品和摄影棚灯条反射。塑料手柄只污旧。",
    "cast_steel": "报废铸钢阀门主体和法兰明显呈深褐/红褐氧化、油泥与积尘，螺栓孔和接缝积锈；银白新金属面改为失去光泽的旧钢材，蓝手轮褪色、剥漆，保留暗淡蓝色身份线索。保留厚法兰、曲面及真实接缝，不套用同一锈贴图，不仅给螺栓点几处轻锈。",
    "aerosol_can": "报废薄壁气雾罐应明显污旧：印刷褪色磨花、标签残损，表面附着灰褐粉尘和油污，钢制罐在掉漆、划伤、卷边处有清楚的不规则氧化/锈斑。保留弱化的主色与结构线索，禁止亮丽完整广告罐和可读新品印刷。塑料罩盖/喷头只积灰黄化擦伤、不生铁锈，薄壁结构不能被画成厚铸铁。",
    "fire_extinguisher": "报废灭火器筒体明显褪色、大片擦伤掉漆，裸钢有清楚可辨的褐色锈蚀、接缝流痕和灰尘油泥；红/绿原漆仅保留暗淡旧色块，标签污损模糊，不能保留亮丽洁净的新筒体。金属握把和连接件可锈蚀，橡胶软管只沾灰油并自然弯曲，不生铁锈，不新增喷射烟雾。",
    "painted_steel_chrome": "报废液压钢缸外壳明显剥漆、锈蚀、油泥和积尘，钢耳环/油口凹处有深褐锈与污垢，原漆暗淡残留。镀铬杆有旧油膜、细擦痕和局部暗斑，只保留弱金属反光，不整根涂成橙锈；禁止干净亮漆缸筒、亮银镜面活塞杆和新品黄标。保持缸筒厚度及机械连接。",
    "mixed_shock": "报废减震器的钢弹簧和钢端件有明显掉漆、褐锈和油灰，弹簧缝积尘；铝件/镀铬杆有灰暗污膜、氧化擦伤，只有微弱反射。原有彩色残漆降低饱和度；橡胶护套和软管积灰油污、轻微老化但不生铁锈。不能保留崭新的彩色弹簧、亮黑新品外壳；保持圈距和连接。",
    "air_shock": "废旧空气避震以明显灰褐积尘、油泥、涂层擦伤、铝表面灰白氧化表现旧化，钢耳件和螺栓有锈斑。黑色气室呈灰暗哑光而非新品亮黑；调节钮仅保留暗淡颜色线索，禁止厚铁锈覆盖铝气室，也不增加螺旋弹簧。",
    "aluminium_steel": "报废气缸铝壳明显灰暗、灰白氧化、密集擦伤并覆盖不均匀油泥积尘；钢拉杆、螺栓、安装端有清楚锈斑。活塞杆有旧油膜和弱反光，禁止干净的银亮铝型材和鲜明新品贴标，禁止把铝壳和杆统一涂成红锈。保持铝/钢/镀铬部件的材质差异。",
    "plastic_pp": "废弃白色PP罐明显积灰、灰褐油污、黄化和擦伤，盖纹及接缝积污，反射变为旧塑料的暗哑漫反射，不能只点几处轻污后仍像白色新品。没有红褐铁锈，不变成金属罐，不无故烧熔或大面积开裂；污损色与附近废料相称。",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(seeds_path: Path, destination: Path) -> dict:
    seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
    reference_root = Path(seeds["reference_root"]).expanduser()
    if not reference_root.is_absolute():
        reference_root = (ROOT / reference_root).resolve()
    profiles = []
    for seed in seeds["objects"]:
        reference = reference_root / seed["reference"]
        if not reference.is_file():
            raise ValueError(f"参考图缺失：{seed['id']} {seed['reference']}")
        length = seed["dimensions"][seed["reference_axis"]]
        cross = seed["dimensions"][seed["cross"]]
        material = seed["material"]
        if material not in WEATHERING:
            raise ValueError(f"{seed['id']} 没有材质旧化：{material}")
        with Image.open(reference) as image:
            image_size = list(image.size)
        profiles.append(
            {
                "id": seed["id"],
                "name": seed["name"],
                "category": seed["category"],
                "reference_filename": seed["reference"],
                "reference_sha256": digest(reference),
                "reference_image_size": image_size,
                "original_filename": seed.get("original"),
                "dimensions_mm": seed["dimensions"],
                "parts_mm": seed["parts"],
                "dimension_ranges_mm": {seed["reference_axis"]: seed["primary_range"]},
                "dimension_evidence": {
                    "status": seed.get("status", seeds["dimension_status_default"]),
                    "sources": seed.get("sources", []),
                    "note": seed.get(
                        "evidence_note",
                        "工作值取自既有说明和参考外形，具体实例尺寸尚未实测确认。",
                    ),
                },
                "size_policy": {
                    "reference_axis": seed["reference_axis"],
                    "reference_length_mm": length,
                    "length_is_overall": bool(seed.get("length_is_overall", True)),
                    "cross_name": seed["cross"],
                    "cross_mm": cross,
                    "render_scale": float(seed.get("render_scale", 1)),
                },
                "material": material,
                "identity_features": seed["features"],
                "shape_en": str(seed.get("shape_en") or "").strip(),
                "weathering_prompt": seed.get("weathering_prompt") or WEATHERING[material],
                "prompt_text": seed.get("prompt_text") or "",
                "prompt_note": str(seed.get("prompt_note") or "").strip(),
                "exposure_hint": seed["exposure"],
                "pose_hint": seed["pose"],
                "forbidden_changes": seed["forbid"],
            }
        )
    result = {
        "schema_version": "1.2",
        "units": "mm",
        "reference_root": seeds["reference_root"],
        "frame_scale": "generate_openai.py 按车斗内宽 2500 mm 和本张斗壁跨度计算，再乘 render_scale",
        "object_count": len(profiles),
        "objects": profiles,
    }
    save(destination / "object_profiles.json", result)
    write_handbook(destination / "物品尺寸手册.md", profiles)
    return result


def write_handbook(path: Path, profiles: list[dict]) -> None:
    lines = [
        "# 物品尺寸",
        "",
        "单位：mm。生成时由 `generate_openai.py` 读取 `object_profiles.json`，按车斗内宽 2500 mm 和该张背景的斗壁跨度计算画面上限，再乘 `render_scale`。六视图只提供形状。",
        "",
        "| ID | 物品 | 工作尺寸 | 参照轴 | 横向 | 缩放 |",
        "|---|---|---|---|---|---|",
    ]
    for profile in profiles:
        dims = "；".join(f"{name} {value:g}" for name, value in profile["dimensions_mm"].items())
        policy = profile["size_policy"]
        lines.append(
            f"| {profile['id']} | {profile['name']} | {dims} | {policy['reference_axis']} | "
            f"{policy['cross_name']} {policy['cross_mm']:g} | {policy['render_scale']:g} |"
        )
    lines.extend(["", "## 逐件", ""])
    for profile in profiles:
        policy = profile["size_policy"]
        parts = "；".join(f"{name} {value:g} mm" for name, value in profile["parts_mm"].items()) or "无"
        forbid = "；".join(profile["forbidden_changes"])
        lines.extend(
            [
                f"### {profile['id']} — {profile['name']}",
                "",
                f"六视图：`{profile['reference_filename']}`。",
                f"尺寸：{profile['dimension_evidence']['status']}。{profile['dimension_evidence']['note']}",
                f"参照轴 {policy['reference_axis']} {policy['reference_length_mm']:g} mm，"
                f"是否整机总长：{'是' if policy['length_is_overall'] else '否'}。"
                f"横向 {policy['cross_name']} {policy['cross_mm']:g} mm。缩放 {policy['render_scale']:g}。",
                f"零件：{parts}。",
                "身份：" + "；".join(profile["identity_features"]) + "。",
                f"露出：{profile['exposure_hint']}",
                f"姿态：{profile['pose_hint']}",
                f"禁止：{forbid}。",
                "",
            ]
        )
    lines.extend(
        [
            "修改 `profiles/object_profile_seeds.json` 后，在「程序」目录运行 `python3 tools/build_object_profiles.py`。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=Path, default=ROOT / "profiles/object_profile_seeds.json")
    parser.add_argument("--output", type=Path, default=ROOT / "profiles")
    args = parser.parse_args()
    result = build(args.seeds, args.output)
    print(json.dumps({"objects": result["object_count"], "status": "rebuilt"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
