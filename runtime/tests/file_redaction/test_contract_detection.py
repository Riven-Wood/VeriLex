from verilex_runtime.file_redaction.ner import detect
from verilex_runtime.file_redaction.rules import DEFAULT_KINDS


def selected(text, *kinds):
    return [(hit['kind'], hit['old']) for hit in detect(text, list(kinds), [])]


def test_contract_party_names_are_whole_without_adjacent_legal_text():
    assert selected('委托方：南京致理文化艺术有限公司', 'organization') == [('organization', '南京致理文化艺术有限公司')]
    assert selected('受托方：北京致理文化传媒有限公司', 'organization', 'address') == [('organization', '北京致理文化传媒有限公司')]
    assert selected('未经南京致理文化艺术有限公司同意，乙方不得转让。', 'organization') == [('organization', '南京致理文化艺术有限公司')]


def test_contract_ordinary_language_and_legal_names_are_not_redacted():
    text='根据《中华人民共和国民法典》，甲方应当按照约定在中国使用云杉艺术展作品。'
    assert selected(text, 'person', 'address', 'organization') == []


def test_party_names_do_not_consume_contract_words_before_them():
    for text, name in [
        ('甲方委托北京远方科技有限公司完成服务。', '北京远方科技有限公司'),
        ('双方同意南京致远科技有限公司继续履约。', '南京致远科技有限公司'),
        ('本合同由北京远方科技有限公司签署。', '北京远方科技有限公司'),
        ('甲方是北京远方科技有限公司。', '北京远方科技有限公司'),
        ('委托方：经纬科技有限公司', '经纬科技有限公司'),
        ('委托方：为民科技有限公司', '为民科技有限公司'),
    ]:
        assert selected(text, 'organization') == [('organization', name)]


def test_labeled_account_and_representative_are_detected():
    assert selected('账号：6222 0212 3456 7890 123', 'bank') == [('bank', '6222 0212 3456 7890 123')]
    assert ('person', '林明远') in selected('授权代表：林明远', 'person')
    assert ('phone', '13800138000') in selected('联系电话：13800138000', 'phone')


def test_opening_bank_name_is_detected_without_removing_its_label_or_following_account():
    assert selected('开户银行：北京某某银行', 'organization') == [('organization', '北京某某银行')]
    assert selected('开户行：中国银行北京分行 账号：12345678', 'organization') == [('organization', '中国银行北京分行')]
    assert selected('开户银行：北京某某银行账号：12345678', 'organization') == [('organization', '北京某某银行')]
    assert selected('开户银行：北京某某银行', 'bank') == []
    assert selected('乙方应当通过银行办理转账。', 'organization') == []


def test_companies_included_in_default_options():
    assert 'organization' in DEFAULT_KINDS
