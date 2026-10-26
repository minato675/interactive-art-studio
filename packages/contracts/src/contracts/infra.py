"""파일 자체는 전송하지 않는 분산 작업 계약."""
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import CreateRequest
from .ue import GenerationConditions

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class FileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    sha256: Digest
    size: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (path.is_absolute() or value != path.as_posix() or any(p in ("", ".", "..") for p in value.split("/"))
                or any(c in value for c in '\\:<>"|?*\x00') or any(ord(c) < 32 for c in value)):
            raise ValueError("번들 내부 상대 경로만 허용합니다")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if any(p.endswith((" ", ".")) or p.split(".")[0].upper() in reserved for p in path.parts):
            raise ValueError("Windows에서 사용할 수 없는 파일 이름입니다")
        return value


class Bundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    artwork_id: Identifier
    version: Identifier
    catalog_version: Identifier
    kind: Literal["runtime_data", "pre_cooked_ue"] = "runtime_data"
    files: list[FileRef] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def distinct_paths(self):
        names = [f.name.casefold() for f in self.files]
        if len(set(names)) != len(names):
            raise ValueError("대소문자를 제외하고도 파일 이름이 달라야 합니다")
        if any(a.startswith(b + "/") for a in names for b in names if a != b):
            raise ValueError("파일과 폴더 경로가 충돌합니다")
        return self


class GenerationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artwork_id: Identifier
    version: Identifier
    catalog_version: Identifier = "v1"
    prompt: str = Field(min_length=1, max_length=16000)
    executor: Literal["manual_codex"] = "manual_codex"
    conditions: GenerationConditions | None = None


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artwork_id: Identifier
    version: Identifier
    files: list[FileRef] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def valid_paths(self):
        Bundle(artwork_id=self.artwork_id, version=self.version, catalog_version="v1", files=self.files)
        return self


class AssemblyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_job_id: Identifier
    source_name: str
    name: str

    @field_validator("source_name", "name")
    @classmethod
    def safe_path(cls, value):
        return FileRef.safe_name(value)


class AssemblySpec(BaseModel):
    target_node: Identifier | None = None
    model_config = ConfigDict(extra="forbid")
    artwork_id: Identifier
    version: Identifier
    catalog_version: Identifier = "v1"
    spec: dict
    assets: dict
    files: list[AssemblyFile] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_files(self):
        refs = [FileRef(name=f.name, sha256="0" * 64, size=0) for f in self.files]
        Bundle(artwork_id=self.artwork_id, version=self.version, catalog_version=self.catalog_version, files=refs)
        if any(f.name.split("/")[0].casefold() in {"scene.json", "spec.json", "assets.json", "bundle.json",
                                                   "assembly-report.json"} for f in self.files):
            raise ValueError("조립 시스템 파일 이름은 입력으로 사용할 수 없습니다")
        return self


class RuntimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artwork_id: Identifier
    version: Identifier
    catalog_version: Identifier = "v1"
    source_job_id: Identifier
    target_node: Identifier


class PublishApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Identifier
    target_node: Identifier
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Identifier
    kind: Literal["diagnostic", "draft", "bundle_verify", "background_generate", "assets_generate", "ue_assemble", "ue_verify", "ue_publish"]
    draft: CreateRequest | None = None
    bundle: Bundle | None = None
    generation: GenerationSpec | None = None
    assembly: AssemblySpec | None = None
    runtime: RuntimeSpec | None = None
    file_transport: Literal["shared", "http"] = "shared"
    depends_on: list[Identifier] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def payload_matches_kind(self):
        if (self.kind == "draft") != (self.draft is not None):
            raise ValueError("draft 작업에는 draft 요청만 지정합니다")
        if (self.kind == "bundle_verify") != (self.bundle is not None):
            raise ValueError("bundle_verify 작업에는 bundle을 지정합니다")
        if self.kind.endswith("_generate") != (self.generation is not None):
            raise ValueError("생성 작업에는 generation 요청을 지정합니다")
        if (self.kind == "ue_assemble") != (self.assembly is not None):
            raise ValueError("ue_assemble 작업에는 assembly 입력을 지정합니다")
        if (self.kind in ("ue_verify", "ue_publish")) != (self.runtime is not None):
            raise ValueError("실행 검증·게시에는 runtime 입력을 지정합니다")
        if self.runtime and (self.file_transport != "http" or self.runtime.source_job_id not in self.depends_on):
            raise ValueError("실행 작업에는 HTTP와 직접 선행 조립 작업이 필요합니다")
        if self.depends_on and not (self.generation or self.assembly or self.runtime):
            raise ValueError("선행 관계는 생성·조립 작업에 지정합니다")
        if self.assembly:
            if self.file_transport != "http" or not self.depends_on:
                raise ValueError("UE 조립에는 HTTP 전송과 선행 생성 작업이 필요합니다")
            if any(f.source_job_id not in self.depends_on for f in self.assembly.files):
                raise ValueError("조립 입력 파일은 직접 선행 작업의 결과여야 합니다")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("선행 작업 ID가 중복됩니다")
        if self.kind == "assets_generate" and not self.depends_on:
            raise ValueError("에셋 생성에는 배경 선행 작업이 필요합니다")
        if len(self.model_dump_json().encode()) > 64 * 1024:
            raise ValueError("작업 메시지는 64 KiB 이하의 메타데이터만 허용합니다")
        return self


class LeaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=128)
    progress: int = Field(default=0, ge=0, le=100)
    error: str = Field(default="", max_length=500)


class Completion(LeaseUpdate):
    result: GenerationResult | None = None


class PlaybackReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    state: Literal["Disabled", "Empty", "WaitingForRuntime", "Playing", "WaitingForNext", "Applying", "Completed", "Error"]
    current_verification_job_id: Identifier | None = None
    next_verification_job_id: Identifier | None = None
    duration_seconds: int = Field(default=120, ge=5, le=3600, strict=True)
    elapsed_seconds: float = Field(default=0, ge=0)
    cached_count: int = Field(default=0, ge=0, le=200)
    last_sync_at: float | None = None
    error: str = Field(default="", max_length=128)
    cached_verification_job_ids: list[Identifier] | None = Field(default=None, max_length=200)
    cache_errors: dict[Identifier, str] | None = Field(default=None, max_length=200)
    current_repeat: int | None = Field(default=None, ge=1, le=100)


class ShowReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["Idle", "Staged", "Ready", "Applied", "Failed", "Unknown"]
    desired_version: str | None = Field(default=None, max_length=96)
    active_version: str | None = Field(default=None, max_length=96)
    runtime_session: str | None = Field(default=None, max_length=128)
    runtime_pid: int | None = Field(default=None, gt=0)
    error: str = Field(default="", max_length=500)
    playback: PlaybackReport | None = None
