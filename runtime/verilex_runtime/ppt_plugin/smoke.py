"""Offline packaged-dependency check; no model, account, or user files."""
import io
import json
from pathlib import Path
from .service import create, open_checked, outline, replace_text


def main():
    original=create([{'title':'打包验证','bullets':['甲公司：合成内容'],'notes':'合成备注'}])
    stream=io.BytesIO();original.save(stream)
    reopened=open_checked(stream.getvalue())
    assert '甲公司' in outline(reopened)['pages'][0]['text']
    assert replace_text(reopened,'甲公司','乙公司')==1
    assert '乙公司' in outline(reopened)['pages'][0]['text']
    assert (Path(__file__).parent/'vendor'/'LICENSE').is_file()
    print(json.dumps({'ok':True,'created':True,'read':True,'replaced':True,'license_bundled':True}))
    return 0
