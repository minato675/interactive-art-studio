"""UE 설치 리소스의 보고와 서버가 고정하는 생성 조건. 실행본 검증을 의미하지 않는다."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TemplateId = Literal["gpu_particles", "image_stir", "fluid", "paint", "deformation"]


class TemplateResource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: TemplateId
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class TemplateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    templates: list[TemplateResource] = Field(max_length=5)

    @model_validator(mode="after")
    def distinct(self):
        if len({r.template for r in self.templates}) != len(self.templates):
            raise ValueError("템플릿 보고가 중복됩니다")
        return self


class GenerationConditions(TemplateResource):
    role: Literal["background", "object"]
    output_name: str = Field(pattern=r"^(background|asset-[0-9]{3})\.png$")
    image_format: Literal["PNG"] = "PNG"
    recommended_size: tuple[int, int]
    alpha_required: bool
    background_usage: Literal["display", "canvas", "style_reference"]
    allowed_render_modes: list[Literal["2d", "2.5d"]] = Field(min_length=1, max_length=2)
    behaviors: list[Literal["float", "follow", "flee", "glow"]] = Field(max_length=4)
    layer_depth: Literal["far"] | None = None
    notes: list[str] = Field(max_length=12)
