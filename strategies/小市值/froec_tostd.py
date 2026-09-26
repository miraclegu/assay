# -*- coding: utf-8 -*-
"""FROEC-TOSTD：**双重排序** —— 锁定市值暴露后，换手率波动还有没有边际贡献。

**为什么是这个因子**（2026-09-26 量的，不是拍脑袋）：

    小市值域（流通市值最小 20%，froec 的作用域）逐年 IC，前瞻 20 日
      to_std20 换手率相对波动   前段 -0.152  后段 -0.142  同号年 100%
      a_std6   成交额标准差      前段 -0.157  后段 -0.149  同号年 100%
      mv       市值            前段 -0.066  后段 -0.115  同号年  88%

    与市值的横截面秩相关（24 个抽样日）
      to_std20  **-0.14**   <- 几乎正交，是【独立】的一条腿
      a_std6    +0.61       <- 一半是市值的影子，所以不选它

★ 前段 <=2015 / 后段 >=2016 两段都同号、逐年 100% 同号 —— 这是它比
  `a_std6` / `mv` 更值得试的**唯一**理由。⚠ 但那是 **IC**，不是回测：
  本项目有两个现成反例（「ROE 加速度不合理的地方正是它有效的地方」推理
  每步都对而回测 0/11；`froec_devban` 指标更连续更合理而 t<=0.41）。
  **IC 只用来排除，不用来证明。**

**怎么分开"池子"与"排序"**（照 `froec_dsort.py` 那次的设计，不自己发明）：

    第一重（锁定市值）  froec 原本的两道筛子一个字不动，只把末层
                        `LIMIT` 放宽到 `ds_pool` 只（仍按 floatmv 升序）
    第二重（比因子）    在这 `ds_pool` 只里取 `candidate_num` 只：
                        ds_by='mv'   按市值取（= 原版）        <- 对照
                        ds_by='to'   按 to_std20 **升序** 取    <- 实验
                        ds_by='tod'  按 to_std20 **降序** 取    <- 反向对照

🔴 **反向对照(`tod`)不能省**：只比 `to` vs `mv` 的话，赢了也说不清是
  "低换手率波动有效"还是"**任何**非市值排序都行"（换个排序就打散了市值
  集中度，那本身可能就是效果）。真有信息量的话应当 `to` > `mv` > `tod`。

**实现上不动一行 SQL。** froec 是 `cand = df['jq_code'].tolist()[:lim]`
—— 按**返回的行序**取前 lim 只。所以只要把 `context.data.query` 返回的
DataFrame 重排就够了，`WHERE` / CTE / 参数一个字没碰。
★ 因子值走 `context.data.factors`（`feed.factors`，2026-09-24 加的）——
  它管 as-of，策略只管阈值与排序，正是那条边界。
🔴 取因子用的日期是 **`kw['sd']`**（froec 自己那个 as-of 日），不是
  `context.current_date` —— 两者差一天的话就是未来函数，**而它不报错**。

🔴 **默认值等价自证**：`ds_by='mv'`（默认）时 `query` **原样透传**，
  所以与直接跑 `froec_traded` 必须**逐日权益指纹逐位相同**。
  这一步不能省 —— 凭印象拼 `_TRADED = {...}` 作废过本项目一整批结论。

⚠ **取不到因子值的票排在最后，不丢掉** —— 丢掉会改变池子，那就同时动了
  两件事（同 `froec_dy` 那条「只改两个 cut 而留着 INNER JOIN 等于 ROE 规则
  只删了一半」）。

参数：
    ds_pool  默认 30    第一重取最小多少只
    ds_by    默认 'mv'  第二重按什么排（mv / to / tod）
    ds_fid   默认 'to_std20'  第二重用哪个因子
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    """私有实例 —— 不复述 froec_traded 的参数，转发给它自己去设。"""
    spec = importlib.util.spec_from_file_location(
        'froec_tostd__traded', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base

_DIRS = {'mv': None, 'to': False, 'tod': True}      # None=不重排；False=升序


def _patch_query(data):
    g = _base.g
    orig = data.query

    def q(sql, **kw):
        # 只管候选池那一发（它才有 pbcut/roecut 两个模板参数）
        if 'pbcut' not in kw or 'roecut' not in kw:
            return orig(sql, **kw)
        by = str(getattr(g, 'ds_by', 'mv'))
        if by not in _DIRS:
            raise ValueError('ds_by 只能是 %s' % '/'.join(_DIRS))
        if _DIRS[by] is None:
            return orig(sql, **kw)          # 🔴 原样透传 = 等价自证的落点
        want = int(kw.get('cand'))
        npool = max(want, int(getattr(g, 'ds_pool', 30)))
        kw = dict(kw, cand=npool)           # 第一重：放宽
        df = orig(sql, **kw)
        if df is None or len(df) == 0:
            return df
        fid = str(getattr(g, 'ds_fid', 'to_std20'))
        codes = df['jq_code'].tolist()
        fv = data.factors(kw['sd'], [fid], codes=codes)
        m = dict(zip(fv['jq_code'], fv[fid])) if fv is not None and len(fv) else {}
        # ⚠ 取不到值的排最后（不丢掉 —— 丢掉会同时改变池子）
        import math
        big = math.inf
        key = []
        for i, c in enumerate(codes):
            v = m.get(c)
            v = big if v is None or v != v else float(v)
            key.append((v, i))              # i 当 tie-break：并列时保持市值序
        order = [i for _, i in sorted(zip(key, range(len(codes))),
                                      key=lambda t: t[0])]
        if _DIRS[by]:                       # 降序：有值的倒过来，缺值的仍在最后
            has = [i for i in order if key[i][0] != big]
            nil = [i for i in order if key[i][0] == big]
            order = has[::-1] + nil
        return df.iloc[order].reset_index(drop=True).head(want)
    return q


def initialize(context):
    g = _base.g
    g.ds_pool = getattr(g, 'ds_pool', 30)
    g.ds_by = getattr(g, 'ds_by', 'mv')
    g.ds_fid = getattr(g, 'ds_fid', 'to_std20')
    if g.ds_by not in _DIRS:
        raise ValueError('ds_by 只能是 %s' % '/'.join(_DIRS))
    _traded.initialize(context)
    d = context.data
    if not getattr(d, '_tostd_wrapped', False):
        d.query = _patch_query(d)
        d._tostd_wrapped = True


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
