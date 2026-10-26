"""시험용 가짜 UE Shipping 실행본. 러너(studio.verify.ue_runner)가 기대하는 runtime 파일 계약을 흉내 낸다.

-ArtworkRelease=<release.json>: 버전이 바뀌면 Reading → Applied(또는 Failed) 순서로 release.json.status.json을 쓴다
  (장면에 "FakeFail"이 있으면 Failed). -ArtworkProbe=<폴더>: Trace.jsonl(Contacts · Hosts[].BehaviorInputs · 긴 프레임),
  Capture.json → <CaptureId>.png. runtime처럼 같은 CaptureId 반복과 이미 있는 PNG는 무시한다.
UDP 손 입력은 -FakePort=<포트>로 받는다. 장면 "FakeMode": react(손 자리가 밝아짐) · still(손을 무시) · stay(손자국이 남음) ·
drift(손과 무관하게 띠가 흐름) · driftreact(띠가 흐르면서 손에도 반응) · floodreact(화면 전체가 계속 바뀌면서 손에도 반응) ·
blank(단색 화면) · fade(손자국이 "FakeHalfLife"초 반감기로 옅어짐) ·
fadeflicker(손이 떠난 뒤 자국이 캡처마다 켜졌다 꺼짐). 장면 "FakeBand": drift 계열 띠 반폭. *hidden(drifthidden · widehidden · floodhidden): 행동 입력은 닿지만 화면에는 손 반응이 안 그려짐(앞 층이
반응을 가리거나 재질 출력이 끊긴 경우). widehidden은 넓은 물결이 흐른다.
-FakeFirstDelay=<초>: 첫 버전 처리를 늦춘다(시작 때 남은 status가 그동안 보인다). -FakeSkipFirstCapture=1: 첫 캡처 요청을 무시한다.
선택: -FakeLongFrames=<n>(행마다 저장된 긴 프레임) -FakeDropped=<n>(행마다 DroppedLongFrames) -FakeRgba=1(RGBA PNG)
-FakePrepareSeconds=<초> -FakePreparedBytes=<바이트> -FakeStaleStatus=1(시작 때 다른 PID의 Applied 상태를 남겨 둠)
-FakeSize=<가로>x<세로>(캡처 크기, 기본 러너 -ResX/-ResY)
-FakeStickyTrace=1: 이미 쓰던 Trace.jsonl이 사라지면 계측 · 캡처를 영구히 멈춘다(runtime이 쓰기 실패 뒤 멈추는 경우)
"""
import json
import os
import socket
import struct
import sys
import time
import uuid
import zlib
from pathlib import Path

args = dict(arg.split("=", 1) for arg in sys.argv[1:] if arg.startswith("-") and "=" in arg)
release, probe = Path(args["-ArtworkRelease"]), Path(args["-ArtworkProbe"])
port = int(args.get("-FakePort", 7001))
long_frames, dropped = int(args.get("-FakeLongFrames", 0)), int(args.get("-FakeDropped", 0))
rgba = args.get("-FakeRgba") == "1"
prepare_seconds, prepared_bytes = float(args.get("-FakePrepareSeconds", 0.2)), int(args.get("-FakePreparedBytes", 1024))
size = args.get("-FakeSize") or f"{args.get('-ResX', 64)}x{args.get('-ResY', 36)}"
first_delay, skip_first_capture = float(args.get("-FakeFirstDelay", 0)), args.get("-FakeSkipFirstCapture") == "1"
WIDTH, HEIGHT = (int(v) for v in size.split("x"))
probe.mkdir(parents=True, exist_ok=True)
receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
receiver.bind(("127.0.0.1", port))
receiver.setblocking(False)
session, request, version, scene, last_capture = str(uuid.uuid4()), 0, None, {}, None
hands, marks, last_trace, pending = {}, set(), 0.0, None
fading, flicker = {}, 0   # 옅어지는 손자국: 위치 → 마지막으로 손이 있던 시각
started = time.monotonic()
sticky, probe_stopped = args.get("-FakeStickyTrace") == "1", False


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    for _ in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:   # 러너가 읽는 중
            time.sleep(0.01)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):   # 러너가 교체하는 중이면 다음 루프에서 다시 읽는다
        return None


def png(path, pixels):
    color = 6 if rgba else 2
    rows = bytearray()
    for y in range(HEIGHT):
        rows.append(0)
        line = pixels[y * WIDTH * 3:(y + 1) * WIDTH * 3]
        if rgba:
            for x in range(WIDTH):
                rows += line[x * 3:x * 3 + 3] + b"\x80"   # 알파는 화면 판정에 쓰지 않는다
        else:
            rows += line

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def active_hands(now):
    return [point for point, seen in hands.values() if now - seen < 0.3]


