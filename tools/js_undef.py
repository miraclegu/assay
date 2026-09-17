# -*- coding: utf-8 -*-
"""JS 的「引用了未定义的名字」扫描器 —— Python 侧那道防线的 **JS 版**。

🔴🔴 为什么要有它（2026-09-16）：用户报「回测里的持仓页面展示不出列表」，
查下来是 `paneHoldings` 用了 `money()`，而它是 `drawDay` 里的一个**局部
const** —— 跨函数引用未定义的名字，切到那个页签当场抛
`money is not defined`、一行都渲染不出来，**而它只在控制台里报**。

拆 `srv/` 那次，Python 侧建过同一道防线（"静态扫每个模块里 Load 但未绑定
的名字"，当场抓出 `_JOBS` 那两处）。**JS 侧一直没有** —— 只有
① `node --check`（只查语法）② 跨文件顶层重名。这次的 bug 正好落在那个缺口里。

判据的取舍：**宁可漏报也不误报**。
  · 声明集合取得偏大（一条 `const a = b + c;` 里 b/c 也算声明）——
    一个天天报假警的检查等于没有检查（同「假告警看多了就不看告警」那条）。
  · 只扫**顶层具名函数**的函数体：那正是"A 函数用了 B 函数的局部名"
    这种形状；嵌套闭包不追，误报会多得压过价值。
实测：正常态 0 处；把 `money` 改回局部立刻报 `paneHoldings() -> money`。
"""
import io, os, re, sys, glob

def strip_js(src):
    """把注释 / 字符串 / 模板串 / 正则字面量换成等长空白，保留换行与括号结构。"""
    out = []
    i, n = 0, len(src)
    prev_sig = ''          # 上一个有意义的字符（判断 / 是除号还是正则）
    while i < n:
        c = src[i]
        two = src[i:i+2]
        if two == '//':
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i)); i = j; continue
        if two == '/*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            seg = src[i:j]
            out.append(''.join(ch if ch == '\n' else ' ' for ch in seg)); i = j; continue
        if c in '"\'':
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == '\\' else 1
            j = min(j + 1, n)
            seg = src[i:j]
            out.append(''.join(ch if ch == '\n' else ' ' for ch in seg)); i = j; continue
        if c == '`':
            # 模板串：${...} 里的是代码，保留；其余变空白
            j = i + 1; buf = [' ']
            while j < n and src[j] != '`':
                if src[j] == '\\': buf.append('  '); j += 2; continue
                if src[j:j+2] == '${':
                    # 🔴 `${...}` 里面是**代码**，要递归 strip 一遍 ——
                    #   原样保留的话里面的 `'<td class="tx">'` 不会被清掉，
                    #   于是 tx / td / class 全被当成标识符（实测 1389 处误报）。
                    depth = 1; k = j + 2; inner = []
                    while k < n and depth:
                        if src[k] == '{': depth += 1
                        elif src[k] == '}': depth -= 1
                        if depth: inner.append(src[k])
                        k += 1
                    buf.append('  ')
                    buf.append(strip_js(''.join(inner)))
                    buf.append(' ')
                    j = k; continue
                buf.append(src[j] if src[j] == '\n' else ' '); j += 1
            buf.append(' ')
            out.append(''.join(buf)); i = min(j + 1, n); continue
        if c == '/' and prev_sig in '(,=:[!&|?{};+-*%~^<>' + '':
            j = i + 1; ok = False
            while j < n and src[j] != '\n':
                if src[j] == '\\': j += 2; continue
                if src[j] == '/': ok = True; break
                j += 1
            if ok:
                j += 1
                while j < n and src[j].isalpha(): j += 1
                out.append(' ' * (j - i)); i = j; continue
        out.append(c)
        if not c.isspace(): prev_sig = c
        i += 1
    return ''.join(out)

ID = re.compile(r'[A-Za-z_$][\w$]*')
KW = set('''await break case catch class const continue debugger default delete do else
export extends finally for function if import in instanceof let new of return static
super switch this throw try typeof var void while with yield async get set'''.split())
LIT = set('true false null undefined NaN Infinity arguments'.split())
BUILTIN = set('''window document console Math JSON Date Array Object String Number Boolean
RegExp Error Promise Set Map WeakMap WeakSet Symbol Proxy Reflect BigInt parseInt parseFloat
isNaN isFinite encodeURIComponent decodeURIComponent encodeURI decodeURI setTimeout
clearTimeout setInterval clearInterval requestAnimationFrame cancelAnimationFrame fetch
localStorage sessionStorage location history navigator alert confirm prompt performance
CustomEvent Event MutationObserver ResizeObserver IntersectionObserver URL URLSearchParams
Intl TextDecoder TextEncoder AbortController Blob FormData FileReader Image Node Element
HTMLElement CanvasRenderingContext2D getComputedStyle globalThis structuredClone queueMicrotask
DOMParser XMLHttpRequest WebSocket crypto atob btoa'''.split())

