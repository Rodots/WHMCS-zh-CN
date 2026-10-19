# 报警项裁决指南

`langtool.py validate` 会报出几类差异。**hard invariants** 必须为 0；
**advisory** 类需要人工判断。本文件记录经过实战确认的判例。

原则：**报警是提问，不是判决。** 盲目「修复」一个正确的译文，会引入真正的问题。

---

## 判例 1：`&amp;` 作为英文连词被吸收 —— 正确，勿改

```
SRC: Save &amp; Activate
DST: 保存并启用
```

`&amp;` 在这里是英文连词 "and" 的转义。中文里「保存并启用」本就不需要连接词，
硬留一个 `&amp;` 反而变成「保存并启用 &amp; 激活」这种夹生表达。

**判断方法**：看 `&amp;` 两侧是不是两个对等的动词/名词短语。
若是，吸收为「并」「与」「及」是正确的本地化。

**何时该改回**：如果 `&amp;` 在原始 HTML 里承担实际渲染职责（罕见），才需要保留。
另外要注意**同一原文必须一致**——如果两处 `Save &amp; Activate` 一处保留一处吸收，那是缺陷。

同类：`&` 直接出现在文本里（非实体）时同理，例如
`Errors & Warnings` → 「错误和警告」也是对的。

---

## 判例 2：URL 正则吞掉全角标点 —— 假阳性，勿改

```
SRC: ... (eg. http://www.example.com/tos.html)
DST: ...（例如 http://www.example.com/tos.html）
```

若校验器报 `URL mismatch`，显示的却是 `http://...tos.html）`（多了一个全角括号），
那是**校验器的正则贪婪**，不是译文问题。`langtool.py` 已排除 CJK 与全角标点，正常情况下不会误报；
若你用的是自制脚本，记得在 URL 字符类里排除 `\u4e00-\u9fff` 与 `\uff00-\uffef`。

---

## 判例 3：裸 URL / 品牌名的值为英文 —— 正确，勿改

```
L82:   Sitejet Builder
L1099: ns1.example.com
L1944: MM/YY
L2150: American Express
L2433: PHP Mail()
```

这些条目**没有可翻译的自然语言内容**。保持英文是正确行为。
`validate` 的 `possibly untranslated prose` 只做提示，逐条确认属于下列类别即可放行：

- 品牌 / 产品名（Sitejet Builder、American Express、XOVI NOW）
- 示例域名 / 邮件 / IP（`ns1.example.com`、`server1.example.com`）
- 格式串（`MM/YY`）
- 技术标识（`PHP Mail()`、`minFraud ID`、`Logo URL`）
- 纯 URL 条目

---

## 判例 4：转义引号被换成全角引号 —— 真实缺陷，必须改

```
SRC: ... must set ... to \"Full Friendly Rewrite\" or \"Friendly index.php\".
DST: ... 将友好 URL 设置为“完全友好重写”或“友好 index.php”。
```

`\"` 计数从 4 变成 0。**这是必须修的**，因为用户在契约里明确要求 `\"` 原样保留。

注意它的危险之处：全角引号 `""` 不是 PHP 定界符，所以**文件仍然能正常解析**，
页面也能打开——错误完全静默，直到某天有人依赖那对引号做字符串比对。
所以不能靠「跑一下试试」发现，只能靠计数校验。

修复时保留中文行文习惯，只把引号换回转义形式：

```
DST: ... 将友好 URL 设置为 \"完全友好重写\" 或 \"友好 index.php\"。
```

---

## 判例 5：占位符丢失 —— 真实缺陷，必须改

```
SRC: You have %s regenerations remaining.
DST: 您还剩几次机会。
```

`%s` 消失会让运行时格式化失败或显示错乱。`normalize` 的护栏会拒绝把这类译文当作规范，
但护栏**只保护归一化过程**，不会替你修好它——所以 `validate` 之后仍要逐条看 hard 区。

---

## 判例 6：同源异译 —— 真实缺陷，但要先判断哪个是对的

```
SRC: Pricing
     [2905,3585] 定价
     [3652,3762,3856,3961,6432] 价格
```

同一个英文原文出现两种译法。默认取多数派，但**多数派不一定对**：
如果「定价」是术语表里定的词，哪怕它只出现 2 次，也应该用它。

所以正确顺序是：先用 `--report-only` 看全景 → 人工挑出重要术语写进 `--map` → 再执行归一化。
剩下的次要词条让工具按多数派自动处理。

注意有些词本就该按上下文分化，例如 `On` 在开关里是「开」、在时间语里是「于」。
`normalize` 按**精确源文**分组，如果发现这种分组过粗，就在 map 里为它显式指定，
或接受这种分化——**不要**让工具把语义不同的用法强行统一。

---

## 判例 7：同形词误译 —— 真实缺陷，结构校验看不到

```
SRC: minutes / tables / bold / running / ticket / apply
DST: 会议纪要 / 桌子 / 大胆 / 跑步 / 门票 / 申请
```

这些值是**合法简体、结构完整、占位符齐全**的，因此 `langtool validate` 与
`residue.py` 都会放行。它们唯一的错误是「义项选错」。
必须由 `scripts/homograph.py` 单独检查：

```bash
python scripts/homograph.py chinese.php
```

**判断方法**：看按键名推断语境。

- `dateTime.minutes` → 时间单位，必须「分钟」；`meeting.minutes` 才是「会议纪要」。
- `markdown.tables` → 表格；家具语境才是「桌子」。
- `vpsnetrunning` → 运行中；运动语境才是「跑步」。
- `feedback*` 里的 ticket → 工单；票务语境才是「门票/机票」。
- `orderpaymenttermadjectivemonthly` → 「月缴」正确；`vpsnetmonthly` → 「每月」。
- `idnLanguage.*` → 语言名（日语/法语/泰语），不是「日本人/法国人/泰国人」。
- DNS 记录类型（TXT/MX/DS/CNAME）和协议名（POP3）**保留原文**，
  不得展开成「防晒」「唱片」「流行音乐 3」。

**修复时必须带上上下文验证**，不要做全局字符串替换——同一个词在不同键里可能都对。
新增的固定译法写成 `--extra badlist.json` 的 `glossary` 规则，作为回归测试。

---

## 收尾检查单

- [ ] `validate` 的 hard problems = 0
- [ ] 所有 `\"` 行数量奇偶校验通过
- [ ] `normalize` 的 `skipped` 列表已人工看过
- [ ] `possibly untranslated` 全部确认属品牌/URL/格式串
- [ ] `lexcheck` 通过
- [ ] 有 PHP 环境的话，`php -l` 通过
- [ ] `homograph.py` 的 hard problems = 0，advisory 已逐条裁决
- [ ] 多义词（running/bold/table/flag/key/ticket/apply…）已按语境核对
- [ ] 表格类数据（语言名、周期、DNS 记录类型）整表一致
- [ ] 用户报告过的具体错例已固化成 `homograph.py` 规则
- [ ] 交付说明写清了：语言代码行、有意未译内容、被吸收的实体