def frame():
    global flicker
    mode, now = scene.get("FakeMode", "react"), time.monotonic()
    pixels = bytearray()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if mode == "blank":
                pixels += b"\x20\x20\x20"
            elif mode in ("floodreact", "floodhidden"):   # 화면 전체가 매번 바뀐다(강한 평소 움직임)
                pixels += bytes([(x * 7 + y * 3 + int(now * 997)) % 120, (x * 5 + int(now * 541)) % 120, 60])
            else:
                pixels += bytes([20 + (x * 3) % 40, 30 + (y * 5) % 50, 60])   # 단색이 아닌 바탕
    points = active_hands(now)
    spots = set()
    if mode == "stay":
        marks.update((int(p[0] * (WIDTH - 1)), int(p[1] * (HEIGHT - 1))) for p in points)
        spots = set(marks)
    elif mode in ("react", "driftreact", "floodreact"):
        spots = {(int(p[0] * (WIDTH - 1)), int(p[1] * (HEIGHT - 1))) for p in points}
    if mode in ("fade", "fadeflicker"):   # 손자국이 남았다가 반감기로 옅어진다
        fading.update({(int(p[0] * (WIDTH - 1)), int(p[1] * (HEIGHT - 1))): now for p in points})
        flicker = 0 if points else flicker + 1
        for (x, y), seen in fading.items():
            level = 0.5 ** ((now - seen) / scene.get("FakeHalfLife", 1.0))
            if mode == "fadeflicker" and flicker:
                level = flicker % 2   # 손이 떠난 뒤 캡처마다 켜졌다 꺼진다(가라앉지 않음)
            for dy in range(-6, 7):
                for dx in range(-6, 7):
                    if 0 <= x + dx < WIDTH and 0 <= y + dy < HEIGHT:
                        i = ((y + dy) * WIDTH + x + dx) * 3
                        pixels[i:i + 3] = bytes(round(v + (240 - v) * level) for v in pixels[i:i + 3])
    for x, y in spots:   # 손 반응: 손 자리에 13픽셀 자국
        for dy in range(-6, 7):
            for dx in range(-6, 7):
                if 0 <= x + dx < WIDTH and 0 <= y + dy < HEIGHT:
                    i = ((y + dy) * WIDTH + x + dx) * 3
                    pixels[i:i + 3] = b"\xf0\xf0\xf0"
    # 손과 무관하게 세로 띠가 흐른다(평소 움직임)
    if mode in ("drift", "driftreact", "drifthidden", "widehidden"):
        column = int((now - started) * 25) % WIDTH
        half = 10 if mode == "widehidden" else scene.get("FakeBand", 1)   # 띠 반폭(픽셀)
        for y in range(HEIGHT):
            for x in range(column - half, column + half + 1):
                if 0 <= x < WIDTH:
                    pixels[(y * WIDTH + x) * 3:(y * WIDTH + x) * 3 + 3] = b"\xc0\xe0\x40"
    return pixels


if args.get("-FakeStaleStatus") == "1" and (pointer := read_json(release)):
    write_json(release.with_suffix(".json.status.json"), {
        "RuntimeSessionId": "old", "RequestId": 7, "ProcessId": 999999, "Phase": "Applied", "Applied": True,
        "RequestedVersion": pointer["Version"], "ActiveReleaseVersion": pointer["Version"], "Error": ""})

while True:
    while True:
        try:
            packet = json.loads(receiver.recv(65536))
        except (BlockingIOError, ConnectionResetError):
            break
        for item in packet["frames"]:
            for sample in item["samples"]:
                hands[item["source_id"]] = (sample["value"], time.monotonic())
    now = time.monotonic()
    pointer = read_json(release)
    if pointer and pointer["Version"] != version and now - started >= first_delay:
        version, request = pointer["Version"], request + 1
        path = release.parent / "versions" / version / "scene.json"
        scene = read_json(path) or {"FakeFail": True}
        marks.clear()
        fading.clear()
        pending = now + 0.3
        write_json(release.with_suffix(".json.status.json"), {
            "RuntimeSessionId": session, "RequestId": request, "ProcessId": os.getpid(), "Phase": "Reading",
            "Applied": False, "RequestedVersion": version, "ActiveReleaseVersion": "", "Error": ""})
    if pending is not None and now >= pending:
        pending, failed = None, "FakeFail" in scene
        write_json(release.with_suffix(".json.status.json"), {
            "RuntimeSessionId": session, "RequestId": request, "ProcessId": os.getpid(),
            "Phase": "Failed" if failed else "Applied", "Applied": not failed, "RequestedVersion": version,
            "ActiveReleaseVersion": "" if failed else version, "Error": "가짜 실패" if failed else "",
            "PreparedBytes": prepared_bytes, "PrepareSeconds": prepare_seconds})
    if sticky and last_trace and not (probe / "Trace.jsonl").exists():
        probe_stopped = True
    if probe_stopped:
        time.sleep(0.02)
        continue
    if now - last_trace > 0.25:
        last_trace = now
        contacts = [{"SourceId": source} for source, (_, seen) in hands.items() if now - seen < 0.3]
        reacting = scene.get("FakeMode", "react") in ("react", "stay", "driftreact", "floodreact", "drifthidden",
                                                      "widehidden", "floodhidden", "fade", "fadeflicker")
        host = {"HostName": "Fake", "BehaviorInputs": [
            {"BindingId": "Fake", "Active": bool(contacts) and reacting,
             "ActiveContacts": len(contacts) if reacting else 0}]}
        row = {"ProcessId": os.getpid(), "MonotonicSeconds": now, "Contacts": contacts,
               "LongFrames": [{}] * long_frames, "DroppedLongFrames": dropped, "Hosts": [host]}
        try:
            with (probe / "Trace.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(row) + "\n")
        except PermissionError:
            pass
    control = read_json(probe / "Capture.json")
    capture = (control or {}).get("CaptureId")
    if capture and capture != last_capture and not (probe / f"{capture}.png").exists():
        last_capture = capture
        if skip_first_capture:
            skip_first_capture = False   # 실기에서 가끔 캡처가 빠지는 경우
        else:
            png(probe / f"{capture}.png", frame())
    time.sleep(0.02)
