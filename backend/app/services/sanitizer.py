import re
from enum import Enum
from pydantic import BaseModel


class SanitizationStatus(str, Enum):
    PASSED = "passed"
    PII_DETECTED = "pii_detected"
    INJECTION_DETECTED = "injection_detected"
    OFF_TOPIC = "off_topic"


class SanitizationResult(BaseModel):
    status: SanitizationStatus
    query: str
    reason: str = ""
    redacted_query: str = ""


class InputSanitizer:
    """
    Standalone sanitization layer for all user queries.

    Checks:
    1. PII detection (SSN, phone, email, credit card patterns)
    2. Prompt-injection detection (common attack patterns)
    3. Scope check (is this a legal-research question?)

    Reusable across research, drafting, and document audit entry points.
    """

    def __init__(self):
        self.pii_patterns = {
            "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
            "phone": r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b",
            "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "credit_card": r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
            "ssn_alt": r"\b\d{9}\b",
        }

        self.injection_patterns = [
            r"(?i)(ignore|forget|disregard).*previous",
            r"(?i)(system|assistant).*prompt",
            r"(?i)act as.*admin",
            r"(?i)(execute|eval|run).*code",
            r"(?i)jailbreak",
            r"(?i)developer.*mode",
            r"(?i)hidden.*instruction",
            r"(?i)\[.*\].*\{.*\}",
            r"(?i)<<.*>>",
        ]

        self.off_topic_keywords = {
            "recipe",
            "cooking",
            "sports",
            "celebrity",
            "movie",
            "music",
            "travel",
            "vacation",
            "gaming",
            "pokemon",
            "minecraft",
            "fortnite",
            "taylor swift",
            "beyonce",
        }

        self.legal_keywords = {
            "case",
            "law",
            "statute",
            "regulation",
            "legal",
            "court",
            "ruling",
            "precedent",
            "constitutional",
            "contract",
            "liability",
            "tort",
            "negligence",
            "fraud",
            "breach",
            "damages",
            "defendant",
            "plaintiff",
            "attorney",
            "counsel",
            "complaint",
            "motion",
            "discovery",
            "deposition",
            "appeal",
            "circuit",
            "supreme",
            "federal",
            "state",
            "criminal",
            "civil",
            "rights",
            "amendment",
            "constitution",
        }

    def sanitize(self, query: str) -> SanitizationResult:
        """
        Sanitize and validate a user query.

        Returns SanitizationResult with status and any redactions/reasons.
        """
        if not query or not query.strip():
            return SanitizationResult(
                status=SanitizationStatus.OFF_TOPIC,
                query=query,
                reason="Query is empty.",
            )

        query_lower = query.lower()

        pii_found = self._detect_pii(query)
        if pii_found:
            redacted = self._redact_pii(query)
            return SanitizationResult(
                status=SanitizationStatus.PII_DETECTED,
                query=query,
                reason=f"Query contains personal information ({pii_found}). "
                f"Please remove sensitive data before proceeding.",
                redacted_query=redacted,
            )

        injection_found = self._detect_injection(query)
        if injection_found:
            return SanitizationResult(
                status=SanitizationStatus.INJECTION_DETECTED,
                query=query,
                reason="Query appears to contain a prompt-injection attempt. "
                "Please ask a legitimate legal question.",
            )

        is_legal = self._is_legal_query(query_lower)
        if not is_legal:
            return SanitizationResult(
                status=SanitizationStatus.OFF_TOPIC,
                query=query,
                reason="This question does not appear to be about legal research. "
                "Please ask about case law, statutes, regulations, or legal principles.",
            )

        return SanitizationResult(
            status=SanitizationStatus.PASSED,
            query=query,
        )

    def _detect_pii(self, query: str) -> str | None:
        """Detect PII patterns; return the first match type or None."""
        for pii_type, pattern in self.pii_patterns.items():
            if re.search(pattern, query):
                return pii_type
        return None

    def _redact_pii(self, query: str) -> str:
        """Replace detected PII with placeholder tokens."""
        redacted = query
        for pii_type, pattern in self.pii_patterns.items():
            redacted = re.sub(pattern, f"[{pii_type.upper()}]", redacted)
        return redacted

    def _detect_injection(self, query: str) -> bool:
        """Detect common prompt-injection patterns."""
        for pattern in self.injection_patterns:
            if re.search(pattern, query):
                return True
        return False

    def _is_legal_query(self, query_lower: str) -> bool:
        """
        Check if query is legal-research-related.

        Returns True if:
        - Query contains one or more legal keywords, OR
        - Query is specific enough (contains numbers/citations that look legal)

        Returns False if:
        - Query contains off-topic keywords
        - Query is too generic and has no legal signals
        """
        if any(keyword in query_lower for keyword in self.off_topic_keywords):
            return False

        if any(keyword in query_lower for keyword in self.legal_keywords):
            return True

        if re.search(r"\d+\s+(?:[Uu]\.?[Ss]\.|[Ff]\.\d?[Dd]|[Ss]\.\s*[Cc][Tt]\.)", query_lower):
            return True

        if re.search(r"(?:section|amendment|statute|rule|code|§)\s+\d+", query_lower):
            return True

        if re.search(r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth)\s+circuit", query_lower):
            return True

        if len(query_lower.split()) < 3:
            return False

        return False


sanitizer = InputSanitizer()


def sanitize_query(query: str) -> SanitizationResult:
    """
    Public entry point for query sanitization.

    Called by all entry points (research, drafting, document audit).
    """
    return sanitizer.sanitize(query)
