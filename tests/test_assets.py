"""UE 작품 소재 목록(assets.json v1): 계약 모델(contracts.assets)과 파일 검사(studio.verify.checks.inspect_assets)."""
import json
import math
import struct
import zlib

import pytest
from pydantic import ValidationError

from contracts.assets import ArtworkAssets
from studio.verify import checks
from studio.verify.checks import inspect_assets

# 생성 쪽에 요청한 세 가지 작품 모양: 유체 배경 + 층 / 그리기(층 2장) / 입자 형상(실루엣)
FLUID_LAYERS = {
    "format": "artwork-assets", "version": 1, "spec_id": "spec-ocean",
    "layers": [
        {"id": "far", "image": "layer_far.png", "depth": "far"},
        {"id": "mid", "image": "layer_mid.png", "depth": "mid"},
        {"id": "near", "image": "layer_near.png", "depth": "near"},
    ],
    "materials": [{"target": "mid", "object": "layer_glow", "values": {"EmissiveStrength": 1.2, "Tint": [0.6, 0.8, 1.0]}}],
    "depth_map": "depth.png",
}
PAINT = {
    "format": "artwork-assets", "version": 1,
    "layers": [
        {"id": "paper", "image": "paper.png", "depth": "far"},
        {"id": "frame", "image": "frame.png", "depth": "near"},
    ],
    "materials": [{"target": "paper", "values": {"Tint": [1.0, 0.95, 0.85]}}],
}
PARTICLES = {
    "format": "artwork-assets", "version": 1,
    "layers": [{"id": "sky", "image": "sky.png", "depth": "far"}],
    "silhouette": "silhouette.json",
}
SAMPLES = [FLUID_LAYERS, PAINT, PARTICLES]


CHANNELS = {2: 3, 6: 4, 4: 2, 3: 1}   # PNG 색 형식 → 채널 수 (RGB · RGBA · 회색+알파 · 팔레트)


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(path, alpha=True, size=(64, 36), color=None, trns=False, opaque_alpha=False):
    """opaque_alpha: RGBA인데 알파가 모두 255(알파 채널은 있지만 실제로는 불투명)."""
    width, height = size
    color = color if color is not None else 6 if alpha else 2
    channels = CHANNELS[color]
    row = bytes(255 if opaque_alpha and color == 6 and i % 4 == 3 else i * 7 % (4 if color == 3 else 256)
                for i in range(width * channels))
    rows = b"".join(b"\x00" + row for _ in range(height))
    extra = chunk(b"PLTE", bytes(range(12))) if color == 3 else b""
    extra += chunk(b"tRNS", b"\x00" * (6 if color == 2 else 1)) if trns else b""   # RGB 투명색은 6바이트
    header = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + extra + chunk(b"IDAT", zlib.compress(rows))
                     + chunk(b"IEND", b""))


def silhouette(path, points=240):
    ring = [[0.5 + 0.3 * math.cos(2 * math.pi * i / points), 0.5 + 0.3 * math.sin(2 * math.pi * i / points)]
            for i in range(points)]
    path.write_text(json.dumps({"kind": "silhouette", "points": ring, "area": 0.28}), encoding="utf-8")


def write_files(folder, data, opaque=()):
    assets = ArtworkAssets.model_validate(data)
    for name in assets.files():
        if name.endswith(".json"):
            silhouette(folder / name)
        else:
            png(folder / name, alpha=name not in opaque)
    return assets


@pytest.mark.parametrize("data", SAMPLES)
def test_samples_pass(tmp_path, data):
    assets = write_files(tmp_path, data)
    assert inspect_assets(tmp_path, assets) == []
    assert assets.unknown_fields() == []


def test_back_layer_may_be_opaque(tmp_path):
    assets = write_files(tmp_path, FLUID_LAYERS, opaque={"layer_far.png", "depth.png"})
    assert assets.back_layer.id == "far"
    assert inspect_assets(tmp_path, assets) == []


def test_front_layer_without_alpha_is_rejected(tmp_path):
    assets = write_files(tmp_path, FLUID_LAYERS, opaque={"layer_near.png"})
    assert inspect_assets(tmp_path, assets) == ["층 near: 맨 뒤가 아닌 층은 알파가 있어야 함"]


