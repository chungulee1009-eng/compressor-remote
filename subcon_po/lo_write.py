"""LibreOffice(UNO) 로 수삽 탭 기입 후 xlsx 저장. writer.py 가 별도 프로세스로 실행.

사용: python lo_write.py job.json   (LO 동봉 파이썬도 가능하도록 표준 라이브러리 + uno 만 사용)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue

FILTERS = {".xlsx": "Calc MS Excel 2007 XML", ".xlsm": "Calc MS Excel 2007 VBA XML",
           ".xls": "MS Excel 97"}
# CellFlags: VALUE 1 | DATETIME 2 | STRING 4 | FORMULA 16
CLEAR_FLAGS = 1 | 2 | 4 | 16


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    pipe = "subconpo_" + uuid.uuid4().hex[:8]
    profile = tempfile.mkdtemp(prefix="lo_profile_")
    proc = subprocess.Popen([job["soffice"], "--headless", "--invisible", "--norestore", "--nologo",
                             f"-env:UserInstallation={uno.systemPathToFileUrl(profile)}",
                             f"--accept=pipe,name={pipe};urp;"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local)
        ctx = None
        for _ in range(120):
            try:
                ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.5)
        if ctx is None:
            raise RuntimeError("soffice 연결 실패")
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(job["src"]), "_blank", 0,
                                           (prop("Hidden", True),))
        sheet = doc.Sheets.getByName(job["sheet"])
        s, e = job["start"] - 1, job["end"] - 1
        for c in job["cols"]:
            sheet.getCellRangeByPosition(c - 1, s, c - 1, e).clearContents(CLEAR_FLAGS)
        for i, vals in enumerate(job["rows"]):
            for c, v in zip(job["cols"], vals):
                if v is None or v == "":
                    continue
                cell = sheet.getCellByPosition(c - 1, s + i)
                if isinstance(v, (int, float)):
                    cell.setValue(v)
                else:
                    cell.setString(str(v))
        doc.calculateAll()
        ext = os.path.splitext(job["dst"])[1].lower()
        doc.storeToURL(uno.systemPathToFileUrl(job["dst"]), (prop("FilterName", FILTERS[ext]),))
        doc.close(True)
        try:
            desktop.terminate()
        except Exception:
            pass
    finally:
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main(sys.argv[1])