def decls_in(seg):
    """这一段代码里**声明**了哪些名字（近似：声明关键字 / 参数 / 解构 / catch / 具名函数）。"""
    out = set()
    # 🔴 一条声明可能有**多个**名字（`let a = 1, b = 2`）、可能是解构
    #   （`const {a, b} = x`）—— 只抓第一个的话 `KTOTAL` / `SHOWEV` 这些
    #   全成了"未定义"（实测 247 处误报）。
    #   ★ 取整条声明里的**所有**标识符：这让"声明集合"偏大（右侧初始化
    #     表达式里引用的名字也被算进来），于是**宁可漏报也不误报** ——
    #     一个天天报假警的检查等于没有检查（同「假告警看多了就不看」那条）。
    for m in re.finditer(r'\b(?:const|let|var)\b([^;]*)', seg):
        out |= set(ID.findall(m.group(1)))
    for m in re.finditer(r'\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)?\s*\(([^)]*)\)', seg):
        if m.group(1): out.add(m.group(1))
        out |= set(ID.findall(m.group(2) or ''))
    for m in re.finditer(r'\bcatch\s*\(([^)]*)\)', seg):
        out |= set(ID.findall(m.group(1)))
    for m in re.finditer(r'\bclass\s+([A-Za-z_$][\w$]*)', seg):
        out.add(m.group(1))
    # 箭头函数的参数
    for m in re.finditer(r'\(([^()]*)\)\s*=>', seg):
        out |= set(ID.findall(m.group(1)))
    for m in re.finditer(r'(?<![\w$.])([A-Za-z_$][\w$]*)\s*=>', seg):
        out.add(m.group(1))
    return out - KW

def top_funcs(code):
    """顶层 `function name(...) {...}` 的 (名字, 起, 止)。"""
    res = []
    for m in re.finditer(r'^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', code, re.M):
        i = code.index('{', m.end() - 1)
        d, j = 0, i
        while j < len(code):
            if code[j] == '{': d += 1
            elif code[j] == '}':
                d -= 1
                if d == 0: break
            j += 1
        res.append((m.group(1), m.start(), j + 1))
    return res

def refs_in(seg):
    """这一段里**引用**到的标识符（跳过 `.prop` 与对象字面量的键）。"""
    out = set()
    for m in ID.finditer(seg):
        nm = m.group(0)
        if nm in KW or nm in LIT: continue
        k = m.start() - 1
        while k >= 0 and seg[k] in ' \t': k -= 1
        if k >= 0 and seg[k] == '.': continue                 # x.prop
        # `1e6` / `0x1f` —— 科学计数法会被切成 `e6`（实测两处误报）
        if k >= 0 and (seg[k].isdigit() or seg[k] == '.'): continue
        if k >= 0 and seg[k] == '?' and k and seg[k-1] == '?': pass
        e = m.end()
        while e < len(seg) and seg[e] in ' \t': e += 1
        if e < len(seg) and seg[e] == ':':                    # {key: v} / label
            b = m.start() - 1
            while b >= 0 and seg[b] in ' \t\n': b -= 1
            if b >= 0 and seg[b] in '{,': continue
        out.add(nm)
    return out

def scan(files):
    codes = {f: strip_js(io.open(f, encoding='utf-8').read()) for f in files}
    # 全局可用：所有文件的**顶层**声明（顶层函数 + 顶层 const/let/var）
    glob_names = set()
    for f, c in codes.items():
        tops = top_funcs(c)
        body_free = c
        for _n, a, b in reversed(tops):
            body_free = body_free[:a] + ' ' * (b - a) + body_free[b:]
        glob_names |= decls_in(body_free)
        glob_names |= {n for n, _a, _b in tops}
    bad = []
    for f, c in codes.items():
        for name, a, b in top_funcs(c):
            seg = c[a:b]
            local = decls_in(seg)
            for r in sorted(refs_in(seg) - local - glob_names - BUILTIN):
                ln = c[:a].count('\n') + seg[:seg.index(r)].count('\n') + 1 if r in seg else 0
                bad.append((f, name, r, ln))
    return bad, glob_names

if __name__ == '__main__':
    root = sys.argv[1] if len(sys.argv) > 1 else 'web'
    files = sorted(glob.glob(os.path.join(root, '**', '*.js'), recursive=True))
    # html 里的内联 <script> 也算
    inline = {}
    for h in sorted(glob.glob(os.path.join(root, '*.html'))):
        src = io.open(h, encoding='utf-8').read()
        js = '\n'.join(re.findall(r'<script>(.*?)</script>', src, re.S))
        if js.strip():
            p = h + '.inline.js'
            io.open(p, 'w', encoding='utf-8').write(js)
            inline[p] = h
    bad, gl = scan(files + list(inline))
    for p in inline: os.remove(p)
    print('全局顶层名 %d 个，扫描 %d 个文件' % (len(gl), len(files) + len(inline)))
    for f, fn, r, ln in bad:
        print('  %-34s %-22s -> %s' % (inline.get(f, f).replace('web/', ''), fn + '()', r))
    print('共 %d 处可疑' % len(bad))
