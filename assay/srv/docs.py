"""srv/docs.py —— 数据字典：直接渲染磁盘上的 md。"""
import os
import re
import time
from datetime import datetime

from . import base
from .base import (_repo_root)


_DOCS = [
    # (key, 侧栏标题, 分组, 相对仓库根的路径)
    ('need',    '1 · 按需求',      '数据字典（索引）', 'datalake/docs/数据字典/1-按需求索引.md'),
    ('api',     '2 · 按接口',      '数据字典（索引）', 'datalake/docs/数据字典/2-按接口索引.md'),
    ('field',   '3 · 按字段',      '数据字典（索引）', 'datalake/docs/数据字典/3-按字段索引.md'),
    ('trap',    '4 · 按陷阱',      '数据字典（索引）', 'datalake/docs/数据字典/4-按陷阱索引.md'),
    ('extapi',  '5 · 外部行情接口', '数据字典（索引）', 'datalake/docs/数据字典/5-外部行情接口.md'),
    ('dictix',  '索引说明',        '数据字典（索引）', 'datalake/docs/数据字典/README.md'),
    ('jq',      '聚宽接口备忘',    '原文出处',             'datalake/docs/聚宽接口备忘.md'),
    ('jqfactor', '聚宽因子口径',   '原文出处',             'datalake/docs/jqfactor-口径.md'),
    ('qmt',     'QMT 探针实测',    '原文出处',             'assay/qmt/PROBE_FINDINGS.md'),
    ('qmtread', 'QMT 移植说明',    '原文出处',             'assay/qmt/README.md'),
    ('lake',    'datalake 说明',   '原文出处',             'datalake/README.md'),
    ('stop',    '止损方案实测',    '实测记录',             'assay/strategies/止损方案实测-固定_移动_吊灯.md'),
    ('exec',    '调仓时点与滑点',  '实测记录',             'assay/strategies/调仓执行时点-滑点与延迟实测.md'),
]



def _doc_path(rel):
    """把 _DOCS 的相对路径解到绝对路径，并确认没跑出仓库根（防目录穿越）。"""
    root = _repo_root()
    p = os.path.normpath(os.path.join(root, rel))
    return p if p.startswith(root) else None



def api_docs(_q):
    """侧栏清单。带 exists/mtime，缺文件在界面上直接显示为灰的，不静默消失。"""
    out = []
    for key, title, group, rel in _DOCS:
        p = _doc_path(rel)
        ok = bool(p) and os.path.isfile(p)
        out.append({'key': key, 'title': title, 'group': group, 'rel': rel,
                    'exists': ok,
                    'mtime': (datetime.fromtimestamp(os.path.getmtime(p))
                              .strftime('%Y-%m-%d %H:%M') if ok else None),
                    'bytes': os.path.getsize(p) if ok else 0})
    return {'docs': out, 'root': _repo_root()}



def api_doc(q):
    key = q.get('key')
    hit = [d for d in _DOCS if d[0] == key]
    if not hit:
        return None
    _, title, group, rel = hit[0]
    p = _doc_path(rel)
    if not p or not os.path.isfile(p):
        return {'key': key, 'title': title, 'group': group, 'rel': rel,
                'missing': True,
                'html': '<p class="dim">文件不存在：<code>%s</code></p>' % _esc(rel)}
    txt = open(p, encoding='utf-8', errors='replace').read()
    return {'key': key, 'title': title, 'group': group, 'rel': rel,
            'mtime': datetime.fromtimestamp(os.path.getmtime(p))
                             .strftime('%Y-%m-%d %H:%M'),
            'html': _md(txt)}



def _esc(t):
    return (t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))



def _md_inline(t):
    """行内标记：`code` / **bold** / [text](url)。

    ★ 顺序与隔离都有讲究，两个坑都踩过：
      1. 必须先把 code 段抠成占位符再处理加粗 —— 否则 `Feed.query(sql, **kw)`
         里的 ** 会和段落后面真正的 **加粗** 配成一对，把结构吃坏。
      2. 加粗用非贪婪 + 负向预查 (?!\\*)，因为加粗内容本身可能含单个星号：
         `**B 股 sh90*/sz20***` —— 用 [^*]+ 匹配不到，会把裸 ** 漏到页面上。
    """
    t = _esc(t)
    holes = []

    def _stash(m):
        holes.append('<code>%s</code>' % m.group(1))
        return '\x00%d\x00' % (len(holes) - 1)
    t = re.sub(r'`([^`]+)`', _stash, t)

    t = re.sub(r'\*\*(.+?)\*\*(?!\*)', lambda m: '<b>%s</b>' % m.group(1), t)

    def _link(m):
        label, href = m.group(1), m.group(2)
        if href.startswith('http'):
            return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (href, label)
        # 站内 md 相对链接：能映射到 _DOCS 的就跳同页，否则只保留文字（不留死链）
        base = os.path.basename(href.split('#')[0])
        for key, _t, _g, rel in _DOCS:
            if os.path.basename(rel) == base:
                return '<a href="#/docs/%s">%s</a>' % (key, label)
        return label
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _link, t)

    return re.sub(r'\x00(\d+)\x00', lambda m: holes[int(m.group(1))], t)



