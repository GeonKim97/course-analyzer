from difflib import SequenceMatcher
import re
import pandas as pd

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

TOOL_CATALOG = [
    {
        "name": "quality_report",
        "label": "데이터 품질 점검표",
        "description": "열별 빈칸 개수, 비정상 숫자(음수/변환 실패), 비슷한 값 후보를 한눈에 점검합니다.",
        "default_columns": ["시간표", "대표교수직급", "폐강일자", "폐강사유", "신청_전"],
        "params_hint": "filters(선택)",
    },
    {
        "name": "group_rate",
        "label": "그룹별 개설·폐강률",
        "description": "단과대, 이수구분, 대상학년 중 하나를 기준으로 강좌(행) 수, 폐강 수, 폐강률을 계산합니다.",
        "default_columns": ["단과대", "폐강일자"],
        "allowed_group_by": ["단과대", "이수구분", "대상학년"],
        "params_hint": "group_by(단과대|이수구분|대상학년), filters(선택)",
    },
    {
        "name": "top_bottom_n",
        "label": "상위·하위 N개 순위",
        "description": "폐강을 제외한 강좌 중 신청 인원(신청_전) 또는 제한 인원(제한_전) 기준 상위·하위 N개를 뽑습니다.",
        "default_columns": ["교과목명", "분반", "단과대", "신청_전", "제한_전", "폐강일자"],
        "params_hint": "metric(신청_전|제한_전|학점), order(top|bottom), n(1~30), filters(선택)",
    },
    {
        "name": "fill_rate",
        "label": "그룹별 충원율 분석",
        "description": "폐강을 제외하고, 제한 인원이 0이거나 빈칸인 행을 뺀 뒤 단과대·이수구분·대상학년별 충원율(신청_전 ÷ 제한_전 × 100)을 계산합니다.",
        "default_columns": ["단과대", "신청_전", "제한_전", "폐강일자"],
        "allowed_group_by": ["단과대", "이수구분", "대상학년"],
        "params_hint": "group_by(단과대|이수구분|대상학년), filters(선택)",
    },
    {
        "name": "distribution",
        "label": "분반당 신청 인원 분포",
        "description": "폐강을 제외한 강좌의 분반당 수강신청 인원(신청_전)이 어느 인원 구간에 몰려 있는지 분포를 계산합니다.",
        "default_columns": ["신청_전", "폐강일자"],
        "params_hint": "filters(선택)",
    },
    {
        "name": "weekday_building",
        "label": "요일별·건물별 분반 분포",
        "description": "시간표 문자열을 쪼개어 요일별 분반 수와 건물별 분반 수를 계산합니다. 시간표가 빈 행은 제외합니다.",
        "default_columns": ["시간표"],
        "params_hint": "filters(선택)",
    },
    {
        "name": "compare_one_vs_all",
        "label": "선택 대상 vs 전체 비교",
        "description": "특정 단과대(또는 이수구분/대상학년)와 전체 강좌의 신청 인원·제한 인원 평균 및 중앙값을 비교합니다.",
        "default_columns": ["단과대", "신청_전", "제한_전", "폐강일자"],
        "params_hint": "group_by(단과대|이수구분|대상학년), target(대상 이름), filters(선택)",
    },
    {
        "name": "custom_query",
        "label": "조건별 맞춤 집계/조회",
        "description": "원하는 조건(단과대, 이수구분, 대상학년, 요일, 폐강여부, 신청인원 범위 등)으로 거른 뒤 그룹별 집계 또는 강좌 목록을 계산합니다.",
        "default_columns": ["단과대", "이수구분", "대상학년", "신청_전", "제한_전", "폐강일자"],
        "params_hint": "filters(dict), group_by(선택: 단과대|이수구분|대상학년|대표교수직급|학점|폐강사유), metric(신청_전|제한_전|학점), limit(1~30)",
    },
]


def _ensure_college_column(df: pd.DataFrame) -> pd.DataFrame:
    """단과대 열이 없으면 개설학과에서 분리해 추가합니다."""
    if "단과대" not in df.columns:
        df = df.copy()
        df["단과대"] = df["개설학과"].apply(
            lambda v: v.split(">>")[0].strip() if isinstance(v, str) and v.strip() else "미상"
        )
    return df


