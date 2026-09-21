# -*- coding: utf-8 -*-
"""看板「📈 个股」那一页的数据层，按产品域分。

依赖是单向的：`find / quote / ctx / link -> base`，`ind -> quote`。
对外契约仍是 `assay.stock`（门面），这里的模块不直接对外。
"""
