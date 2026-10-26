"""central API 클라이언트. 운영 앱은 central 코드를 import하지 않고 이 HTTP 호출과 contracts 모델만 쓴다."""
import ssl
from pathlib import Path
from urllib.parse import quote

import httpx

from contracts.feedback import FeedbackCurrent, FeedbackRecord
from contracts.schemas import ArtworkOut


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


class CentralClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", token: str | None = None,
                 http: httpx.Client | None = None):
        """http를 넘기면 그것을 쓴다(테스트에서 FastAPI TestClient)."""
        headers = {"X-Operator-Token": token} if token else {}
        self.on_unauthorized = None
        context = ssl.create_default_context()
        ca = Path(__file__).with_name("assets") / "central-ca.crt"
        if ca.exists():
            context.load_verify_locations(cafile=str(ca))
        # 자동 모드·확정은 생성이 끝날 때까지 기다리는 호출이라 시간 제한을 넉넉히 둔다.
        self.http = http or httpx.Client(base_url=base_url, headers=headers, timeout=600, verify=context)

    def _call(self, method: str, path: str, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            if response.status_code == 401 and not path.startswith("/auth/") and self.on_unauthorized:
                self.on_unauthorized()
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, detail if isinstance(detail, str) else str(detail))
        return response.json() if response.content else None

    def login(self, username: str, password: str) -> dict:
        result = self._call("POST", "/auth/login", json={"username": username, "password": password}, timeout=20)
        self.http.headers.pop("X-Operator-Token", None)
        self.http.headers["Authorization"] = "Bearer " + result.pop("access_token")
        return result

    def logout(self):
        try:
            self._call("POST", "/auth/logout", timeout=5)
        finally:
            self.http.headers.pop("Authorization", None)

    def accounts(self):
        return self._call("GET", "/auth/accounts", timeout=15)

    def create_account(self, username, password, role="operator"):
        return self._call("POST", "/auth/accounts", json={"username": username, "password": password, "role": role},
                          timeout=20)

    def update_account(self, username, changes):
        return self._call("PUT", f"/auth/accounts/{username}", json=changes, timeout=20)

    def change_password(self, current_password, new_password):
        self._call("POST", "/auth/password", json={"current_password": current_password, "new_password": new_password},
                   timeout=20)
        self.http.headers.pop("Authorization", None)

    def families(self) -> list[dict]:
        return self._call("GET", "/api/families")

    def native_artworks(self):
        return self._call("GET", "/infra/native-artworks", timeout=12)

    def native_play(self, body):
        return self._call("POST", "/infra/native-artworks/play", json=body, timeout=12)

    def native_stop(self, body):
        return self._call("POST", "/infra/native-artworks/stop", json=body, timeout=12)

    def create(self, body: dict) -> ArtworkOut:
        """기존 동기 제작·전시 요청과 과거 검수 기록의 호환 경로."""
        return ArtworkOut.model_validate(self._call("POST", "/api/artworks", json=body))

    def submit_head(self, body: dict, request_id: str) -> dict:
        return self._call("POST", "/infra/head/intakes", json={"request_id": request_id, "request": body}, timeout=20)

    def head_intakes(self) -> list[dict]:
        return self._call("GET", "/infra/head/intakes", timeout=12)

    def exhibition_feed(self) -> dict:
        """전시 자동 순환 상태(켜짐 · 쉬는 이유 · 진행 중 자동 작품 수)."""
        return self._call("GET", "/infra/exhibition/feed", timeout=12)

    def set_exhibition_feed(self, enabled: bool) -> dict:
        return self._call("PUT", "/infra/exhibition/feed", json={"enabled": enabled}, timeout=12)

    def draft(self, body: dict) -> ArtworkOut:
        """편집 모드: AI 초안(status=awaiting)."""
        return ArtworkOut.model_validate(self._call("POST", "/api/drafts", json=body))

    def confirm(self, artwork_id: int, changes: dict) -> ArtworkOut:
        """편집 모드: 바꾼 칸만 보내 확정한다."""
        return ArtworkOut.model_validate(self._call("POST", f"/api/artworks/{artwork_id}/confirm", json=changes))

    def review(self, artwork_id: int, approve: bool, note: str = "") -> ArtworkOut:
        """검수 대기 작품 승인·반려. 승인이면 남은 제작이 끝날 때까지 기다린다."""
        return ArtworkOut.model_validate(self._call("POST", f"/api/artworks/{artwork_id}/review",
                                                    json={"approve": approve, "note": note}))

    def review_file(self, artwork_id: int, name: str) -> bytes:
        """소재 검수 때 보여 줄 소재 파일."""
        response = self.http.get(f"/api/artworks/{artwork_id}/review/files/{name}")
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.text)
        return response.content

    def artworks(self, limit: int = 30) -> list[ArtworkOut]:
        return [ArtworkOut.model_validate(row) for row in self._call("GET", "/api/artworks",
                                                                      params={"limit": limit})]

    def nodes(self) -> list[dict]:
        """인증된 Show Agent의 상태 보고. 작품 생성 결과와 별도 조회한다."""
        return self._call("GET", "/infra/nodes")

    def monitor_snapshot(self, offset: int = 0, limit: int = 200) -> dict:
        """운영 현황용 조회. 오래 걸리는 작품 생성 호출과 별도 제한 시간을 적용한다."""
        return {"jobs": self._call("GET", "/infra/jobs", params={"offset": offset, "limit": limit}, timeout=12),
                "nodes": self._call("GET", "/infra/nodes", timeout=12)}

    def job(self, job_id: str) -> dict:
        return self._call("GET", f"/infra/jobs/{quote(job_id, safe='')}", timeout=12)

    def head_workflow(self, workflow_id: str) -> dict:
        return self._call("GET", f"/infra/head/workflows/{quote(workflow_id, safe='')}", timeout=12)

    def feedback_snapshot(self, offset: int = 0) -> dict:
        current = FeedbackCurrent.model_validate(self._call("GET", "/infra/feedback/current", timeout=12))
        records = self._call("GET", "/infra/feedback", params={"limit": 50, "offset": offset}, timeout=12)
        return {"current": current.model_dump(), "records": [FeedbackRecord.model_validate(r).model_dump() for r in records]}

    def save_feedback(self, body: dict) -> dict:
        return FeedbackRecord.model_validate(self._call("POST", "/infra/feedback", json=body, timeout=12)).model_dump()

    def change_feedback(self, feedback_id: str, enabled: bool, revision: int) -> dict:
        return FeedbackRecord.model_validate(self._call("PATCH", f"/infra/feedback/{quote(feedback_id, safe='')}",
                   json={"enabled": enabled, "expected_revision": revision}, timeout=12)).model_dump()

    def artwork(self, artwork_id: int) -> ArtworkOut:
        return ArtworkOut.model_validate(self._call("GET", f"/api/artworks/{artwork_id}"))

    def show_dashboard(self, node_id: str | None = None, publication_id: str | None = None,
                       library_offset: int = 0, search: str = "") -> dict:
        params = {"library_offset": library_offset, "search": search}
        if node_id:
            params["node_id"] = node_id
        value = self._call("GET", "/infra/show-dashboard", params=params, timeout=12)
        value["publication"] = self.job(publication_id) if publication_id else None
        return value

    def save_show_plan(self, node_id: str, revision: int, plan: dict) -> dict:
        return self._call("PUT", f"/infra/show-dashboard/{quote(node_id, safe='')}",
                          json={"expected_revision": revision, "plan": plan}, timeout=12)

    def show_preview(self, ref: dict) -> bytes:
        """고정된 작업 결과 파일만 인증 다운로드. 응답 크기를 제한한다."""
        import hashlib

        path = f"/infra/jobs/{quote(ref['job_id'], safe='')}/files/{quote(ref['sha256'], safe='')}"
        with self.http.stream("GET", path, timeout=12) as response:
            if response.status_code >= 400:
                if response.status_code == 401 and self.on_unauthorized:
                    self.on_unauthorized()
                raise ApiError(response.status_code, "미리보기 조회 실패")
            value = bytearray()
            for chunk in response.iter_bytes():
                value.extend(chunk)
                if len(value) > 16 * 1024 * 1024:
                    raise ValueError("preview_too_large")
        if len(value) != ref["size"] or hashlib.sha256(value).hexdigest() != ref["sha256"]:
            raise ValueError("preview_integrity_mismatch")
        return bytes(value)

    def apply_show_artwork(self, verification_id: str, body: dict) -> dict:
        return self._call("POST", f"/infra/jobs/{quote(verification_id, safe='')}/approve-publication",
                          json=body, timeout=12)
