import email
import re

from mailur import local, gmail


def test_binary_msg():
    assert local.binary_msg('Ответ: 42').as_bytes() == '\r\n'.join([
        'MIME-Version: 1.0',
        'Content-Transfer-Encoding: binary',
        'Content-Type: text/plain; charset="utf-8"',
        '',
        'Ответ: 42'
    ]).encode()

    assert local.binary_msg('Ответ: 42').as_string() == '\r\n'.join([
        'MIME-Version: 1.0',
        'Content-Transfer-Encoding: base64',
        'Content-Type: text/plain; charset="utf-8"',
        '',
        '0J7RgtCy0LXRgjogNDI=\r\n'
    ])


def test_fetch_and_parse(clean_users, gm_client, some):
    lm = local.client()
    gmail.fetch_folder()
    local.parse()

    def gm_uidnext():
        res = lm.getmetadata(local.ALL, 'gmail/uidnext/all')
        assert res == [(b'All (/private/gmail/uidnext/all {12}', some), b')']
        return some.value

    def mlr_uidnext():
        res = lm.getmetadata(local.PARSED, 'uidnext')
        assert res == [(b'Parsed (/private/uidnext {1}', some), b')']
        return some.value

    assert gm_uidnext().endswith(b',1')
    assert lm.getmetadata(local.PARSED, 'uidnext') == [
        b'Parsed (/private/uidnext NIL)'
    ]

    gm_client.add_emails()
    gmail.fetch_folder()
    local.parse()
    assert gm_uidnext().endswith(b',2')
    assert mlr_uidnext() == b'2'
    assert lm.select(local.ALL) == [b'1']
    assert lm.select(local.PARSED) == [b'1']

    gm_client.add_emails([{'txt': '1'}, {'txt': '2'}])
    gmail.fetch_folder()
    local.parse()
    assert gm_uidnext().endswith(b',4')
    assert mlr_uidnext() == b'4'
    assert lm.select(local.ALL) == [b'3']
    assert lm.select(local.PARSED) == [b'3']

    gmail.fetch_folder()
    local.parse('all')
    assert gm_uidnext().endswith(b',4')
    assert mlr_uidnext() == b'4'
    assert lm.select(local.ALL) == [b'3']
    assert lm.select(local.PARSED) == [b'3']
    assert lm.status(local.PARSED, '(UIDNEXT)') == [b'Parsed (UIDNEXT 7)']


def get_latest(box='All'):
    lm = local.client(box)
    res = lm.fetch('*', '(flags body[])')
    msg = res[0][1]
    print(msg.decode())
    msg = email.message_from_bytes(msg)
    line = res[0][0].decode()
    print(line)
    flags = re.search('FLAGS \(([^)]*)\)', line).group(1)
    return flags, msg


def get_msgs(box='All', uids='1:*'):
    lm = local.client(box)
    res = lm.fetch(uids, '(flags body[])')
    return [(
        re.search('FLAGS \(([^)]*)\)', res[i][0].decode()).group(1),
        email.message_from_bytes(res[i][1])
    ) for i in range(0, len(res), 2)]


def test_fetched_msg(gm_client):
    gm_client.add_emails()
    gmail.fetch_folder('\\All')
    _, msg = get_latest()
    # headers
    sha256 = msg.get('X-SHA256')
    assert sha256 and re.match('<[a-z0-9]{64}>', sha256)
    uid = msg.get('X-GM-UID')
    assert uid and uid == '<101>'
    msgid = msg.get('X-GM-MSGID')
    assert msgid and msgid == '<10100>'
    thrid = msg.get('X-GM-THRID')
    assert thrid and thrid == '<10100>'

    lm = local.client()
    gm_client.add_emails([
        {'flags': '\\Flagged', 'labels': '"\\\\Inbox" "\\\\Sent" label'}
    ])
    gmail.fetch_folder('\\All')
    flags, msg = get_latest()
    assert '\\Flagged \\Recent #inbox #sent #t1' == flags
    assert local.get_tags(lm) == {'#t1': 'label'}

    gm_client.add_emails([{'labels': 'label "another label"'}])
    gmail.fetch_folder('\\All')
    flags, msg = get_latest()
    assert '\\Recent #t1 #t2' == flags
    assert local.get_tags(lm) == {'#t1': 'label', '#t2': 'another label'}

    gm_client.add_emails([{}])
    gmail.fetch_folder('\\Junk')
    flags, msg = get_latest()
    assert '#spam' in flags

    gm_client.add_emails([{}])
    gmail.fetch_folder('\\Trash')
    flags, msg = get_latest()
    assert '#trash' in flags


def test_thrids(clean_users, gm_client):
    gm_client.add_emails([{}])
    gmail.fetch_folder()
    local.parse()
    msgs = get_msgs('Parsed')
    assert ['#latest'] == [i[0] for i in msgs]
    gm_client.add_emails([{}])
    gmail.fetch_folder()
    local.parse()
    msgs = get_msgs('Parsed')
    assert ['#latest', '#latest'] == [i[0] for i in msgs]

    gm_client.add_emails([{'in_reply_to': '<101@mlr>'}])
    gmail.fetch_folder()
    local.parse()
    msgs = get_msgs('Parsed')
    assert ['', '#latest', '#latest'] == [i[0] for i in msgs]

    gm_client.add_emails([{'refs': '<101@mlr> <102@mlr>'}])
    gmail.fetch_folder()
    local.parse()
    msgs = get_msgs('Parsed')
    assert ['', '', '', '#latest'] == [i[0] for i in msgs]

    local.parse('all')
    msgs = get_msgs('Parsed')
    assert ['', '', '', '#latest'] == [i[0] for i in msgs]


def test_parsed_msg(gm_client):
    gm_client.add_emails([{'flags': '\\Flagged'}])
    gmail.fetch_folder()
    local.parse()
    flags, msg = get_latest('Parsed')
    assert 'X-UID' in msg
    assert re.match('<\d+>', msg['X-UID'])
    assert '\\Flagged' in flags