@pytest.mark.parametrize("color, trns, ok", [
    (4, False, True),    # 회색 + 알파
    (3, True, True),     # 팔레트 + 투명색
    (3, False, False),   # 팔레트만
    (2, True, True),     # RGB + 투명색
])
def test_front_layer_alpha_kinds(tmp_path, color, trns, ok):
    assets = write_files(tmp_path, PAINT)
    png(tmp_path / "frame.png", color=color, trns=trns)
    assert (inspect_assets(tmp_path, assets) == []) is ok


def test_back_layer_is_by_depth_not_order(tmp_path):
    data = {**PAINT, "layers": list(reversed(PAINT["layers"]))}
    assets = write_files(tmp_path, data, opaque={"paper.png"})
    assert assets.back_layer.id == "paper" and inspect_assets(tmp_path, assets) == []


def test_missing_and_broken_files(tmp_path):
    assets = write_files(tmp_path, FLUID_LAYERS)
    (tmp_path / "depth.png").unlink()
    (tmp_path / "layer_mid.png").write_bytes(b"not a png")
    problems = inspect_assets(tmp_path, assets)
    assert "파일 없음: depth.png" in problems
    assert any(p.startswith("층 mid: UE 파일 규칙 위반") for p in problems)


def test_missing_silhouette_file(tmp_path):
    assets = write_files(tmp_path, PARTICLES)
    (tmp_path / "silhouette.json").unlink()
    assert inspect_assets(tmp_path, assets) == ["파일 없음: silhouette.json"]


def test_broken_png_header_is_rejected_not_crash(tmp_path):
    assets = write_files(tmp_path, PARTICLES)
    (tmp_path / "sky.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\x00" * 12) + chunk(b"IEND", b""))
    assert inspect_assets(tmp_path, assets)[0].startswith("층 sky: UE 파일 규칙 위반")


def test_too_large_layer(tmp_path, monkeypatch):
    assets = write_files(tmp_path, PARTICLES)
    png(tmp_path / "sky.png", alpha=False, size=(4097, 2))
    assert any("4096" in p for p in inspect_assets(tmp_path, assets))
    monkeypatch.setitem(checks.MAX_BYTES, "png", 100)   # 로더의 바이트 제한(32MiB)을 작게 바꿔 확인
    png(tmp_path / "sky.png", alpha=False)
    assert inspect_assets(tmp_path, assets) == ["층 sky: UE 파일 규칙 위반: 100바이트 초과"]


def test_bad_silhouette(tmp_path):
    assets = write_files(tmp_path, PARTICLES)
    silhouette(tmp_path / "silhouette.json", points=50)
    assert inspect_assets(tmp_path, assets) == ["실루엣: 점이 200개 미만"]
    (tmp_path / "silhouette.json").write_text('{"kind": "trace", "steps": []}', encoding="utf-8")
    assert inspect_assets(tmp_path, assets)[0].startswith("실루엣 형식 오류")


