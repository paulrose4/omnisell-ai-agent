"""Domain Guardrail — Bilingual Keyword-Tree-Based Input Filtering
=================================================================

This module is the **first line of defense** for OmniSell AI agents. It gates
every user input *before* it reaches the multi-agent runtime using a bilingual
(Chinese + English) keyword tree covering the full e-commerce operation domain.

Why a Keyword Tree (Not an ML Classifier)?
-------------------------------------------
1. **Transparent & auditable** — every allow/block decision is traceable
   to specific keyword matches. No black-box model decisions.
2. **Zero external dependencies** — no NLP library, no model download,
   no GPU needed. Works entirely on substring matching.
3. **Works for unsegmented Chinese** — substring matching handles Chinese
   without word segmentation (unlike token-based NLP).
4. **Fast & cheap** — O(n * k) where n is input length, k is keyword count.
   Typically <1ms per request.
5. **Easy to extend** — adding a new category or keyword requires no
   retraining, just appending to a list.
6. **Bilingual by design** — Chinese and English keywords coexist in the
   same tree. Mixed-language inputs (common in SEA e-commerce) are
   handled naturally.

Architecture Flow:
  DomainKeywordTree (7 categories, 20+ subcategories)
    |
    v
  preprocess() — NFC normalization, lowercase, strip special chars
    |
    v
  DomainGuardrail.check() — match against keyword tree + harmful patterns
    |
    v
  GuardrailResult — is_allowed, confidence, matched_paths, reason

Three Strategy Tiers:
  * strict   — only requests hitting e-commerce domain keywords pass.
               Best for production agents with narrow scope.
  * moderate — e-commerce keywords OR general business vocabulary pass.
               Best for internal dashboards with broader queries.
  * permissive — everything passes except clearly harmful content
                 (jailbreak prompts, prompt-injection, ad spam).
               Best for R&D environments with open-ended exploration.

Security Integration Points:
  - agents/group_chat.py:start_chat() — gates all user task descriptions
  - api/routes/customer_routes.py — gates customer questions before RAG
  - api/middleware.py — can be wired for API-level filtering
  - safety/tool_permissions.py — coordinates with permission model
  - safety/refund_constraint.py — refund keywords feed into HITL escalation
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from .audit import AuditLogger
except ImportError:  # pragma: no cover - exercised when run as a script
    from audit import AuditLogger  # type: ignore[no-redef]


# --------------------------------------------------------------------------- #
# Keyword tree definition
# --------------------------------------------------------------------------- #


@dataclass
class SubcategoryNode:
    """A leaf group of keywords under a domain category."""

    name: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class CategoryNode:
    """A top-level e-commerce domain category with optional subcategories."""

    name: str
    keywords: list[str] = field(default_factory=list)
    subcategories: dict[str, SubcategoryNode] = field(default_factory=dict)


@dataclass
class DomainKeywordTree:
    """Root of the bilingual domain keyword tree."""

    categories: dict[str, CategoryNode] = field(default_factory=dict)

    def iter_paths(self) -> list[tuple[str, str | None, list[str]]]:
        """Return (category, subcategory|None, keywords) tuples for matching."""
        paths: list[tuple[str, str | None, list[str]]] = []
        for cat_key, cat in self.categories.items():
            if cat.keywords:
                paths.append((cat_key, None, cat.keywords))
            for sub_key, sub in cat.subcategories.items():
                paths.append((cat_key, sub_key, sub.keywords))
        return paths


def _build_default_tree() -> DomainKeywordTree:
    """Build the default bilingual e-commerce keyword tree (7 categories).

    Coverage:
      product — 商品管理 / Product Management
      order   — 订单管理 / Order Management
      customer — 客户服务 / Customer Service
      compliance — 合规风控 / Compliance & Risk
      marketing — 营销推广 / Marketing
      payment  — 支付结算 / Payment & Settlement
      analytics — 数据分析 / Analytics
    """
    return DomainKeywordTree(
        categories={
            "product": CategoryNode(
                name="商品管理 / Product Management",
                keywords=["商品", "产品", "上架", "下架", "价格", "库存", "描述", "图片", "listing", "product"],
                subcategories={
                    "inventory": SubcategoryNode(
                        name="库存 / Inventory",
                        keywords=["库存", "备货", "inventory", "stock", "restock"],
                    ),
                    "pricing": SubcategoryNode(
                        name="价格 / Pricing",
                        keywords=["价格", "调价", "定价", "pricing", "price"],
                    ),
                    "description": SubcategoryNode(
                        name="描述 / Description",
                        keywords=["描述", "详情", "description", "detail"],
                    ),
                },
            ),
            "order": CategoryNode(
                name="订单管理 / Order Management",
                keywords=["订单", "order"],
                subcategories={
                    "cancel": SubcategoryNode(
                        name="取消 / Cancel",
                        keywords=["取消", "cancel"],
                    ),
                    "refund": SubcategoryNode(
                        name="退款 / Refund",
                        keywords=["退款", "refund"],
                    ),
                    "exchange": SubcategoryNode(
                        name="换货 / Exchange",
                        keywords=["换货", "退换", "exchange", "return"],
                    ),
                    "shipping": SubcategoryNode(
                        name="物流发货 / Shipping",
                        keywords=["物流", "发货", "快递", "ship", "shipping", "tracking", "运单"],
                    ),
                    "logistics": SubcategoryNode(
                        name="仓储物流 / Warehouse & Logistics",
                        keywords=["仓库", "仓储", "打包", "warehouse", "logistics", "delivery", "fulfillment", "dispatch"],
                    ),
                },
            ),
            "customer": CategoryNode(
                name="客户服务 / Customer Service",
                keywords=["客服", "客户", "售后", "customer", "service"],
                subcategories={
                    "complaint": SubcategoryNode(
                        name="投诉 / Complaint",
                        keywords=["投诉", "举报", "complaint", "dispute", "report"],
                    ),
                    "support": SubcategoryNode(
                        name="帮助支持 / Support",
                        keywords=["问", "帮", "support", "help", "guide", "how"],
                    ),
                    "account": SubcategoryNode(
                        name="账号管理 / Account",
                        keywords=["账号", "密码", "登录", "account", "password", "login", "register"],
                    ),
                },
            ),
            "compliance": CategoryNode(
                name="合规风控 / Compliance & Risk",
                keywords=["合规", "风控", "风险", "compliance", "risk", "policy"],
                subcategories={
                    "fraud": SubcategoryNode(
                        name="欺诈检测 / Fraud",
                        keywords=["欺诈", "fraud", "scam", "counterfeit", "假货"],
                    ),
                    "abnormal": SubcategoryNode(
                        name="异常检测 / Abnormal",
                        keywords=["异常", "abnormal", "anomaly"],
                    ),
                    "sensitive": SubcategoryNode(
                        name="敏感词 / Sensitive",
                        keywords=["敏感词", "违禁", "sensitive", "banned", "restricted"],
                    ),
                },
            ),
            "marketing": CategoryNode(
                name="营销推广 / Marketing",
                keywords=["营销", "促销", "活动", "marketing", "campaign"],
                subcategories={
                    "coupon": SubcategoryNode(
                        name="优惠券 / Coupon",
                        keywords=["优惠券", "券", "coupon", "voucher"],
                    ),
                    "promotion": SubcategoryNode(
                        name="促销活动 / Promotion",
                        keywords=["促销", "打折", "promotion", "discount", "sale"],
                    ),
                },
            ),
            "payment": CategoryNode(
                name="支付结算 / Payment & Settlement",
                keywords=["支付", "付款", "结算", "收款", "payment", "settlement", "wallet", "balance"],
                subcategories={
                    "method": SubcategoryNode(
                        name="支付方式 / Payment Method",
                        keywords=["支付宝", "微信支付", "信用卡", "银行卡", "alipay", "wechat pay", "credit card", "debit", "bank transfer"],
                    ),
                    "fee": SubcategoryNode(
                        name="手续费 / Transaction Fee",
                        keywords=["手续费", "费率", "fee", "charge", "commission"],
                    ),
                    "refund_payment": SubcategoryNode(
                        name="退款到账 / Refund Status",
                        keywords=["退回到", "退款到账", "原路返回", "refund to", "refund status", "退款进度", "退款时间"],
                    ),
                },
            ),
            "analytics": CategoryNode(
                name="数据分析 / Analytics",
                keywords=["数据", "分析", "报表", "趋势", "analytics", "report", "dashboard", "metrics"],
                subcategories={
                    "sales": SubcategoryNode(
                        name="销售分析 / Sales Analytics",
                        keywords=["销售额", "销量", "转化率", "revenue", "conversion", "gmv", "aov", "客单价"],
                    ),
                    "traffic": SubcategoryNode(
                        name="流量分析 / Traffic Analytics",
                        keywords=["流量", "访客", "曝光", "traffic", "visitor", "impression", "click", "ctr"],
                    ),
                },
            ),
        }
    )


DEFAULT_DOMAIN_TREE = _build_default_tree()

# General business vocabulary used by the moderate strategy.
# Allows broader operational queries without requiring strict e-commerce domain keywords.
GENERAL_BUSINESS_TERMS: list[str] = [
    # English general business
    "business", "report", "analysis", "task", "data", "dashboard",
    "kpi", "sales", "revenue", "forecast",
    "summary", "insight", "trend", "metric", "benchmark", "optimize",
    "performance", "growth", "target", "budget", "cost",
    # Chinese general business
    "业务", "报表", "分析", "任务", "数据", "看板", "销量", "营收", "预测", "运营",
    "周期", "汇总", "概览", "指标", "优化", "绩效", "增长", "预算", "成本",
    # Cross-functional
    "供应商", "supplier", "channel", "渠道", "platform", "策略",
    "workflow", "automation", "自动化", "流程", "审批",
    "通知", "提醒", "notification", "alert", "schedule", "定时",
]

# Clearly harmful / out-of-scope patterns always blocked by permissive strategy.
# Updated to include refund execution bypass attempts and admin override patterns.
HARMFUL_PATTERNS: list[str] = [
    # Prompt injection / jailbreak
    "ignore previous", "ignore above", "ignore all previous",
    "disregard previous", "forget your instructions",
    "system prompt", "reveal your instructions", "show me your prompt",
    "jailbreak", "dan mode", "developer mode",
    "you are now dan", "act as dan",
    # Ad spam / social engineering
    "vx:", "wechat:", "加微信", "加我微信", "代理赚钱", "兼职刷单", "刷单",
    # Refund bypass attempts (coexists with :py:mod:`safety.refund_constraint`)
    "execute refund", "auto refund", "自动退款", "直接退款", "帮我退款",
    "自动赔付", "自动补偿",
    # Admin override / destructive operations
    "bypass approval", "绕过审批", "跳过审核", "skip review",
    "admin override", "delete all", "drop table", "shutdown",
    "sudo", "rm -rf", "format c:",
]


# --------------------------------------------------------------------------- #
# Strategy and result types
# --------------------------------------------------------------------------- #


class GuardrailStrategy(str, Enum):
    STRICT = "strict"
    MODERATE = "moderate"
    PERMISSIVE = "permissive"


# Reason returned to callers/users. Deliberately non-revealing.
_BLOCK_REASON = "请求不在可处理范围内。 / Request is outside the processable scope."


@dataclass
class GuardrailResult:
    """Outcome of a domain guardrail check."""

    is_allowed: bool
    confidence: float
    matched_paths: list[str]
    reason: str
    strategy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_allowed": self.is_allowed,
            "confidence": self.confidence,
            "matched_paths": self.matched_paths,
            "reason": self.reason,
            "strategy": self.strategy,
        }


class DomainBlockedError(RuntimeError):
    """Raised when a request is blocked by the domain guardrail."""

    def __init__(self, result: GuardrailResult) -> None:
        self.result = result
        super().__init__(result.reason)


# --------------------------------------------------------------------------- #
# Preprocessing
# --------------------------------------------------------------------------- #

_SPECIAL_CHAR_RE = re.compile(r"[^\w\u4e00-\u9fff\s]", re.UNICODE)


def preprocess(text: str) -> str:
    """Normalize input for matching: NFC, lowercase, strip special chars.

    Chinese characters are preserved (no segmentation needed because we use
    substring matching), while punctuation/symbols are collapsed to spaces.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text)
    # Convert full-width alphanumerics/punctuation to half-width.
    normalized = normalized.translate(
        str.maketrans({chr(i): chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)})
    )
    lower = normalized.lower()
    cleaned = _SPECIAL_CHAR_RE.sub(" ", lower)
    return re.sub(r"\s+", " ", cleaned).strip()


