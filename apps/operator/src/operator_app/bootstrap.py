"""최초 관리자 발급. 서버 소유자의 비공개 env 경로만 명시적으로 입력한다."""
import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .client import CentralClient
from .login import AccountsDialog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    args = parser.parse_args()
    values = dict(line.split("=", 1) for line in args.env.read_text(encoding="utf-8-sig").splitlines()
                  if line.strip() and not line.lstrip().startswith("#") and "=" in line)
    if not values.get("OPERATOR_TOKEN") or values.get("CENTRAL_URL") != "https://k15a504.p.ssafy.io":
        parser.error("운영 서버의 관리자 설정 파일이 필요합니다")
    app = QApplication(sys.argv)
    client = CentralClient(values["CENTRAL_URL"], values["OPERATOR_TOKEN"])
    del values
    dialog = AccountsDialog(client)
    dialog.role.setCurrentText("admin")
    dialog.show()
    app.exec()
    client.http.close()


if __name__ == "__main__":
    main()
