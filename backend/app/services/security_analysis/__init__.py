"""静态安全结构证据模块的公开入口。"""

from .contracts import SecurityEvidencePack
from .flow_analysis import DataFlowAnalyzer, ProgramGraphSecurityFlowAnalyzer
from .frontends import JavaSecurityFrontend, LanguageFrontend, PythonSecurityFrontend
from .knowledge import (
    NullSecurityKnowledgeProvider,
    SecurityKnowledgeDocument,
    SecurityKnowledgeProvider,
    SecurityKnowledgeRequest,
)
from .evidence_query import SecurityEvidenceCatalog
from .evidence_selection import SecurityEvidenceSelector
from .registry import FrontendRegistry, RulePackRegistry, SemanticsRegistry
from .rules import RulePack
from .scanner import SecurityScanner
from .semantics import JavaLanguageSemantics, LanguageSemantics, PythonLanguageSemantics
from .service import SecurityAnalysisService

__all__ = [
    "DataFlowAnalyzer",
    "FrontendRegistry",
    "JavaLanguageSemantics",
    "JavaSecurityFrontend",
    "LanguageFrontend",
    "LanguageSemantics",
    "NullSecurityKnowledgeProvider",
    "ProgramGraphSecurityFlowAnalyzer",
    "PythonLanguageSemantics",
    "PythonSecurityFrontend",
    "RulePack",
    "RulePackRegistry",
    "SecurityAnalysisService",
    "SecurityEvidencePack",
    "SecurityEvidenceCatalog",
    "SecurityEvidenceSelector",
    "SecurityKnowledgeDocument",
    "SecurityKnowledgeProvider",
    "SecurityKnowledgeRequest",
    "SecurityScanner",
    "SemanticsRegistry",
]
