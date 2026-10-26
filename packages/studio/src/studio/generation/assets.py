"""Worker — 소재: 필요한 소재 계산 → 풀에서 고르기 / 새로 생성 → 입구 검사(verify) → 풀 저장."""
import uuid
from pathlib import Path

from sqlmodel import Session, select

from contracts.families import FAMILIES
from contracts.schemas import AssetChoice, Element, Mode, Needs
from db.models import Asset
from studio.verify.checks import inspect_image

from .images import ImageProvider

IMAGE_KINDS = {"background", "panel", "fluid_image", "skin"}


def needs(elements: list[Element]) -> Needs:
    required, optional = [], []
    for element in elements:
        spec = FAMILIES[element.family]
        required += [k for k in spec.needs if k not in required]
        optional += [k for k in spec.optional if k not in optional]
    return Needs(required=required, optional=[k for k in optional if k not in required])


def pick(session: Session, kind: str, theme: str) -> Asset | None:
    """승인된 것 중 가장 적게 쓴 것. 실제 운영: 재사용 상한·비상용 예비(controller store.pick_pool_asset)."""
    query = (select(Asset).where(Asset.kind == kind, Asset.theme == theme, Asset.status == "approved")
             .order_by(Asset.uses, Asset.id))
    return session.exec(query).first()


def generate(kind: str, theme: str, palette: list[str], provider: ImageProvider, data_dir: Path, seed: int) -> Path:
    if kind not in IMAGE_KINDS:
        raise RuntimeError(f"{kind}는 아직 생성기가 없음 (영상·소리는 연결 예정)")
    folder = data_dir / "generated"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{kind}_{theme}_{uuid.uuid4().hex[:10]}.png"
    path.write_bytes(provider.generate(f"{theme} {kind}", palette, seed))
    return path


def gather(session: Session, required: list[str], choice: AssetChoice, mode: Mode, theme: str, palette: list[str],
           provider: ImageProvider, data_dir: Path, set_id: str, seed: int,
           status: str = "approved") -> tuple[dict[str, Path], list[str]]:
    """'풀에서 고르기'는 풀 먼저, 없으면 생성. 그 밖과 전시 모드는 항상 생성(새 소재는 풀에 쌓음). 검사 탈락이면 예외.
    status="pending"이면 새 소재는 사람이 승인할 때까지 풀에서 뽑히지 않는다."""
    pool_first = choice is AssetChoice.pool and mode is not Mode.exhibition
    assets, log = {}, []
    created = session.info.setdefault("new_files", [])   # 실패 시 지울 파일(central.pipeline._rollback)
    for index, kind in enumerate(required):
        asset = pick(session, kind, theme) if pool_first else None
        if asset:
            asset.uses += 1
            log.append(f"{kind}: 풀에서 사용 ({Path(asset.path).name})")
        else:
            path = generate(kind, theme, palette, provider, data_dir, seed + index)
            created.append(path)
            problems, digest = inspect_image(session, path)
            if problems:
                raise RuntimeError(f"{kind} 입구 검사 탈락: {', '.join(problems)}")
            asset = Asset(kind=kind, theme=theme, path=path.as_posix(), sha256=digest, set_id=set_id, uses=1,
                          status=status)
            log.append(f"{kind}: 새로 생성({provider.name}) → 입구 검사 통과 → 풀 저장 ({path.name})")
        session.add(asset)
        assets[kind] = Path(asset.path)
    return assets, log