def _md(text):
    """够用就好的 markdown -> html：标题 / 表格 / 代码块 / 列表 / 引用 / 分隔线。

    不引第三方库：这份 server 是零依赖的单文件（除 pandas），
    为一个查看页装 markdown 依赖不值得。文档里实际用到的语法就这几种。
    """
    out, i = [], 0
    lines = text.replace('\r\n', '\n').split('\n')
    n = len(lines)
    while i < n:
        ln = lines[i]

        if ln.startswith('```'):                       # 代码块
            i += 1
            buf = []
            while i < n and not lines[i].startswith('```'):
                buf.append(lines[i]); i += 1
            i += 1
            out.append('<pre><code>%s</code></pre>' % _esc('\n'.join(buf)))
            continue

        if re.match(r'^\s*\|.*\|\s*$', ln) and i + 1 < n \
                and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip('|').split('|')]
            i += 2
            body = []
            while i < n and re.match(r'^\s*\|.*\|\s*$', lines[i]):
                body.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            t = ['<div class="dtw"><table class="dt"><thead><tr>']
            t += ['<th>%s</th>' % _md_inline(c) for c in head]
            t.append('</tr></thead><tbody>')
            for row in body:
                t.append('<tr>')
                for k in range(len(head)):
                    t.append('<td>%s</td>' % _md_inline(row[k] if k < len(row) else ''))
                t.append('</tr>')
            t.append('</tbody></table></div>')
            out.append(''.join(t))
            continue

        m = re.match(r'^(#{1,6})\s+(.*)$', ln)
        if m:
            lv = len(m.group(1))
            out.append('<h%d class="dh dh%d">%s</h%d>'
                       % (min(lv + 1, 6), lv, _md_inline(m.group(2)), min(lv + 1, 6)))
            i += 1
            continue

        if re.match(r'^\s*(-{3,}|\*{3,})\s*$', ln):
            out.append('<hr>'); i += 1; continue

        if ln.startswith('>'):
            # ★ 引用块里可能嵌标题、表格、列表（datalake/README 就有），
            #   所以剥掉 '> ' 之后【递归渲染】，不能把整块拼成一个段落 ——
            #   拼了的话表格的 |---|---| 会原样漏到页面上。
            buf = []
            while i < n and lines[i].startswith('>'):
                buf.append(re.sub(r'^>\s?', '', lines[i])); i += 1
            out.append('<blockquote>%s</blockquote>' % _md('\n'.join(buf)))
            continue

        if re.match(r'^\s*[-*]\s+', ln) or re.match(r'^\s*\d+\.\s+', ln):
            ordered = bool(re.match(r'^\s*\d+\.\s+', ln))
            items = []
            while i < n and (re.match(r'^\s*[-*]\s+', lines[i])
                             or re.match(r'^\s*\d+\.\s+', lines[i])
                             or (items and lines[i].startswith('  ') and lines[i].strip())):
                if re.match(r'^\s*[-*]\s+', lines[i]) or re.match(r'^\s*\d+\.\s+', lines[i]):
                    items.append(re.sub(r'^\s*(?:[-*]|\d+\.)\s+', '', lines[i]))
                else:
                    items[-1] += ' ' + lines[i].strip()       # 续行并进上一条
                i += 1
            tag = 'ol' if ordered else 'ul'
            out.append('<%s>%s</%s>' % (tag, ''.join(
                '<li>%s</li>' % _md_inline(x) for x in items), tag))
            continue

        if not ln.strip():
            i += 1; continue

        buf = [ln]                                      # 段落：连续非空行合并
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r'^(#{1,6}\s|```|>|\s*\||\s*[-*]\s|\s*\d+\.\s|-{3,}\s*$)', lines[i]):
            buf.append(lines[i]); i += 1
        out.append('<p>%s</p>' % _md_inline(' '.join(x.strip() for x in buf)))
    return '\n'.join(out)


# ==================== 实盘模块（live）====================
# ★ 与 /api/backtest 同哲学：**默认关闭**。它会起后台线程定时跑策略，
#   而看板本身是纯读的、随时重启无代价。把「读」和「有副作用的写」
#   用一个开关分开，重启服务就不会打断正在跑的东西。
