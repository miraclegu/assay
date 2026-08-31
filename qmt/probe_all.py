#coding:gbk
#==============================================================================
# QMT 接口探针 —— 唯一的一个，不要再新建
#==============================================================================
# 工作方式：
#   已确认的写进 CONFIRMED，只打印结论、【不再调接口】；
#   未确定的放 OPEN，每跑一轮把确认下来的从 OPEN 挪进 CONFIRMED。
#   文件会随着核对推进自然变短，而且随时能看到「已知什么、还差什么」的全貌。
#
# 跑法：QMT -> 新建 Python 策略 -> 粘贴 -> 周期 1d -> 区间含 2025-06-30 -> 贴回日志
#
# 三条纪律（前几轮踩出来的）：
#   1) 每节独立 try/except —— 曾经一节抛异常把整轮结果打断了一半
#   2) 不用 `not r` 判空 —— 返回可能是 pandas Panel，会抛
#      "The truth value of a Panel is ambiguous"
#   3) 凡本地有标准答案的一律嵌进来自判，不靠人肉比对
#==============================================================================

REF_DATE = '20250630'
CODE  = '601398.SH'
CODES = ['601398.SH', '601088.SH']

# ---------------------------------------------------------------- 已确认
# 格式: 项目 -> (确认日期, 结论)
CONFIRMED = [
    ('行情 get_market_data_ex', '2026-08-30',
     "字段 open/close/high/low/volume/amount/preClose 全有；dividend_type='front' 可用。"
     '601398 与 601088 的 OHLC+成交额与本地【逐项精确吻合】。可放心用。'),
    ('合约详情 C.get_instrumentdetail(code)', '2026-08-30',
     '可用，返回 dict。OpenDate=上市日(5/5 对)、InstrumentName=名称、'
     'FloatVolume/FloatVolumn=流通股本(两种拼写都有)、PreClose、IsTrading、InstrumentStatus。'),
    ('分红 C.get_divid_factors(code)', '2026-08-30',
     '只接受 1~2 个参数(传 start/end 报 TypeError)。返回 {毫秒时间戳: [7个数]}；'
     'key=除权日【北京时间00:00】(用 utcfromtimestamp 会差一天)；'
     '[0]=每股税前现金分红(元/股)，与本地 bonus_ratio_rmb/10 【11/11 精确吻合】；'
     '[3][4]=配股比例/配股价；[6]=复权因子。'),
    ('指数日线', '2026-08-30',
     "000300.SH 可取(399300.SZ 返回空)。-> beta 对沪深300 自己回归即可，不需要外部数据。"),
    ('账户 get_trade_detail_data', '2026-08-30',
     "模块级可调；空账号 '' 也能读到 ACCOUNT 1 条(回测用默认账户)，"
     'm_dBalance/m_dAvailable/m_dAssetBalance 等字段齐全。POSITION 空仓时 0 条。'
     'passorder 全局可见。'),
    ('全A股票池', '2026-08-30',
     "C.get_stock_list_in_sector('沪深A股') -> 5216 只(本地同日在市 5152，量级对)；"
     "'沪深京A股' 5555；'沪深300' 300。'A股'/'全部A股' 等名字为 0。"),
    ('[!] 涨跌停价不能用 UpStopPrice/DownStopPrice', '2026-08-30',
     '它们是【实时值不是历史值】：探针跑于 2026-08-30 而参考日 2025-06-30，'
     '601398 返回 8.60/7.04 而实际 8.25/6.75；300750 返回 447.60 而实际 301.19。'
     '必须自己按 preClose×(1±涨跌幅) 算 —— 移植里 _limit_price 本来就是这么做的。'),
    ('[定案] dir(C) = 115 个成员，方法清单已列全', '2026-08-31',
     'get_sector_list 确实不存在，但同族有：create_sector / get_sector / '
     'get_stock_list_in_sector / get_industry / get_raw_financial_data / '
     'get_financial_data / get_finance / get_his_st_data / get_divid_factors / '
     'get_instrumentdetail / get_float_caps / get_total_share / get_turn_over_rate / '
     'get_weight_in_index / get_top10_share_holder / get_holder_num / get_factor_data / '
     'get_smallcap / get_midcap / get_largecap / is_suspended_stock / get_trading_dates。'
     '★ 教训：先 dir 再谈有没有。前几轮「取不到」全是猜错名字。'),
    ('xtquant 可 import，六个函数都在', '2026-08-30',
     'get_sector_list / get_stock_list_in_sector / download_sector_data / '
     'get_financial_data / download_financial_data / get_instrument_detail 全部存在。'),
    ('[环境] 用的是完整版 QMT 交易端，不是 miniQMT', '2026-08-31',
     '所以【策略移植一律走 ContextInfo】—— 行情/合约详情/分红/账户/沪深A股池 '
     '都已确认可用。xtdata 那套 connect(127.0.0.1:58610) 是 miniQMT/极简模式的形态，'
     '它只影响「批量导出数据给 datalake 用」，不影响策略能不能跑。'
     '所以下面 S 系列即使全挂，froec/v0b/红利 的移植也不受阻 —— '
     '真正卡住的只有 C.get_financial_data 返回 NaN 这一条。'),
    ('[!] xtdata 不是 miniQMT 专有，但策略用不上', '2026-08-31',
     '报错路径 D:\\国金证券QMT交易端\\bin.x64\\lib\\site-packages\\xtquant\\xtdata.py '
     '就在【完整版 QMT】目录里 —— xtquant 是随 QMT 一起装的，import 也成功，'
     '六个函数都在。报的是【连接错误不是导入错误】：xtdata 是独立客户端，'
     '要连 QMT 数据服务端口(58610)，完整版里该接口默认不一定开。'
     '（且调用前必须先 xtdata.connect(ip, port=58610)，直接调会抛 get_client 异常。）'
     '★ 策略移植只走 ContextInfo，用不到它；xtdata 只在「批量导出数据给 datalake」'
     '时才需要 —— 那是另一件事，本探针已不再测它。'),
    ('[**] 财务数据补完后可用 —— 四个字段全有值', '2026-08-31',
     'ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int / '
     'ASHAREINCOME.net_profit_excl_min_int_inc / CAPITALSTRUCTURE.total_capital / '
     'CAPITALSTRUCTURE.circulating_capital 全部非空。'
     'PERSHAREINDEX.s_fa_eps_basic 也有值(601398=0.98)，v0b 的累计 EPS 有着落。'
     '表名必须【全大写】：Balance. / Income. / CapitalStructure. / BALANCESHEET. 全 NaN。'
     '扣非净利两个候选(ASHAREFINANCIALINDICATOR.net_profit_after_ded_nr_lp / '
     'deducted_profit) 全 NaN —— ★ 只说明这两个名字不对，不说明没有。'),
    ('财务调用签名', '2026-08-31',
     '必须传 start/end 且格式 YYYYMMDD（带横杠全 NaN、不传报 TypeError）。'
     '多股票多字段返回 pandas Panel：items=股票 / major_axis=【交易日】/ minor_axis=字段。'
     '★ report_type 是【第 5 个位置参数且必须是整数】：传 1 或 0 都返回数据，'
     "传 'announce'/'report'/'1'/'0' 一律返回 None（静默，不报错）—— "
     '硬写字符串形态的风险是财务整片为空而策略不报错。移植里 _call_fin 已改成'
     '降级阶梯并缓存首个可用形态。'),
    ('[**] 公告日 m_anntime / 报告期 m_timetag —— 一直都有', '2026-08-31',
     '★ 前几轮说「公告日拿不到」是错的：我只请求了 4 个业务字段，当然只返回 4 列。'
     '把它当【字段】显式请求就有：ASHAREINCOME.m_anntime（也存在于 ASHAREBALANCESHEET）'
     '和 ASHAREINCOME.m_timetag，都是【毫秒时间戳】。实测 601398 与本地逐字段吻合：'
     '  m_timetag=1.703952e12 -> 2023-12-31 = 报告期（本地 report_date 同）；'
     '  m_anntime=1.7115552e12 -> 2024-03-28 = 公告日（本地 pub_date 同）。'
     '且可与业务字段同批请求，返回 columns=[net_profit_excl_min_int_inc, m_anntime]。'
     '而 ann_dt / announce_date / anndate / report_date / first_ann_dt 全 NaN（名字不对）。'
     '-> 移植已改用真实公告日，删掉「法定披露截止日」兜底（原本会晚 0~32 天）。'),
    ('[定案] get_instrumentdetail 30 个键里【没有行业】', '2026-08-31',
     '全量键已打：CreateDate/DownStopPrice/ExchangeCode/ExchangeID/ExpireDate/'
     'FloatVolume/FloatVolumn/HSGTFlag/InstrumentID/InstrumentName/InstrumentStatus/'
     'IsRecent/IsTrading/LastVolume/LongMarginRatio/MainContract/OpenDate/PreClose/'
     'PriceTick/ProductID/ProductName/RzrkCode/SettlementPrice/ShortMarginRatio/'
     'TotalVolume/TotalVolumn/TradingDay/UniCode/UpStopPrice/VolumeMultiple。'
     'ProductID 和 ProductName 对 601398/601088/300750 【全是空字符串】-> 行业只能另找。'
     '★ 但捡到一个：TotalVolume=总股本、FloatVolume=流通股本，不查财务表也能算市值。'),
    ('[?] get_industry / get_sector 存在，之前【参数传反了】', '2026-08-31',
     "两者都报 missing 1 required positional argument（'indu…' / 'sector'），"
     '说明要的是【板块/行业名】不是股票代码 —— 我传了 601398.SH 所以返回空 list []。'
     "get_stock_type('601398.SH') -> int 0。"
     '通达信中文行业名走 get_stock_list_in_sector 基本不通（16 个名字里只有'
     '「水泥」返回 31 只且没命中参考票，其余全 0）。★ 正确写法是前缀+行业名（SW1银行），见「板块名前缀实测：SW1 全通」那条。'),
    ('[定案] report_type 的真正含义 —— 未来函数的根因', '2026-08-31',
     '官方文档（迅投知识库 innerApi/data_function）：'
     "report_type='announce_time' 按【公告期】取数（发布日之后到下个财报发布日之间"
     "给的都是该期财报的值，这是默认值、时点正确）；'report_time' 按【报告期】取数。"
     '★ 移植原来硬写的正是 report_type=\'report_time\' —— 主动要了未来函数那一版，'
     '这解释了 601398 在 20241231 就给出 2025-03-29 才公告的年报。'
     "已改为 'announce_time' 优先的降级阶梯。注意传不认识的字符串是【静默返回 None】。"),
    ('[定案] 板块名 = 前缀 + 行业名（不加分隔符）', '2026-08-31',
     "官方与社区一致：如 'SW1汽车' / 'CSRC1采矿业'。前缀族："
     'SW1/SW2=申万一二级，CSRC1/CSRC2=证监会，THY1/THY2=通达信行业，'
     'TGN/GN=概念，DY1=地域。★ 上一轮试的是光秃秃的「银行」，当然返回 0 —— '
     '不是取不到，是名字少了前缀。'),
    ('[定案] get_raw_financial_data 可用，且【不做日度插值】', '2026-08-31',
     "C.get_raw_financial_data([field], codes, s, e) -> {code: dict}，实测两只票都有值。"
     '官方说明：与 get_financial_data 同参数，但只返回原始报告期行、不按交易日插值 —— '
     '这正是我们要的形态。★ 内层结构已在「get_raw_financial_data 返回三层嵌套 dict」那条查明。'),
    ('[定案] 这几个 C 方法直接可用（免去查财务表）', '2026-08-31',
     "get_total_share('601398.SH') -> int 356406257089（= detail 的 TotalVolume）；"
     "get_float_caps('601398.SH') -> int 269612212539（= FloatVolume）；"
     "is_suspended_stock('601398.SH') -> bool False；"
     "get_weight_in_index('000300.SH','601398.SH') -> float 0.992（沪深300 权重%）。"
     "get_turn_over_rate('601398.SH') -> nan（要么要传日期，要么要先下数据）。"),
    ('[定案] get_his_st_data 接【单个字符串】不接 list', '2026-08-31',
     "C.get_his_st_data('601398.SH') -> dict（工行从没 ST 所以是空 dict，不是不可用）；"
     "传 list 报错泄露了内部名 _PyContextInfo.get_st_status(list)；传 (code, s, e) 报"
     "'takes 2 positional arguments but 4 were given' -> 只接 1 个参数。"
     '★ 已试 4 只候选 ST 股全返回空 dict，不再追 —— ST 判定用「沪深风险警示」板块。'),
    ('[定案] 扣非净利 8 个候选全 NaN；但 EPS 有着落', '2026-08-31',
     'ASHAREFINANCIALINDICATOR 的 s_fa_deductedprofit / deductedprofit / '
     's_fa_roe_deducted / np_cut、ASHAREINCOME 的 np_cut / '
     'net_profit_excl_min_int_inc_ded_nr、PERSHAREINDEX.s_fa_epsdeducted 全 NaN。'
     '★ 但 PERSHAREINDEX.s_fa_eps_diluted -> 0.98 有值（601398 2024）。'
     '停止逐个猜名字 —— 用 get_raw_financial_data 把整表字段清单拉出来找，见 H2。'),
    ('[定案] 两个方法的缺参已知', '2026-08-31',
     "get_factor_data() 报 missing 3 required positional arguments: 'stock_list…' -> "
     '至少 (stock_list, ?, ?)；'
     "get_trading_dates('SH','20240101','20240131') 报 missing 1 required "
     "positional argument: 'count' -> 第 4 个参数是 count。"),
    ('[**] report_type 三列对照定案 —— 且【默认值与文档不符】', '2026-08-31',
     '601398 净利(元) 三种调法逐日对照：'
     '  日期      announce_time   不传          report_time；'
     '  20241230  2.6902e+11      2.6902e+11    2.6902e+11；'
     '  20241231  2.6902e+11      3.6586e+11    3.6586e+11  <- 提前 88 天；'
     '  20250328  2.6902e+11      3.6586e+11    3.6586e+11；'
     '  20250331  3.6586e+11      8.4156e+10    8.4156e+10。'
     "★ 显式传 'announce_time' 完全正确：年报公告日 2025-03-29(周六)，03-28 还是 Q3 值，"
     '03-31 才切到年报值。★★ 但【不传 == report_time == 未来函数】—— '
     "文档写的「默认即 announce_time」与实测不符，必须显式传，不能靠默认。"),
    ('[**] 板块名前缀实测：SW1 全通，THY1/TGN 全 0', '2026-08-31',
     "get_stock_list_in_sector('SW1银行') -> 42 只含 601398 [OK]；'银行' -> 0 只。"
     'get_industry(名) 与 get_stock_list_in_sector(名) 【返回完全一致】，可互换。'
     "SW2股份制银行 -> 9 只；CSRC1金融业 -> 120 只含 601398；CSRC1采矿业 -> 83 只含 601088；"
     'SW1煤炭 -> 33 只含 601088；SW1电力设备 -> 415 只含 300750；SW1汽车 -> 323 只。'
     '★ SW1 全 31 个一级行业逐个跑：31 个都有成分股，合计 5551 只（全市场约 5200，量级对）'
     ' -> 申万一级映射完全可用，已接进 _resolve_industry_sectors。'
     "[!] THY1银行 / TGN银行 都是 0 只 —— 通达信那套在本环境没有成分数据，别用。"),
    ('[!] 申万版本差：本地 2014 版 vs QMT 2021 版', '2026-08-31',
     "本地策略用聚宽 sw_l1_name（申万 2014 版，'钢铁I'/'采掘I'/'交运设备I'/'金融服务I'），"
     'QMT 的 SW1 是 2021 版 31 个一级行业。对应关系：'
     '采掘(2014)->煤炭+石油石化，金融服务(2014)->银行+非银金融（都已被黑名单其它项覆盖）；'
     '★ 交运设备(2014) 在 2021 版散入 汽车/机械设备/国防军工，【无法对应，只能放弃】。'
     '所以 QMT 版行业过滤与本地不完全一致。可接受的理由：逐年独立回测里该过滤 '
     't=+1.56、11 年中 7 年为正，本就不显著。但差异写在代码注释里，不藏着。'),
    ('[定案] get_raw_financial_data 返回三层嵌套 dict', '2026-08-31',
     "{code: {field: {报告期毫秒时间戳: 值}}}。实测 601398 取 2023-2024 净利 -> "
     '8 个键 [1680192000000(=2023-03-31), …, 1703952000000(=2023-12-31), …] '
     '正好是 8 个季报，【无日度插值】—— 干净的报告期序列。'
     '[!] 但它【不带公告日】，要 PIT 还得配 m_anntime 或 announce_time 口径。'
     "[!] 整表拉不出来：['ASHAREINCOME'] -> 空 dict；'ASHAREINCOME'(字符串) -> TypeError；"
     "['ASHAREINCOME.*'] -> 键回来了但值是空 dict。所以【拿不到全字段清单】，"
     '字段名只能查文档。'),
    ('[定案] ContextInfo 版 get_instrumentdetail 没有 iscomplete', '2026-08-31',
     "get_instrumentdetail(code, True) 报 'takes 2 positional arguments but 3 were given'，"
     'get_instrument_detail 同。iscomplete 是 xtdata 版才有的参数 -> 扩展字段这条路断了，'
     '行业改走 SW1 板块（已通）。'),
    ('[定案] get_trading_dates 可用；另两个方法的签名', '2026-08-31',
     "get_trading_dates('SH','20240101','20240131',100) -> list len=22 "
     "['20240102','20240103',…] [OK] 第 4 个参数是 count；"
     "get_trading_dates('SH','','',10) -> 最近 10 个交易日 -> 可用来取交易日历。"
     "get_factor_data 签名是 (stock_list, factor_list, start_date, end_date)，"
     "但 factor_list=['pe'] 返回 None -> 因子名不对，价值不大先搁置。"
     "get_turn_over_rate 只接 1 个参数(code)且返回 nan -> 搁置。"),
    ('[?] get_his_st_data 四只候选 ST 股全返回空 dict', '2026-08-31',
     '000005.SZ / 600870.SH / 000561.SZ / 601258.SH 全是 dict len=0。'
     '可能这几只当前已摘帽或已退市，也可能要先下载数据。'
     '★ 不追了：ST 判定已有可用方案 —— 沪深风险警示板块（206 只，已实测）。'),
    ('[定案] 财务表只有 5 张，ASHAREFINANCIALINDICATOR【不存在】', '2026-08-31',
     '官方口径：ASHAREBALANCESHEET(资产负债表) / ASHAREINCOME(利润表) / '
     'ASHARECASHFLOW(现金流量表) / CAPITALSTRUCTURE(股本表) / PERSHAREINDEX(主要指标)，'
     '另有 Top10Holder / Top10FlowHolder / HolderNum。'
     '★ 这解释了上一轮扣非 8 个候选为什么全 NaN —— 其中 6 个挂在这张根本不存在的表上。'
     "★ 字段还支持【中文写法】：['利润表.净利润'] / ['资产负债表.固定资产']。"),
    ('[**] I5 定案：分红【没有归属报告期】-> 红利只能保持信号执行器形态', '2026-08-31',
     'get_divid_factors 的 7 个数全部查明：[0]=每股税前现金分红，[1][2][3][4][5] 实测'
     '【全是 0.0】（送股/转增/配股/股改类，两只银行 12 条全为 0），[6]=复权因子。'
     '★ 7 个数里没有任何日期形态，拿不到归属报告期，也拿不到预案公告日。'
     'key 是【除权日】：601398 六条 QMT key 比本地 a_registration_date 晚 1~3 天，'
     '正是登记日->次一交易日除权，与本地 a_xr_date 对齐。'
     'C.get_dividend / get_divid_plan / get_bonus 【都不存在】，'
     'dir(C) 里与分红相关的只有 get_divid_factors 和 dividend_type。'
     '-> 红利指数增强的 fiscal_year 股息率口径【无法在 QMT 侧复现】，而它是'
     'A、B 两条腿【共用】的前置过滤 -> 整条策略不能原生移植。'
     '换回 rolling365 = 用回已知错的算法：工行 2024 起改半年派，'
     'as-of 2025-06-01 虚高 +46.0%、2026-06-01 虚高 +53.0%（已用本地真值复算）。'),
    ('[定案] 每股分红 [0] 与本地 12/12 精确吻合', '2026-08-31',
     '601398：0.3035 / 0.3064 / 0.1434 / 0.1646 / 0.1414 / 0.1689；'
     '600036：1.522 / 1.738 / 1.972 / 2.000 / 1.012999 / 1.002999（本地 1.013 / 1.003）。'
     '★ 上一轮说「600036 最后两条差恰好 1.0」是【我把截图里首位的 1 读成了 0】，'
     '不是数据问题 —— 教训：低分辨率截图上的首位数字不能靠肉眼，要让探针自己交叉验算。'
     'J2 的验算：用复权因子 x 除权日 preClose 反算每股，八条比值全在 1.017~1.061，'
     '都 ≈1（不是 ≈0.01）-> 量级可信。比值系统性略大 2~6% 是因为 preClose 取的是'
     '除权日的前收盘（还含分红），反算会低估 —— 是公式的近似，不是数据问题。'),
    ('[**] I1/I2 定案：扣非在 PERSHAREINDEX，且【中文字段名可用】', '2026-08-31',
     '挂对表就有值（上一轮全 NaN 是因为挂在不存在的 ASHAREFINANCIALINDICATOR）：'
     'PERSHAREINDEX.adjusted_earnings_per_share=扣非每股收益(601398 -> 0.97/0.98)；'
     'adjusted_net_profit_rate=扣非净利同比；du_return_on_equity=净资产收益率(10.037)；'
     'du_profit_rate=净利润同比。'
     '★ 中文写法【全部可用且与英文一一对应】（同值同覆盖）：'
     "'主要指标.扣非每股收益' / '主要指标.净资产收益率' / '主要指标.净利润同比增长' / "
     "'利润表.净利润'(2.69929e+11) / '资产负债表.固定资产'(2.71028e+11)。"
     '仍全 NaN（名字不对，不要再试）：PERSHAREINDEX 的 s_fa_deductedprofit / '
     'inc_total_revenue_rate / main_business_income_rate。'),
    ('[**] I3 定案：QMT 增长率是【累计同比】，本地 inc_* 是【单季同比】', '2026-08-31',
     'du_profit_rate（百分数）vs 本地【全口径累计】同比，逐只对数：'
     '  601398 2024  0.5012%  vs  0.50%  [OK]；'
     '  600036 2023  6.2544%  vs  6.25%  [OK]；'
     '  600036 2024  1.0493%  vs  1.05%  [OK]；'
     '  601398 2023  0.8301%  vs  1.13%  差 0.30pp（唯一不合，J3 已查明来源不明）。'
     '3/4 精确吻合 -> 口径判定为【累计同比】。'
     '★ 而本地 inc_net_profit_year_on_year 是【单季同比】（601398 2024 = 1.35%，'
     '与累计 0.50% 完全不同）-> 【不能直接替换】。'
     '要复现只能自己从累计转单季：ASHAREINCOME 累计值 + m_timetag 报告期 -> 累计差 -> '
     '单季 -> 同比。froec 移植里的 _single_quarter 已经是这个逻辑，可复用。'),
    ('[**] J1 定案：扣非每股收益是【累计季度序列】，但覆盖【稀疏】', '2026-08-31',
     '601398 四个报告窗口全部非空 -> 有 Q1/H1/Q3/年报四期，不是只有年度值：'
     '2024Q1 窗口(04-25~05-10) 3 个非空值 0.97（= 2023 年报累计）；'
     '2024 中报窗口(08-25~09-10) 7 个非空值 0.46（= 2024H1 累计，约年报的一半，'
     '符合累计口径）；三季报窗口 4 个非空；2025 年报窗口 8 个非空值 0.98。'
     '-> 单季扣非净利可以做：累计(t) - 累计(t-1)，再乘总股本。'
     '[!] 但覆盖稀疏：2024 全年只有 57 个非空，而 du_return_on_equity 同期 359 个'
     '（几乎每个交易日都有）。所以取扣非【必须查区间取末个非空】不能查单日；'
     '且要用 m_timetag 确认拿到的是哪一期 —— 不能假定是最新期。'),
    ('[!!] J3 定案：du_profit_rate【不可无条件信任】，改用原值自算', '2026-08-31',
     '★ 好消息：QMT 的净利原值与本地【8/8 精确吻合】（601398 2021~2024，'
     '归母 3483.38/3604.83/3639.93/3658.63 亿，'
     '全口径 3502.16/3610.38/3651.16/3669.46 亿）-> 完全可以自己算同比。'
     '★ 坏消息：du_profit_rate 用 QMT 自己的原值复算，3/4 命中 1/4 不命中：'
     '601398 2024 全口径同比 +0.5012% == QMT 0.5012% 精确；'
     '而 601398 2023 归母 +0.9737% / 全口径 +1.1295% / 混口径 +0.8185%，'
     'QMT 报 0.8301% —— 三个候选口径【一个都不是】，来源不明。'
     '-> 结论：不要用 du_profit_rate / adjusted_net_profit_rate 这类【成品比率】，'
     '用 ASHAREINCOME 原值 + m_timetag 自己算。原值已验证与本地一致，比率没有。'
     '（顺带纠正自己一个错：上一轮把「本地归母 2022 = 3610.4 亿」写进了探针，'
     '那其实是【全口径】3610.38，归母是 3604.83 —— 两个口径不能混着记。）'),
    ('[!!] QMT 财务【按报告期前向填充】= 未来函数', '2026-08-31',
     '实测 601398：20241225~1230 净利 2690.3 亿(2024Q3)，20241231 变成 3658.6 亿'
     '(2024年报) —— 而 2024 年报公告日是 2025-03-29，提前 88 天。'
     '数值本身与本地精确吻合，错的是【时间对齐】：按报告期切换、不按公告日。'
     '直接按 ref_date 取值就是前视。移植已修：_attach_report_cols 重写为'
     '「按季末识别报告期 + 值跳变才算真发布 + 法定截止日兜底」。'),
    ('ST 板块名 = 沪深风险警示', '2026-08-31',
     "十个候选里只有 '沪深风险警示' 命中，206 只（本地 2025-06-30 是 175 只，"
     '探针取最新时点，量级对）。ST板块 / ST / 风险警示 / *ST / ST股票 / 沪深ST / '
     '两市ST / ST及*ST 全部 0 只。'),
    ('[已推翻] 早期结论「行业板块不是申万命名」', '2026-08-31',
     '★ 这条已被同日的实测推翻，保留是为了记住错在哪：'
     '当时试了 银行 / SW银行 / 申万银行 / 申万一级-银行 / 行业-银行 / 银行I / '
     '银行(申万) / 证监会行业-金融业 / 金融业 —— 全部 0 只，于是断言「不是申万命名」。'
     "实际正确写法是【前缀+行业名不加分隔符】：'SW1银行' -> 42 只含 601398，"
     'SW1 全 31 个一级行业都有成分股。当时试的 9 个名字全都少了 SW1 前缀，'
     "或者多了分隔符（'SW银行' 少个 1、'申万一级-银行' 多个横杠）。"
     '错因：靠穷举猜名字，而文档一句话就说清了。正确结论见'
     '「[**] 板块名前缀实测：SW1 全通」那条。'),
]

