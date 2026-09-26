"""pytest 公共夹具：在导入应用前把数据库指向临时文件。"""
import os
import tempfile

import pytest

# app.db 在导入时读取 DB_PATH，须在任何 app.* 导入之前设置
_TMP = tempfile.NamedTemporaryFile(prefix="raytrace-test-", suffix=".db", delete=False)
_TMP.close()
os.environ["DB_PATH"] = _TMP.name


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
