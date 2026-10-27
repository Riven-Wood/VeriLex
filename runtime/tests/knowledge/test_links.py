from verilex_runtime.knowledge.links import source_links

def test_links_keep_positions_and_skip_code():
    text = '# 文档\r\n[法条](法律.md#第一条) ![[判决|摘要]]\n`[[不可读]]`\n```md\n[[不可读2]]\n```\n'
    links = source_links(text,0,len(text))
    assert [link.target for link in links] == ['法律.md#第一条','判决']
    assert links[1].embedded
    assert text[links[0].start:links[0].end] == '[法条](法律.md#第一条)'
    assert source_links(text,links[0].start+1,len(text)) == links[1:]


def test_angle_targets_and_escaped_links():
    text = '[book](<book title.md>) \\[[escaped]] [remote](https://example.test)'
    assert [link.target for link in source_links(text,0,len(text))] == ['book title.md','https://example.test']
