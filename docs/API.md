# 接口

`POST /api/run`：{"text":"CSV内容"}。同规则版本与输入摘要只产生一条批次。

`GET/POST /api/1` 至 `/api/7`：独立样例或自定义文件计算。各模块返回 report、quarantine、balance。

`POST /api/8/correction`：{"id":"运行编号"}。复检确认同一空白被多个检测批共用且超标时，生成关联各批次的污染更正单，冻结原回执与原复核意见；重复调用返回原单，不新增。

`POST /api/8/retry`：{"id":...,"override?":{"status":"PASS|BLANK_FAIL"},"fail_at_batch"?:n}。从最后确认批次续跑，已确认批次不重跑；同一空白重试复用同一更正单。`fail_at_batch` 用于模拟续跑失败。

`GET /api/8/order/{id}`：查询某运行的更正单与原回执快照；无单返回 404。

更正单进行中（PROCESSING/FAILED）`/api/review` 一律 409 PAUSED，原回执继续展示并附暂停原因；完成后默认“隔离关联批次”仍阻断放行，仅复测 PASS 重判的关联（批次,检测项）解除空白污染阻断。

`POST /api/review`：id、整数revision、decision取通过或退回。旧版本409；阻断项不能通过。

`GET /api/export/{id}`：唯一证据CSV，含运行摘要、原回执（original_receipt）、更正单（correction_order）、关联批次处置（correction_batch）与隔离记录。`GET /health`：存活检查。

错误：结构/数值422，版本冲突409，不存在404，文件过大413。逐行异常进入quarantine。
