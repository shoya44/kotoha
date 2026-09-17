"""テストの下ごしらえ。

**同じ記述を減らすためであって、中身を隠すためではない。** どのテストが
何を偽物にしているかは、そのファイルを読んで分かるようにしておきたい。
だから差し替え（stub）はここに置かない。置くのは、どのテストでも同じ
「一時DBを用意する」ところだけ。

保存先を先に一時DBへ向けるのは、本番の data/kotoha.sqlite3 を絶対に
触らないため。db 側は呼ばれたときに config.DB_PATH を見るので、
import の順番は本当は問わないが、読む人に意図が伝わるよう先に置く。
"""

import tempfile
import unittest
from pathlib import Path

from kotoha import config
from kotoha.memory import db


def use_temp_db(name: str):
    """保存先を一時DBへ向け、その置き場を返す。

    呼んだ側が tearDownModule() で片づける。閉じ忘れたDB接続があると
    Windowsでは消せずに落ちるので、そこで気づける。
    """
    tmp = tempfile.TemporaryDirectory(prefix=f"kotoha {name} ")
    config.DB_PATH = Path(tmp.name) / "test.sqlite3"
    return tmp


class DbCase(unittest.TestCase):
    """毎回まっさらなDBで始める。前のテストの続きから始めない。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
