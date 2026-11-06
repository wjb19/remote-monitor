from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "services"))

from camera.manager import CameraManager  # noqa: E402
from core.jetson_discover import JetsonLocator  # noqa: E402
from core.mqtt_ingest import run_mqtt_ingest  # noqa: E402
from core.telemetry_store import TelemetryStore  # noqa: E402
from rmlab_shared.settings import MainSettings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("rmlab.core")

settings = MainSettings()
settings.data_root.mkdir(parents=True, exist_ok=True)

camera_manager = CameraManager(settings.data_root)
telemetry = TelemetryStore(settings.data_root / "telemetry.sqlite3")
locator = JetsonLocator(settings, settings.data_root)
STARTED = time.time()
WEB = ROOT / "web" / "public"
EDGE_DIR = settings.data_root / "edge_events"
EDGE_DIR.mkdir(parents=True, exist_ok=True)


def store_edge_jpeg(data: bytes, meta: dict) -> tuple[int, str]:
    stamp = float(meta.get("ts") or time.time())
    camera_id = meta.get("camera_id") or "hub"
    index = meta.get("frame_index")
    suffix = f"_{index}" if index is not None else ""
    name = f"{stamp:.6f}{suffix}_{camera_id}.jpg"
    path = EDGE_DIR / name
    path.write_bytes(data)
    return telemetry.add_edge_event(meta, str(path)), str(path)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    camera_manager.on_motion = store_edge_jpeg
    camera_manager.start()
    mqtt_task = asyncio.create_task(
        run_mqtt_ingest(telemetry, settings.mqtt_host, settings.mqtt_port)
    )
    discover_task = asyncio.create_task(locator.watch())
    try:
        yield
    finally:
        discover_task.cancel()
        mqtt_task.cancel()
        for task in (discover_task, mqtt_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        camera_manager.stop()


app = FastAPI(title="Remote Monitor Lab", version="0.1.0", lifespan=lifespan)
if WEB.is_dir():
    app.mount("/static", StaticFiles(directory=WEB), name="static")


class CameraPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    device: str | None = None
    width: int | None = Field(default=None, ge=160, le=3840)
    height: int | None = Field(default=None, ge=120, le=2160)
    fps: int | None = Field(default=None, ge=1, le=60)
    jpeg_quality: int | None = Field(default=None, ge=30, le=95)
    buffer_seconds: int | None = Field(default=None, ge=10, le=86400)
    max_gb: float | None = Field(default=None, ge=0.05, le=500)
    ring_stride: int | None = Field(default=None, ge=1, le=60)


class IlluminatorBody(BaseModel):
    on: bool


class TelemetryPoints(BaseModel):
    points: list[dict]


class AlertConfigPatch(BaseModel):
    voltage_min_v: float | None = Field(default=None, ge=3.0, le=6.0)
    voltage_alert_cooldown_s: int | None = Field(default=None, ge=30, le=86400)


class MotionConfigPatch(BaseModel):
    pre_s: float | None = Field(default=None, ge=0, le=15)
    post_s: float | None = Field(default=None, ge=0.5, le=60)
    threshold: float | None = Field(default=None, ge=0.5, le=100)
    cooldown_s: float | None = Field(default=None, ge=1, le=600)
    fps: float | None = Field(default=None, ge=1, le=15)
    device: str | None = None


def _evaluate_voltage_alerts(points: list[dict]) -> list[dict]:
    cfg = telemetry.get_alert_config()
    min_v = float(cfg.get("voltage_min_v", "4.60"))
    cooldown = float(cfg.get("voltage_alert_cooldown_s", "300"))
    fired: list[dict] = []
    for p in points:
        if p.get("metric") != "jetson.voltage_in":
            continue
        value = float(p["value"])
        if value >= min_v:
            continue
        if telemetry.recent_alert("voltage_low", cooldown):
            continue
        msg = f"Jetson supply voltage low: {value:.3f} V (threshold {min_v:.3f} V)"
        payload = {"value": value, "threshold": min_v, "tags": p.get("tags") or {}}
        telemetry.add_alert("voltage_low", msg, payload)
        log.warning(msg)
        fired.append({"kind": "voltage_low", "message": msg, "payload": payload})
    return fired


async def _probe(url: str, timeout: float = 2.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            body: Any
            try:
                body = r.json()
            except Exception:
                body = r.text[:200]
            return {"ok": r.is_success, "status_code": r.status_code, "body": body}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "service": "core",
        "uptime_s": round(time.time() - STARTED, 1),
        "region": settings.region,
        "cameras": len(camera_manager.list()),
    }


@app.get("/api/status")
async def status() -> dict:
    jetson_health, chirpstack = await asyncio.gather(
        _probe(f"{locator.url.rstrip('/')}/health"),
        _probe(f"{settings.chirpstack_url.rstrip('/')}/"),
    )
    cams = camera_manager.list()
    return {
        "main": {
            "ok": True,
            "advertise_host": settings.main_advertise_host,
            "api": f"http://{settings.main_advertise_host}:{settings.port}",
            "uptime_s": round(time.time() - STARTED, 1),
            "region": settings.region,
        },
        "jetson": {
            "url": locator.url,
            "ssh": locator.ssh,
            "discovery": locator.snapshot(),
            **jetson_health,
        },
        "chirpstack": {
            "url": settings.chirpstack_url,
            **chirpstack,
        },
        "mqtt": {
            "host": settings.mqtt_host,
            "port": settings.mqtt_port,
        },
        "cameras": [
            {
                "id": c["config"]["id"],
                "name": c["name"],
                "running": c["running"],
                "device": c["config"]["device"],
                "buffer_seconds": c["config"]["buffer_seconds"],
                "frame_count": c["buffer"]["frame_count"],
            }
            for c in cams
        ],
        "telemetry": {
            "jetson_voltage_in": telemetry.latest("jetson.voltage_in"),
            "tk1_supply_current_a": telemetry.latest("tk1.supply_current_a"),
            "tk1_supply_voltage_v": telemetry.latest("tk1.supply_voltage_v"),
            "tk1_supply_power_w": telemetry.latest("tk1.supply_power_w"),
            "tk1_tboard_c": telemetry.latest("tk1.tboard_c"),
            "lora_e5_temperature_c": telemetry.latest("lora_e5.temperature_c"),
            "lora_e5_vdd_v": telemetry.latest("lora_e5.vdd_v"),
            "nodes": telemetry.latest_per_node(
                ["node.temperature_c", "node.vdd_v", "node.humidity_pct", "node.uplink"]
            ),
            "gateway_rx": telemetry.latest("gateway.rx"),
            "gateway_rssi": telemetry.latest("gateway.rssi"),
            "location": telemetry.latest_location(),
            "alert_config": telemetry.get_alert_config(),
            "recent_alerts": telemetry.list_alerts(limit=5),
        },
        "edge_events": telemetry.list_edge_events(limit=40),
    }


@app.post("/api/telemetry/points")
async def api_telemetry_points(body: TelemetryPoints) -> dict:
    n = telemetry.insert_points(body.points)
    alerts = _evaluate_voltage_alerts(body.points)
    return {"ok": True, "inserted": n, "alerts": alerts}


@app.get("/api/telemetry/series")
async def api_telemetry_series(
    metric: str = Query(default="jetson.voltage_in"),
    since_s: float = Query(default=3600, ge=60, le=86400 * 30),
    limit: int = Query(default=2000, ge=10, le=20000),
) -> dict:
    return {"metric": metric, "points": telemetry.query(metric, since_s=since_s, limit=limit)}


@app.get("/api/alerts/config")
async def api_alerts_config_get() -> dict:
    return telemetry.get_alert_config()


@app.patch("/api/alerts/config")
async def api_alerts_config_patch(body: AlertConfigPatch) -> dict:
    updates = body.model_dump(exclude_none=True)
    return telemetry.set_alert_config(updates)


@app.get("/api/alerts")
async def api_alerts(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    return {"alerts": telemetry.list_alerts(limit=limit)}


@app.post("/api/edge/events")
async def api_edge_events(
    frame: UploadFile = File(...),
    ts: str | None = Form(default=None),
    score: str | None = Form(default=None),
    device: str | None = Form(default=None),
    camera_id: str | None = Form(default="jetson-cam0"),
    kind: str | None = Form(default="motion"),
    window_id: str | None = Form(default=None),
    frame_index: str | None = Form(default=None),
    frame_count: str | None = Form(default=None),
    offset_s: str | None = Form(default=None),
    pre_s: str | None = Form(default=None),
    post_s: str | None = Form(default=None),
    objects: str | None = Form(default=None),
    detector: str | None = Form(default=None),
    label: str | None = Form(default=None),
    aspect: str | None = Form(default=None),
) -> dict:
    data = await frame.read()
    if not data:
        raise HTTPException(400, "empty frame")
    stamp = float(ts) if ts else time.time()
    parsed_objects = None
    if objects:
        try:
            loaded = json.loads(objects)
            if isinstance(loaded, list):
                parsed_objects = loaded
        except (TypeError, ValueError):
            parsed_objects = None
    meta = {
        "ts": stamp,
        "score": float(score) if score is not None else None,
        "device": device,
        "camera_id": camera_id,
        "kind": kind,
        "window_id": window_id,
        "frame_index": int(frame_index) if frame_index not in (None, "") else None,
        "frame_count": int(frame_count) if frame_count not in (None, "") else None,
        "offset_s": float(offset_s) if offset_s not in (None, "") else None,
        "pre_s": float(pre_s) if pre_s not in (None, "") else None,
        "post_s": float(post_s) if post_s not in (None, "") else None,
        "objects": parsed_objects,
        "detector": detector,
        "label": label,
        "aspect": aspect,
    }
    eid, path = store_edge_jpeg(data, meta)
    return {"ok": True, "id": eid, "path": path, "meta": meta}


@app.post("/api/edge/audio")
async def api_edge_audio(
    audio: UploadFile = File(...),
    ts: str | None = Form(default=None),
    score: str | None = Form(default=None),
    device: str | None = Form(default=None),
    camera_id: str | None = Form(default="tk1-cam0"),
    kind: str | None = Form(default="audio"),
    window_id: str | None = Form(default=None),
    pre_s: str | None = Form(default=None),
    post_s: str | None = Form(default=None),
    duration_s: str | None = Form(default=None),
) -> dict:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio")
    stamp = float(ts) if ts else time.time()
    name = f"{stamp:.6f}_{camera_id}.wav"
    path = EDGE_DIR / name
    path.write_bytes(data)
    meta = {
        "ts": stamp,
        "score": float(score) if score is not None else None,
        "device": device,
        "camera_id": camera_id,
        "kind": kind or "audio",
        "window_id": window_id,
        "pre_s": float(pre_s) if pre_s not in (None, "") else None,
        "post_s": float(post_s) if post_s not in (None, "") else None,
        "duration_s": float(duration_s) if duration_s not in (None, "") else None,
    }
    eid = telemetry.add_edge_event(meta, str(path))
    return {"ok": True, "id": eid, "path": str(path), "meta": meta}


@app.get("/api/edge/events")
async def api_edge_events_list(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {"events": telemetry.list_edge_events(limit=limit)}


@app.get("/api/edge/events/{event_id}/frame")
async def api_edge_event_frame(event_id: int) -> Response:
    events = telemetry.list_edge_events(limit=500)
    match = next((e for e in events if e["id"] == event_id), None)
    if not match or not match.get("path"):
        raise HTTPException(404, "event not found")
    path = Path(match["path"])
    if not path.is_file():
        raise HTTPException(404, "frame missing on disk")
    media = "audio/wav" if path.suffix.lower() == ".wav" else "image/jpeg"
    return Response(content=path.read_bytes(), media_type=media)


@app.get("/api/edge/events/{event_id}/audio")
async def api_edge_event_audio(event_id: int) -> Response:
    events = telemetry.list_edge_events(limit=500)
    match = next((e for e in events if e["id"] == event_id), None)
    if not match or not match.get("path"):
        raise HTTPException(404, "event not found")
    path = Path(match["path"])
    if not path.is_file() or path.suffix.lower() != ".wav":
        raise HTTPException(404, "audio missing on disk")
    return Response(content=path.read_bytes(), media_type="audio/wav")


@app.get("/api/cameras")
async def api_cameras() -> dict:
    return {"cameras": camera_manager.list()}


@app.get("/api/cameras/{camera_id}")
async def api_camera(camera_id: str) -> dict:
    w = camera_manager.get(camera_id)
    if not w:
        raise HTTPException(404, "camera not found")
    return w.status()


@app.patch("/api/cameras/{camera_id}")
async def api_camera_patch(camera_id: str, body: CameraPatch) -> dict:
    patch = body.model_dump(exclude_none=True)
    try:
        return camera_manager.update(camera_id, patch)
    except KeyError:
        raise HTTPException(404, "camera not found") from None


@app.get("/api/cameras/{camera_id}/snapshot")
async def api_snapshot(camera_id: str) -> Response:
    w = camera_manager.get(camera_id)
    if not w:
        raise HTTPException(404, "camera not found")
    jpeg = w.latest_jpeg()
    if not jpeg:
        raise HTTPException(503, "no frame yet")
    return Response(content=jpeg, media_type="image/jpeg")


@app.get("/api/cameras/{camera_id}/stream")
async def api_stream(camera_id: str):
    w = camera_manager.get(camera_id)
    if not w:
        raise HTTPException(404, "camera not found")

    boundary = b"frame"

    async def gen():
        while True:
            jpeg = w.latest_jpeg()
            if jpeg:
                yield (
                    b"--" + boundary + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: "
                    + str(len(jpeg)).encode()
                    + b"\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )
            await asyncio.sleep(0.1)

    return StreamingResponse(
        gen(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary.decode()}",
    )


@app.get("/api/cameras/{camera_id}/buffer")
async def api_buffer(
    camera_id: str,
    limit: int = Query(default=120, ge=1, le=2000),
) -> dict:
    w = camera_manager.get(camera_id)
    if not w:
        raise HTTPException(404, "camera not found")
    return {"stats": w.ring.stats(), "frames": w.ring.list_frames(limit=limit)}


@app.get("/api/cameras/{camera_id}/buffer/frame")
async def api_buffer_frame(
    camera_id: str,
    ts: float = Query(..., description="Unix timestamp to seek near"),
) -> Response:
    w = camera_manager.get(camera_id)
    if not w:
        raise HTTPException(404, "camera not found")
    path = w.ring.frame_near(ts)
    if not path or not path.is_file():
        raise HTTPException(404, "no frame near timestamp")
    return Response(content=path.read_bytes(), media_type="image/jpeg")


@app.post("/api/cameras/{camera_id}/illuminator")
async def api_illuminator(camera_id: str, body: IlluminatorBody) -> dict:
    try:
        return camera_manager.set_illuminator(camera_id, body.on)
    except KeyError:
        raise HTTPException(404, "camera not found") from None


@app.get("/api/jetson/motion")
async def api_jetson_motion_get() -> dict:
    result = await _probe(f"{locator.url.rstrip('/')}/api/motion", timeout=4.0)
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "jetson motion config unavailable")
    return result["body"]


@app.post("/api/jetson/motion")
async def api_jetson_motion_post(body: MotionConfigPatch) -> dict:
    payload = body.model_dump(exclude_none=True)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.post(
                f"{locator.url.rstrip('/')}/api/motion",
                json=payload,
            )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:200])
    return r.json()


@app.post("/api/jetson/discover")
async def api_jetson_discover() -> dict:
    return await locator.scan()


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    html_path = WEB / "index.html"
    if html_path.is_file():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Remote Monitor Lab</h1><p>Missing web/public/index.html</p>"