# 本地标准答案（2025-06-30）
REF = {
    'st_count': 175,
    'st_head': ['000004.SZ', '000070.SZ', '000430.SZ', '000488.SZ', '000504.SZ'],
    'industry_n': {'电子': 599, '电气设备': 453, '家用电器': 132, '银行': 42, '煤炭': 38},
    'industry_of': {'601398.SH': '银行', '601088.SH': '煤炭',
                    '000651.SZ': '家用电器', '300750.SZ': '电气设备'},
    'fin_601398_2024': {'归母净资产_亿': 34946.0, '归母净利_亿': 3658.6,
                        '总股本_亿股': 3564.1, '流通股本_亿股': 2696.1},
}

_done = [False]


def init(C):
    print('>>>>>> 探针已加载 <<<<<<  参考日 %s' % REF_DATE)
    print('完整版 QMT：本探针只走 ContextInfo（策略就是这么跑的），共 5 节。')
    print('所有接口调用（含 ContextInfo）均已套 %d 秒超时，不会静默卡死。'
          % CALL_TIMEOUT)
    print('[!] QMT 正在补充数据时不要跑本探针 —— 即使不卡，探到的也是')
    print('    半完成状态的数据，一部分有值一部分 NaN，会误判。等补完再跑。')


def handlebar(C):
    if _done[0]:
        return
    _done[0] = True
    print('=' * 78)
    print('已确认 %d 项（只打印结论，不再调接口）' % len(CONFIRMED))
    print('=' * 78)
    for name, when, concl in CONFIRMED:
        print('  v %s   [%s]' % (name, when))
        for ln in _wrap(concl, 72):
            print('      ' + ln)
    for name, fn in OPEN:
        print('')
        print('=' * 78)
        print('[待确认] %s' % name)
        print('=' * 78)
        try:
            fn(C)
        except Exception as e:
            import traceback
            print('   !! %s: %s' % (type(e).__name__, str(e)[:120]))
            for ln in traceback.format_exc().splitlines()[-3:]:
                print('      ' + ln)
    print('')
    print('=' * 78)
    print('[探针] 完。把整段贴回 —— 确认下来的会被挪进 CONFIRMED，不再重跑。')
    print('=' * 78)


