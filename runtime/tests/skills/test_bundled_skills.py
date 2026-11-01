import shutil
from pathlib import Path

from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.skills.loader import discover_skills


def settings(root):
    return RuntimeSettings(host='127.0.0.1', port=0, state_dir=root, api_token='test')


def test_new_install_seeds_bundled_skills_and_resources_once(tmp_path):
    settings(tmp_path)
    root = tmp_path / 'skills'
    names = {r.name for r in discover_skills(root)}
    assert {'cn-law-hub', 'contract-copilot', 'patent-disclosure-skill'} <= names
    assert len(names) == 28  # 26 distributed and the 2 existing built-in skills
    assert 'chinese-legal-commercial' in names
    assert {'contract-review', 'contract-party-check-qcc', 'license-validation-qcc', 'labor-compliance-qcc', 'ip-infringement-alert-qcc'} <= names
    assert (root / 'chinese-legal-commercial/upstream/skills/nda-review/SKILL.md').is_file()
    assert (root / 'cn-law-hub' / 'LICENSE').is_file()
    assert (root / 'contract-copilot' / 'LICENSE.txt').is_file()
    assert (root / 'patent-disclosure' / 'LICENSE').is_file()
    shutil.rmtree(root / 'cn-law-hub')
    settings(tmp_path)
    assert not (root / 'cn-law-hub').exists()


def test_upgrade_never_overwrites_existing_skill(tmp_path):
    root = tmp_path / 'skills'
    own = root / 'my-contract'; own.mkdir(parents=True)
    text = '---\nname: contract-copilot\ndescription: 我的技能\n---\n我的内容\n'
    (own / 'SKILL.md').write_text(text)
    settings(tmp_path)
    assert (own / 'SKILL.md').read_text() == text
    assert not (root / 'contract-copilot').exists()
    assert (root / 'cn-law-hub' / 'SKILL.md').is_file()


def test_resource_reader_limits_reads_to_unchanged_distributed_files(tmp_path):
    from verilex_runtime.skills.bundled import read_distributed_resource
    from verilex_runtime.skills.loader import find_skill
    settings(tmp_path)
    root = tmp_path / 'skills'
    record = find_skill(root, 'patent-disclosure-skill')
    assert '专利' in read_distributed_resource(record, 'prompts/intake.md')
    import pytest
    for path in ('../legal-research/SKILL.md', '/etc/passwd', '.env', 'prompts/../../secret'):
        with pytest.raises(ValueError):
            read_distributed_resource(record, path)
    asset = root / 'patent-disclosure' / 'prompts/intake.md'
    asset.write_text('私人修改')
    with pytest.raises(ValueError):
        read_distributed_resource(record, 'prompts/intake.md')


def test_distributed_resource_rejects_symlink_escape(tmp_path):
    from verilex_runtime.skills.bundled import read_distributed_resource
    from verilex_runtime.skills.loader import find_skill
    import pytest
    settings(tmp_path)
    root = tmp_path / 'skills'
    record = find_skill(root, 'patent-disclosure-skill')
    asset = root / 'patent-disclosure' / 'prompts/intake.md'
    expected = asset.read_text()
    target = tmp_path / 'outside.md'; target.write_text(expected)
    asset.unlink(); asset.symlink_to(target)
    with pytest.raises(ValueError):
        read_distributed_resource(record, 'prompts/intake.md')


def test_missing_bundled_skill_fails_without_marking_complete(tmp_path, monkeypatch):
    import pytest
    from verilex_runtime.skills import bundled
    missing = tmp_path / 'missing'; missing.mkdir()
    monkeypatch.setattr(bundled, '_BUNDLED', missing)
    with pytest.raises(RuntimeError, match='Bundled skill'):
        settings(tmp_path / 'state')
    assert not (tmp_path / 'state/skills/.seeded-distributed-skills-v1').exists()
