from difflib import SequenceMatcher
from functools import wraps
import hmac
from io import BytesIO
import json
import logging
import os
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from google import genai
from google.genai import types
import pandas as pd

from tools import TOOL_CATALOG, TOOL_FUNCTIONS, run_tool

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(24)

GEMINI_MODEL = "gemini-3.5-flash-lite"
MIN_PASSWORD_LENGTH = 8

COLUMN_NAMES = [
    "조직분류코드",
    "교과목번호",
    "교과목명",
    "분반",
    "이수구분",
    "학점",
    "이론",
    "실습",
    "개설학과",
    "대상학년",
    "신청_전",
    "신청_1",
    "신청_2",
    "신청_3",
    "신청_4",
    "제한_전",
    "제한_1",
    "제한_2",
    "제한_3",
    "제한_4",
    "시간표",
    "대표교수직급",
    "폐강일자",
    "폐강사유",
]

NUMERIC_COLUMNS = [
    "학점",
    "이론",
    "실습",
    "신청_전",
    "신청_1",
    "신청_2",
    "신청_3",
    "신청_4",
    "제한_전",
    "제한_1",
    "제한_2",
    "제한_3",
    "제한_4",
]

CATEGORICAL_CHECK_COLUMNS = ["이수구분", "대표교수직급", "폐강사유"]

# 업로드 파일은 디스크에 저장하지 않고 메모리에만 1개 보관합니다.
APP_STATE = {
    "filename": None,
    "df": None,
    "inspect_summary": None,
    "last_run_results": None,
}


def get_configured_password() -> str:
    """환경변수 APP_PASSWORD를 읽습니다(8자리 미만이면 빈 문자열 취급)."""
    load_dotenv(override=True)
    pw = (os.getenv("APP_PASSWORD") or "").strip()
    if len(pw) < MIN_PASSWORD_LENGTH:
        return ""
    return pw


