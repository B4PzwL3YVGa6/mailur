import functools as ft
import json
import os
import re
from concurrent import futures
from contextlib import contextmanager
from imaplib import CRLF

IMAP_DEBUG = int(os.environ.get('IMAP_DEBUG', 1))


class Error(Exception):
    def __repr__(self):
        return '%s.%s: %s' % (__name__, self.__class__.__name__, self.args)


def check(res):
    typ, data = res
    if typ != 'OK':
        raise Error(typ, data)
    return data


def check_fn(func):
    def inner(*a, **kw):
        return check(func(*a, **kw))
    return ft.wraps(func)(inner)


def check_uid(con, name):
    return check_fn(ft.partial(con.uid, name))


def client_readonly(ctx, connect, debug=IMAP_DEBUG):
    con = connect()
    con.debug = debug
    con.recreate = ft.partial(recreate, con, connect)

    ctx.logout = con.logout
    ctx.list = check_fn(con.list)
    ctx.fetch = ft.partial(fetch, con)
    ctx.status = ft.partial(status, con)
    ctx.search = ft.partial(search, con)
    ctx.select = ft.partial(select, con)
    ctx.select_tag = ft.partial(select_tag, con)
    ctx.box = lambda: getattr(con, 'current_box', None)
    ctx.str = lambda: '%s[%r]' % (ctx.__class__.__name__, ctx.box())
    return con


def client_full(ctx, connect, debug=IMAP_DEBUG):
    con = client_readonly(ctx, connect, debug=debug)
    ctx.append = check_fn(con.append)
    ctx.expunge = check_fn(con.expunge)
    ctx.sort = check_uid(con, 'SORT')
    ctx.thread = ft.partial(thread, con)
    ctx.fetch = ft.partial(fetch, con)
    ctx.store = ft.partial(store, con)
    ctx.getmetadata = ft.partial(getmetadata, con)
    ctx.setmetadata = ft.partial(setmetadata, con)
    ctx.multiappend = ft.partial(multiappend, con)


def recreate(con, connect):
    box = getattr(con, 'current_box', None)
    con = connect()
    if box:
        select(con, box, con.is_readonly)
    return con


@contextmanager
def cmd(con, name):
    tag = con._new_tag()

    def start(args):
        if isinstance(args, str):
            args = args.encode()
        return con.send(b'%s %s %s' % (tag, name.encode(), args))
    yield tag, start, lambda: con._command_complete(name, tag)


def multiappend(con, box, msgs):
    if not msgs:
        return

    with cmd(con, 'APPEND') as (tag, start, complete):
        send = start
        for time, flags, msg in msgs:
            args = (' (%s) %s %s' % (flags, time, '{%s}' % len(msg)))
            if send == start:
                args = '%s %s' % (box, args)
            send(args.encode() + CRLF)
            send = con.send
            while con._get_response():
                if con.tagged_commands[tag]:   # BAD/NO?
                    return tag
            con.send(msg)
        con.send(CRLF)
        return check(complete())


def _mdkey(key):
    if not key.startswith('/private'):
        key = '/private/%s' % key
    return key


def setmetadata(con, box, key, value):
    key = _mdkey(key)
    with cmd(con, 'SETMETADATA') as (tag, start, complete):
        args = '%s (%s %s)' % (box, key, json.dumps(value))
        start(args.encode() + CRLF)
        typ, data = complete()
        return check(con._untagged_response(typ, data, 'METADATA'))


def getmetadata(con, box, key):
    key = _mdkey(key)
    with cmd(con, 'GETMETADATA') as (tag, start, complete):
        args = '%s (%s)' % (box, key)
        start(args.encode() + CRLF)
        typ, data = complete()
        return check(con._untagged_response(typ, data, 'METADATA'))


def select(con, box, readonly=True):
    res = check(con.select(box, readonly))
    con.current_box = box.decode() if isinstance(box, bytes) else box
    return res


def select_tag(con, tag, readonly=True):
    if isinstance(tag, str):
        tag = tag.encode()
    folders = check(con.list())
    for f in folders:
        if not re.search(br'^\([^)]*?%s' % re.escape(tag), f):
            continue
        folder = f.rsplit(b' "/" ', 1)[1]
        break
    return select(con, folder, readonly)


def status(con, box, fields):
    box = con.current_box if box is None else box
    return check(con.status(box, fields))


def search(con, *criteria):
    return check(con.uid('SEARCH', None, *criteria))


def thread(con, *criteria):
    res = check(con.uid('THREAD', *criteria))
    return parse_thread(res[0].decode())


def fetch(con, uids, fields):
    if not isinstance(uids, (str, bytes)):
        @ft.wraps(fetch)
        def inner(uids):
            return fetch(con, ','.join(uids), fields)

        res = partial_uids(delayed_uids(inner, uids))
        res = ([] if len(i) == 1 and i[0] is None else i for i in res)
        return sum(res, [])
    return check(con.uid('FETCH', uids, fields))


def store(con, uids, command, flags):
    if not isinstance(uids, (str, bytes)):
        @ft.wraps(store)
        def inner(uids):
            return store(con, ','.join(uids), command, flags)

        res = partial_uids(delayed_uids(inner, uids))
        res = ([] if len(i) == 1 and i[0] is None else i for i in res)
        return sum(res, [])
    return check(con.uid('STORE', uids, command, flags))


def parse_thread(line):
    if isinstance(line, bytes):
        line = line.decode()

    threads = []
    uids = []
    uid = ''
    opening = 0
    for i in line:
        if i == '(':
            opening += 1
        elif i == ')':
            if uid:
                uids.append(uid)
                uid = ''

            opening -= 1
            if opening == 0:
                threads.append(uids)
                uids = []
        elif i == ' ':
            uids.append(uid)
            uid = ''
        else:
            uid += i
    return threads


def pack_uids(uids):
    uids = sorted(int(i) for i in uids)
    result = ''
    for i, uid in enumerate(uids):
        if i == 0:
            result += str(uid)
        elif uid - uids[i-1] == 1:
            if len(uids) == (i + 1):
                if not result.endswith(':'):
                    result += ':'
                result += str(uid)
            elif result.endswith(':'):
                pass
            else:
                result += ':'
        elif result.endswith(':'):
            result += '%d,%d' % (uids[i-1], uid)
        else:
            result += ',%s' % uid
    return result


def delayed_uids(func, uids, *a, **kw):
    @ft.wraps(func)
    def inner(uids, num=None):
        num = '#%s' % num if num else ''
        try:
            res = func(uids, *a, **kw)
            print('## %s: done%s' % (inner.desc, num))
        except Exception as e:
            import logging
            logging.exception(e)
            print('## %s: ERROR%s %r' % (inner.desc, num, e))
            raise
        return res

    inner.uids = list(uids)
    inner.desc = '%s(%s)' % (func.__name__, ', '.join(
        ['uids'] +
        [repr(i) for i in a] +
        (['**%r'] % kw if kw else [])
    ))
    return inner


def partial_uids(delayed, size=5000, threads=1):
    uids = delayed.uids
    if not uids:
        return []
    elif len(uids) <= size:
        return [delayed(uids)]

    jobs = []
    with futures.ThreadPoolExecutor(threads) as pool:
        for i in range(0, len(uids), size):
            num = '%02d' % (i // size + 1)
            few = uids[i:i+size]
            jobs.append(pool.submit(delayed, few, num))
            print('## %s#%s: %s uids' % (delayed.desc, num, len(few)))
    return [f.result() for f in jobs]
