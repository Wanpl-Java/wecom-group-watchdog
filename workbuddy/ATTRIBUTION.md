# 第三方归属

## anti-aistyle-zh

- 来源：https://github.com/RichardCao/anti-aistyle-zh
- 协议：MIT（见 `vendor/anti-aistyle-zh/LICENSE`）
- 用途：JumpServer 企微售后「回复建议」去 AI 味（客服口径 / 改写强度=平衡）
- 整合方式：
  - 上游 skill 副本：`workbuddy/vendor/anti-aistyle-zh/`
  - 售后收缩合同：`workbuddy/anti-aistyle-support/SKILL.md`
  - 运行时注入：`app/anti_aistyle.py` → `app/workbuddy.py` system prompt
  - 轻量后处理：去掉「希望对您有帮助」等套话行
