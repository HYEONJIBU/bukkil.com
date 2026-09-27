"""환경 설정: API 키와 DB 경로. 프로젝트 루트의 .env 파일을 자동으로 읽는다."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(PROJECT_ROOT / ".env")


def api_key() -> str:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "DART_API_KEY가 설정되지 않았습니다. finvault/.env 파일에 DART_API_KEY=... 를 넣거나 "
            "환경변수로 지정하세요. (발급: https://opendart.fss.or.kr)"
        )
    return key


def db_path() -> Path:
    return Path(os.environ.get("FINVAULT_DB", PROJECT_ROOT / "data" / "finvault.db"))