def _wrap(s, n):
    out, cur = [], ''
    for ch in s:
        cur += ch
        if len(cur) >= n:
            out.append(cur); cur = ''
    if cur:
        out.append(cur)
    return out


CALL_TIMEOUT = 6                  # 单次接口调用上限（秒），ContextInfo 也套
STALL_MAX = 4                     # 连续超时几次后熔断
_STALL = [0]


def _call_timeout(fn, sec=None):
    """给可能阻塞的调用套超时。

    [!] QMT 的 print 是【缓冲】的：策略线程一旦阻塞，之前打印的内容也刷不出来
        —— 表现就是「点回测什么日志都没有」。实测被 xtdata.connect 卡过一次。

    [!] ContextInfo 的调用【同样要套】：QMT 客户端在补充数据时，
        C.get_financial_data / C.get_stock_list_in_sector 都可能阻塞等数据。
        一开始只套了 xtdata 是漏的 —— 而 F2 恰好排第一节，一卡又是全无输出。
    """
    import threading
    sec = CALL_TIMEOUT if sec is None else sec
    # ★ 熔断：连续超时 STALL_MAX 次后直接放弃，不再逐个等。
    #   没有熔断时，「QMT 正在补数据」这种场景下 44 个调用点各等 8 秒 = 352 秒，
    #   人会以为又卡死了。
    if _STALL[0] >= STALL_MAX:
        raise RuntimeError('接口持续阻塞，已熔断（QMT 是不是正在补充数据？'
                           '补完再跑本探针）')
    box = {}

    def _run():
        try:
            box['r'] = fn()
        except Exception as e:                      # noqa: BLE001
            box['e'] = e

    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()
    t.join(sec)
    if t.is_alive():
        _STALL[0] += 1
        raise RuntimeError('超时 %ds（第 %d 次；连续 %d 次就熔断）'
                           % (sec, _STALL[0], STALL_MAX))
    _STALL[0] = 0                      # 有一次成功就复位
    if 'e' in box:
        raise box['e']
    return box.get('r')