@pytest.mark.parametrize("change, reason", [
    ({"format": "scene"}, "format"),
    ({"version": 2}, "version"),
    ({"layers": []}, "layers"),
    ({"layers": [{"id": "a", "image": "../a.png", "depth": "far"}]}, "image"),
    ({"layers": [{"id": "a", "image": "a.jpg", "depth": "far"}]}, "image"),
    ({"layers": [{"id": "a", "image": "a.png", "depth": "far"}, {"id": "b", "image": "b.png", "depth": "far"}]},
     "깊이마다 층은 하나"),
    ({"layers": [{"id": "a", "image": "a.png", "depth": "far"}, {"id": "a", "image": "b.png", "depth": "mid"}]},
     "층 id가 겹침"),
    ({"materials": [{"target": "nope", "values": {}}]}, "대상 nope"),
    ({"materials": [{"target": "far", "values": {"Roughness": 1.5}}]}, "Roughness는 0.0~1.0"),
    ({"materials": [{"target": "far", "values": {"Metallic": 1.1}}]}, "Metallic는 0.0~1.0"),
    ({"materials": [{"target": "far", "values": {"Specular": -0.1}}]}, "Specular는 0.0~1.0"),
    ({"materials": [{"target": "far", "values": {"EmissiveStrength": 8.1}}]}, "EmissiveStrength는 0.0~8.0"),
    ({"materials": [{"target": "far", "values": {"NormalStrength": 8.1}}]}, "NormalStrength는 0.0~8.0"),
    ({"materials": [{"target": "far", "values": {"DyeColorStrength": 4.1}}]}, "DyeColorStrength는 0.0~4.0"),
    ({"materials": [{"target": "far", "values": {"Roughness": [0.5]}}]}, "Roughness는 0.0~1.0 숫자"),
    ({"materials": [{"target": "far", "values": {"Tint": [1, 2, 0]}}]}, "Tint는 0~1 RGB"),
    ({"materials": [{"target": "far", "values": {"BaseColor": [0.5, 0.5]}}]}, "BaseColor는 0~1 RGB"),
    ({"materials": [{"target": "far", "values": {"Wobble": 1}}]}, "허용하지 않는 재질 값"),
    ({"depth_map": "maps/depth.png"}, "depth_map"),
    ({"silhouette": "silhouette.png"}, "silhouette"),
    ({"objects": [{"id": "fish", "image": "fish.png", "position": [1.2, 0.5]}]}, "position은 0~1"),
    ({"objects": [{"id": "fish", "image": "fish.png", "position": [0.5, 0.5], "size": 0}]}, "size"),
    ({"objects": [{"id": "Fish", "image": "fish.png", "position": [0.5, 0.5]}]}, "id"),
    ({"objects": [{"id": "f" * 49, "image": "fish.png", "position": [0.5, 0.5]}]}, "id"),
    ({"objects": [{"id": "fish", "image": "fish.png", "position": [0.5, 0.5], "behaviors": ["float", "follow"]}]},
     "하나만"),
    ({"objects": [{"id": "fish", "image": "fish.png", "position": [0.5, 0.5], "behaviors": ["follow", "flee"]}]},
     "하나만"),
    ({"objects": [{"id": "fish", "image": "fish.png", "position": [0.5, 0.5], "behaviors": ["glow", "glow"]}]},
     "행동이 겹침"),
    ({"objects": [{"id": "fish", "image": "fish.png", "position": [0.5, 0.5], "behaviors": ["dance"]}]}, "behaviors"),
    ({"objects": [{"id": "fish", "image": "a.png", "position": [0.1, 0.1]},
                  {"id": "fish", "image": "b.png", "position": [0.2, 0.2]}]}, "객체 id가 겹침"),
    ({"objects": [{"id": f"o{i}", "image": "a.png", "position": [0.5, 0.5]} for i in range(13)]}, "objects"),
])
def test_contract_rejects(change, reason):
    with pytest.raises(ValidationError, match=reason):
        ArtworkAssets.model_validate({**FLUID_LAYERS, **change})


def test_missing_required_fields():
    for name in ("format", "version", "layers"):
        with pytest.raises(ValidationError, match=name):
            ArtworkAssets.model_validate({k: v for k, v in FLUID_LAYERS.items() if k != name})


def test_unknown_fields_are_warnings():
    data = {**FLUID_LAYERS, "camera": "wide",
            "layers": [{**FLUID_LAYERS["layers"][0], "opacity": 0.5}, *FLUID_LAYERS["layers"][1:]],
            "materials": [{**FLUID_LAYERS["materials"][0], "blend": "add"}]}
    assets = ArtworkAssets.model_validate(data)
    assert assets.unknown_fields() == ["camera", "layers[0].opacity", "materials[0].blend"]


def test_object_images_need_alpha(tmp_path):
    data = {**FLUID_LAYERS, "objects": [{"id": "fish", "image": "fish.png", "position": [0.3, 0.6],
                                         "behaviors": ["float", "glow"]}]}
    assets = write_files(tmp_path, data, opaque=("sky.png", "fish.png"))
    assert inspect_assets(tmp_path, assets) == ["객체 fish: 오려 낸 객체 PNG는 알파가 있어야 함"]
    png(tmp_path / "fish.png", alpha=True)
    assert inspect_assets(tmp_path, assets) == []
    assert "fish.png" in assets.files()


def test_round_trip_json():
    assets = ArtworkAssets.model_validate_json(json.dumps(FLUID_LAYERS))
    assert json.loads(assets.model_dump_json(exclude_none=True)) == {**FLUID_LAYERS, "objects": []}
