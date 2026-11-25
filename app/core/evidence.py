"""Screenshot evidence extraction for match results.

The user can upload the post-match summary screen (print/foto). The module
stores the image, tries to extract the scoreline and stores the raw extracted
JSON for auditing. Extraction is pluggable:

* ``vision`` — any OpenAI-compatible multimodal endpoint configured through
  ``VISION_API_URL`` / ``VISION_API_KEY`` / ``VISION_MODEL``;
* ``ocr`` — local Tesseract via ``pytesseract`` + Pillow (optional extra);
* ``none`` — the image is still stored and the user can type the score manually.

Nothing is applied automatically: the dashboard pre-fills the correction form
with the suggestion and the user confirms it.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings

ALLOWED_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

_DATE_RE = re.compile(
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"
)
_SCORE_PATTERNS = (
    ("dash", re.compile(r"(?<!\d)(\d{1,2})\s*[-\u2013\u2014]\s*(\d{1,2})(?!\d)")),
    ("x", re.compile(r"(?<!\d)(\d{1,2})\s*[xX]\s*(\d{1,2})(?!\d)")),
    ("colon", re.compile(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)")),
)
_CONTEXT_RE = re.compile(
    r"placar|resultado|score|final|gols?\b|goals?\b", re.IGNORECASE
)
_GOAL_LINE_RE = re.compile(
    r"(?:gol|goal)\s*(?:de|do|da|by)?\s*:?\s*(?P<player>[^\n,;|]+)"
    r"(?:[\s\-–—]*?(?P<minute>\d{1,3})'?)?",
    re.IGNORECASE,
)


class EvidenceError(Exception):
    """Raised when an upload is invalid (size/type)."""


@dataclass
class StoredEvidence:
    stored_path: str
    original_name: str | None
    content_type: str | None
    size_bytes: int
    sha256: str


@dataclass
class Extraction:
    extractor: str
    data: dict[str, object] = field(default_factory=dict)
    raw_text: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def has_score(self) -> bool:
        return (
            isinstance(self.data.get("club_a_goals"), int)
            and isinstance(self.data.get("club_b_goals"), int)
        )


def save_image(
    data: bytes,
    original_name: str | None,
    content_type: str | None,
    match_id: str,
    settings: Settings,
) -> StoredEvidence:
    """Validate and persist an uploaded image under ``EVIDENCE_DIR``."""
    if not data:
        raise EvidenceError("Arquivo vazio.")
    if len(data) > settings.EVIDENCE_MAX_BYTES:
        raise EvidenceError(
            f"Arquivo maior que o limite de {settings.EVIDENCE_MAX_BYTES // (1024 * 1024)} MB."
        )

    mime = (content_type or "").split(";")[0].strip().lower()
    suffix = ALLOWED_CONTENT_TYPES.get(mime)
    if suffix is None:
        name_suffix = Path(original_name or "").suffix.lower()
        suffix = name_suffix if name_suffix in ALLOWED_SUFFIXES else None
    if suffix is None:
        raise EvidenceError("Envie uma imagem PNG, JPG ou WEBP.")

    directory = Path(settings.EVIDENCE_DIR) / str(match_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)

    return StoredEvidence(
        stored_path=str(path),
        original_name=original_name,
        content_type=mime or "image/png",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _loads_json_object(raw: str) -> dict[str, object]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 99:
        return None
    return number


def normalize_extraction(data: dict[str, object]) -> dict[str, object]:
    """Accept a few common key spellings and return a stable shape."""
    goals_a = _coerce_int(
        data.get("club_a_goals")
        if data.get("club_a_goals") is not None
        else data.get("home_goals", data.get("score_a", data.get("team_a_goals")))
    )
    goals_b = _coerce_int(
        data.get("club_b_goals")
        if data.get("club_b_goals") is not None
        else data.get("away_goals", data.get("score_b", data.get("team_b_goals")))
    )
    team_a = (
        data.get("team_a")
        or data.get("home_team")
        or data.get("team_a_name")
    )
    team_b = (
        data.get("team_b")
        or data.get("away_team")
        or data.get("team_b_name")
    )

    scorers = data.get("scorers") or data.get("goals") or []
    if not isinstance(scorers, list):
        scorers = []

    confidence = data.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_value = None

    return {
        "club_a_goals": goals_a,
        "club_b_goals": goals_b,
        "team_a": str(team_a) if team_a else None,
        "team_b": str(team_b) if team_b else None,
        "scorers": scorers,
        "cards": data.get("cards") or data.get("cartoes") or [],
        "possession": data.get("possession"),
        "extra": data.get("extra"),
        "confidence": confidence_value,
        "notes": data.get("notes"),
        "raw": data,
    }


def _name_similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    a = left.strip().lower()
    b = right.strip().lower()
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def align_extraction(
    data: dict[str, object],
    club_a_name: str | None,
    club_b_name: str | None,
) -> tuple[dict[str, object], list[str]]:
    """Map the two teams in the screenshot to our ``club_a``/``club_b``.

    The screenshot may show the teams in the opposite order, so the score is
    swapped when the names clearly match the other way around.
    """
    warnings: list[str] = []
    goals_a = data.get("club_a_goals")
    goals_b = data.get("club_b_goals")
    team_a = data.get("team_a")
    team_b = data.get("team_b")

    direct = _name_similarity(str(team_a or ""), club_a_name) + _name_similarity(
        str(team_b or ""), club_b_name
    )
    swapped = _name_similarity(str(team_a or ""), club_b_name) + _name_similarity(
        str(team_b or ""), club_a_name
    )

    if swapped > direct and swapped >= 1.0:
        data["club_a_goals"], data["club_b_goals"] = goals_b, goals_a
        data["team_a"], data["team_b"] = team_b, team_a
        data["swapped"] = True
        warnings.append(
            "A ordem dos times no print era inversa; o placar foi ajustado."
        )
    elif direct < 1.0 and (team_a or team_b):
        data["swapped"] = False
        warnings.append(
            "Não foi possível confirmar os nomes dos times no print. "
            "Verifique o placar antes de salvar."
        )
    else:
        data["swapped"] = False

    return data, warnings


def parse_summary_text(
    text: str,
    club_a_name: str | None = None,
    club_b_name: str | None = None,
) -> dict[str, object]:
    """Extract a score candidate from OCR text.

    This is intentionally conservative: it returns the most plausible score
    and all candidates so the UI can show them for confirmation.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    date_spans: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in _DATE_RE.finditer(text or "")
    ]

    candidates: list[dict[str, object]] = []
    for order, (kind, pattern) in enumerate(_SCORE_PATTERNS):
        for match in pattern.finditer(text or ""):
            if any(
                match.start() < end and match.end() > start
                for start, end in date_spans
            ):
                continue
            a = _coerce_int(match.group(1))
            b = _coerce_int(match.group(2))
            if a is None or b is None:
                continue
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end].strip()
            context = bool(_CONTEXT_RE.search(line))
            spaced = bool(re.search(r"\d\s+[-\u2013\u2014]\s+\d", match.group(0)))
            score = 0
            if context:
                score += 10
            if spaced:
                score += 5
            if kind == "x":
                score += 4
            if kind == "dash":
                score += 3
            if a <= 9 and b <= 9:
                score += 1
            candidates.append(
                {
                    "a": a,
                    "b": b,
                    "raw": match.group(0),
                    "line": line,
                    "score": score,
                    "order": order,
                }
            )

    candidates.sort(key=lambda item: (-int(item["score"]), int(item["order"])))
    data: dict[str, object] = {
        "club_a_goals": None,
        "club_b_goals": None,
        "score_candidates": candidates[:8],
        "raw_text": text,
        "confidence": 0.35 if candidates else None,
    }
    if candidates:
        best = candidates[0]
        data["club_a_goals"] = best["a"]
        data["club_b_goals"] = best["b"]
        data["detected_line"] = best["line"]

    if club_a_name and club_b_name:
        data["team_a"] = club_a_name
        data["team_b"] = club_b_name

    return data