def _peek(r):
    """挑【非 NaN 的值】出来 —— 只看结构会漏掉「结构对但全空」。"""
    try:
        import numpy as np
        if r is None:
            return 'None'
        if hasattr(r, 'values'):
            v = np.asarray(r.values, dtype='float64').ravel()
            ok = v[~np.isnan(v)]
            return ('非空 %d 个，样例 %s' % (len(ok), ok[:4])) if len(ok) else '全 NaN'
        if isinstance(r, dict):
            return 'dict 键 %s' % list(r)[:3]
        return str(r)[:80]
    except Exception as e:
        return '看不了(%s)' % str(e)[:40]


# ================================ OPEN 已清空 ================================
#
# 2026-08-31：J1/J2/J3 全部定案，OPEN 里【暂时没有待确认项】。
#
# 三条策略线的移植状态：
#   froec / froec_traded / froec_traded_stop35  -> 已原生移植，数据全部到位
#   sgmspeg_v0b   -> 需要 jqfactor 近似（Barra 式 5 年回归斜率），保持信号执行器
#   红利指数增强   -> 【结构性不可原生移植】：fiscal_year 股息率口径要分红的
#                    【归属报告期】，而 get_divid_factors 的 7 个数里没有任何日期，
#                    且 dir(C) 里没有别的分红接口。这是两条腿共用的前置过滤，
#                    所以整条策略只能保持信号执行器形态。见 CONFIRMED 的 I5。
#
# 再有新问题时在这里按同样形态加一节 def xN_xxx(C) 并挂进 OPEN。
# 纪律没变：先 dir(C)/__doc__ 枚举 -> 再查官方文档 -> 探针只做验证且带真值对数。
# 本探针【不许出现模块级 import】—— 一律函数内导入（已踩两次）。


# [!] 顺序有讲究：【不碰网络】的先跑。
# 本探针只走 ContextInfo —— 完整版 QMT 里策略就是这么跑的。
# xtdata 那套（connect 127.0.0.1:58610）已从 OPEN 移除，理由见 CONFIRMED
# 里「xtdata 不是 miniQMT 专有，但策略用不上」那条。
OPEN = [
    # 暂无待确认项 —— 见上方「OPEN 已清空」的说明
]
