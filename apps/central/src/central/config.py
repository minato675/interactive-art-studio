"""환경 변수 설정. 비밀 값은 코드·저장소에 두지 않는다. 모든 값은 Settings()를 만들 때 읽는다."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = ""):
    return field(default_factory=lambda: os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("STUDIO_DATA", "runtime")))
    database_url: str = _env("DATABASE_URL")             # 비면 data_dir/studio.db (SQLite)
    operator_token: str = _env("OPERATOR_TOKEN")         # 비면 토큰 검사 생략(로컬 개발)
    renderer: str = _env("RENDERER", "both")             # both(기본, 임시) / ue / td(보관)
    llm_provider: str = _env("LLM_PROVIDER", "none")     # none(규칙) / gms(SSAFY GMS) / ollama(로컬)
    llm_fallback: str = _env("LLM_FALLBACK")             # 비움 / ollama: 주 LLM에 닿지 못하면 이 PC의 Ollama(EXAONE)에 물음
    gms_key: str = _env("GMS_KEY")                       # 비밀 값. 저장소에 두지 않음
    gms_model: str = _env("GMS_MODEL", "gpt-6.1-sol")
    director_model: str = _env("DIRECTOR_MODEL", "gpt-6.1-sol")   # 전시 디렉터만 GMS에서 쓰는 모델
    gms_url: str = _env("GMS_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses")
    ollama_model: str = _env("OLLAMA_MODEL", "exaone3.5:7.8b")   # 비상업 라이선스. 상업 배포 시 qwen3:4b 등으로 지정
    ollama_url: str = _env("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
    recent_count: int = 5   # 전시 모드 반복 회피에 쓰는 최근 작품 수
    broker_url: str = _env("BROKER_URL")
    planning_queue: str = _env("PLANNING_QUEUE", "planning")
    agent_keys_json: str = _env("AGENT_KEYS_JSON", "{}")
    lease_seconds: int = field(default_factory=lambda: int(os.environ.get("LEASE_SECONDS", "120")))
    max_attempts: int = field(default_factory=lambda: int(os.environ.get("JOB_MAX_ATTEMPTS", "3")))
    head_provider: str = _env("HEAD_PROVIDER", "gms")
    head_model: str = _env("HEAD_MODEL", "gpt-6.1-sol")
    head_url: str = _env("HEAD_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses")
    head_timeout_seconds: int = field(default_factory=lambda: int(os.environ.get("HEAD_TIMEOUT_SECONDS", "600")))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""), repr=False)
    artifact_max_bytes: int = field(default_factory=lambda: int(os.environ.get("ARTIFACT_MAX_BYTES", str(64 * 2**20))))
    # 전시 자동 순환(infra/HEAD_EXHIBITION_FEED.md)
    exhibition_feed_max_in_flight: int = field(
        default_factory=lambda: int(os.environ.get("EXHIBITION_FEED_MAX_IN_FLIGHT", "10")))
    exhibition_feed_stale_seconds: int = field(   # 임시값. S15P31A504-218에서 확정
        default_factory=lambda: int(os.environ.get("EXHIBITION_FEED_STALE_SECONDS", "1800")))
    exhibition_feed_retry_seconds: int = field(
        default_factory=lambda: int(os.environ.get("EXHIBITION_FEED_RETRY_SECONDS", "60")))

    def __post_init__(self):
        if self.renderer not in ("td", "ue", "both"):
            raise ValueError(f"RENDERER는 td / ue / both 중 하나: {self.renderer}")
        if self.lease_seconds < 10 or not 1 <= self.max_attempts <= 20:
            raise ValueError("LEASE_SECONDS >= 10, JOB_MAX_ATTEMPTS는 1~20")
        if self.artifact_max_bytes < 1:
            raise ValueError("ARTIFACT_MAX_BYTES는 양수여야 합니다")
        if self.head_timeout_seconds < 1:
            raise ValueError("HEAD_TIMEOUT_SECONDS는 양수여야 합니다")
        if min(self.exhibition_feed_max_in_flight, self.exhibition_feed_stale_seconds,
               self.exhibition_feed_retry_seconds) < 1:
            raise ValueError("EXHIBITION_FEED_* 설정은 양수여야 합니다")
        registry = json.loads(self.agent_keys_json)
        tokens = []
        for config in registry.values():
            allowed = {"generation": {"background_generate", "assets_generate"},
                       "production": {"bundle_verify", "ue_assemble", "ue_verify"}, "show": {"ue_publish"}}.get(config.get("role"), set())
            if "enabled" in config and not isinstance(config["enabled"], bool):
                raise ValueError("노드 enabled는 bool이어야 합니다")
            kinds = config.get("job_kinds")
            if "job_kinds" in config and (not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds)
                                      or len(kinds) != len(set(kinds)) or not set(kinds).issubset(allowed)):
                raise ValueError("노드 역할에 맞는 job_kinds 목록이 필요합니다")
            if config.get("role") not in ("generation", "production", "show") or not config.get("catalog_version"):
                raise ValueError("Agent 역할·카탈로그 버전을 지정해야 합니다")
            token = config.get("token", "")
            if len(token) < 16:
                raise ValueError("Agent 토큰은 16자 이상이어야 합니다")
            tokens.append(token)
        if len(tokens) != len(set(tokens)):
            raise ValueError("Agent마다 다른 토큰을 사용해야 합니다")

    def llm_args(self, provider: str, director: bool = False) -> tuple[str, str, str]:
        """그 공급자의 (모델, 주소, 키). make_llm에 그대로 넘긴다. director면 GMS 모델만 디렉터용으로 바꾼다."""
        if provider == "gms":
            return self.director_model if director else self.gms_model, self.gms_url, self.gms_key
        return self.ollama_model, self.ollama_url, ""

    def head_connection(self, profile: str):
        from studio.head.responses import ResponsesHead

        if profile == "test":
            return ResponsesHead("openai", "gpt-6-luna", "https://api.openai.com/v1/responses", self.openai_api_key,
                                 timeout_seconds=self.head_timeout_seconds)
        return ResponsesHead(self.head_provider, self.head_model, self.head_url, self.gms_key,
                             timeout_seconds=self.head_timeout_seconds)

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'studio.db').as_posix()}"

    @property
    def delivery_dir(self) -> Path:
        """UE 전달 폴더 (release.json → scene.json)"""
        return self.data_dir / "delivery"

    @property
    def td_delivery_dir(self) -> Path:
        """TD 전달 폴더 (release.json → artwork.json)"""
        return self.data_dir / "delivery_td"

    @property
    def review_delivery_dir(self) -> Path:
        """개발용 UE 후보 검수 폴더. 파일 게시와 실제 시연 노드의 적용 상태는 별개다."""
        return self.data_dir / "review" / "delivery"

    @property
    def review_td_delivery_dir(self) -> Path:
        """최종 검수 때 TD 검수 폴더"""
        return self.data_dir / "review" / "delivery_td"