def _extract_with_vision(
    image_path: Path,
    settings: Settings,
    content_type: str | None,
    hint: str,
) -> Extraction:
    import httpx

    mime = (content_type or "image/png").split(";")[0]
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = (
        "Você recebe um print do resumo de uma partida de EA FC Clubs. "
        "Extraia o resultado real e as informações resumidas. Responda APENAS "
        "com um objeto JSON válido, sem markdown, com as chaves: "
        '{"team_a": string|null, "team_b": string|null, '
        '"club_a_goals": int|null, "club_b_goals": int|null, '
        '"scorers": [{"player": string, "team": string, "minute": int|null}], '
        '"cards": [{"player": string, "team": string, "type": string}], '
        '"possession": string|null, "confidence": number, "notes": string|null}. '
        "team_a é o time mostrado à esquerda e team_b o da direita. "
        "Não invente: use null quando não conseguir ler. "
        f"Contexto do clube monitorado: {hint}"
    )
    payload = {
        "model": settings.VISION_MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Você é um extrator de dados de placares de futebol. "
                    "Sua única saída é JSON válido."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{encoded}"},
                    },
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.VISION_API_KEY}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=settings.VISION_TIMEOUT_SECONDS) as client:
        response = client.post(settings.VISION_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()

    content = body["choices"][0]["message"]["content"]
    data = normalize_extraction(_loads_json_object(str(content)))
    return Extraction(extractor="vision", data=data, raw_text=str(content))


def _extract_with_ocr(
    image_path: Path,
    club_a_name: str | None,
    club_b_name: str | None,
) -> Extraction | None:
    try:
        from PIL import Image  # type: ignore[import-not-found]
        import pytesseract  # type: ignore[import-not-found]
    except ImportError:
        return None

    try:
        image = Image.open(image_path)
        # Small screenshots benefit from upscaling before OCR.
        if image.width < 1200:
            ratio = 1200 / max(image.width, 1)
            image = image.resize(
                (int(image.width * ratio), int(image.height * ratio))
            )
        text = pytesseract.image_to_string(image, lang="por+eng")
    except Exception as exc:  # noqa: BLE001 - OCR is best effort
        return Extraction(
            extractor="ocr",
            data={},
            raw_text=None,
            warnings=[f"OCR local falhou: {exc}"],
        )

    data = parse_summary_text(text, club_a_name, club_b_name)
    warnings = [
        "Texto lido por OCR local; confira o placar antes de salvar."
    ] if data.get("club_a_goals") is not None else [
        "O OCR não encontrou um placar legível. Preencha manualmente."
    ]
    return Extraction(extractor="ocr", data=data, raw_text=text, warnings=warnings)


def extract_summary(
    image_path: Path,
    settings: Settings,
    *,
    club_a_name: str | None = None,
    club_b_name: str | None = None,
    content_type: str | None = None,
) -> Extraction:
    """Try vision first, then local OCR, then return a manual-fallback result."""
    warnings: list[str] = []
    hint = f"{club_a_name or 'Time A'} x {club_b_name or 'Time B'}"

    if settings.VISION_API_KEY:
        try:
            extraction = _extract_with_vision(
                image_path, settings, content_type, hint
            )
            extraction.data, extra_warnings = align_extraction(
                extraction.data, club_a_name, club_b_name
            )
            extraction.warnings.extend(warnings + extra_warnings)
            return extraction
        except Exception as exc:  # noqa: BLE001 - fall back to OCR/manual
            warnings.append(f"Extração por visão falhou: {exc}")

    ocr = _extract_with_ocr(image_path, club_a_name, club_b_name)
    if ocr is not None:
        ocr.warnings = warnings + ocr.warnings
        return ocr

    return Extraction(
        extractor="none",
        data={
            "club_a_goals": None,
            "club_b_goals": None,
            "scorers": [],
        },
        raw_text=None,
        warnings=warnings
        + [
            "Nenhum extrator de imagem está configurado. "
            "O print foi salvo como evidência; preencha o placar manualmente "
            "ou configure VISION_API_KEY (API multimodal) / pytesseract (OCR)."
        ],
    )


def parse_goal_lines(text: str) -> list[dict[str, object]]:
    """Best-effort extraction of goal scorer lines (used by OCR fallback)."""
    goals: list[dict[str, object]] = []
    for match in _GOAL_LINE_RE.finditer(text or ""):
        player = (match.group("player") or "").strip(" -–—|")
        if not player:
            continue
        minute = _coerce_int(match.group("minute"))
        goals.append({"player": player, "minute": minute})
    return goals
