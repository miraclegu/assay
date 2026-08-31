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
    ('[**] 官方表清单（xtdata 文档）= 8 张，确实没有分红表', '2026-09-01',
     "download_financial_data / get_financial_data 的 table_list 官方取值："
     "'Balance'(资产负债表) / 'Income'(利润表) / 'CashFlow'(现金流量表) / "
     "'Capital'(股本表) / 'Holdernum'(股东数) / 'Top10holder'(十大股东) / "
     "'Top10flowholder'(十大流通股东) / 'Pershareindex'(每股指标)。"
     '★ 8 张里【没有分红表 / 除权除息表 / 分红送转表】。'
     '这条来自官方文档的完整清单，不是一个个试出来的 —— 比之前说「只有 5 张」准确'
     '（当时漏了三张股东表）。'),
    ('[**] get_divid_factors 的 7 个数 —— 官方字段定义', '2026-09-01',
     '[0] interest    每股股利（税前，元）；'
     '[1] stockBonus  每股红股（股）；'
     '[2] stockGift   每股转增股本（股）；'
     '[3] allotNum    每股配股数（股）；'
     '[4] allotPrice  配股价格（元）；'
     '[5] gugai       是否股改（股改在算复权系数时有特殊算法）；'
     '[6] dr          除权系数。'
     '★ 官方定义确认【没有报告期字段】—— 之前是从实测值全为 0 推的，现在有文档依据。'
     '也解释了两只银行 [1]~[5] 为何全是 0：它们只派现金，不送股不转增不配股不股改。'),
    ('[定案] 财务表只有 5 张，ASHAREFINANCIALINDICATOR【不存在】', '2026-08-31',
     '官方口径：ASHAREBALANCESHEET(资产负债表) / ASHAREINCOME(利润表) / '
     'ASHARECASHFLOW(现金流量表) / CAPITALSTRUCTURE(股本表) / PERSHAREINDEX(主要指标)，'
     '另有 Top10Holder / Top10FlowHolder / HolderNum。'
     '★ 这解释了上一轮扣非 8 个候选为什么全 NaN —— 其中 6 个挂在这张根本不存在的表上。'
     "★ 字段还支持【中文写法】：['利润表.净利润'] / ['资产负债表.固定资产']。"),
    ('[已作废] L3 的「字段存在性判别器」—— M1 找到两个反例', '2026-09-01',
     '★★ 上一轮宣布 Method2 的 nan = 字段不存在，M1 立刻推翻了它，两个反例：'
     '  [1] adjusted_earnings_per_share：Method2 -> nan，但 Method1 【非空 13/13 值 0.97】；'
     '  [2] ASHARECASHFLOW.dividend_interest_payment：Method2 -> nan，'
     '      但中文写法「现金流量表.分配股利、利润或偿付利息支付的现金」Method1 【非空 843 个】。'
     '-> Method2 的 nan 有第二种含义（该字段在 Method2 的默认 barpos 上无值），'
     '   【不能用来判定字段是否存在】。'
     '★ 连带后果：dividend_per_share 返回 nan 也【不能】证明它不存在 —— '
     '  分红字段的证据基础退回到「Method1 全 NaN」，而全 NaN 分不清名字错还是没数据。'
     '★ 教训：一个判别规则只跑了 4 个样本（其中 2 个是我自己挑的对照）就宣布成立，'
     '  样本量根本不够。要先在【已知有值】的字段上验证规则本身。'
     '（下面保留原文，记住错在哪。）'),
    ('[?] 原 L3 判别器（已作废，保留原文）', '2026-09-01',
     'get_financial_data 的第二种签名 (tabname, colname, market, code) 返回单个 float：'
     "  Method2('PERSHAREINDEX','adjusted_net_profit','SH','601398')       -> 0.0；"
     "  Method2('PERSHAREINDEX','__不存在的字段__','SH','601398')            -> nan；"
     "  Method2('PERSHAREINDEX','dividend_per_share','SH','601398')        -> nan；"
     "  Method2('ASHAREINCOME','net_profit_excl_min_int_inc','SH','601398')-> 7.5786e+10。"
     '★ 判别规则：【nan = 字段不存在；任何数值（含 0.0）= 字段存在】。'
     'dividend_per_share 与我编造的字段名返回完全一样 -> 该字段在本环境【确实不存在】。'
     '这比之前的「全 NaN」精确得多 —— 全 NaN 分不清是名字错还是没下载数据。'
     '★ 以后测「有没有某字段」一律先用 Method2 判别，别再用 Method1 看 NaN。'
     '★ 另：adjusted_net_profit 返回 0.0 而非 nan -> 字段【存在】，只是该 barpos 上是 0。'),
    ('[**] M1 定案：PERSHAREINDEX 15 个字段里【只有 9 个有值】', '2026-09-01',
     '601398 2024-04 窗口逐个实测（Method1 非空 13/13）：'
     's_fa_ocfps 3.9758 / s_fa_bps 9.55 / s_fa_eps_basic 0.98 / s_fa_eps_diluted 0.98 / '
     's_fa_undistributedps 5.3649 / s_fa_surpluscapitalps 0.4157 / '
     'adjusted_earnings_per_share 0.97 / du_return_on_equity 10.037 / gear_ratio 91.5507。'
     '[!!] 六个【金额类】字段值【恒为 0】（不是 NaN 是 0，所以非空检查也拦不住）：'
     'inc_revenue / inc_gross_profit / inc_profit_before_tax / du_profit / '
     'inc_net_profit / adjusted_net_profit。'
     '★ 纠正上一轮：我说「adjusted_net_profit 直接就是扣非净利润，不必绕 EPS x 股本」'
     '是【错的】—— 它恒为 0，扣非只能走 adjusted_earnings_per_share x get_total_share。'
     '★ 金额一律去三大报表取（ASHAREINCOME 原值已验证与本地 8/8 吻合）。'
     '★ Method1 与 Method2 取的【不是同一报告期】：s_fa_bps 9.55 vs 5.46、'
     'du_return_on_equity 10.037 vs 3.7869、s_fa_eps_basic 0.98 vs 0.21 -> '
     'Method2 只适合快速探路，取值一律用 Method1 + m_timetag。'),
    ('[定案] M2：raw 的空 field_list 也不能枚举，这条路彻底封死', '2026-09-01',
     "get_raw_financial_data([], ['601398.SH'], 2024全年) -> 外层有 code 键但"
     '【内层是空 dict】；单日窗口同样为空；raw 只给表名 -> dict len=0；'
     'get_financial_data([], …) -> DataFrame 字段 0 个。'
     '-> QMT 侧【没有任何字段枚举入口】，字段名只能查官方文档。'
     '文档里「field_list=[] 取全部字段」那条是 get_local_data 的约定，'
     '而 get_local_data 本身已过时（见下一条）。'),
    ('[定案] 空 field_list 不能枚举字段', '2026-09-01',
     "get_financial_data([], …) -> DataFrame 【字段 0 个】；"
     "get_financial_data(['PERSHAREINDEX.'], …) -> 一个空名列 ['']。"
     '文档里「field_list=[] 取全部字段」那条约定是 get_local_data 的，财务接口不认。'
     "[?] get_raw_financial_data([], …) -> dict len=1 keys=['601398.SH'] —— "
     '内层没打开（探针漏了 _dig），可能才是枚举入口。见 OPEN 的 M2。'),
    ('[**] get_local_data 已过时 -> 用 get_market_data_ex(subscribe=False)', '2026-09-01',
     'QMT 自己的提示原文：「get_local_data接口版本较老，推荐使用 get_market_data_ex 替代，'
     '参数 subscribe 设置为 False，只取本地数据不从服务器订阅数据」。'
     '★ 这条对【批量导出数据给 datalake】有用 —— 那件事一直卡在 xtdata 连不上端口 58610，'
     '而 get_market_data_ex(subscribe=False) 是 ContextInfo 侧的本地取数方案。'),
    ('[**] PERSHAREINDEX 官方字段清单（15 个）—— 不再猜名字', '2026-09-01',
     's_fa_ocfps=每股经营活动现金流量 / s_fa_bps=每股净资产 / '
     's_fa_eps_basic=基本每股收益 / s_fa_eps_diluted=稀释每股收益 / '
     's_fa_undistributedps=每股未分配利润 / s_fa_surpluscapitalps=每股资本公积金 / '
     'adjusted_earnings_per_share=扣非每股收益 / inc_revenue=主营收入 / '
     'inc_gross_profit=毛利润 / inc_profit_before_tax=利润总额 / du_profit=净利润 / '
     'inc_net_profit=归属母公司净利润 / adjusted_net_profit=扣非净利润 / '
     'du_return_on_equity=净资产收益率 / gear_ratio=资产负债比率。'
     '★★ 这 15 个里【没有任何分红/股利字段】。'
     '★ 两个会咬人的纠正：'
     '① adjusted_net_profit 【直接就是扣非净利润】—— 上一轮费劲用「扣非EPS x 总股本」'
     '还原、还撞上 57/244 的稀疏覆盖，其实有直接字段；'
     '② 迅投的 inc_ 前缀【不是增长率】：inc_revenue/inc_gross_profit/inc_net_profit '
     '都是【金额】。而聚宽的 inc_net_profit_year_on_year 是【增长率】—— '
     '同样的前缀在两个源里含义相反，这种碰撞比「取不到」危险得多。'),
    ('[定案] K 轮：分红的【归属报告期】在 ContextInfo API 里确实没有', '2026-09-01',
     '已穷尽所有可能提供它的入口，不是猜：'
     '① get_divid_factors -> 除权日 + 每股金额（金额与本地 12/12 精确吻合），无报告期；'
     '② PERSHAREINDEX 官方 15 个字段里没有分红字段；实测 dividend_per_share / '
     'dividend_per_share_before_tax / s_fa_dps / cash_dividend_per_share / '
     'dividend_payout_ratio / dividend_yield 以及中文「每股股利/每股分红/每股现金股利/'
     '股利支付率/股息率」全 NaN；'
     '③ get_dividend / get_divid_plan / get_bonus 都不存在；'
     '④ 财务表只有 5 张 + Top10Holder/Top10FlowHolder/HolderNum。'
     '★ 唯一有值的分红相关字段是「现金流量表.分配股利、利润或偿付利息支付的现金」'
     '（= ASHARECASHFLOW.dividend_interest_payment，非空 843 个），'
     '但它【不能用】：付现口径 + 银行混入偿付利息。本地 601398 逐年：'
     '2021 948 亿 / 2022 1194 亿 / 2023 【11290 亿】/ 2024 1773 亿，'
     '而真实现金分红只有 1045/1082/1092/1098 亿 —— 2023 差 10 倍。'
     '★ 结论的正确说法：QMT 的分红数据是【完整的公司行为记录】（除权日+金额，'
     '执行系统需要的就是这个），缺的是【按会计年度归属】这一层分析口径 —— '
     '那是 Wind/聚宽式的加工层，不是原始行情数据。'),
    ('[?] I5 结论【已降级】—— 当时只探了一个接口就下结论', '2026-09-01',
     '★★ 这是同类方法论错误的【第四次】：下面这条说「拿不到归属报告期」，'
     '但我当时【只探了 get_divid_factors 一个接口，完全没查财务表】。'
     '而财务表本来就是按报告期索引的（m_timetag / m_anntime 已验证可用），'
     '官方文档说 PERSHAREINDEX（主要指标）里有 dividend_per_share【每股股利】。'
     '若成立，fiscal_year 口径能原生实现，下面这条要整条推翻。见 OPEN 的 K1-K3。'
     '教训：一个能力有没有，要把【所有可能提供它的接口】都查过才能下结论 —— '
     '分红数据既可能在分红接口里，也可能在财务表里。'),
    ('[?] 原 I5（保留原文，待 K 轮判决）', '2026-08-31',
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


    ('[定案] 多因子数据这条路【放弃】—— 数据源已停更，不是权限问题', '2026-09-01',
     '[1] 下载位置不在【数据管理->补充数据】，而在【行情界面 -> 扩展数据 -> 因子库】；'
     '用户实测补充数据里确实没有「多因子数据」这一项。'
     '[2] ★ 迅投社区管理员原话：「iQuant 多因子数据由国信提供，现已不再更新」——'
     '这解释了为什么下载后拿到 NaN。迅投 VIP 的因子服务「即将上线」，是未来的事。'
     '[3] 还有版本因素：QMT 的 Python 库分 Py36普通版 / Py36因子版，'
     '只有因子版带因子数据功能；且「下载了因子版想切回普通版要删除所有库」。'
     '-> 所以 N3 里 PE/PB 全空【不是】因子名写错、也不是没勾下载，'
     '而是这套数据本身停更了。因子路线不再尝试。'
     '★ 但这不影响主线：DividData 是随行情/除权数据一起下的，与多因子无关，'
     'O1-O3 不碰 get_factor_data。'),
    ('[**] O 轮定案：DividData 是【LevelDB】，Finance 是每股每表一个私有 .DAT', '2026-09-01',
     'datadir/DividData：42 个 .ldb + LOG，LOG 里是标准 LevelDB 日志'
     '（Recovering log #905 / Level-0 table #908 / Expanding@0 1+1）-> 开放格式。'
     'O3 读出的 key 形如 SZ|139051|4000|1555862400000，最后一段是毫秒时间戳'
     '（1555862400000 -> 2019-04-22、1564675200000 -> 2019-08-02），'
     '都是【除权日】量级，与 get_divid_factors 一致。'
     '第二段 139051 是内部 ID 不是股票代码；.ldb 的文件名是 sstable 编号也不是代码，'
     '所以 O2 在 DividData 下搜 601398 没命中是正常的。'
     '★ datadir/Finance：按 市场/86400/<代码>_700N.DAT 组织，N=1..8 —— '
     '正好对上官方 8 张财务表。SH 18662 个文件 / SZ 23345 / BJ 2584，全是私有二进制'
     '（magic 形如 00 c8 16 d4 0f 01 ...），没有可读片段。'
     '★ datadir/EP：70 个子目录形如 <因子名>_Xdat，每个里面只有一个 35K 的 config(json)，'
     '【没有 data.fe】-> 因子只有配置没有数据，与「iQuant 多因子已停更」吻合。'
     '因子名可见：10日振幅 / 10日涨跌幅 / 12日bias / 12日roc / 12日rsi / 1个月收益率 / '
     '1月换手率 / 20日振幅 / 24日rsi / 30日振幅 …… 全是技术类，没有股息/分红类。'),
    ('[**] N 轮：datadir 结构 = 找对了枚举入口，但目标找错了', '2026-09-01',
     'datadir = D:/国金证券QMT交易端/datadir（从 xtquant.__file__ 反推成功）。'
     "下面 13 项：BJ / CC / DividData / EP / Finance / Industry / SH / SZ / "
     "Sector / TradeDateAndETFStockListCache / Weight / increase / quotetimeinfo。"
     '★★ 有【DividData】目录 —— 分红数据的磁盘落点；旁边还有 Finance(财务)、'
     'Industry(行业)、Sector(板块)、Weight(指数权重)。'
     '我一直在找 API 里的分红字段，而数据是按【专门目录】存的。'
     '★ EP 存在但里面【没有 *_Xdat2】-> 多因子数据确实没下载。'
     '这也解释了 N3 里连【已知存在】的 PE/PB 都取不到 —— '
     '「先验证已知存在的因子」这个判据起作用了：全空说明是没下载数据，'
     '不是因子名写错。要用多因子得先去客户端【数据管理->补充数据】勾【多因子数据】。'),


# ============================== 待确认 P：LevelDB ==========================
#
# N1 保留（P 轮要用它定位 datadir）；O1-O3 已定案，见 CONFIRMED。


# N 轮的跨节状态。不用 g —— g 是 QMT 注入的，本地干跑时不存在，
# 而这两节的枚举逻辑值得能在本地验证。
_STATE = {}


def n1_locate_datadir(C):
    """先把 datadir 找到 —— 后面枚举因子名全靠它。

    已知安装根（从早前 xtdata 的报错路径读出来的，路径里带反斜杠这里用正斜杠写）：
        D:/国金证券QMT交易端/bin.x64/lib/site-packages/xtquant/xtdata.py
    所以安装根是 D:/国金证券QMT交易端。datadir 位置各版本不同，都试一遍；
    另外从 xtquant 的实际位置反推，比硬编码可靠。
    """
    import os
    import sys
    print('   -- 运行环境线索 --')
    try:
        print('      cwd        = %s' % os.getcwd()[:110])
    except Exception as e:
        print('      cwd        取不到 %s' % str(e)[:40])
    print('      executable = %s' % str(getattr(sys, 'executable', '?'))[:110])
    for pth in list(getattr(sys, 'path', []))[:6]:
        print('      sys.path[] = %s' % str(pth)[:110])

    roots = []
    try:
        import xtquant
        d = os.path.abspath(xtquant.__file__)
        for _ in range(5):
            d = os.path.dirname(d)
        print('      xtquant 反推安装根 = %s' % d[:110])
        roots += [d, os.path.join(d, 'datadir')]
    except Exception as e:
        print('      xtquant 反推失败: %s' % str(e)[:60])
    roots += [r'D:\国金证券QMT交易端', r'D:\国金证券QMT交易端\datadir',
              r'C:\国金证券QMT交易端', r'C:\国金证券QMT交易端\datadir']

    _STATE['datadir'] = None
    for r in roots:
        try:
            ok = os.path.isdir(r)
        except Exception:
            ok = False
        print('      %-56s %s' % (str(r)[:56], '存在' if ok else '无'))
        if not ok:
            continue
        if os.path.basename(r).lower() == 'datadir':
            _STATE['datadir'] = _STATE['datadir'] or r
        else:
            cand = os.path.join(r, 'datadir')
            if os.path.isdir(cand):
                _STATE['datadir'] = _STATE['datadir'] or cand
    print('   -> datadir = %s' % (_STATE['datadir'] or '未找到'))


# ============================== 待确认 O：DividData ==========================
#
# ★★ N2 找对了枚举入口，但我找错了目标。datadir 下 13 项：
#     ['BJ','CC','DividData','EP','Finance','Industry','SH','SZ','Sector',
#      'TradeDateAndETFStockListCache','Weight','increase','quotetimeinfo']
#
#   [1] DividData —— 分红数据就在这里。API 不给报告期，不代表文件里没有。
#   [2] Finance   —— 财务数据。
#   [3] EP 存在但里面没有 *_Xdat2 -> 多因子数据确实没下载，
#       这也解释了 N3 里连【已知存在】的 PE/PB 都取不到 —— 判据起作用了。
#
# 本轮只做一件事：把 DividData / Finance / EP 的目录结构和文件格式看清楚。
# 不解析内容，先确认「有什么、什么格式、多大」。


# ============================== 待确认 P：LevelDB key 结构 ===================
#
# O 轮定案：DividData 是【LevelDB】（42 个 .ldb + LOG 里的 "Recovering log /
# Level-0 table / Expanding@0" 是标准 LevelDB 日志），开放格式不是私有二进制。
# O3 读出的 key 形如：   SZ|139051|4000|1555862400000
#   最后一段是毫秒时间戳（1555862400000 -> 2019-04-22、1564675200000 -> 2019-08-02），
#   都是【除权日】的量级，与 get_divid_factors 一致。
#
# 所以只剩一个问题值得问：那个 4000 是不是【唯一的记录类型】？
#   只有 4000  -> 存储层就一种记录（除权事件），没有报告期，这条路到此为止
#   有别的类型 -> 值得看那些记录里有什么
#
# 做法：不装 leveldb 库（QMT 是 py36，装不了 plyvel）。.ldb 里的 key 是明文，
#       直接按正则扫可见片段，统计【第三段】的取值分布。这不是解析数据，
#       只是数 key 的形状 —— 够回答上面那个问题。
#
# [!] 只读、不写、不改任何文件。每个文件只读前 4MB，最多扫 6 个文件。


def p1_ldb_keys(C):
    import os
    import re
    dd = _STATE.get('datadir')
    if not dd:
        print('   [!] datadir 未找到，先看 N1')
        return
    root = os.path.join(dd, 'DividData')
    if not os.path.isdir(root):
        print('   DividData 不存在')
        return
    try:
        files = sorted(x for x in os.listdir(root) if x.endswith('.ldb'))
    except Exception as e:
        print('   列目录失败 %s' % str(e)[:50])
        return
    print('   %d 个 .ldb；扫前 6 个（每个只读前 4MB）' % len(files))

    # key 形如 SZ|139051|4000|1555862400000
    pat = re.compile(rb'(S[HZ]|BJ)\|(\d{1,8})\|(\d{1,6})\|(\d{9,14})')
    types, mkts, ids, tss, n_key = {}, {}, {}, [], 0
    for fn in files[:6]:
        fp = os.path.join(root, fn)
        try:
            f = open(fp, 'rb')
            try:
                buf = f.read(4 * 1024 * 1024)
            finally:
                f.close()
        except Exception as e:
            print('      %s 读不了 %s' % (fn, str(e)[:40]))
            continue
        hit = pat.findall(buf)
        n_key += len(hit)
        for mkt, iid, typ, ts in hit:
            t = typ.decode('ascii')
            types[t] = types.get(t, 0) + 1
            m = mkt.decode('ascii')
            mkts[m] = mkts.get(m, 0) + 1
            if len(ids) < 8:
                ids[iid.decode('ascii')] = 1
            if len(tss) < 4000:
                tss.append(int(ts))
        print('      %-16s %d 个 key' % (fn, len(hit)))

    print('   ---- 共 %d 个 key ----' % n_key)
    print('   市场段取值: %s' % sorted(mkts.items(), key=lambda kv: -kv[1]))
    print('   ★ 第三段（类型码）取值分布: %s'
          % sorted(types.items(), key=lambda kv: -kv[1])[:20])
    print('   第二段（内部 ID）样例: %s' % sorted(ids)[:8])
    if tss:
        import datetime as dt
        tss = sorted(t for t in tss if 9e11 < t < 2e12)      # 只看合理的毫秒戳
        if tss:
            def d(x):
                return dt.datetime.utcfromtimestamp(
                    x / 1000.0 + 8 * 3600).strftime('%Y-%m-%d')
            print('   时间戳范围: %s ~ %s（共 %d 个合理值）'
                  % (d(tss[0]), d(tss[-1]), len(tss)))
            # 除权日应集中在 5~8 月；若集中在 3/6/9/12 月末则更像报告期
            mon = {}
            for x in tss:
                mm = int(d(x)[5:7])
                mon[mm] = mon.get(mm, 0) + 1
            print('   月份分布: %s' % sorted(mon.items()))
            print('   [判读] 集中在 5~8 月 -> 是【除权日】；')
            print('          集中在 3/6/9/12 月末 -> 是【报告期】，那就有戏了。')
    print('   ---- 结论怎么读 ----')
    print('   类型码只有一个值   -> 存储层就一种记录，没有报告期，路到此为止；')
    print('   有多个值           -> 把每个类型的 key 样例打出来再判（下一轮）。')


def p2_ldb_sample(C):
    """挑几条 key 连同它后面的字节打出来 —— 看 value 里有没有第二个日期。

    get_divid_factors 返回 7 个数，若 value 里能数出 7 个 double，
    就说明存储层和 API 是一一对应的，API 没有隐藏字段。
    """
    import os
    import re
    import struct
    dd = _STATE.get('datadir')
    if not dd:
        return
    root = os.path.join(dd, 'DividData')
    if not os.path.isdir(root):
        return
    files = sorted(x for x in os.listdir(root) if x.endswith('.ldb'))
    pat = re.compile(rb'(S[HZ]|BJ)\|(\d{1,8})\|(\d{1,6})\|(\d{9,14})')
    shown = 0
    for fn in files[:3]:
        fp = os.path.join(root, fn)
        try:
            f = open(fp, 'rb')
            try:
                buf = f.read(2 * 1024 * 1024)
            finally:
                f.close()
        except Exception:
            continue
        for m in pat.finditer(buf):
            if shown >= 6:
                break
            key = m.group(0).decode('ascii')
            tail = buf[m.end():m.end() + 96]
            txt = ''.join(chr(b) if 32 <= b < 127 else '.'
                          for b in bytearray(tail))
            print('   key = %s' % key)
            print('      可见: %s' % txt)
            # 把后面的字节按 double 解一遍，看有没有像每股股利/复权因子的数
            vals = []
            for off in range(0, min(len(tail) - 8, 72)):
                try:
                    v = struct.unpack_from('<d', tail, off)[0]
                except Exception:
                    continue
                if v == v and 1e-6 < abs(v) < 1e6:
                    vals.append((off, round(v, 6)))
            print('      像数值的 double(偏移,值): %s' % vals[:10])
            shown += 1
        if shown >= 6:
            break
    if not shown:
        print('   没扫到 key —— 看 P1 的正则是否匹配')
    print('   [判读] 数值里出现 0.3035 / 1.0234 这类 -> 就是 interest 与 dr，')
    print('          说明存储层与 get_divid_factors 一一对应，API 没藏字段。')


# [!] 顺序有讲究：【不碰网络】的先跑。
# 本探针只走 ContextInfo —— 完整版 QMT 里策略就是这么跑的。
# xtdata 那套（connect 127.0.0.1:58610）已从 OPEN 移除，理由见 CONFIRMED
# 里「xtdata 不是 miniQMT 专有，但策略用不上」那条。
OPEN = [
    ('N1 定位 datadir（P 轮要用）', n1_locate_datadir),
    ('P1 ★★ 扫 .ldb 的 key：类型码是不是只有 4000 一个值', p1_ldb_keys),
    ('P2 挑几条 key 连 value 一起看，数得出 7 个 double 就说明 API 没藏字段', p2_ldb_sample),
]
