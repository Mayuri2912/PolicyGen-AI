"""Rule-based document analysis for mock mode.

This is NOT artificial intelligence.  It is a deterministic keyword and
pattern analyzer that lets the complete workflow run without an API key.  It
reads the real extracted content (so its findings quote the uploaded files),
but it has no understanding of meaning, and everything it produces is labelled
as simulated throughout the product.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime

from app.schemas.analysis import REQUIREMENT_CATEGORIES
from app.services.ai_analysis.facts import extract_facts, normalise_value

# --------------------------------------------------------------- vocabulary
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "vendor_management": ("vendor", "third-party", "third party", "supplier", "outsourc", "contractor", "subprocessor", "due diligence", "service provider"),
    "cloud_security": ("cloud", "aws", "azure", "gcp", "bucket", "iaas", "saas", "paas", "tenant", "kubernetes", "container", "workload"),
    "access_control": ("access", "authenticat", "authoriz", "password", "mfa", "multi-factor", "privileg", "account", "login", "sign-in", "role-based", "joiner", "leaver"),
    "data_privacy": ("personal data", "privacy", "pii", "data subject", "consent", "personal information"),
    "incident_management": ("incident", "breach", "escalat", "forensic"),
    "business_continuity": ("backup", "restor", "recovery", "continuity", "disaster", "rto", "rpo", "failover"),
    "risk_management": ("risk assessment", "risk register", "risk treatment", "risk appetite", "risk owner", "residual risk", "risk"),
    "audit_compliance": ("audit", "complian", "regulat", "evidence", "certif"),
    "employee_responsibilities": ("employee", "staff", "training", "awareness", "personnel", "workforce", "users must", "all users"),
    "technical_operational": ("encrypt", "firewall", "patch", "vulnerab", "antivirus", "malware", "logging", "log ", "monitor", "siem", "network", "configuration", "endpoint", "tls"),
    "information_security": ("information security", "security policy", "confidential", "classif", "asset", "security"),
}

RISK_CATALOGUE: list[dict] = [
    {
        "key": "weak_authentication", "title": "Weak authentication", "category": "Weak authentication",
        "topic": r"password|authenticat|login|sign-in|credential",
        "control": r"multi-factor|\bmfa\b|two-factor|\b2fa\b",
        "weakness": r"(no|without|lack of|missing) (mfa|multi-factor)|(mfa|multi-factor)[^.]{0,40}(not|disabled|missing|no\b)|weak password|default password|single-factor",
        "theme": r"\bmfa\b|multi-factor|two-factor|password|authenticat|passphrase|lockout",
        "likelihood": 4, "impact": 4,
        "mitigation": "Require multi-factor authentication for remote, privileged and cloud access and enforce a strong password standard.",
        "control_name": "Multi-factor authentication", "type": "technical", "function": "preventive",
        "objective": "Prevent account compromise from stolen or guessed passwords.",
    },
    {
        "key": "access_review", "title": "Inadequate access reviews", "category": "Inadequate access reviews",
        "topic": r"access|account|privileg",
        "control": r"access reviews?|recertif|user access review",
        "weakness": r"access reviews?[^.]{0,60}(overdue|not |never|missed|incomplete)|no (periodic |regular |formal )?access review|not been reviewed|review overdue",
        "theme": r"access reviews?|recertif|review[^.]{0,25}access|access[^.]{0,30}review",
        "likelihood": 3, "impact": 3,
        "mitigation": "Perform and evidence periodic access reviews, with more frequent reviews for privileged accounts.",
        "control_name": "Periodic access review", "type": "administrative", "function": "detective",
        "objective": "Detect and remove access that is no longer appropriate.",
    },
    {
        "key": "unauthorized_access", "title": "Unauthorized access", "category": "Unauthorized access",
        "topic": r"access|account|privileg|permission",
        "control": r"least privilege|role-based|\brbac\b|access (request|approval)|approved by",
        "weakness": r"unauthori[sz]ed|excessive (access|privilege)|shared (account|credential|password)|generic account|orphan|dormant|departed|still active",
        "theme": r"least privilege|role-based|\brbac\b|access (request|approval|right|control)|provision|leaver|terminat|privileg|shared account|unauthori[sz]ed",
        "likelihood": 3, "impact": 4,
        "mitigation": "Enforce least-privilege, role-based access with documented approval, and remove access promptly when people leave or change role.",
        "control_name": "Role-based access control with approval", "type": "technical", "function": "preventive",
        "objective": "Ensure only authorized people hold the access they need.",
    },
    {
        "key": "data_leakage", "title": "Data leakage", "category": "Data leakage",
        "topic": r"confidential|personal data|sensitive|customer (data|information)|data",
        "control": r"encrypt|\bdlp\b|data loss prevention|classif",
        "weakness": r"unencrypted|not encrypted|leak|exfiltrat|publicly accessible|public (bucket|share|link)|exposed",
        "theme": r"encrypt|\bdlp\b|data loss|classif|leak|confidential|sensitive data|data (at rest|in transit)",
        "likelihood": 3, "impact": 5,
        "mitigation": "Classify data, encrypt sensitive data at rest and in transit, and restrict external sharing.",
        "control_name": "Data encryption and loss prevention", "type": "technical", "function": "preventive",
        "objective": "Keep sensitive data from being disclosed to unauthorized parties.",
    },
    {
        "key": "third_party", "title": "Third-party security risk", "category": "Third-party security risks",
        "topic": r"vendor|supplier|third[- ]party|outsourc|service provider",
        "control": r"due diligence|vendor (risk )?assessment|security assessment|right to audit",
        "weakness": r"(vendor|supplier|third[- ]party)[^.]{0,60}(not (been )?assessed|no assessment|overdue|expired|without (a )?(contract|dpa|nda|assessment))|no (dpa|right to audit|nda)|assessment (is )?(overdue|expired|not (performed|completed))",
        "theme": r"vendor|supplier|third[- ]party|due diligence|outsourc|subprocessor",
        "likelihood": 3, "impact": 4,
        "mitigation": "Assess vendors before onboarding and periodically, and include security, breach-notification and audit clauses in contracts.",
        "control_name": "Vendor security due diligence", "type": "administrative", "function": "preventive",
        "objective": "Understand and control the risk a vendor introduces before and during the engagement.",
    },
    {
        "key": "cloud_misconfiguration", "title": "Cloud misconfiguration", "category": "Cloud misconfiguration",
        "topic": r"cloud|\baws\b|azure|\bgcp\b|bucket|tenant|saas",
        "control": r"baseline|\bcspm\b|configuration (scan|review|standard|management)|infrastructure as code|hardening",
        "weakness": r"misconfigur|publicly accessible|public bucket|open to the internet|0\.0\.0\.0/0|unrestricted (inbound|access)|root account[^.]{0,40}(used|without|no )",
        "theme": r"cloud|bucket|tenant|configuration baseline|misconfig|security group|\bcspm\b",
        "likelihood": 4, "impact": 4,
        "mitigation": "Define secure configuration baselines for cloud services and continuously check for drift and public exposure.",
        "control_name": "Cloud configuration baseline and continuous checks", "type": "technical", "function": "detective",
        "objective": "Prevent and detect insecure cloud settings.",
    },
    {
        "key": "incident_response", "title": "Inadequate incident response", "category": "Inadequate incident response",
        "topic": r"incident|breach|security event",
        "control": r"incident response (plan|procedure|team)|escalation (path|procedure|matrix)|reported within",
        "weakness": r"no (formal |documented )?incident (response|management)|incident[^.]{0,50}(not (been )?(tested|documented|defined)|ad hoc|informal)|not reported",
        "theme": r"incident|breach|escalat",
        "likelihood": 3, "impact": 4,
        "mitigation": "Document, communicate and test an incident response procedure with defined roles, severity levels and reporting timelines.",
        "control_name": "Incident response procedure and testing", "type": "administrative", "function": "corrective",
        "objective": "Contain and recover from incidents quickly and consistently.",
    },
    {
        "key": "backup", "title": "Incomplete backup procedures", "category": "Incomplete backup procedures",
        "topic": r"backup|restor|recover|continuity",
        "control": r"restore tests?|restoration tests?|tested (quarterly|annually|regularly|monthly)|offsite|off-site|immutable",
        "weakness": r"backups?[^.]{0,60}(not (been )?(tested|encrypted|performed|monitored)|fail|untested|incomplete)|no (offsite|off-site) backup|restor[^.]{0,40}(never|not) (been )?tested",
        "theme": r"backup|restor|recovery|\brto\b|\brpo\b|continuity",
        "likelihood": 3, "impact": 4,
        "mitigation": "Define backup scope and frequency, keep protected offsite copies and perform regular restore tests.",
        "control_name": "Backups with periodic restore testing", "type": "technical", "function": "corrective",
        "objective": "Ensure data can be recovered after loss or corruption.",
    },
    {
        "key": "security_awareness", "title": "Weak security awareness", "category": "Weak security awareness",
        "topic": r"employee|staff|personnel|training|users",
        "control": r"awareness training|security training|phishing (simulation|exercise)",
        "weakness": r"training[^.]{0,50}(not (been )?(completed|provided|mandatory)|overdue|low completion|incomplete)|no (security )?awareness|phishing[^.]{0,30}(click|fail)",
        "theme": r"training|awareness|phishing",
        "likelihood": 3, "impact": 3,
        "mitigation": "Provide mandatory security awareness training at onboarding and at least annually, supported by phishing exercises.",
        "control_name": "Security awareness training", "type": "administrative", "function": "preventive",
        "objective": "Reduce incidents caused by human error.",
    },
    {
        "key": "monitoring", "title": "Inadequate monitoring", "category": "Inadequate monitoring",
        "topic": r"\blogs?\b|logging|monitor|server|network|cloud",
        "control": r"\bsiem\b|logs? (are|is) (reviewed|monitored|retained|collected)|centrali[sz]ed logging|alerting|security monitoring|audit logs?",
        "weakness": r"logs?[^.]{0,50}(not (been )?(reviewed|enabled|retained|monitored|collected)|disabled)|no (centrali[sz]ed )?(logging|monitoring|alerting)|not monitored",
        "theme": r"\blogs?\b|logging|monitor|\bsiem\b|alert|audit trail",
        "likelihood": 3, "impact": 3,
        "mitigation": "Enable and centrally retain security logs and review alerts for critical systems.",
        "control_name": "Security logging and monitoring", "type": "technical", "function": "detective",
        "objective": "Detect security events in time to respond.",
    },
    {
        "key": "compliance", "title": "Compliance gap", "category": "Compliance gaps",
        "topic": r"complian|audit|regulat|standard",
        "control": r"internal audit|compliance review|audited (annually|regularly)|compliance (monitoring|check)",
        "weakness": r"non-?complian|audit finding|non-?conformit|not compliant|polic[^.]{0,40}(outdated|not (been )?(reviewed|approved))|expired",
        "theme": r"audit|complian|regulat",
        "likelihood": 2, "impact": 3,
        "mitigation": "Schedule periodic compliance reviews and track findings to closure.",
        "control_name": "Periodic compliance review", "type": "administrative", "function": "detective",
        "objective": "Identify and correct departures from requirements.",
    },
    {
        "key": "ownership", "title": "Missing or unclear ownership", "category": "Missing or unclear ownership",
        "topic": r"responsib|owner|accountab",
        "control": r"(is|are) responsible for|owners? (is|are) (assigned|defined)|accountable (for|to)|\braci\b|risk owner|system owner|data owner",
        "weakness": r"no (defined |assigned |clear |named )?owner|owner(ship)? (is )?(not|unclear|undefined|unassigned)|unclear (responsib|ownership)|responsibilit[^.]{0,30}(not|un)(defined|clear|assigned)",
        "theme": r"owner|accountab|responsib",
        "likelihood": 3, "impact": 3,
        "mitigation": "Assign and document a named owner for each policy, system, dataset and risk.",
        "control_name": "Documented ownership and accountability", "type": "administrative", "function": "preventive",
        "objective": "Make sure someone is accountable for every control and asset.",
    },
]
CATALOGUE_BY_KEY = {entry["key"]: entry for entry in RISK_CATALOGUE}
for _entry in RISK_CATALOGUE:
    for _field in ("topic", "control", "weakness", "theme"):
        _entry[f"{_field}_re"] = re.compile(_entry[_field], re.IGNORECASE)

REQUIREMENT_CUE = re.compile(
    r"\b(must|shall|should|required to|is required|are required|needs? to|ha(s|ve) to|may not|prohibited|not permitted|mandatory|"
    r"will be (reviewed|conducted|performed|maintained|enforced|retained|reported|tested))\b",
    re.IGNORECASE,
)
WEAKNESS_CUE = re.compile(
    r"\b(no formal|not (yet |been |currently )?(implemented|enforced|defined|documented|performed|conducted|tested|reviewed|encrypted|in place|enabled|monitored|restricted|completed|assessed)|"
    r"lack(s|ing)? of|absence of|outdated|unsupported|unpatched|overdue|never (been )?(tested|reviewed)|"
    r"shared (account|password|credential)s?|without (encryption|mfa|approval|review|logging|monitoring|a contract)|"
    r"audit finding|non-?conformit|deficien|vulnerab|publicly accessible|expired|inadequate|insufficient|ad hoc|informal|"
    r"misconfigur|does not|do not have|is missing|are missing)\b",
    re.IGNORECASE,
)
CONTROL_CUE = re.compile(
    r"\b(is|are|has been|have been)\s+(currently\s+)?(enabled|enforced|implemented|encrypted|configured|deployed|performed|conducted|reviewed|"
    r"monitored|maintained|restricted|logged|tested|in place|established|backed up|required for all|collected|retained)\b|"
    r"\b(we|the organi[sz]ation|the company|it team|security team|the it department)\s+(currently\s+)?(uses?|maintains?|performs?|conducts?|enforces?|operates?|reviews?)\b|"
    r"\bcurrently (uses?|enforces?|maintains?)\b",
    re.IGNORECASE,
)
PARTIAL_CUE = re.compile(r"\b(partial(ly)?|planned|will be|scheduled|roadmap|in progress|being rolled out|pilot|some |not all)\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(not|no|never|without|lack)\b", re.IGNORECASE)

CONTROL_NAMES: tuple[tuple[str, str, str, str], ...] = (
    # pattern, name, type, function
    (r"multi-factor|\bmfa\b|two-factor", "Multi-factor authentication", "technical", "preventive"),
    (r"access reviews?|recertif", "Periodic access review", "administrative", "detective"),
    (r"least privilege|role-based|\brbac\b", "Role-based access control", "technical", "preventive"),
    (r"password", "Password standard", "technical", "preventive"),
    (r"encrypt|\btls\b", "Encryption of data", "technical", "preventive"),
    (r"restore test|restoration test", "Backup restore testing", "technical", "corrective"),
    (r"backup", "Data backup", "technical", "corrective"),
    (r"\bsiem\b|logging|\blogs?\b|monitor|alert", "Security logging and monitoring", "technical", "detective"),
    (r"training|awareness|phishing", "Security awareness training", "administrative", "preventive"),
    (r"patch|vulnerability scan", "Patch and vulnerability management", "technical", "corrective"),
    (r"firewall|network segment|security group", "Network access restriction", "technical", "preventive"),
    (r"incident response|incident management", "Incident response procedure", "administrative", "corrective"),
    (r"due diligence|vendor (risk )?assessment|security questionnaire", "Vendor due diligence", "administrative", "preventive"),
    (r"contract|agreement|\bnda\b|\bdpa\b", "Contractual security obligations", "administrative", "preventive"),
    (r"change (management|request|approval)", "Change management", "administrative", "preventive"),
    (r"antivirus|anti-malware|\bedr\b|endpoint protection", "Endpoint protection", "technical", "preventive"),
    (r"classif", "Data classification", "administrative", "preventive"),
    (r"audit", "Internal audit", "administrative", "detective"),
    (r"badge|cctv|visitor|physical access|locked", "Physical access control", "physical", "preventive"),
    (r"risk (assessment|register)", "Risk assessment process", "administrative", "detective"),
)
_CONTROL_NAME_RES = tuple((re.compile(p, re.IGNORECASE), n, t, f) for p, n, t, f in CONTROL_NAMES)

ROLE_RE = re.compile(
    r"\b(Chief Information Security Officer|CISO|Chief Technology Officer|CTO|CIO|IT Security Team|Information Security (?:Team|Manager|Officer)|"
    r"Security (?:Team|Operations(?: Team)?|Officer)|Data Protection Officer|DPO|Human Resources|HR(?: Department| Team)?|System Owners?|Data Owners?|"
    r"Asset Owners?|Risk Owners?|Department Heads?|Line Managers?|IT Managers?|IT (?:Team|Department|Operations)|Internal Audit|"
    r"Compliance (?:Team|Officer|Manager)|Procurement(?: Team)?|Legal(?: Team| Department)?|Executive Management|Senior Management|"
    r"Risk Committee|Cloud (?:Platform )?Team|Cloud (?:Architect|Security Engineer)|DevOps(?: Team)?|Service Desk|Help ?desk|Vendor (?:Manager|Owner)|"
    r"Incident Response Team|All (?:employees|staff|users)|Employees|Vendors|Suppliers|Contractors)\b"
)
RESPONSIBILITY_CUE = re.compile(
    r"\b(responsible for|accountable for|must|shall|approves?|reviews?|ensures?|owns?|maintains?|is the owner|reports? to|oversees?)\b",
    re.IGNORECASE,
)
SYSTEM_RE = re.compile(
    r"\b(AWS|Amazon Web Services|Microsoft Azure|Azure|Google Cloud|GCP|Microsoft 365|Office 365|Google Workspace|Active Directory|Entra ID|"
    r"Okta|Salesforce|SAP|GitHub|GitLab|Jira|Slack|Zoom|VPN|SIEM|S3|EC2|Kubernetes|ServiceNow|Workday|Snowflake|Oracle|SQL Server|"
    r"ERP|CRM|HRIS|Splunk|CloudTrail)\b"
)
ORG_RE = re.compile(
    r"\b([A-Z][A-Za-z&]+(?: [A-Z][A-Za-z&]+){0,3} (?:Pvt\.? Ltd\.?|Private Limited|Ltd\.?|Limited|Inc\.?|LLC|LLP|Corporation|Corp\.?|GmbH|PLC))(?=[\s,.;:)]|$)"
)

NEGATIVE_VALUES = {
    "no", "n", "false", "disabled", "none", "not enabled", "missing", "not signed", "not completed", "not performed", "not assessed",
    "overdue", "expired", "pending", "never", "not reviewed", "fail", "failed", "non-compliant", "not compliant", "not in place",
    "not tested", "not encrypted", "not started", "not available", "not defined",
}
_CLOSED_VALUES = {"closed", "resolved", "remediated", "fixed", "done", "completed"}
POSITIVE_VALUES = {
    "yes", "y", "true", "enabled", "signed", "completed", "performed", "compliant", "pass", "passed", "in place", "tested",
    "encrypted", "implemented", "valid", "current", "closed",
}
_DEPARTED = {"terminated", "left", "resigned", "departed", "offboarded", "leaver", "ex-employee"}
_ACTIVE = {"active", "enabled", "yes", "true"}
_STOPWORDS = {
    "that", "this", "with", "from", "have", "must", "shall", "should", "will", "been", "their", "which", "where", "when", "than",
    "them", "they", "each", "such", "into", "also", "other", "only", "every", "least", "more", "within", "before", "after", "these",
    "those", "used", "using", "being", "does", "about", "organization", "organisation", "company", "required", "ensure",
}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_LEADING_BULLET = re.compile(r"^[^\w\[(\"'<]+")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%b %Y", "%B %Y", "%Y/%m/%d", "%m/%d/%Y")


# ------------------------------------------------------------------ helpers
def sentences(text: str):
    for line in text.split("\n"):
        line = _LEADING_BULLET.sub("", line.strip())
        if not line or line.startswith(("Table:", "Worksheet ", "CSV data")):
            continue
        for sentence in _SENTENCE_SPLIT.split(line):
            sentence = sentence.strip()
            if 25 <= len(sentence) <= 500:
                yield sentence


def classify_category(text: str) -> str:
    lowered = text.lower()
    scores = {cat: sum(lowered.count(k) for k in keys) for cat, keys in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "organizational"


def catalogue_match(text: str, field: str = "weakness") -> dict | None:
    for entry in RISK_CATALOGUE:
        if entry[f"{field}_re"].search(text):
            return entry
    return None


def themes(text: str) -> set[str]:
    return {entry["key"] for entry in RISK_CATALOGUE if entry["theme_re"].search(text)}


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z\-]{3,}", text.lower())
    return {re.sub(r"(ing|ed|es|s|ion)$", "", w) for w in words if w not in _STOPWORDS}


def name_control(text: str) -> tuple[str, str, str]:
    for pattern, name, control_type, function in _CONTROL_NAME_RES:
        if pattern.search(text):
            return name, control_type, function
    return short_title(text, 60) or "Documented control", "administrative", "preventive"


def short_title(text: str, limit: int = 90) -> str:
    """The text itself as a title, cut at a word boundary when it is long."""
    text = re.sub(r"\s+", " ", text).strip().rstrip(".;:,")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def scale_value(value: str) -> int:
    value = (value or "").strip().lower()
    match = re.match(r"^(\d+)", value)
    if match:
        number = int(match.group(1))
        return number if 1 <= number <= 5 else 0
    words = (
        ("very high", 5), ("almost certain", 5), ("catastrophic", 5), ("critical", 5), ("severe", 5), ("extreme", 5),
        ("very low", 1), ("negligible", 1), ("insignificant", 1), ("rare", 1),
        ("high", 4), ("likely", 4), ("major", 4),
        ("medium", 3), ("moderate", 3), ("possible", 3),
        ("low", 2), ("unlikely", 2), ("minor", 2),
    )
    for word, number in words:
        if value.startswith(word):
            return number
    return 0


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value or len(value) > 20:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value[:19].split("T")[0] if "T" in value else value, fmt).date()
        except ValueError:
            continue
    return None


def _column(headers: list[str], *patterns: str, exclude: str = "") -> int | None:
    for pattern in patterns:
        for index, header in enumerate(headers):
            lowered = header.lower()
            if re.search(pattern, lowered) and not (exclude and re.search(exclude, lowered)):
                return index
    return None


def _row_quote(headers: list[str], row: list[str], max_len: int = 220) -> str:
    """A verbatim prefix of the row as it appears in the extracted text."""
    cells = [f"{h}: {v}" for h, v in zip(headers, row) if v]
    quote = ""
    for cell in cells:
        candidate = f"{quote}; {cell}" if quote else cell
        if len(candidate) > max_len and quote:
            break
        quote = candidate
    return quote[: max_len + 80]


def _status_from(text: str) -> str:
    return "partially_implemented" if PARTIAL_CUE.search(text) else "implemented"


class _Collector:
    """Accumulates findings for one chunk and assigns local ids."""

    def __init__(self) -> None:
        self.requirements: list[dict] = []
        self.risks: list[dict] = []
        self.controls: list[dict] = []
        self.responsibilities: list[dict] = []
        self.facts: list[dict] = []
        self._seen: set[tuple[str, str]] = set()

    def _new(self, kind: str, key: str) -> bool:
        marker = (kind, re.sub(r"\W+", " ", key.lower()).strip()[:160])
        if marker in self._seen:
            return False
        self._seen.add(marker)
        return True

    def requirement(self, statement: str, location: str, evidence: str) -> None:
        if self._new("req", statement):
            self.requirements.append(
                {
                    "local_id": f"R{len(self.requirements) + 1}",
                    "statement": statement.rstrip(" ;"),
                    "category": classify_category(statement),
                    "basis": "documented",
                    "source_location": location,
                    "evidence": evidence[:300],
                    "confidence": "medium",
                }
            )

    def risk(self, **fields) -> str | None:
        if not self._new("risk", fields["title"] + fields.get("description", "")[:60]):
            return None
        local_id = f"K{len(self.risks) + 1}"
        self.risks.append(
            {
                "local_id": local_id,
                "existing_controls": [],
                "recommended_mitigation": "",
                "residual_likelihood": 0,
                "residual_impact": 0,
                "source_location": "",
                "evidence": "",
                "confidence": "medium",
                "scoring_basis": "estimated",
                **fields,
            }
        )
        return local_id

    def control(self, **fields) -> str | None:
        if not self._new("ctl", fields["name"] + fields.get("basis", "") + fields.get("description", "")[:50]):
            return None
        local_id = f"C{len(self.controls) + 1}"
        self.controls.append(
            {
                "local_id": local_id,
                "objective": "",
                "description": "",
                "owner": "",
                "related_requirements": [],
                "related_risks": [],
                "recommended_action": "",
                "source_location": "",
                "evidence": "",
                "confidence": "medium",
                **fields,
            }
        )
        return local_id

    def responsibility(self, role: str, responsibility: str, location: str, evidence: str) -> None:
        if self._new("resp", role + responsibility):
            self.responsibilities.append(
                {
                    "role": role,
                    "responsibility": responsibility,
                    "basis": "documented",
                    "source_location": location,
                    "evidence": evidence[:300],
                }
            )


# ------------------------------------------------------------ text analysis
def _analyze_sentence(sentence: str, location: str, out: _Collector) -> None:
    role = ROLE_RE.search(sentence)
    assigns_role = bool(role and RESPONSIBILITY_CUE.search(sentence[role.end() : role.end() + 80]))
    if assigns_role:
        out.responsibility(role.group(1), sentence.rstrip("."), location, sentence)

    if REQUIREMENT_CUE.search(sentence):
        out.requirement(sentence, location, sentence)
        return

    weakness = catalogue_match(sentence, "weakness")
    # A sentence that assigns a duty ("X reports overdue assessments") is not
    # itself a weakness unless it matches a specific weakness pattern.
    if weakness or (WEAKNESS_CUE.search(sentence) and not assigns_role):
        entry = weakness or catalogue_match(sentence, "theme")
        out.risk(
            title=short_title(sentence),
            description=sentence,
            category=entry["category"] if entry else "Operational weakness",
            origin="documented",
            likelihood=entry["likelihood"] if entry else 3,
            impact=entry["impact"] if entry else 3,
            recommended_mitigation=entry["mitigation"] if entry else "Assess this weakness and define a treatment action with an owner and due date.",
            source_location=location,
            evidence=sentence[:300],
            confidence="medium",
            catalogue_key=entry["key"] if entry else "",
        )
        return

    if CONTROL_CUE.search(sentence) and not NEGATION.search(sentence):
        name, control_type, function = name_control(sentence)
        out.control(
            name=name,
            description=sentence,
            control_type=control_type,
            control_function=function,
            status=_status_from(sentence),
            basis="existing",
            source_location=location,
            evidence=sentence[:300],
        )


def _extract_facts(text: str, location: str, out: _Collector) -> None:
    for fact in extract_facts(text):
        out.facts.append({**fact, "source_location": location})


# ----------------------------------------------------------- table analysis
def _is_risk_register(headers: list[str]) -> bool:
    joined = " | ".join(h.lower() for h in headers)
    return "risk" in joined and bool(re.search(r"likelihood|probability|impact|severity|rating|score", joined))


def _findings_column(headers: list[str]) -> int | None:
    """Index of the text column of an audit / assessment findings table."""
    has_rating = _column(headers, r"severity|rating|priority|status") is not None
    index = _column(headers, r"^(finding|issue|observation|weakness|gap)s?$", r"finding|observation", exclude=r"\b(id|ref|no)\b")
    return index if has_rating else None


def _analyze_findings(unit: dict, text_i: int, out: _Collector) -> None:
    """Each open row of a findings table is a weakness the document records."""
    headers, rows, location = unit["headers"], unit["rows"], unit["location"]
    severity_i = _column(headers, r"severity|rating|priority")
    status_i = _column(headers, r"status")
    for offset, row in enumerate(rows):
        finding = row[text_i].strip()
        status = row[status_i].strip().lower() if status_i is not None else ""
        if not finding or status in _CLOSED_VALUES:
            continue
        entry = catalogue_match(finding, "weakness") or catalogue_match(f"{finding} {' '.join(row)}", "theme")
        severity = scale_value(row[severity_i]) if severity_i is not None else 0
        out.risk(
            title=short_title(finding),
            description=finding + (f" (recorded status: {row[status_i]})" if status else ""),
            category=entry["category"] if entry else "Assessment finding",
            origin="documented",
            likelihood=entry["likelihood"] if entry else 3,
            impact=severity or (entry["impact"] if entry else 3),
            recommended_mitigation=entry["mitigation"] if entry else "Define a remediation action for this finding with an owner and due date.",
            source_location=f"{location}, row {unit.get('first_row', 1) + offset}",
            evidence=_row_quote(headers, row),
            confidence="high",
            catalogue_key=entry["key"] if entry else "",
        )


def _analyze_risk_register(unit: dict, out: _Collector) -> None:
    headers, rows, location = unit["headers"], unit["rows"], unit["location"]
    title_i = _column(headers, r"risk (title|name|description|statement|event)", r"^risk$", r"\brisk\b", r"threat|issue|finding",
                      exclude=r"\b(id|ref|no|level|rating|score|owner|category|type|residual|status)\b")
    if title_i is None:
        return
    desc_i = _column(headers, r"description|detail|cause|consequence", exclude=r"control")
    like_i = _column(headers, r"likelihood|probability", exclude=r"residual")
    impact_i = _column(headers, r"impact|severity|consequence", exclude=r"residual")
    control_i = _column(headers, r"existing control|current control|controls? in place|^controls?$|existing (mitigation|measure)")
    mitigation_i = _column(headers, r"mitigation|treatment|action|recommend|remediation|response", exclude=r"existing|current")
    owner_i = _column(headers, r"owner|responsible|assigned")
    category_i = _column(headers, r"category|type|area|domain")
    res_like_i = _column(headers, r"residual[^|]*(likelihood|probability)")
    res_impact_i = _column(headers, r"residual[^|]*(impact|severity)")

    def cell(row, index):
        return row[index].strip() if index is not None and index < len(row) else ""

    for offset, row in enumerate(rows):
        title = cell(row, title_i)
        if not title:
            continue
        row_location = f"{location}, row {unit.get('first_row', 1) + offset}"
        quote = _row_quote(headers, row)
        description = cell(row, desc_i) if desc_i not in (None, title_i) else ""
        if len(title) > 110:
            description, title = description or title, title[:100].rsplit(" ", 1)[0] + "..."
        entry = catalogue_match(f"{title} {description}", "theme")
        likelihood, impact = scale_value(cell(row, like_i)), scale_value(cell(row, impact_i))
        documented = bool(likelihood and impact)
        existing = [c.strip(" -") for c in re.split(r"[;\n]|(?<=\.)\s+(?=[A-Z])", cell(row, control_i)) if len(c.strip()) > 3]
        existing = [c for c in existing if c.lower() not in NEGATIVE_VALUES and not c.lower().startswith(("none", "no control", "n/a"))]
        mitigation = cell(row, mitigation_i) or (entry["mitigation"] if entry else "")
        risk_id = out.risk(
            title=title,
            description=description or title,
            category=cell(row, category_i) or (entry["category"] if entry else "Uncategorized"),
            origin="documented",
            likelihood=likelihood or (entry["likelihood"] if entry else 3),
            impact=impact or (entry["impact"] if entry else 3),
            scoring_basis="documented" if documented else "estimated",
            existing_controls=existing,
            recommended_mitigation=mitigation,
            residual_likelihood=scale_value(cell(row, res_like_i)),
            residual_impact=scale_value(cell(row, res_impact_i)),
            source_location=row_location,
            evidence=quote,
            confidence="high" if documented else "medium",
            catalogue_key=entry["key"] if entry else "",
        )
        if risk_id is None:
            continue
        for control_text in existing:
            name, control_type, function = name_control(control_text)
            out.control(
                name=name, description=control_text, control_type=control_type, control_function=function,
                status=_status_from(control_text), basis="existing", related_risks=[risk_id],
                owner=cell(row, owner_i), source_location=row_location, evidence=quote,
            )
        if cell(row, mitigation_i):
            name, control_type, function = name_control(mitigation)
            out.control(
                name=name, description=mitigation, control_type=control_type, control_function=function,
                status="not_implemented", basis="recommended", related_risks=[risk_id],
                recommended_action=mitigation, owner=cell(row, owner_i), source_location=row_location, evidence=quote,
            )
        if cell(row, owner_i):
            out.responsibility(cell(row, owner_i), f"Owner of the risk '{title}' and its treatment", row_location, quote)


def _analyze_records(unit: dict, out: _Collector) -> None:
    headers, rows, location = unit["headers"], unit["rows"], unit["location"]
    if not rows:
        return
    total = len(rows)
    label_i = _column(headers, r"name|user|vendor|supplier|system|asset|application|service|account") or 0
    table_context = f"{unit.get('heading', '')} {' '.join(headers)}"

    def label(row) -> str:
        return row[label_i] if label_i < len(row) and row[label_i] else "a record"

    for index, header in enumerate(headers):
        if index == label_i:
            continue
        values = [(row[index].strip().lower() if index < len(row) else "") for row in rows]
        filled = [v for v in values if v]
        if not filled:
            continue
        negatives = [i for i, v in enumerate(values) if v in NEGATIVE_VALUES]
        positives = [i for i, v in enumerate(values) if v in POSITIVE_VALUES]
        flag_like = len(negatives) + len(positives) >= 0.6 * len(filled)
        entry = catalogue_match(f"{header}", "theme") or catalogue_match(table_context, "theme")

        if negatives and flag_like:
            first = rows[negatives[0]]
            examples = ", ".join(label(rows[i]) for i in negatives[:5])
            breakdown = Counter(rows[i][index].strip() for i in negatives)
            if len(breakdown) == 1:
                title = f"'{header}' is '{first[index]}' for {len(negatives)} of {total} records"
            else:
                detail = ", ".join(f"{value}: {count}" for value, count in breakdown.most_common())
                title = f"'{header}' is not satisfied for {len(negatives)} of {total} records ({detail})"
            out.risk(
                title=title,
                description=(
                    f"In {location}, {len(negatives)} of {total} records have '{header}' recorded as a negative value "
                    f"(for example: {examples})."
                ),
                category=entry["category"] if entry else "Control gap in records",
                origin="documented",
                likelihood=entry["likelihood"] if entry else 3,
                impact=entry["impact"] if entry else 3,
                recommended_mitigation=entry["mitigation"] if entry else f"Review the records where '{header}' is not satisfied and remediate them.",
                source_location=f"{location}, row {unit.get('first_row', 1) + negatives[0]}",
                evidence=_row_quote(headers, first),
                confidence="high",
                catalogue_key=entry["key"] if entry else "",
            )
        if positives and flag_like:
            first = rows[positives[0]]
            name = entry["control_name"] if entry and entry["theme_re"].search(header) else header
            out.control(
                name=name,
                description=f"'{header}' is recorded as '{first[index]}' for {len(positives)} of {total} records in {location}.",
                control_type=entry["type"] if entry else "administrative",
                control_function=entry["function"] if entry else "preventive",
                status="implemented" if not negatives else "partially_implemented",
                basis="existing",
                source_location=f"{location}, row {unit.get('first_row', 1) + positives[0]}",
                evidence=_row_quote(headers, first),
                confidence="high",
            )

        lowered = header.lower()
        is_expiry = bool(re.search(r"expir|renewal|valid (until|to)", lowered))
        if is_expiry or re.search(r"review|assess|audit|test|last", lowered):
            today, overdue = date.today(), []
            for i, row in enumerate(rows):
                parsed = parse_date(row[index] if index < len(row) else "")
                if parsed and ((is_expiry and parsed < today) or (not is_expiry and (today - parsed).days > 365)):
                    overdue.append(i)
            if overdue:
                first = rows[overdue[0]]
                wording = "has passed" if is_expiry else "is more than 12 months old"
                out.risk(
                    title=f"'{header}' {wording} for {len(overdue)} of {total} records",
                    description=(
                        f"In {location}, the date in '{header}' {wording} for {len(overdue)} records "
                        f"(for example: {', '.join(label(rows[i]) for i in overdue[:5])})."
                    ),
                    category=entry["category"] if entry else "Overdue activity",
                    origin="documented",
                    likelihood=3,
                    impact=entry["impact"] if entry else 3,
                    recommended_mitigation=f"Bring '{header}' up to date for the affected records and track the dates going forward.",
                    source_location=f"{location}, row {unit.get('first_row', 1) + overdue[0]}",
                    evidence=_row_quote(headers, first),
                    confidence="high",
                    catalogue_key=entry["key"] if entry else "",
                )

    employment_i = _column(headers, r"employ|staff status|hr status")
    account_i = _column(headers, r"account (status|state)|account enabled|account active")
    if employment_i is not None and account_i is not None:
        stale = [
            i for i, row in enumerate(rows)
            if row[employment_i].strip().lower() in _DEPARTED and row[account_i].strip().lower() in _ACTIVE
        ]
        if stale:
            entry = CATALOGUE_BY_KEY["unauthorized_access"]
            out.risk(
                title=f"Active accounts for departed users ({len(stale)} of {total} records)",
                description=(
                    f"In {location}, {len(stale)} accounts remain active although '{headers[employment_i]}' shows the person has left "
                    f"(for example: {', '.join(label(rows[i]) for i in stale[:5])})."
                ),
                category=entry["category"], origin="documented", likelihood=4, impact=4,
                recommended_mitigation="Disable the affected accounts and link account removal to the HR leaver process.",
                source_location=f"{location}, row {unit.get('first_row', 1) + stale[0]}",
                evidence=_row_quote(headers, rows[stale[0]]), confidence="high", catalogue_key=entry["key"],
            )

    owner_i = _column(headers, r"owner|responsible|assigned to")
    if owner_i is not None:
        for offset, row in enumerate(rows[:40]):
            if owner_i < len(row) and row[owner_i].strip() and owner_i != label_i:
                out.responsibility(
                    row[owner_i].strip(), f"{headers[owner_i]} for {label(row)}",
                    f"{location}, row {unit.get('first_row', 1) + offset}", _row_quote(headers, row),
                )

    for offset, row in enumerate(rows):
        for cell_text in row:
            if len(cell_text) > 45:
                for sentence in sentences(cell_text):
                    _analyze_sentence(sentence, f"{location}, row {unit.get('first_row', 1) + offset}", out)


# -------------------------------------------------------------- entry points
def extract_findings(payload: dict) -> dict:
    """Mock implementation of the ``extract_findings`` task."""
    out = _Collector()
    full_text = []
    for unit in payload["units"]:
        full_text.append(f"{unit.get('heading', '')}\n{unit['text']}")
        if unit["kind"] == "table" and unit.get("headers"):
            findings_i = _findings_column(unit["headers"])
            if _is_risk_register(unit["headers"]):
                _analyze_risk_register(unit, out)
            elif findings_i is not None:
                _analyze_findings(unit, findings_i, out)
            else:
                _analyze_records(unit, out)
        else:
            for sentence in sentences(unit["text"]):
                _analyze_sentence(sentence, unit["location"], out)
        _extract_facts(unit["text"], unit["location"], out)
    text = "\n".join(full_text)

    # Inferred risks: the topic is discussed, no related control is evidenced
    # and no documented risk already covers it.
    documented_keys = {r.get("catalogue_key") for r in out.risks}
    candidates = []
    for entry in RISK_CATALOGUE:
        mentions = len(entry["topic_re"].findall(text))
        if mentions >= 2 and entry["key"] not in documented_keys and not entry["control_re"].search(text):
            candidates.append((mentions, entry))
    for _, entry in sorted(candidates, key=lambda c: -c[0])[:4]:
        risk_id = out.risk(
            title=entry["title"],
            description=(
                f"The reviewed content refers to this area but does not evidence a related control "
                f"({entry['control_name'].lower()}). This is an inference from the absence of evidence, not a confirmed weakness."
            ),
            category=entry["category"], origin="inferred",
            likelihood=entry["likelihood"], impact=entry["impact"],
            recommended_mitigation=entry["mitigation"], confidence="low", catalogue_key=entry["key"],
        )
        if risk_id:
            out.control(
                name=entry["control_name"], objective=entry["objective"], description=entry["mitigation"],
                control_type=entry["type"], control_function=entry["function"], status="not_implemented",
                basis="recommended", related_risks=[risk_id], recommended_action=entry["mitigation"], confidence="low",
            )

    # Recommended controls for documented risks that have a catalogue entry.
    for risk in out.risks:
        entry = CATALOGUE_BY_KEY.get(risk.get("catalogue_key") or "")
        if entry and risk["origin"] == "documented":
            out.control(
                name=entry["control_name"], objective=entry["objective"], description=entry["mitigation"],
                control_type=entry["type"], control_function=entry["function"], status="not_implemented",
                basis="recommended", related_risks=[risk["local_id"]], recommended_action=entry["mitigation"],
            )

    # Link controls to the requirements they share a theme with.
    for control in out.controls:
        control_themes = themes(f"{control['name']} {control['description']}")
        control["related_requirements"] = [
            r["local_id"] for r in out.requirements if control_themes & themes(r["statement"])
        ][:6]
    for risk in out.risks:
        risk.pop("catalogue_key", None)

    categories = Counter(r["category"] for r in out.requirements)
    top = [REQUIREMENT_CATEGORIES[c].lower() for c, _ in categories.most_common(2)]
    organization = ORG_RE.search(text)
    title = payload.get("document_title") or payload.get("file_name", "document")
    if _is_any_risk_register(payload):
        category = "Risk assessment register"
    elif any(u["kind"] == "table" for u in payload["units"]) and not out.requirements:
        category = "Records / register"
    else:
        category = REQUIREMENT_CATEGORIES.get(classify_category(text), "General document").replace(" requirements", " document")
    return {
        "document_purpose": (
            f"'{title}' contains material on " + (" and ".join(top) if top else category.lower())
            + f" ({len(out.requirements)} requirement statements, {len(out.risks)} risk indicators found by keyword rules)."
        ),
        "document_category": category,
        "organization_name": organization.group(1) if organization else "",
        "stakeholders": sorted({m.group(1) for m in ROLE_RE.finditer(text)})[:20],
        "systems": sorted({m.group(1) for m in SYSTEM_RE.finditer(text)})[:20],
        "requirements": out.requirements,
        "risks": out.risks,
        "controls": out.controls,
        "responsibilities": out.responsibilities,
        "facts": out.facts,
    }


def _is_any_risk_register(payload: dict) -> bool:
    return any(u["kind"] == "table" and u.get("headers") and _is_risk_register(u["headers"]) for u in payload["units"])


def score_policy_types(text: str, policy_types: list[dict], identity: str = "") -> list[tuple[int, dict, list[str]]]:
    """Rank policy types by keyword hits in ``text`` and in the document titles (``identity``).

    Returns (score, type, matched keywords), best first.
    """
    lowered, identity = text.lower(), identity.lower()
    ranked = []
    for policy in policy_types:
        hits = {k: min(lowered.count(k), 4) for k in policy.get("keywords", [])}
        in_title = [k for k in policy.get("keywords", []) if k in identity]
        matched = in_title + [k for k, n in sorted(hits.items(), key=lambda kv: -kv[1]) if n and k not in in_title]
        ranked.append((sum(hits.values()) + 12 * len(in_title), policy, matched))
    return sorted(ranked, key=lambda r: -r[0])


def consolidate(payload: dict) -> dict:
    """Mock implementation of the ``consolidate`` task."""
    requirements, risks, controls = payload["requirements"], payload["risks"], payload["controls"]
    documents, facts = payload["documents"], payload["facts"]

    corpus = " ".join(
        [d.get("title", "") + " " + d.get("purpose", "") + " " + d.get("category", "") for d in documents]
        + [r["statement"] for r in requirements]
        + [f"{r['title']} {r['description']}" for r in risks]
        + [f"{c['name']} {c['description']}" for c in controls]
    )
    identity = " | ".join(f"{d.get('title', '')} {d.get('category', '')}" for d in documents)
    ranked = score_policy_types(corpus, payload["policy_types"], identity)
    best_score, best, matched = ranked[0]
    alternatives = [
        {"policy_type": p["id"], "reason": f"Related keywords found: {', '.join(m[:4])}."}
        for score, p, m in ranked[1:4] if score > 0
    ]

    # Gap analysis by shared theme / vocabulary.
    existing = [c for c in controls if c["basis"] == "existing"]
    recommended = [c for c in controls if c["basis"] == "recommended"]
    control_themes = {c["ref_id"]: themes(f"{c['name']} {c['description']}") for c in controls}
    control_tokens = {c["ref_id"]: tokens(f"{c['name']} {c['description']}") for c in controls}
    risk_themes = {r["ref_id"]: themes(f"{r['title']} {r['description']} {r['category']}") for r in risks}
    level_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    gaps = []
    for requirement in requirements:
        req_themes, req_tokens = themes(requirement["statement"]), tokens(requirement["statement"])

        def related(control, req_themes=req_themes, req_tokens=req_tokens) -> bool:
            shared = req_tokens & control_tokens[control["ref_id"]]
            return bool(req_themes & control_themes[control["ref_id"]]) or len(shared) >= max(2, min(len(req_tokens), 6) // 2)

        matched_existing = [c for c in existing if related(c)]
        matched_recommended = [c for c in recommended if related(c)]
        related_risks = [r for r in risks if req_themes & risk_themes[r["ref_id"]]]
        documented_weakness = [r for r in related_risks if r["origin"] == "documented"]
        implemented = [c for c in matched_existing if c["status"] == "implemented"]

        if implemented and not documented_weakness:
            status = "compliant"
            gap_text = ""
        elif matched_existing:
            status = "partially_addressed"
            gap_text = (
                "The documents evidence a related control, but it is only partly in place or the documents also record a "
                "related weakness."
            )
        else:
            status = "not_evidenced"
            gap_text = "The documents do not evidence a control that meets this requirement."
        worst = min((level_rank.get(r.get("level", "medium"), 2) for r in related_risks), default=2)
        priority = "low" if status == "compliant" else ["critical", "high", "medium", "low"][worst]
        gaps.append(
            {
                "requirement_id": requirement["ref_id"],
                "requirement": requirement["statement"],
                "existing_evidence": "; ".join(f"{c['ref_id']} {c['name']}" for c in matched_existing[:3]),
                "status": status,
                "identified_gap": gap_text,
                "associated_risk_ids": [r["ref_id"] for r in related_risks[:4]],
                "recommended_control_ids": [c["ref_id"] for c in matched_recommended[:3]],
                "recommended_control": matched_recommended[0]["name"] if matched_recommended else "",
                "priority": priority,
                "suggested_action": (
                    "Retain the evidence and keep the control under periodic review." if status == "compliant"
                    else (matched_recommended[0]["recommended_action"] if matched_recommended else "Define, assign and evidence a control for this requirement.")
                ),
            }
        )

    # Contradictions: the same fact topic with different values.
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for fact in facts:
        grouped[fact["topic"]][normalise_value(fact["value"])].append(fact)
    contradictions = []
    for topic, values in grouped.items():
        if len(values) > 1:
            contradictions.append(
                {
                    "topic": topic,
                    "description": f"The documents give different values for '{topic.lower()}': "
                    + "; ".join(f"'{v}'" for v in values) + ".",
                    "sources": [
                        {"file_name": f["file_name"], "detail": f"{f['source_location']}: \"{f['evidence']}\""}
                        for facts_for_value in values.values() for f in facts_for_value[:2]
                    ],
                }
            )

    fact_topics = set(grouped)
    stated = lambda pattern: any(re.search(pattern, r["statement"], re.IGNORECASE) for r in requirements)  # noqa: E731
    missing = []
    checks = (
        (not payload.get("organization_name"), "Organization name", "It appears on the cover page and throughout the policy.", "Policy owner"),
        (not payload["responsibilities"], "Roles accountable for the requirements", "Requirements without an owner are rarely implemented.", "Senior management"),
        ("Policy review frequency" not in fact_topics, "Policy review frequency", "The policy must state how often it is reviewed.", "Policy owner"),
        (not stated(r"exception|waiver|deviation"), "Exception and approval process", "People need a defined route for cases where a requirement cannot be met.", "Policy owner"),
        ("Incident reporting window" not in fact_topics and not stated(r"incident"), "Incident reporting contact and time limit", "Staff need to know how fast and to whom to report.", "Security team"),
        (not stated(r"disciplin|sanction|non-?complian|violation"), "Consequences of non-compliance", "A policy should state what happens when it is not followed.", "HR / Legal"),
        (not any(r.get("scoring_basis") == "documented" for r in risks) and bool(risks), "Documented likelihood and impact ratings", "The risk ratings shown were estimated by rules, not taken from the documents.", "Risk owner"),
    )
    for condition, item, why, owner in checks:
        if condition:
            missing.append({"item": item, "why_it_matters": why, "suggested_owner": owner})

    ordered = sorted(risks, key=lambda r: -r.get("score", 0))
    recommendations = [
        {
            "title": f"Treat risk {r['ref_id']}: {r['title']}"[:140],
            "description": r.get("recommended_mitigation") or "Define a treatment action with an owner and due date.",
            "priority": r.get("level") if r.get("level") in level_rank else "medium",
            "related_ids": [r["ref_id"]],
        }
        for r in ordered[:6]
    ]
    not_evidenced = [g for g in gaps if g["status"] == "not_evidenced"]
    if not_evidenced:
        recommendations.append(
            {
                "title": f"Evidence the {len(not_evidenced)} requirements currently marked Not Evidenced",
                "description": "Assign an owner to each, record the control that meets it, or plan one.",
                "priority": "high",
                "related_ids": [g["requirement_id"] for g in not_evidenced[:8]],
            }
        )
    if contradictions:
        recommendations.append(
            {
                "title": "Resolve contradictory values between source documents",
                "description": "Agree a single value for: " + ", ".join(c["topic"].lower() for c in contradictions) + ".",
                "priority": "medium",
                "related_ids": [],
            }
        )

    documented = sum(1 for r in risks if r["origin"] == "documented")
    categories = Counter(r["category_label"] for r in requirements)
    subject_areas = [name for name, _ in categories.most_common(5)]
    file_list = ", ".join(d["file_name"] for d in documents)
    summary = (
        f"Simulated rule-based analysis of {len(documents)} file(s) ({file_list}). "
        f"Keyword rules identified {len(requirements)} requirement statements, {len(risks)} risks "
        f"({documented} evidenced in the documents, {len(risks) - documented} inferred) and {len(controls)} controls "
        f"({len(existing)} existing, {len(recommended)} recommended). "
        + (f"The most common requirement areas are: {', '.join(a.lower() for a in subject_areas[:3])}. " if subject_areas else "")
        + f"{len(not_evidenced)} of {len(gaps)} requirements have no supporting control evidenced in the documents."
        + (f" {len(contradictions)} contradiction(s) between source values were detected." if contradictions else "")
    )
    return {
        "overall_summary": summary,
        "document_summaries": [
            {"file_name": d["file_name"], "summary": d.get("purpose", ""), "category": d.get("category", "")} for d in documents
        ],
        "document_category": Counter(d.get("category", "") for d in documents).most_common(1)[0][0] if documents else "",
        "subject_areas": subject_areas,
        "recommended_policy_type": best["id"] if best_score > 0 else "information_security",
        "recommendation_rationale": (
            f"Keyword rules matched this policy type best (score {best_score}), on terms including: {', '.join(matched[:6])}."
            if best_score > 0 else "No policy-specific keywords were found, so the general Information Security Policy is suggested."
        ),
        "alternative_policy_types": alternatives,
        "gap_analysis": gaps,
        "contradictions": contradictions,
        "missing_information": missing,
        "assumptions": [
            "The uploaded documents are current and reflect the organization's actual practice.",
            "Likelihood and impact ratings not taken from the documents are rule-based estimates and must be validated.",
            "Roles named in the documents exist and can be assigned the responsibilities described.",
            "This analysis was produced by keyword rules in mock mode, not by an AI model, and may miss or misread content.",
        ],
        "recommendations": recommendations,
    }
