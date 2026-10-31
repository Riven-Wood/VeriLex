"""Build the offline, sandboxed .doc metadata reader for the macOS ARM bundle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        print('Legacy DOC reader: this host is outside the supported macOS ARM bundle.')
        return
    lock_path = REPO / 'third_party/legacy_doc_reader/sources.lock.json'
    lock = json.loads(lock_path.read_text())
    source = REPO / 'runtime/tools/legacy_doc_reader/LegacyLayout.java'
    fingerprint = hashlib.sha256(lock_path.read_bytes() + source.read_bytes() + Path(__file__).read_bytes()
        + (lock_path.parent / 'LICENSE').read_bytes() + (lock_path.parent / 'NOTICE').read_bytes()).hexdigest()
    output = REPO / 'desktop/src-tauri/generated/legacy-reader-macos-arm64'
    if (output / 'build.sha256').is_file() and (output / 'build.sha256').read_text() == fingerprint:
        print('Legacy DOC reader is current.')
        return
    cache = Path.home() / 'Library/Caches/VeriLex/legacy-doc-reader-v1'
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(name: str, item: dict) -> Path:
        target = cache / name
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == item['sha256']:
            return target
        temporary = target.with_suffix(target.suffix + '.download')
        with urllib.request.urlopen(item['url'], timeout=60) as incoming, temporary.open('wb') as saved:
            shutil.copyfileobj(incoming, saved)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('Legacy reader dependency checksum mismatch: ' + name)
        temporary.replace(target)
        return target

    archive = fetch('jdk.tar.gz', lock['jdk'])
    jdk_root = cache / 'jdk'
    if not (jdk_root / 'complete.sha256').is_file() or (jdk_root / 'complete.sha256').read_text() != lock['jdk']['sha256']:
        jdk_root.mkdir(exist_ok=True)
        with tarfile.open(archive) as package:
            package.extractall(jdk_root, filter='data')
        (jdk_root / 'complete.sha256').write_text(lock['jdk']['sha256'])
    jdk = next(jdk_root.glob('*/Contents/Home'))
    jars = [fetch(item['name'], item) for item in lock['jars']]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='legacy-reader-', dir=output.parent) as work:
        work = Path(work)
        runtime = work / 'runtime'
        subprocess.run([str(jdk / 'bin/jlink'), '--add-modules',
            'java.base,java.xml,java.desktop,java.logging,java.management,jdk.unsupported,jdk.charsets',
            '--strip-debug', '--no-man-pages', '--no-header-files', '--compress=zip-6',
            '--output', str(runtime)], check=True)
        reader = runtime / 'reader'; reader.mkdir()
        for jar in jars: shutil.copy2(jar, reader / jar.name)
        classes = work / 'classes'; classes.mkdir()
        subprocess.run([str(jdk / 'bin/javac'), '--release', '21', '-cp', str(reader / '*'),
            '-d', str(classes), str(source)], check=True)
        subprocess.run([str(jdk / 'bin/jar'), '--create', '--file', str(reader / 'legacy-layout.jar'),
            '-C', str(classes), '.'], check=True)
        shutil.copy2(lock_path, runtime / 'sources.lock.json')
        shutil.copy2(REPO / 'third_party/legacy_doc_reader/LICENSE', runtime / 'APACHE-LICENSE')
        shutil.copy2(REPO / 'third_party/legacy_doc_reader/NOTICE', runtime / 'APACHE-NOTICE')
        (runtime / 'build.sha256').write_text(fingerprint)
        # Generated output only. Never touch the installed application here.
        if output.exists(): shutil.rmtree(output)
        shutil.move(str(runtime), str(output))
    print('Built offline legacy DOC reader:', output)


if __name__ == '__main__':
    main()