def _apply_filters(df: pd.DataFrame, filters: dict | None) -> tuple[pd.DataFrame, list[str]]:
    """안전한 사전 정의 필터를 적용하고 적용된 조건 설명 목록을 반환합니다."""
    work_df = _ensure_college_column(df).copy()
    if not isinstance(filters, dict) or not filters:
        return work_df, []

    mask = pd.Series(True, index=work_df.index)
    desc = []

    for col in ("단과대", "이수구분", "대상학년", "대표교수직급"):
        val = filters.get(col)
        if val is not None and val != "":
            if isinstance(val, list):
                vals = [str(x).strip() for x in val if str(x).strip()]
                if vals:
                    mask &= work_df[col].astype(str).isin(vals)
                    desc.append(f"{col} in {vals}")
            else:
                v_str = str(val).strip()
                mask &= work_df[col].astype(str) == v_str
                desc.append(f"{col}='{v_str}'")

    status = filters.get("폐강여부")
    if status == "정상":
        mask &= work_df["폐강일자"].isna()
        desc.append("폐강여부='정상'")
    elif status == "폐강":
        mask &= work_df["폐강일자"].notna()
        desc.append("폐강여부='폐강'")

    day = filters.get("요일")
    if isinstance(day, str) and day.strip() in ("월", "화", "수", "목", "금", "토", "일"):
        d_char = day.strip()
        mask &= work_df["시간표"].fillna("").astype(str).str.contains(rf"\([^)]*{d_char}", regex=True)
        desc.append(f"요일='{d_char}'")

    for bound_key, col, op in (
        ("min_신청_전", "신청_전", ">="),
        ("max_신청_전", "신청_전", "<="),
        ("min_제한_전", "제한_전", ">="),
        ("max_제한_전", "제한_전", "<="),
    ):
        if filters.get(bound_key) is not None and filters.get(bound_key) != "":
            try:
                num = float(filters[bound_key])
                if op == ">=":
                    mask &= work_df[col] >= num
                else:
                    mask &= work_df[col] <= num
                desc.append(f"{col} {op} {int(num) if num.is_integer() else num}")
            except (TypeError, ValueError):
                pass

    return work_df[mask].copy(), desc


