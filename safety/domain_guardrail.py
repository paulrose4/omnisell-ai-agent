"""Domain Guardrail for OmniSell AI.

This module gates user input *before* it reaches the multi-agent runtime.
It classifies a request against a bilingual (Chinese + English) keyword tree
covering the core e-commerce operation domains and decides whether the request
is within the platform's processable scope.

Three strategies are supported:
  * strict     - only requests that hit e-commerce domain keywords pass.
  * moderate   - e-commerce keywords *or* general business vocabulary pass.
  * permissive - everything passes except clearly harmful content
                 (jailbreak prompts, prompt-injection markers, ad spam).

Zero external NLP dependencies: matching is done via normalized substring
search so it works for both segmented English and unsegmented Chinese text.
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
    """Build the default bilingual e-commerce keyword tree."""
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
                },
            ),
            "customer": CategoryNode(
                name="客户服务 / Customer Service",
                keywords=["客服", "客户", "售后", "customer", "service"],
                subcategories={
                    "complaint": SubcategoryNode(
                        name="投诉 / Complaint",
                        keywords=["投诉", "complaint", "complain"],
                    ),
                    "faq": SubcategoryNode(
                        name="常见问题 / FAQ",
                        keywords=["faq", "常见问题", "疑问"],
                    ),
                    "warranty": SubcategoryNode(
                        name="保修 / Warranty",
                        keywords=["保修", "质保", "warranty", "guarantee"],
                    ),
                },
            ),
            "compliance": CategoryNode(
                name="合规与风险 / Compliance and Risk",
                keywords=["风控", "合规", "异常", "compliance", "risk"],
                subcategories={
                    "fraud": SubcategoryNode(
                        name="欺诈检测 / Fraud",
                        keywords=["欺诈", "fraud", "scam"],
                    ),
                    "abnormal": SubcategoryNode(
                        name="异常检测 / Abnormal",
                        keywords=["异常", "abnormal", "anomaly"],
                    ),
                    "sensitive": SubcategoryNode(
                        name="敏感词 / Sensitive",
                        keywords=["敏感词", "违禁", "sensitive", "banned"],
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
        }
    )


DEFAULT_DOMAIN_TREE = _build_default_tree()

# General business vocabulary used by the moderate strategy.
GENERAL_BUSINESS_TERMS: list[str] = [
    "business",
    "report",
    "analysis",
    "task",
    "data",
    "dashboard",
    "kpi",
    "sales",
    "revenue",
    "forecast",
    "业务",
    "报表",
    "分析",
    "任务",
    "数据",
    "看板",
    "销量",
    "营收",
    "预测",
    "运营",
]

# Clearly harmful / out-of-scope patterns always blocked by permissive.
HARMFUL_PATTERNS: list[str] = [
    "ignore previous",
    "ignore above",
    "ignore all previous",
    "disregard previous",
    "forget your instructions",
    "system prompt",
    "reveal your instructions",
    "show me your prompt",
    "jailbreak",
    "dan mode",
    "developer mode",
    "you are now dan",
    "act as dan",
    "vx:",
    "wechat:",
    "加微信",
    "加我微信",
    "代理赚钱",
    "兼职刷单",
    "刷单",
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
    normalized = normalized.translate(str.maketrans({chr(i): chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)}))
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
    """Bilingual domain guardrail with three strictness levels."""

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