def require_auth(fn):
    """8자리 이상 비밀번호 인증이 완료된 세션만 API 접근을 허용합니다."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not get_configured_password():
            return jsonify({"error": "서버의 .env 파일에 8자리 이상의 APP_PASSWORD를 설정해 주세요."}), 503
        if not session.get("authenticated"):
            return jsonify({"error": "비밀번호 인증이 필요합니다. 먼저 잠금을 해제해 주세요."}), 401
        return fn(*args, **kwargs)
    return wrapper


def get_gemini_client() -> genai.Client:
    """환경변수에서 Gemini API 키를 확인하고 클라이언트를 생성합니다."""
    load_dotenv(override=True)
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일에 실제 API 키를 입력해 주세요.")
    return genai.Client(api_key=api_key)


def find_similar_value_candidates(df: pd.DataFrame) -> list[dict]:
    """범주형 열에서 띄어쓰기나 표현이 조금 다른 비슷한 값 후보를 찾습니다."""
    candidates = []
    for col in CATEGORICAL_CHECK_COLUMNS:
        counts = df[col].dropna().value_counts().to_dict()
        values = list(counts.keys())
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                a, b = values[i], values[j]
                a_ns, b_ns = a.replace(" ", ""), b.replace(" ", "")
                no_space_match = a_ns == b_ns
                ratio = SequenceMatcher(None, a_ns, b_ns).ratio()
                if no_space_match or (min(len(a_ns), len(b_ns)) >= 4 and ratio >= 0.78):
                    candidates.append(
                        {
                            "column": col,
                            "value_a": a,
                            "count_a": int(counts[a]),
                            "value_b": b,
                            "count_b": int(counts[b]),
                            "reason": "띄어쓰기 차이" if no_space_match else "유사한 표현",
                        }
                    )
    return candidates


def _clean_json_text(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def parse_and_filter_suggestions(raw_text: str) -> tuple[list[dict], list[str]]:
    """AI 응답 JSON을 파싱하고 도구 카탈로그에 없는 제안은 거절합니다."""
    parsed = json.loads(_clean_json_text(raw_text))
    items = parsed.get("suggestions", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        raise ValueError("AI가 목록 형태의 JSON을 반환하지 않았습니다.")

    catalog_map = {item["name"]: item for item in TOOL_CATALOG}
    accepted = []
    rejected = []
    seen = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or item.get("name") or "").strip()
        if tool_name not in TOOL_FUNCTIONS:
            rejected.append(tool_name or "(이름 없음)")
            continue
        if tool_name in seen:
            continue
        seen.add(tool_name)

        meta = catalog_map[tool_name]
        columns = item.get("columns")
        if not isinstance(columns, list) or not columns:
            columns = meta.get("default_columns", [])

        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        accepted.append(
            {
                "tool_name": tool_name,
                "label": meta["label"],
                "reason": str(item.get("reason") or meta["description"]).strip(),
                "columns": [str(c) for c in columns],
                "params": params,
            }
        )

    return accepted[:5], rejected


def parse_ask_plan(raw_text: str) -> dict:
    """자유 질문에 대한 AI 도구+조건 JSON을 파싱하고 허용된 도구인지 검증합니다."""
    parsed = json.loads(_clean_json_text(raw_text))
    if not isinstance(parsed, dict):
        raise ValueError("AI가 올바른 JSON 객체 형식을 반환하지 않았습니다.")

    tool_name = str(parsed.get("tool_name") or "").strip()
    if tool_name not in TOOL_FUNCTIONS:
        raise ValueError(f"AI가 허용되지 않은 도구({tool_name or '없음'})를 선택해 실행을 차단했습니다.")

    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    explanation = str(parsed.get("explanation") or "질문에 맞는 도구를 선택해 계산했습니다.").strip()
    return {"tool_name": tool_name, "params": params, "explanation": explanation}


@app.get("/")
def index():
    """메인 웹 화면(index.html)을 보여줍니다."""
    return render_template("index.html")


@app.get("/health")
def health():
    """서버가 정상 작동 중인지 확인하는 기본 경로입니다."""
    logger.info("GET /health 요청 수신")
    return jsonify({"status": "ok", "message": "서버가 정상 작동 중입니다."})


@app.get("/api/auth/status")
def auth_status():
    """현재 세션의 비밀번호 인증 상태를 확인합니다."""
    configured = bool(get_configured_password())
    return jsonify(
        {
            "authenticated": bool(configured and session.get("authenticated")),
            "password_configured": configured,
            "min_length": MIN_PASSWORD_LENGTH,
        }
    )


@app.post("/api/auth/login")
def auth_login():
    """8자리 이상 비밀번호를 확인해 앱 잠금을 해제합니다."""
    configured = get_configured_password()
    if not configured:
        return jsonify({"error": "서버의 .env 파일에 8자리 이상의 APP_PASSWORD를 설정해 주세요."}), 503

    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"비밀번호는 {MIN_PASSWORD_LENGTH}자리 이상 입력해야 합니다."}), 400

    if not hmac.compare_digest(password, configured):
        logger.warning("비밀번호 인증 실패")
        return jsonify({"error": "비밀번호가 올바르지 않습니다."}), 401

    session["authenticated"] = True
    logger.info("비밀번호 인증 성공")
    return jsonify({"authenticated": True})


@app.post("/api/auth/logout")
def auth_logout():
    """세션 인증을 해제해 앱을 다시 잠급니다."""
    session.pop("authenticated", None)
    logger.info("앱 잠금(로그아웃) 완료")
    return jsonify({"authenticated": False})


@app.post("/inspect")
@require_auth
def inspect_file():
    """업로드된 .xls 파일을 메모리에서 읽고 구조·품질·경고 목록을 진단합니다."""
    if "file" not in request.files:
        return jsonify({"error": "업로드된 파일이 없습니다. .xls 파일을 선택해 주세요."}), 400

    uploaded = request.files["file"]
    filename = (uploaded.filename or "").strip()
    if not filename:
        return jsonify({"error": "파일 이름이 비어 있습니다. .xls 파일을 선택해 주세요."}), 400

    if not filename.lower().endswith(".xls"):
        return jsonify({"error": "지원하지 않는 파일 형식입니다. .xls 파일만 업로드할 수 있습니다."}), 400

    try:
        raw_bytes = uploaded.read()
        if not raw_bytes:
            return jsonify({"error": "빈 파일입니다. 내용이 있는 .xls 파일을 올려 주세요."}), 400

        raw_df = pd.read_excel(BytesIO(raw_bytes), header=None, dtype=str, engine="xlrd")
    except Exception as exc:
        logger.exception("엑셀 파일 읽기 실패: %s", filename)
        return jsonify({"error": f"엑셀 파일을 읽는 중 오류가 발생했습니다: {exc}"}), 400

    if raw_df.shape[0] < 3 or raw_df.shape[1] != len(COLUMN_NAMES):
        return (
            jsonify(
                {
                    "error": (
                        f"예상한 개설강좌 양식(최소 3행, {len(COLUMN_NAMES)}열)과 다릅니다. "
                        f"(현재: {raw_df.shape[0]}행 × {raw_df.shape[1]}열)"
                    )
                }
            ),
            400,
        )

    df = raw_df.iloc[2:].copy().reset_index(drop=True)
    df.columns = COLUMN_NAMES

    for col in df.columns:
        df[col] = df[col].apply(
            lambda v: v.strip() if isinstance(v, str) and v.strip() != "" else None
        )

    numeric_issues = []
    for col in NUMERIC_COLUMNS:
        raw_series = df[col]
        converted = pd.to_numeric(raw_series, errors="coerce")

        fail_mask = raw_series.notna() & converted.isna()
        fail_rows = [int(idx) + 3 for idx in df.index[fail_mask]]

        neg_mask = converted < 0
        neg_rows = [int(idx) + 3 for idx in df.index[neg_mask]]

        if fail_rows or neg_rows:
            numeric_issues.append(
                {
                    "column": col,
                    "conversion_fail_count": len(fail_rows),
                    "conversion_fail_excel_rows": fail_rows[:20],
                    "negative_count": len(neg_rows),
                    "negative_excel_rows": neg_rows[:20],
                }
            )

        df[col] = converted

    df["단과대"] = df["개설학과"].apply(
        lambda v: v.split(">>")[0].strip() if isinstance(v, str) and v.strip() else "미상"
    )
    college_counts = df["단과대"].value_counts().to_dict()

    missing_summary = []
    for col in COLUMN_NAMES:
        miss_cnt = int(df[col].isna().sum())
        if miss_cnt > 0:
            note = "정상 개설 강좌(해당 없음)" if col in ("폐강일자", "폐강사유") else "누락 또는 미배정 확인 필요"
            missing_summary.append(
                {
                    "column": col,
                    "missing_count": miss_cnt,
                    "filled_count": int(len(df) - miss_cnt),
                    "note": note,
                }
            )

    similar_candidates = find_similar_value_candidates(df)

    cancelled_mask = df["폐강일자"].notna()
    cancelled_count = int(cancelled_mask.sum())
    active_count = int((~cancelled_mask).sum())
    zero_cap_active_count = int(((~cancelled_mask) & (df["제한_전"] == 0)).sum())

    course_stats = {
        "total_rows": int(len(df)),
        "active_rows": active_count,
        "cancelled_rows": cancelled_count,
        "cancellation_rate_pct": round(cancelled_count / len(df) * 100, 2),
        "unique_course_section_count": int(df[["교과목번호", "분반"]].drop_duplicates().shape[0]),
        "unique_course_code_count": int(df["교과목번호"].nunique()),
        "unique_course_name_count": int(df["교과목명"].nunique()),
        "college_count": int(df["단과대"].nunique()),
        "zero_capacity_active_count": zero_cap_active_count,
    }

    warnings = [
        {
            "category": "열 이름 2줄 처리",
            "message": "1~2행이 병합된 열 이름이라 3행부터 데이터로 읽고, 24개 열 이름을 직접 지정했습니다.",
        },
        {
            "category": "글자 숫자 변환",
            "message": f"글자로 저장된 {len(NUMERIC_COLUMNS)}개 숫자 열(학점·이론·실습·신청_*·제한_*)을 숫자로 변환했습니다.",
        },
    ]

    if numeric_issues:
        issue_desc = ", ".join(
            f"{item['column']}(음수 {item['negative_count']}건: 엑셀 {item['negative_excel_rows']}행, 변환실패 {item['conversion_fail_count']}건)"
            for item in numeric_issues
        )
        warnings.append(
            {
                "category": "비정상 값",
                "message": f"숫자 열에서 비정상 값이 발견되었습니다(자동 수정 안 함): {issue_desc}",
            }
        )

    if zero_cap_active_count > 0:
        warnings.append(
            {
                "category": "비정상 값",
                "message": f"폐강 제외 강좌 중 수강제한인원(제한_전)이 0인 행이 {zero_cap_active_count}건 있습니다. 충원율 계산 시 제외됩니다.",
            }
        )

    if missing_summary:
        miss_desc = ", ".join(f"{m['column']}({m['missing_count']}건)" for m in missing_summary)
        warnings.append(
            {
                "category": "빈칸",
                "message": f"빈칸이 있는 열: {miss_desc}. 폐강일자·폐강사유 빈칸은 정상 강좌(해당 없음)입니다.",
            }
        )

    if similar_candidates:
        sim_desc = ", ".join(
            f"{c['column']}: '{c['value_a']}'({c['count_a']}건) vs '{c['value_b']}'({c['count_b']}건)"
            for c in similar_candidates
        )
        warnings.append(
            {
                "category": "비슷한 값 후보",
                "message": f"같은 뜻인데 표기가 다른 값 후보가 있습니다: {sim_desc}",
            }
        )

    preview_df = df[COLUMN_NAMES].head(3).astype(object).where(pd.notnull(df[COLUMN_NAMES].head(3)), None)
    summary = {
        "filename": filename,
        "rows": int(len(df)),
        "columns": len(COLUMN_NAMES),
        "column_names": COLUMN_NAMES,
        "course_stats": course_stats,
        "colleges": college_counts,
        "isu_types": df["이수구분"].dropna().unique().tolist(),
        "grades": df["대상학년"].dropna().unique().tolist(),
        "missing_summary": missing_summary,
        "numeric_issues": numeric_issues,
        "similar_candidates": similar_candidates,
        "warnings": warnings,
        "preview": preview_df.to_dict(orient="records"),
    }

    APP_STATE["filename"] = filename
    APP_STATE["df"] = df
    APP_STATE["inspect_summary"] = summary
    APP_STATE["last_run_results"] = None

    logger.info("POST /inspect 완료: %s (%d행, 경고 %d건)", filename, len(df), len(warnings))
    return jsonify(summary)


@app.post("/suggest")
@require_auth
def suggest_analyses():
    """진단 요약과 도구 목록만 AI에게 보내 실행 가능한 분석 후보 3~5개를 JSON으로 받습니다."""
    summary = APP_STATE.get("inspect_summary")
    if not summary:
        return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드하고 진단을 실행해 주세요."}), 400

    safe_summary = {k: v for k, v in summary.items() if k != "preview"}

    prompt = (
        "너는 대학 개설강좌 데이터를 점검하는 행정 분석 도우미다.\n"
        "아래 [진단 요약]과 [허용된 도구 목록]만 보고, 지금 이 데이터에서 해볼 만한 분석 후보 3~5개를 골라라.\n"
        "규칙:\n"
        "1) 반드시 [허용된 도구 목록]에 있는 tool_name만 사용하라. 목록에 없는 도구는 절대 만들지 마라.\n"
        "2) 출력은 반드시 아래 JSON 형식만 반환하라.\n"
        '{"suggestions": [{"tool_name": "도구이름", "reason": "이유(한국어 1~2문장)", "columns": ["사용할열1", "사용할열2"]}]}\n\n'
        f"[허용된 도구 목록]\n{json.dumps(TOOL_CATALOG, ensure_ascii=False)}\n\n"
        f"[진단 요약]\n{json.dumps(safe_summary, ensure_ascii=False)}"
    )

    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        accepted, rejected = parse_and_filter_suggestions(response.text or "")
    except ValueError as exc:
        logger.warning("POST /suggest 요청/검증 오류: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("POST /suggest Gemini 호출 실패")
        return jsonify({"error": f"AI 분석 제안을 받아오는 중 오류가 발생했습니다: {exc}"}), 502

    if rejected:
        logger.warning("도구 목록 밖의 AI 제안 거절됨: %s", rejected)

    if not accepted:
        return jsonify({"error": "AI가 허용된 도구 목록 안의 분석을 제안하지 못했습니다. 다시 시도해 주세요."}), 400

    logger.info("POST /suggest 완료: 제안 %d건 (거절 %d건)", len(accepted), len(rejected))
    return jsonify({"suggestions": accepted, "rejected_tools": rejected})


@app.post("/ask")
@require_auth
def ask_question():
    """사용자 자유 질문을 AI가 도구+조건 JSON으로 변환하고 pandas가 직접 계산합니다(방안 A)."""
    df = APP_STATE.get("df")
    summary = APP_STATE.get("inspect_summary")
    if df is None or not summary:
        return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드하고 진단을 실행해 주세요."}), 400

    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "분석하고 싶은 질문을 입력해 주세요."}), 400

    metadata = {
        "단과대_목록": list((summary.get("colleges") or {}).keys()),
        "이수구분_목록": summary.get("isu_types") or [],
        "대상학년_목록": summary.get("grades") or [],
        "요일_목록": ["월", "화", "수", "목", "금", "토", "일"],
        "폐강여부_옵션": ["정상", "폐강", "전체"],
    }

    prompt = (
        "너는 대학 행정 직원의 질문을 안전한 분석 도구 호출(JSON)로 변환하는 번역기다.\n"
        "절대로 직접 숫자를 계산하거나 파이썬 코드를 쓰지 마라.\n"
        "아래 [허용된 도구 목록]과 [데이터 메타정보]를 참고해 질문에 가장 알맞은 도구 1개와 조건(params)을 골라 JSON으로만 답하라.\n\n"
        "필터(params.filters)에서 쓸 수 있는 키:\n"
        "- 단과대, 이수구분, 대상학년, 대표교수직급, 폐강여부('정상'|'폐강'|'전체'), 요일('월'|'화'|'수'|'목'|'금'|'토'|'일'), "
        "min_신청_전, max_신청_전, min_제한_전, max_제한_전\n\n"
        "출력 JSON 형식:\n"
        '{"tool_name": "도구이름", "params": {"group_by": "...", "filters": {...}}, "explanation": "왜 이 도구와 조건을 골랐는지 1문장"}\n\n'
        f"[허용된 도구 목록]\n{json.dumps(TOOL_CATALOG, ensure_ascii=False)}\n\n"
        f"[데이터 메타정보]\n{json.dumps(metadata, ensure_ascii=False)}\n\n"
        f"[사용자 질문]\n{question}"
    )

    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        plan = parse_ask_plan(response.text or "")
        result = run_tool(df, plan["tool_name"], plan["params"])
    except ValueError as exc:
        logger.warning("POST /ask 요청/검증 오류: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("POST /ask 처리 실패")
        return jsonify({"error": f"질문 분석 중 오류가 발생했습니다: {exc}"}), 502

    APP_STATE["last_run_results"] = [result]
    logger.info("POST /ask 완료: 질문='%s' -> 도구=%s", question[:40], plan["tool_name"])
    return jsonify(
        {
            "question": question,
            "plan": plan,
            "results": [result],
        }
    )


@app.post("/run")
@require_auth
def run_analyses():
    """사용자가 선택한 도구들을 pandas로 계산하고 결과를 메모리에 보관합니다."""
    df = APP_STATE.get("df")
    if df is None:
        return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드하고 진단을 실행해 주세요."}), 400

    payload = request.get_json(silent=True) or {}
    raw_selected = payload.get("selected") or payload.get("tools") or []
    if not isinstance(raw_selected, list) or not raw_selected:
        return jsonify({"error": "실행할 분석 도구를 하나 이상 선택해 주세요."}), 400

    results = []
    for item in raw_selected:
        if isinstance(item, str):
            tool_name = item.strip()
            params = {}
        elif isinstance(item, dict):
            tool_name = str(item.get("tool_name") or item.get("name") or "").strip()
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
        else:
            continue

        if tool_name not in TOOL_FUNCTIONS:
            return jsonify({"error": f"허용되지 않은 분석 도구입니다: {tool_name}"}), 400

        result = run_tool(df, tool_name, params)
        results.append(result)

    if not results:
        return jsonify({"error": "실행할 유효한 분석 도구가 없습니다."}), 400

    APP_STATE["last_run_results"] = results
    logger.info("POST /run 완료: %d개 도구 실행", len(results))
    return jsonify({"filename": APP_STATE.get("filename"), "results": results})


def build_explain_prompt(mode: str, summary: dict, results: list[dict] | None) -> str:
    """모드 A(비교용) 또는 모드 B(정식) 보고서 작성용 프롬프트를 만듭니다."""
    if mode == "A":
        safe_summary = {k: v for k, v in summary.items() if k != "preview"}
        return (
            "아래 대학 개설강좌 파일의 진단 요약만 보고 분석해서 설명해 줘.\n\n"
            f"[진단 요약]\n{json.dumps(safe_summary, ensure_ascii=False)}"
        )

    return (
        "너는 대학 행정 직원을 위해 데이터 분석 보고서를 작성하는 도우미다.\n"
        "아래 [계산 결과 JSON]만 보고 마크다운(.md) 형식의 정식 보고서를 작성하라.\n\n"
        "[필수 규칙]\n"
        "1. 주어진 숫자만 인용하라. 새 숫자를 만들거나 다시 계산하지 마라. 파일만으로 알 수 없는 것은 알 수 없다고 써라.\n"
        "2. 표에 없는 수치나 이유를 추측해서 지어내지 마라.\n"
        "3. 코딩 초보자인 대학 행정 직원이 바로 이해할 수 있도록 쉽고 명확한 한국어로 작성하라.\n\n"
        "[보고서 구성 (반드시 아래 7개 제목을 순서대로 사용)]\n"
        "## 1. 무엇을 봤는가\n"
        "## 2. 기준(정의)과 사용한 열\n"
        "## 3. 제외된 항목\n"
        "## 4. 계산 결과\n"
        "## 5. 읽어낼 수 있는 것\n"
        "## 6. 이 데이터로는 알 수 없는 것\n"
        "## 7. 다음에 확인해볼 것\n\n"
        f"[계산 결과 JSON]\n{json.dumps(results, ensure_ascii=False)}"
    )


@app.post("/explain")
@require_auth
def explain_results():
    """모드 A(진단 요약만) 또는 모드 B(계산 결과 JSON만)로 AI 해석 보고서를 생성합니다."""
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "B").strip().upper()
    if mode not in ("A", "B"):
        return jsonify({"error": "보고서 모드는 'A' 또는 'B'만 선택할 수 있습니다."}), 400

    summary = APP_STATE.get("inspect_summary")
    if not summary:
        return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드하고 진단을 실행해 주세요."}), 400

    results = APP_STATE.get("last_run_results")
    if mode == "B" and not results:
        return jsonify({"error": "모드 B 보고서를 만들려면 먼저 하나 이상의 분석을 실행해 주세요."}), 400

    prompt = build_explain_prompt(mode, summary, results)

    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2 if mode == "B" else 0.7),
        )
        report_text = (response.text or "").strip()
    except ValueError as exc:
        logger.warning("POST /explain 요청 오류: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("POST /explain Gemini 호출 실패")
        return jsonify({"error": f"AI 보고서를 생성하는 중 오류가 발생했습니다: {exc}"}), 502

    if not report_text:
        return jsonify({"error": "AI가 빈 보고서를 반환했습니다. 다시 시도해 주세요."}), 502

    logger.info("POST /explain 완료: 모드 %s (%d자)", mode, len(report_text))
    return jsonify(
        {
            "mode": mode,
            "filename": APP_STATE.get("filename"),
            "report": report_text,
        }
    )


if __name__ == "__main__":
    logger.info("Flask 서버를 시작합니다: http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