def quality_report(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 1: 열별 빈칸 개수, 비정상 숫자, 비슷한 값 후보를 점검합니다."""
    params = params or {}
    filtered_df, filter_desc = _apply_filters(df, params.get("filters"))
    total_all = int(len(df))
    total_rows = int(len(filtered_df))
    rows = []

    for col in filtered_df.columns:
        if col == "단과대":
            continue
        miss_cnt = int(filtered_df[col].isna().sum())
        if miss_cnt > 0:
            rows.append(
                {
                    "점검항목": "빈칸",
                    "대상열": col,
                    "건수": miss_cnt,
                    "세부내용": "정상 개설 강좌(해당 없음)" if col in ("폐강일자", "폐강사유") else "누락 또는 미배정 확인 필요",
                }
            )

    for col in NUMERIC_COLUMNS:
        if col not in filtered_df.columns:
            continue
        ser = filtered_df[col]
        neg_idx = filtered_df.index[ser < 0].tolist()
        if neg_idx:
            excel_rows = [int(i) + 3 for i in neg_idx[:10]]
            rows.append(
                {
                    "점검항목": "비정상 숫자(음수)",
                    "대상열": col,
                    "건수": len(neg_idx),
                    "세부내용": f"엑셀 행 위치: {excel_rows}",
                }
            )

    for col in CATEGORICAL_CHECK_COLUMNS:
        if col not in filtered_df.columns:
            continue
        counts = filtered_df[col].dropna().value_counts().to_dict()
        values = list(counts.keys())
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                a, b = str(values[i]), str(values[j])
                a_ns, b_ns = a.replace(" ", ""), b.replace(" ", "")
                no_space_match = a_ns == b_ns
                ratio = SequenceMatcher(None, a_ns, b_ns).ratio()
                if no_space_match or (min(len(a_ns), len(b_ns)) >= 4 and ratio >= 0.78):
                    rows.append(
                        {
                            "점검항목": "비슷한 값 후보",
                            "대상열": col,
                            "건수": int(counts[values[i]] + counts[values[j]]),
                            "세부내용": f"'{a}'({counts[values[i]]}건) vs '{b}'({counts[values[j]]}건)",
                        }
                    )

    excl = total_all - total_rows
    return {
        "tool_name": "quality_report",
        "title": "데이터 품질 점검표" + (f" ({', '.join(filter_desc)})" if filter_desc else ""),
        "definition": "대상 행의 빈칸 개수, 음수 값, 표기가 비슷한 값 후보를 점검한 결과입니다.",
        "used_rows": total_rows,
        "excluded_rows": excl,
        "excluded_reason": f"필터 조건({', '.join(filter_desc)}) 외 행 제외" if excl > 0 else "없음",
        "used_columns": ["전체 열(빈칸)", "숫자 열(음수)", "이수구분·대표교수직급·폐강사유(유사값)"],
        "unit": "건",
        "cautions": "원본의 비정상 값(-1 등)이나 띄어쓰기 차이는 자동으로 수정하지 않았습니다.",
        "table": rows,
        "chart": None,
    }


def group_rate(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 2: 그룹(단과대/이수구분/대상학년)별 행 수·폐강 수·폐강률을 계산합니다."""
    params = params or {}
    group_by = params.get("group_by", "단과대")
    if group_by not in ("단과대", "이수구분", "대상학년"):
        group_by = "단과대"

    work_df, filter_desc = _apply_filters(df, params.get("filters"))
    missing_group_mask = work_df[group_by].isna()
    valid_df = work_df[~missing_group_mask].copy()
    valid_df["_is_cancelled"] = valid_df["폐강일자"].notna()

    if len(valid_df) == 0:
        table = []
    else:
        grouped = (
            valid_df.groupby(group_by, dropna=False)
            .agg(전체강좌수=("_is_cancelled", "count"), 폐강수=("_is_cancelled", "sum"))
            .reset_index()
        )
        grouped["정상강좌수"] = grouped["전체강좌수"] - grouped["폐강수"]
        grouped["폐강률(%)"] = (grouped["폐강수"] / grouped["전체강좌수"] * 100).round(2)
        grouped = grouped.sort_values(by=["폐강률(%)", "전체강좌수"], ascending=[False, False])
        table = grouped.to_dict(orient="records")

    top_chart = table[:12]
    excl_total = int(len(df) - len(valid_df))
    reason_parts = []
    if filter_desc:
        reason_parts.append(f"필터({', '.join(filter_desc)}) 외 {int(len(df) - len(work_df))}건")
    if int(missing_group_mask.sum()) > 0:
        reason_parts.append(f"{group_by} 빈칸 {int(missing_group_mask.sum())}건")

    return {
        "tool_name": "group_rate",
        "title": f"{group_by}별 강좌 수 및 폐강률" + (f" [{', '.join(filter_desc)}]" if filter_desc else ""),
        "definition": f"엑셀 한 행을 분반 1건으로 보고, {group_by}별로 전체 행 수와 폐강(폐강일자가 비어 있지 않은 행) 수, 폐강률(폐강 수 ÷ 전체 행 수 × 100)을 계산했습니다.",
        "used_rows": int(len(valid_df)),
        "excluded_rows": excl_total,
        "excluded_reason": " / ".join(reason_parts) if reason_parts else "없음",
        "used_columns": [group_by, "폐강일자"],
        "unit": "건, %",
        "cautions": "같은 과목·분반이라도 개설학과가 다르면 여러 행으로 집계됩니다.",
        "table": table,
        "chart": {
            "type": "bar",
            "labels": [str(r[group_by]) for r in top_chart],
            "values": [float(r["폐강률(%)"]) for r in top_chart],
            "dataset_label": f"{group_by}별 폐강률(%)",
        } if top_chart else None,
    }


def top_bottom_n(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 3: 폐강 제외 강좌 중 지정한 숫자 열 기준 상위·하위 N개를 추출합니다."""
    params = params or {}
    metric = params.get("metric", "신청_전")
    if metric not in ("신청_전", "제한_전", "학점"):
        metric = "신청_전"
    order = params.get("order", "top")
    n = max(1, min(int(params.get("n", 10)), 30))

    work_df, filter_desc = _apply_filters(df, params.get("filters"))
    cancelled_mask = work_df["폐강일자"].notna()
    invalid_metric_mask = work_df[metric].isna() | (work_df[metric] < 0)
    valid_df = work_df[~(cancelled_mask | invalid_metric_mask)].copy()

    ascending = order == "bottom"
    sorted_df = valid_df.sort_values(by=metric, ascending=ascending).head(n)
    cols = ["교과목번호", "교과목명", "분반", "단과대", "이수구분", "학점", "신청_전", "제한_전"]
    out_df = sorted_df[cols].astype(object).where(pd.notnull(sorted_df[cols]), None)
    rows = out_df.to_dict(orient="records")
    label = "하위" if ascending else "상위"

    excl_total = int(len(df) - len(valid_df))
    reason = f"폐강({int(cancelled_mask.sum())}건) 및 {metric} 누락/음수 제외"
    if filter_desc:
        reason = f"필터({', '.join(filter_desc)}) + " + reason

    return {
        "tool_name": "top_bottom_n",
        "title": f"{metric} {label} {n}개 강좌" + (f" [{', '.join(filter_desc)}]" if filter_desc else ""),
        "definition": f"폐강되지 않은 강좌 중 {metric} 값이 유효한 행만 대상으로 {label} {n}개를 정렬했습니다.",
        "used_rows": int(len(valid_df)),
        "excluded_rows": excl_total,
        "excluded_reason": reason,
        "used_columns": cols + ["폐강일자"],
        "unit": "명" if metric in ("신청_전", "제한_전") else "학점",
        "cautions": "동점 강좌가 있을 경우 파일 앞쪽 행이 먼저 표시될 수 있습니다.",
        "table": rows,
        "chart": {
            "type": "bar",
            "labels": [f"{r['교과목명']}({r['분반']})" for r in rows],
            "values": [float(r[metric]) for r in rows],
            "dataset_label": f"{metric} ({label} {n})",
        } if rows else None,
    }


def fill_rate(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 4: 폐강 및 제한_전 0·빈칸을 제외하고 그룹별 충원율을 계산합니다."""
    params = params or {}
    group_by = params.get("group_by", "단과대")
    if group_by not in ("단과대", "이수구분", "대상학년"):
        group_by = "단과대"

    work_df, filter_desc = _apply_filters(df, params.get("filters"))
    cancelled_mask = work_df["폐강일자"].notna()
    active_df = work_df[~cancelled_mask].copy()

    bad_cap_mask = active_df["제한_전"].isna() | (active_df["제한_전"] <= 0)
    bad_req_mask = active_df["신청_전"].isna() | (active_df["신청_전"] < 0)
    valid_df = active_df[~(bad_cap_mask | bad_req_mask)].copy()

    if len(valid_df) == 0:
        rows = []
        over_100_count = 0
    else:
        valid_df["_fill_pct"] = valid_df["신청_전"] / valid_df["제한_전"] * 100
        grouped = (
            valid_df.groupby(group_by, dropna=False)
            .agg(
                계산강좌수=("_fill_pct", "count"),
                총신청인원=("신청_전", "sum"),
                총제한인원=("제한_전", "sum"),
                평균충원율=("_fill_pct", "mean"),
                중앙값충원율=("_fill_pct", "median"),
            )
            .reset_index()
        )
        grouped["가중충원율(%)"] = (grouped["총신청인원"] / grouped["총제한인원"] * 100).round(2)
        grouped["평균충원율(%)"] = grouped["평균충원율"].round(2)
        grouped["중앙값충원율(%)"] = grouped["중앙값충원율"].round(2)
        grouped = grouped.drop(columns=["평균충원율", "중앙값충원율"]).sort_values(
            by="가중충원율(%)", ascending=False
        )
        rows = grouped.to_dict(orient="records")
        over_100_count = int((valid_df["_fill_pct"] > 100).sum())

    excluded_total = int(len(df) - len(valid_df))
    reason = f"폐강 강좌 {int(cancelled_mask.sum())}건 + 정상 강좌 중 제한_전 0 또는 빈칸 {int(bad_cap_mask.sum())}건 제외"
    if filter_desc:
        reason = f"필터({', '.join(filter_desc)}) + " + reason

    return {
        "tool_name": "fill_rate",
        "title": f"{group_by}별 충원율 (신청_전 ÷ 제한_전)" + (f" [{', '.join(filter_desc)}]" if filter_desc else ""),
        "definition": "폐강되지 않은 강좌 중 제한 인원(제한_전)이 1명 이상인 행만 대상으로, 가중충원율(총신청 ÷ 총제한 × 100)과 분반별 평균·중앙값 충원율을 계산했습니다.",
        "used_rows": int(len(valid_df)),
        "excluded_rows": excluded_total,
        "excluded_reason": reason,
        "used_columns": [group_by, "신청_전", "제한_전", "폐강일자"],
        "unit": "%, 명, 건",
        "cautions": f"신청 인원이 제한 인원보다 많아 100%를 넘는 강좌가 {over_100_count}건 포함되어 있습니다.",
        "table": rows,
        "chart": {
            "type": "bar",
            "labels": [str(r[group_by]) for r in rows[:12]],
            "values": [float(r["가중충원율(%)"]) for r in rows[:12]],
            "dataset_label": f"{group_by}별 가중충원율(%)",
        } if rows else None,
    }


def distribution(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 5: 폐강 제외 강좌의 분반당 수강신청 인원(신청_전) 구간별 분포를 계산합니다."""
    params = params or {}
    work_df, filter_desc = _apply_filters(df, params.get("filters"))
    cancelled_mask = work_df["폐강일자"].notna()
    invalid_req_mask = (~cancelled_mask) & (work_df["신청_전"].isna() | (work_df["신청_전"] < 0))
    valid_df = work_df[~(cancelled_mask | invalid_req_mask)].copy()

    bins = [-1, 0, 10, 20, 30, 50, 100, float("inf")]
    labels = ["0명", "1~10명", "11~20명", "21~30명", "31~50명", "51~100명", "101명 이상"]
    if len(valid_df) == 0:
        rows = []
    else:
        valid_df["_구간"] = pd.cut(valid_df["신청_전"], bins=bins, labels=labels)
        dist = valid_df["_구간"].value_counts(sort=False).reset_index()
        dist.columns = ["신청인원구간", "강좌수"]
        dist["비율(%)"] = (dist["강좌수"] / len(valid_df) * 100).round(2)
        rows = dist.to_dict(orient="records")

    reason = f"폐강 강좌 {int(cancelled_mask.sum())}건 및 신청_전 누락/음수 {int(invalid_req_mask.sum())}건 제외"
    if filter_desc:
        reason = f"필터({', '.join(filter_desc)}) + " + reason

    return {
        "tool_name": "distribution",
        "title": "분반당 수강신청 인원(신청_전) 구간별 분포" + (f" [{', '.join(filter_desc)}]" if filter_desc else ""),
        "definition": "폐강되지 않은 강좌를 대상으로 분반당 신청 인원(신청_전)을 7개 구간으로 나누어 빈도를 계산했습니다.",
        "used_rows": int(len(valid_df)),
        "excluded_rows": int(len(df) - len(valid_df)),
        "excluded_reason": reason,
        "used_columns": ["신청_전", "폐강일자"],
        "unit": "건, %",
        "cautions": "같은 과목·분반이 개설학과별로 나뉜 경우 각각 1건으로 집계됩니다.",
        "table": rows,
        "chart": {
            "type": "bar",
            "labels": [str(r["신청인원구간"]) for r in rows],
            "values": [int(r["강좌수"]) for r in rows],
            "dataset_label": "구간별 강좌 수(건)",
        } if rows else None,
    }


def weekday_building(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 6: 시간표를 쪼개 요일별·건물별 분반 수를 계산합니다."""
    params = params or {}
    work_df, filter_desc = _apply_filters(df, params.get("filters"))
    empty_tt_mask = work_df["시간표"].isna()
    valid_df = work_df[~empty_tt_mask].copy()

    weekday_order = ["월", "화", "수", "목", "금", "토", "일"]
    weekday_counts = {d: 0 for d in weekday_order}
    building_counts: dict[str, int] = {}

    for tt in valid_df["시간표"]:
        blocks = re.findall(r"([^(),]+?)\(([^)]+)\)", str(tt))
        days_in_course = set()
        buildings_in_course = set()
        for room, inside in blocks:
            m = re.match(r"^([가-힣A-Za-z]+?)(?:B?\d.*)?$", room.strip())
            bldg = m.group(1) if m else room.strip()
            if bldg:
                buildings_in_course.add(bldg)
            for d in re.findall(r"[월화수목금토일]", inside):
                days_in_course.add(d)

        for d in days_in_course:
            weekday_counts[d] += 1
        for b in buildings_in_course:
            building_counts[b] = building_counts.get(b, 0) + 1

    table = []
    for d in weekday_order:
        if weekday_counts[d] > 0:
            table.append({"구분": "요일", "항목": f"{d}요일", "분반수": weekday_counts[d]})
    for b, cnt in sorted(building_counts.items(), key=lambda x: x[1], reverse=True):
        table.append({"구분": "건물", "항목": b, "분반수": cnt})

    weekday_rows = [r for r in table if r["구분"] == "요일"]
    reason = f"시간표가 빈 칸인 행 {int(empty_tt_mask.sum())}건 제외"
    if filter_desc:
        reason = f"필터({', '.join(filter_desc)}) + " + reason

    return {
        "tool_name": "weekday_building",
        "title": "요일별·건물별 분반 수" + (f" [{', '.join(filter_desc)}]" if filter_desc else ""),
        "definition": "시간표가 있는 행을 대상으로 괄호 앞 건물명과 괄호 안 요일을 분리해, 각 요일·건물에 배정된 분반(행) 수를 셌습니다.",
        "used_rows": int(len(valid_df)),
        "excluded_rows": int(len(df) - len(valid_df)),
        "excluded_reason": reason,
        "used_columns": ["시간표"],
        "unit": "건(분반)",
        "cautions": "한 강좌가 여러 요일이나 여러 건물에서 열리면 해당 요일/건물마다 각각 1건씩 중복 집계됩니다.",
        "table": table,
        "chart": {
            "type": "bar",
            "labels": [r["항목"] for r in weekday_rows],
            "values": [int(r["분반수"]) for r in weekday_rows],
            "dataset_label": "요일별 분반 수(건)",
        } if weekday_rows else None,
    }


def compare_one_vs_all(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 7: 선택한 대상(단과대 등)과 전체 강좌의 평균·중앙값을 비교합니다."""
    params = params or {}
    group_by = params.get("group_by", "단과대")
    if group_by not in ("단과대", "이수구분", "대상학년"):
        group_by = "단과대"

    work_df = _ensure_college_column(df).copy()
    cancelled_mask = work_df["폐강일자"].notna()
    invalid_mask = work_df["신청_전"].isna() | (work_df["신청_전"] < 0) | work_df["제한_전"].isna() | (work_df["제한_전"] < 0)
    exclude_mask = cancelled_mask | invalid_mask | work_df[group_by].isna()
    valid_df = work_df[~exclude_mask].copy()

    target = params.get("target")
    if not target or target not in valid_df[group_by].values:
        target = str(valid_df[group_by].value_counts().idxmax())

    target_df = valid_df[valid_df[group_by] == target]

    table = [
        {
            "비교구분": f"선택: {target}",
            "강좌수": int(len(target_df)),
            "신청인원_평균": round(float(target_df["신청_전"].mean()), 2),
            "신청인원_중앙값": round(float(target_df["신청_전"].median()), 2),
            "제한인원_평균": round(float(target_df["제한_전"].mean()), 2),
            "제한인원_중앙값": round(float(target_df["제한_전"].median()), 2),
        },
        {
            "비교구분": "전체(폐강 제외)",
            "강좌수": int(len(valid_df)),
            "신청인원_평균": round(float(valid_df["신청_전"].mean()), 2),
            "신청인원_중앙값": round(float(valid_df["신청_전"].median()), 2),
            "제한인원_평균": round(float(valid_df["제한_전"].mean()), 2),
            "제한인원_중앙값": round(float(valid_df["제한_전"].median()), 2),
        },
    ]

    return {
        "tool_name": "compare_one_vs_all",
        "title": f"{group_by} '{target}' vs 전체 비교",
        "definition": f"폐강되지 않은 유효 강좌를 대상으로 '{target}'과 전체의 신청 인원(신청_전)·제한 인원(제한_전) 평균 및 중앙값을 비교했습니다.",
        "used_rows": int(len(valid_df)),
        "excluded_rows": int(exclude_mask.sum()),
        "excluded_reason": f"폐강 강좌({int(cancelled_mask.sum())}건) 및 인원 누락/음수 행 제외",
        "used_columns": [group_by, "신청_전", "제한_전", "폐강일자"],
        "unit": "명, 건",
        "cautions": "대상을 별도로 지정하지 않으면 강좌 수가 가장 많은 그룹이 자동 선택됩니다.",
        "table": table,
        "chart": {
            "type": "bar",
            "labels": [f"{target} 신청평균", "전체 신청평균", f"{target} 신청중앙값", "전체 신청중앙값"],
            "values": [
                table[0]["신청인원_평균"],
                table[1]["신청인원_평균"],
                table[0]["신청인원_중앙값"],
                table[1]["신청인원_중앙값"],
            ],
            "dataset_label": "신청 인원 비교(명)",
        },
    }


def custom_query(df: pd.DataFrame, params: dict | None = None) -> dict:
    """도구 8: 사용자 질문에 맞춰 안전한 필터와 그룹 집계 또는 강좌 목록을 계산합니다."""
    params = params or {}
    filters = params.get("filters") if isinstance(params.get("filters"), dict) else {}
    group_by = params.get("group_by")
    if group_by not in ("단과대", "이수구분", "대상학년", "대표교수직급", "학점", "폐강사유"):
        group_by = None

    metric = params.get("metric", "신청_전")
    if metric not in ("신청_전", "제한_전", "학점"):
        metric = "신청_전"

    limit = max(1, min(int(params.get("limit", 15)), 30))

    filtered_df, filter_desc = _apply_filters(df, filters)
    invalid_metric = filtered_df[metric].isna() | (filtered_df[metric] < 0)
    valid_df = filtered_df[~invalid_metric].copy()

    if group_by:
        grp_valid = valid_df[valid_df[group_by].notna()].copy()
        if len(grp_valid) == 0:
            table = []
        else:
            grouped = (
                grp_valid.groupby(group_by, dropna=False)
                .agg(
                    강좌수=(metric, "count"),
                    합계=(metric, "sum"),
                    평균=(metric, "mean"),
                    중앙값=(metric, "median"),
                )
                .reset_index()
            )
            grouped["평균"] = grouped["평균"].round(2)
            grouped["중앙값"] = grouped["중앙값"].round(2)
            grouped = grouped.sort_values(by="강좌수", ascending=False).head(limit)
            table = grouped.to_dict(orient="records")
        chart = (
            {
                "type": "bar",
                "labels": [str(r[group_by]) for r in table[:12]],
                "values": [int(r["강좌수"]) for r in table[:12]],
                "dataset_label": f"{group_by}별 강좌 수(건)",
            }
            if table
            else None
        )
        used_count = int(len(grp_valid))
        used_cols = [group_by, metric]
    else:
        cols = ["교과목번호", "교과목명", "분반", "단과대", "이수구분", "학점", "신청_전", "제한_전", "시간표", "폐강일자"]
        out = valid_df.sort_values(by=metric, ascending=False).head(limit)[cols]
        out = out.astype(object).where(pd.notnull(out), None)
        table = out.to_dict(orient="records")
        chart = None
        used_count = int(len(valid_df))
        used_cols = cols

    excl_total = int(len(df) - used_count)
    cond_text = ", ".join(filter_desc) if filter_desc else "전체 데이터"
    return {
        "tool_name": "custom_query",
        "title": f"맞춤 분석 ({cond_text})" + (f" — {group_by}별 집계" if group_by else ""),
        "definition": f"조건({cond_text})에 맞는 유효 강좌를 대상으로 " + (f"{group_by}별 {metric} 집계를 계산했습니다." if group_by else f"상위 {limit}개 강좌를 조회했습니다."),
        "used_rows": used_count,
        "excluded_rows": excl_total,
        "excluded_reason": f"조건 외 행 및 {metric} 누락/음수 행 제외" if excl_total > 0 else "없음",
        "used_columns": used_cols,
        "unit": "건, 명" if metric in ("신청_전", "제한_전") else "건, 학점",
        "cautions": "AI는 조건과 도구 이름만 선택했고, 모든 수치는 pandas가 직접 계산했습니다.",
        "table": table,
        "chart": chart,
    }


TOOL_FUNCTIONS = {
    "quality_report": quality_report,
    "group_rate": group_rate,
    "top_bottom_n": top_bottom_n,
    "fill_rate": fill_rate,
    "distribution": distribution,
    "weekday_building": weekday_building,
    "compare_one_vs_all": compare_one_vs_all,
    "custom_query": custom_query,
}


def run_tool(df: pd.DataFrame, tool_name: str, params: dict | None = None) -> dict:
    """도구 이름으로 등록된 pandas 분석 함수를 실행합니다."""
    if tool_name not in TOOL_FUNCTIONS:
        raise ValueError(f"허용되지 않은 도구 이름입니다: {tool_name}")
    return TOOL_FUNCTIONS[tool_name](df, params)