def _count_hits(haystack: str, needles: list[str]) -> list[str]:
    """Return the subset of needles found as substrings in haystack."""
    return [n for n in needles if n and n in haystack]


# --------------------------------------------------------------------------- #
# Guardrail
# --------------------------------------------------------------------------- #


class DomainGuardrail:
    """Bilingual domain guardrail with three strictness levels.

    Example usage::

        guardrail = DomainGuardrail(strategy="strict", audit_logger=logger)
        result = guardrail.check("帮我上架商品并设置价格和库存")
        if result.is_allowed:
            process(result)
        else:
            reject(result.reason)
    """

    def __init__(
        self,
        tree: DomainKeywordTree | None = None,
        strategy: str | GuardrailStrategy = GuardrailStrategy.MODERATE,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.tree = tree or DEFAULT_DOMAIN_TREE
        if isinstance(strategy, GuardrailStrategy):
            self.strategy = strategy
        else:
            self.strategy = GuardrailStrategy(str(strategy))
        self.audit_logger = audit_logger

    def check(self, text: str) -> GuardrailResult:
        """Classify text and return a GuardrailResult.

        When the request is blocked and an audit logger is attached, a
        domain_blocked audit record is written.
        """
        cleaned = preprocess(text)
        domain_hits = self._match_domain(cleaned)
        general_hits = _count_hits(cleaned, GENERAL_BUSINESS_TERMS)
        harmful_hits = _count_hits(cleaned, HARMFUL_PATTERNS)

        allowed, confidence = self._decide(domain_hits, general_hits, harmful_hits, cleaned)

        matched_paths = [f"{p[0]}/{p[1]}" if p[1] else p[0] for p in domain_hits]

        if allowed:
            reason = ""
        else:
            reason = _BLOCK_REASON
            self._audit_block(cleaned, confidence, matched_paths, harmful_hits)

        return GuardrailResult(
            is_allowed=allowed,
            confidence=confidence,
            matched_paths=matched_paths,
            reason=reason,
            strategy=self.strategy.value,
        )

    def check_or_raise(self, text: str) -> GuardrailResult:
        """Like check() but raise DomainBlockedError on block."""
        result = self.check(text)
        if not result.is_allowed:
            raise DomainBlockedError(result)
        return result

    def _match_domain(self, cleaned: str) -> list[tuple[str, str | None, list[str]]]:
        """Return tree paths (category, subcategory, matched keywords)."""
        hits: list[tuple[str, str | None, list[str]]] = []
        for cat_key, sub_key, keywords in self.tree.iter_paths():
            matched = _count_hits(cleaned, keywords)
            if matched:
                hits.append((cat_key, sub_key, matched))
        return hits

    def _decide(
        self,
        domain_hits: list[tuple[str, str | None, list[str]]],
        general_hits: list[str],
        harmful_hits: list[str],
        cleaned: str,
    ) -> tuple[bool, float]:
        """Apply the active strategy and return (allowed, confidence)."""
        domain_count = sum(len(h[2]) for h in domain_hits)
        domain_paths = len(domain_hits)

        if self.strategy is GuardrailStrategy.STRICT:
            if not cleaned or domain_count == 0:
                return False, 0.0
            if harmful_hits:
                return False, 0.0
            return True, self._confidence(domain_count, domain_paths)

        if self.strategy is GuardrailStrategy.MODERATE:
            if not cleaned:
                return False, 0.0
            if harmful_hits:
                return False, 0.0
            if domain_count == 0 and not general_hits:
                return False, 0.0
            total = domain_count + len(general_hits)
            return True, self._confidence(total, domain_paths)

        # PERMISSIVE
        if harmful_hits:
            return False, 0.0
        if not cleaned:
            return True, 0.0
        total = domain_count + len(general_hits)
        return True, self._confidence(total or 1, domain_paths)

    @staticmethod
    def _confidence(hit_count: int, path_count: int) -> float:
        """Smooth confidence in [0, 1]; saturates at ~3 keyword hits."""
        base = min(1.0, hit_count / 3.0)
        breadth = min(0.2, path_count * 0.05)
        return round(min(1.0, base + breadth), 4)

    def _audit_block(
        self,
        cleaned: str,
        confidence: float,
        matched_paths: list[str],
        harmful_hits: list[str],
    ) -> None:
        if self.audit_logger is None:
            return
        preview = cleaned[:80]
        self.audit_logger.log(
            event_type="domain_blocked",
            action="start_chat",
            status="blocked",
            details={
                "strategy": self.strategy.value,
                "confidence": confidence,
                "matched_paths": matched_paths,
                "harmful_hits": harmful_hits,
                "preview": preview,
            },
            actor="domain_guardrail",
            risk_level="medium",
        )


__all__ = [
    "CategoryNode",
    "DEFAULT_DOMAIN_TREE",
    "DomainBlockedError",
    "DomainGuardrail",
    "DomainKeywordTree",
    "GENERAL_BUSINESS_TERMS",
    "GuardrailResult",
    "GuardrailStrategy",
    "HARMFUL_PATTERNS",
    "SubcategoryNode",
    "preprocess",
]
